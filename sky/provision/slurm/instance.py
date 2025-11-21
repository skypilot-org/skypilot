"""Slurm instance provisioning."""

import re
import tempfile
import time
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

SHARED_SKY_DIRECTORY_NAME = '.sky_clusters'
# TODO(kevin): This assumes $HOME is in a shared filesystem.
# We should probably make it configurable, and add a check
# during sky check.
SHARED_ROOT_SKY_DIRECTORY = f'~/{SHARED_SKY_DIRECTORY_NAME}'

POLL_INTERVAL_SECONDS = 2
# Default KillWait is 30 seconds, so we add some buffer time here.
_TIMEOUT_SECONDS_FOR_JOB_TERMINATION = 60


def _sky_cluster_home_dir(cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot cluster's home directory path on the Slurm cluster."""
    return f'{SHARED_ROOT_SKY_DIRECTORY}/{cluster_name_on_cloud}'


def _skypilot_runtime_dir(cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot runtime directory path on the Slurm cluster."""
    return f'/tmp/{cluster_name_on_cloud}'


@timeline.event
def _create_virtual_instance(
        region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Creates a Slurm virtual instance from the config.

    A Slurm virtual instance is created by submitting a long-running
    job with sbatch, to mimic a cloud VM.
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

    # COMPLETING state occurs when a job is being terminated - during this phase,
    # slurmd sends SIGTERM to tasks, waits for KillWait period, sends SIGKILL if
    # needed, runs epilog scripts, and notifies slurmctld. This typically happens
    # when a previous job with the same name is being cancelled or has finished.
    # Jobs can get stuck in COMPLETING if epilog scripts hang or tasks don't
    # respond to signals, so we wait with a timeout.
    completing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        ['completing'],
    )
    start_time = time.time()
    while (completing_jobs and
           time.time() - start_time < _TIMEOUT_SECONDS_FOR_JOB_TERMINATION):
        logger.debug(f'Found {len(completing_jobs)} completing jobs. '
                     f'Waiting for them to finish: {completing_jobs}')
        time.sleep(POLL_INTERVAL_SECONDS)
        completing_jobs = client.query_jobs(
            cluster_name_on_cloud,
            ['completing'],
        )
    if completing_jobs:
        # TODO(kevin): Automatically handle this, following the suggestions in
        # https://slurm.schedmd.com/troubleshoot.html#completing
        raise RuntimeError(f'Found {len(completing_jobs)} jobs still in '
                           'completing state after '
                           f'{_TIMEOUT_SECONDS_FOR_JOB_TERMINATION}s. '
                           'This is typically due to non-killable processes '
                           'associated with the job.')

    # Check if job already exists
    existing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        ['pending', 'running'],
    )
    if existing_jobs:
        assert len(
            existing_jobs
        ) == 1, f'Multiple jobs found with name {cluster_name_on_cloud}: {existing_jobs}'

        job_id = existing_jobs[0]
        logger.debug(
            f'Job with name {cluster_name_on_cloud} already exists (JOBID: {job_id})'
        )

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
        # By default stdout and stderr will be written to $HOME/slurm-%j.out
        # (because we invoke sbatch from $HOME)
        # Redirect elsewhere to not pollute the home directory.
        # Note: The output of the batch step does not contain any
        # useful logs typically, since we just do a sleep infinity.
        f'#SBATCH --output={SHARED_SKY_DIRECTORY_NAME}/slurm-%j.out',
        f'#SBATCH --error={SHARED_SKY_DIRECTORY_NAME}/slurm-%j.out',
        f'#SBATCH --nodes={num_nodes}',
        f'#SBATCH --cpus-per-task={int(resources["cpus"])}',
        f'#SBATCH --mem={int(resources["memory"])}G',
    ]
    if (accelerator_type is not None and accelerator_type.upper() != 'NONE' and
            accelerator_count > 0):
        provision_lines.append(f'#SBATCH --gres=gpu:{accelerator_type.lower()}:'
                               f'{accelerator_count}')

    sky_dir = _sky_cluster_home_dir(cluster_name_on_cloud)
    provision_lines.extend([
        '',
        # Create sky directory for the cluster.
        # TODO(kevin): Since this is run inside the sbatch script, failures
        # will not be surfaced in a synchronous way. We should add a check
        # to verify the creation of the directory.
        f'mkdir -p {sky_dir}',
        # Suppress login messages.
        f'touch {sky_dir}/.hushlogin',
        'sleep infinity',
    ])
    provision_script = '\n'.join(provision_lines)

    # To bootstrap things, we need to do it with SSHCommandRunner first.
    # SlurmCommandRunner is for after the virtual instances are created.
    controller_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )

    # Rsync the provision script to a temporary location on the controller node
    with tempfile.NamedTemporaryFile(mode='w',
                                     prefix='sky_provision_',
                                     delete=True) as f:
        f.write(provision_script)
        f.flush()
        src_path = f.name
        tgt_path = f'/tmp/sky_provision_{cluster_name_on_cloud}.sh'
        controller_node_runner.rsync(src_path,
                                     tgt_path,
                                     up=True,
                                     stream_logs=False)

    job_id = client.submit_job(partition, cluster_name_on_cloud, tgt_path)
    logger.debug(f'Successfully submitted Slurm job {job_id} for cluster '
                 f'{cluster_name_on_cloud} with {num_nodes} nodes')

    nodes, _ = client.get_job_nodes(job_id, wait=True)
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
        'completing': status_lib.ClusterStatus.UP,
        'completed': None,
        'cancelled': None,
        'failed': status_lib.ClusterStatus.INIT,
    }

    statuses = {}
    for state, sky_status in status_map.items():
        if non_terminated_only and sky_status is None:
            continue

        jobs = client.query_jobs(
            cluster_name_on_cloud,
            [state],
        )

        for job_id in jobs:
            statuses[job_id] = (sky_status, None)

    return statuses


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster (Slurm in this case)."""
    return _create_virtual_instance(region, cluster_name_on_cloud, config)


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
        cluster_name_on_cloud,
        ['running'],
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
                    constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
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

    client.cancel_jobs_by_name(cluster_name_on_cloud)

    # Clean up the sky directory.
    sky_dir = _sky_cluster_home_dir(cluster_name_on_cloud)
    skypilot_runtime_dir = _skypilot_runtime_dir(cluster_name_on_cloud)
    controller_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
    )
    cleanup_cmd = f'rm -rf {sky_dir} {skypilot_runtime_dir}'
    rc, _, stderr = controller_node_runner.run(cleanup_cmd,
                                               require_outputs=True)
    if rc != 0:
        logger.warning(
            f'Failed to clean up {sky_dir} and {skypilot_runtime_dir}: {stderr}'
        )


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

    cluster_name_on_cloud = cluster_info.get_head_instance().tags.get(
        constants.TAG_SKYPILOT_CLUSTER_NAME, None)
    assert cluster_name_on_cloud is not None, cluster_info

    # There can only be one InstanceInfo per instance_id.
    instances = [
        instance_infos[0] for instance_infos in cluster_info.instances.values()
    ]

    # Note: For Slurm, the external IP for all instances is the same,
    # it is the login node's. The internal IP is the private IP of the node.
    runners = [
        command_runner.SlurmCommandRunner(
            (instance_info.external_ip, instance_info.ssh_port),
            sky_dir=_sky_cluster_home_dir(cluster_name_on_cloud),
            skypilot_runtime_dir=_skypilot_runtime_dir(cluster_name_on_cloud),
            **credentials) for instance_info in instances
    ]

    return runners
