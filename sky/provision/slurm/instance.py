"""Slurm instance provisioning."""

import collections
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import slurm
from sky.provision import common
from sky.provision import constants
from sky.provision.slurm import utils as slurm_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import timeline

logger = sky_logging.init_logger(__name__)


def _get_sky_cluster_dir(cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot cluster's home directory path on the Slurm cluster."""
    return f'~/sky/{cluster_name_on_cloud}'


@timeline.event
def _create_jobs(region: str, cluster_name_on_cloud: str,
                 config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create Slurm virtual instances based on the config.

    A Slurm virtual instance is created by submitting a long-running job.
    """
    provider_config = config.provider_config
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    partition = slurm_utils.get_partition_from_config(provider_config)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    # Check if job already exists
    existing_jobs = client.query_jobs(
        ['pending', 'running'],
        cluster_name_on_cloud,
    )

    if existing_jobs:
        if len(existing_jobs) > 1:
            raise RuntimeError(
                f'Multiple jobs found with name "{cluster_name_on_cloud}": '
                f'{existing_jobs}. This indicates a resource leak. '
                'Use "sky down" to terminate the cluster.')

        job_id = existing_jobs[0]
        logger.info(f'Job {job_id} already exists for cluster '
                    f'{cluster_name_on_cloud}')

        # Wait for nodes to be allocated (job might be in PENDING state)
        nodes, _ = client.get_job_nodes(job_id, wait=True)
        return common.ProvisionRecord(provider_name='slurm',
                                      region=region,
                                      zone=None,
                                      cluster_name=cluster_name_on_cloud,
                                      head_instance_id=slurm_utils.instance_id(
                                          job_id, nodes[0]),
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    resources = config.node_config
    num_nodes = config.count
    # TODO(kevin): Support multi-node.
    assert num_nodes == 1

    accelerator_type = resources.get('accelerator_type')
    accelerator_count_raw = resources.get('accelerator_count')
    try:
        accelerator_count = int(accelerator_count_raw)
    except (TypeError, ValueError):
        accelerator_count = 0
    provision_lines = [
        '#!/bin/bash',
        f'#SBATCH --job-name={cluster_name_on_cloud}',
        f'#SBATCH --output={cluster_name_on_cloud}.out',
        f'#SBATCH --error={cluster_name_on_cloud}.err',
        f'#SBATCH --nodes={num_nodes}',
        f'#SBATCH --cpus-per-task={int(resources["cpus"])}',
        f'#SBATCH --mem={int(resources["memory"])}G',
    ]
    if (accelerator_type and accelerator_type.upper() != 'NONE' and
            accelerator_count > 0):
        provision_lines.append(f'#SBATCH --gres=gpu:{accelerator_type.lower()}:'
                               f'{accelerator_count}')
    provision_lines.extend(['', 'sleep infinity'])
    provision_script = '\n'.join(provision_lines)

    # To bootstrap things, we need to do it with SSHCommandRunner first.
    # SlurmCommandRunner is for after the virtual instances are created.
    controller_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    # TODO(kevin): Make this more robust and configurable.
    sky_dir = _get_sky_cluster_dir(cluster_name_on_cloud)
    # Create sky directory and .hushlogin (to suppress MOTD/login messages)
    setup_cmd = f'mkdir -p {sky_dir} && touch {sky_dir}/.hushlogin'
    rc, _, stderr = controller_node_runner.run(setup_cmd, require_outputs=True)
    if rc != 0:
        raise RuntimeError(f'Failed to create directory {sky_dir}: {stderr}\n'
                           f'Command: {setup_cmd}\n'
                           f'Return code: {rc}')

    with tempfile.NamedTemporaryFile(mode='w',
                                     prefix='sky_provision_',
                                     delete=True) as f:
        f.write(provision_script)
        f.flush()
        src_path = f.name
        tgt_path = f'{sky_dir}/provision.sh'
        controller_node_runner.rsync(src_path, tgt_path, up=True, stream_logs=False)

    job_id = client.submit_job(partition, cluster_name_on_cloud, tgt_path)
    logger.debug(f'Successfully submitted Slurm job {job_id} for cluster '
                 f'{cluster_name_on_cloud} with {num_nodes} nodes')

    nodes, _ = client.get_job_nodes(job_id)
    created_instance_ids = [
        slurm_utils.instance_id(job_id, node) for node in nodes
    ]

    return common.ProvisionRecord(provider_name='slurm',
                                  region=region,
                                  zone=None,
                                  cluster_name=cluster_name_on_cloud,
                                  head_instance_id=created_instance_ids[0],
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


@common_utils.retry
def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del retry_if_missing  # Unused for SLURM
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)

    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    # Map Slurm job states to SkyPilot ClusterStatus
    # Slurm states: https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
    status_map = {
        'pending': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        'completed': None,
        'cancelled': None,
        'failed': status_lib.ClusterStatus.INIT,
    }

    statuses = {}
    for state, sky_status in status_map.items():
        if non_terminated_only and sky_status is None:
            continue

        jobs = client.query_jobs(
            state_filters=[state],
            job_name=cluster_name_on_cloud,
        )

        for job_id in jobs:
            statuses[job_id] = (sky_status, None)

    return statuses


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster (Slurm in this case)."""
    return _create_jobs(region, cluster_name_on_cloud, config)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    del region, cluster_name_on_cloud, state
    # We already wait for the instances to be running in run_instances.
    # So we don't need to wait here.


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region
    assert provider_config is not None, cluster_name_on_cloud

    # The SSH host is the remote machine running slurmctld daemon.
    # Cross-cluster operations are supported by interacting with
    # the current controller. For details, please refer to
    # https://slurm.schedmd.com/multi_cluster.html.
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    # Find running job for this cluster
    running_jobs = client.query_jobs(
        state_filters=['running'],
        job_name=cluster_name_on_cloud,
    )

    if not running_jobs:
        # No running jobs found - cluster may be in pending or terminated state
        return common.ClusterInfo(
            instances={},
            head_instance_id=None,
            ssh_user=ssh_user,
            provider_name='slurm',
            provider_config=provider_config,
        )
    assert len(
        running_jobs
    ) == 1, f'Multiple running jobs found for cluster {cluster_name_on_cloud}: {running_jobs}'

    job_id = running_jobs[0]
    # Running jobs should already have nodes allocated, so don't wait
    nodes, node_ips = client.get_job_nodes(job_id, wait=False)

    instances = {
        f'{slurm_utils.instance_id(job_id, node)}': [
            common.InstanceInfo(
                instance_id=slurm_utils.instance_id(job_id, node),
                internal_ip=node_ip,
                external_ip=ssh_host,
                ssh_port=ssh_port,
                tags={
                    'sky_dir': _get_sky_cluster_dir(cluster_name_on_cloud),
                    'job_id': job_id,
                },
            )
        ] for node, node_ip in zip(nodes, node_ips)
    }

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=slurm_utils.instance_id(job_id, nodes[0]),
        ssh_user=ssh_user,
        provider_name='slurm',
        provider_config=provider_config,
    )


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Keep the Slurm virtual instances running."""
    raise NotImplementedError()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud

    if worker_only:
        logger.warning(
            'worker_only=True is not supported for Slurm, this is a no-op.')
        return

    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    partition = slurm_utils.get_partition_from_config(provider_config)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    client.cancel_job(cluster_name_on_cloud)


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    pass


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    pass


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Dict[str, Any],
) -> List[command_runner.CommandRunner]:
    """Get a command runner for the given cluster."""
    assert cluster_info.provider_config is not None, cluster_info

    if cluster_info.head_instance_id is None:
        # No running job found
        return []

    job_id = cluster_info.get_head_instance().tags.get('job_id', None)
    # There can only be one InstanceInfo per instance_id, so we can just get the first one.
    instances = [
        instance_infos[0] for instance_infos in cluster_info.instances.values()
    ]

    # Note: For Slurm, the external IP for all instances is the same,
    # it is the login node's. The internal IP is the private IP of the node.
    runners = [
        command_runner.SlurmCommandRunner(
            (instance_info.external_ip, instance_info.ssh_port),
            sky_dir=instance_info.tags.get('sky_dir', None),
            **credentials) for instance_info in instances
    ]

    return runners
