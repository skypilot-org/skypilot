"""Slurm instance provisioning."""

import tempfile
import textwrap
import time
from typing import Any, cast, Dict, List, Optional, Tuple

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import slurm
from sky.provision import common
from sky.provision import constants
from sky.provision.slurm import utils as slurm_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = sky_logging.init_logger(__name__)

# TODO(kevin): This assumes $HOME is in a shared filesystem.
# We should probably make it configurable, and add a check
# during sky check.
SHARED_ROOT_SKY_DIRECTORY = '~/.sky_clusters'
PROVISION_SCRIPTS_DIRECTORY_NAME = '.sky_provision'
PROVISION_SCRIPTS_DIRECTORY = f'~/{PROVISION_SCRIPTS_DIRECTORY_NAME}'

POLL_INTERVAL_SECONDS = 2
# Default KillWait is 30 seconds, so we add some buffer time here.
_JOB_TERMINATION_TIMEOUT_SECONDS = 60
_SKY_DIR_CREATION_TIMEOUT_SECONDS = 30


def _sky_cluster_home_dir(cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot cluster's home directory path on the Slurm cluster.

    This path is assumed to be on a shared NFS mount accessible by all nodes.
    To support clusters with non-NFS home directories, we would need to let
    users specify an NFS-backed "working directory" or use a different
    coordination mechanism.
    """
    return f'{SHARED_ROOT_SKY_DIRECTORY}/{cluster_name_on_cloud}'


def _sbatch_provision_script_path(filename: str) -> str:
    """Returns the path to the sbatch provision script on the login node."""
    # Put sbatch script in $HOME instead of /tmp as there can be
    # multiple login nodes, and different SSH connections
    # can land on different login nodes.
    return f'{PROVISION_SCRIPTS_DIRECTORY}/{filename}'


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
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
    partition = slurm_utils.get_partition_from_config(provider_config)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
    )

    # COMPLETING state occurs when a job is being terminated - during this
    # phase, slurmd sends SIGTERM to tasks, waits for KillWait period, sends
    # SIGKILL if needed, runs epilog scripts, and notifies slurmctld. This
    # typically happens when a previous job with the same name is being
    # cancelled or has finished. Jobs can get stuck in COMPLETING if epilog
    # scripts hang or tasks don't respond to signals, so we wait with a
    # timeout.
    completing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        ['completing'],
    )
    start_time = time.time()
    while (completing_jobs and
           time.time() - start_time < _JOB_TERMINATION_TIMEOUT_SECONDS):
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
                           f'{_JOB_TERMINATION_TIMEOUT_SECONDS}s. '
                           'This is typically due to non-killable processes '
                           'associated with the job.')

    # Check if job already exists
    existing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        ['pending', 'running'],
    )

    # Get provision_timeout from config. If not specified, use None,
    # which will use the default timeout specified in the Slurm adaptor.
    provision_timeout = skypilot_config.get_effective_region_config(
        cloud='slurm',
        region=region,
        keys=('provision_timeout',),
        default_value=None)

    if existing_jobs:
        assert len(existing_jobs) == 1, (
            f'Multiple jobs found with name {cluster_name_on_cloud}: '
            f'{existing_jobs}')

        job_id = existing_jobs[0]
        logger.debug(f'Job with name {cluster_name_on_cloud} already exists '
                     f'(JOBID: {job_id})')

        # Wait for nodes to be allocated (job might be in PENDING state)
        nodes, _ = client.get_job_nodes(job_id,
                                        wait=True,
                                        timeout=provision_timeout)
        return common.ProvisionRecord(provider_name='slurm',
                                      region=region,
                                      zone=partition,
                                      cluster_name=cluster_name_on_cloud,
                                      head_instance_id=slurm_utils.instance_id(
                                          job_id, nodes[0]),
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    resources = config.node_config

    # Note: By default Slurm terminates the entire job allocation if any node
    # fails in its range of allocated nodes.
    # In the future we can consider running sbatch with --no-kill to not
    # automatically terminate a job if one of the nodes it has been
    # allocated fails.
    num_nodes = config.count

    accelerator_type = resources.get('accelerator_type')
    accelerator_count_raw = resources.get('accelerator_count')
    try:
        accelerator_count = int(
            accelerator_count_raw) if accelerator_count_raw is not None else 0
    except (TypeError, ValueError):
        accelerator_count = 0

    skypilot_runtime_dir = _skypilot_runtime_dir(cluster_name_on_cloud)
    sky_home_dir = _sky_cluster_home_dir(cluster_name_on_cloud)
    ready_signal = f'{sky_home_dir}/.sky_sbatch_ready'
    slurm_marker_file = f'{sky_home_dir}/{slurm_utils.SLURM_MARKER_FILE}'

    # Build the sbatch script
    gpu_directive = ''
    if (accelerator_type is not None and accelerator_type.upper() != 'NONE' and
            accelerator_count > 0):
        gpu_directive = (f'#SBATCH --gres=gpu:{accelerator_type}:'
                         f'{accelerator_count}')

    # By default stdout and stderr will be written to $HOME/slurm-%j.out
    # (because we invoke sbatch from $HOME). Redirect elsewhere to not pollute
    # the home directory.
    provision_script = textwrap.dedent(f"""\
        #!/bin/bash
        #SBATCH --job-name={cluster_name_on_cloud}
        #SBATCH --output={PROVISION_SCRIPTS_DIRECTORY_NAME}/slurm-%j.out
        #SBATCH --error={PROVISION_SCRIPTS_DIRECTORY_NAME}/slurm-%j.out
        #SBATCH --nodes={num_nodes}
        #SBATCH --wait-all-nodes=1
        # Let the job be terminated rather than requeued implicitly.
        #SBATCH --no-requeue
        #SBATCH --cpus-per-task={int(resources["cpus"])}
        #SBATCH --mem={int(resources["memory"])}G
        {gpu_directive}

        # Cleanup function to remove cluster dirs on job termination.
        cleanup() {{
            # The Skylet is daemonized, so it is not automatically terminated when
            # the Slurm job is terminated, we need to kill it manually.
            echo "Terminating Skylet..."
            if [ -f "{skypilot_runtime_dir}/.sky/skylet_pid" ]; then
                kill $(cat "{skypilot_runtime_dir}/.sky/skylet_pid") 2>/dev/null || true
            fi
            echo "Cleaning up sky directories..."
            # Clean up sky runtime directory on each node.
            # NOTE: We can do this because --nodes for both this srun and the
            # sbatch is the same number. Otherwise, there are no guarantees
            # that this srun will run on the same subset of nodes as the srun
            # that created the sky directories.
            srun --nodes={num_nodes} rm -rf {skypilot_runtime_dir}
            rm -rf {sky_home_dir}
        }}
        trap cleanup TERM

        # Create sky home directory for the cluster.
        mkdir -p {sky_home_dir}
        # Create sky runtime directory on each node.
        srun --nodes={num_nodes} mkdir -p {skypilot_runtime_dir}
        # Marker file to indicate we're in a Slurm cluster.
        touch {slurm_marker_file}
        # Suppress login messages.
        touch {sky_home_dir}/.hushlogin
        # Signal that the sbatch script has completed setup.
        touch {ready_signal}
        sleep infinity
        """)

    # To bootstrap things, we need to do it with SSHCommandRunner first.
    # SlurmCommandRunner is for after the virtual instances are created.
    login_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
    )

    cmd = f'mkdir -p {PROVISION_SCRIPTS_DIRECTORY}'
    rc, stdout, stderr = login_node_runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    subprocess_utils.handle_returncode(
        rc,
        cmd,
        'Failed to create provision scripts directory on login node.',
        stderr=f'{stdout}\n{stderr}')
    # Rsync the provision script to the login node
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=True) as f:
        f.write(provision_script)
        f.flush()
        src_path = f.name
        tgt_path = _sbatch_provision_script_path(f'{cluster_name_on_cloud}.sh')
        login_node_runner.rsync(src_path, tgt_path, up=True, stream_logs=False)

    job_id = client.submit_job(partition, cluster_name_on_cloud, tgt_path)
    logger.debug(f'Successfully submitted Slurm job {job_id} to partition '
                 f'{partition} for cluster {cluster_name_on_cloud} '
                 f'with {num_nodes} nodes')

    nodes, _ = client.get_job_nodes(job_id,
                                    wait=True,
                                    timeout=provision_timeout)
    created_instance_ids = [
        slurm_utils.instance_id(job_id, node) for node in nodes
    ]

    # Wait for the sbatch script to create the cluster's sky directories,
    # to avoid a race condition where post-provision commands try to
    # access the directories before they are created.
    ready_check_cmd = (f'end=$((SECONDS+{_SKY_DIR_CREATION_TIMEOUT_SECONDS})); '
                       f'while [ ! -f {ready_signal} ]; do '
                       'if (( SECONDS >= end )); then '
                       'exit 1; fi; '
                       'sleep 0.5; '
                       'done')
    rc, stdout, stderr = login_node_runner.run(ready_check_cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    subprocess_utils.handle_returncode(
        rc,
        ready_check_cmd,
        'Failed to verify sky directories creation.',
        stderr=f'{stdout}\n{stderr}')

    return common.ProvisionRecord(provider_name='slurm',
                                  region=region,
                                  zone=partition,
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
    del cluster_name, retry_if_missing  # Unused for Slurm
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)

    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
    )

    # Map Slurm job states to SkyPilot ClusterStatus
    # Slurm states:
    # https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
    # TODO(kevin): Include more states here.
    status_map = {
        'pending': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        'completing': status_lib.ClusterStatus.UP,
        'completed': None,
        'cancelled': None,
        # NOTE: Jobs that get cancelled (from sky down) will go to failed state
        # with the reason 'NonZeroExitCode' and remain in the squeue output for
        # a while.
        'failed': None,
        'node_fail': None,
    }

    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for state, sky_status in status_map.items():
        jobs = client.query_jobs(
            cluster_name_on_cloud,
            [state],
        )

        for job_id in jobs:
            if state in ('pending', 'failed', 'node_fail', 'cancelled',
                         'completed'):
                reason = client.get_job_reason(job_id)
                if non_terminated_only and sky_status is None:
                    # TODO(kevin): For better UX, we should also find out
                    # which node(s) exactly that failed if it's a node_fail
                    # state.
                    logger.debug(f'Job {job_id} is terminated, but '
                                 'query_instances is called with '
                                 f'non_terminated_only=True. State: {state}, '
                                 f'Reason: {reason}')
                    continue
                statuses[job_id] = (sky_status, reason)
            else:
                nodes, _ = client.get_job_nodes(job_id, wait=False)
                for node in nodes:
                    instance_id = slurm_utils.instance_id(job_id, node)
                    statuses[instance_id] = (sky_status, None)

        # TODO(kevin): Query sacct too to get more historical job info.
        # squeue only includes completed jobs that finished in the last
        # MinJobAge seconds (default 300s). Or could be earlier if it
        # reaches MaxJobCount first (default 10_000).

    return statuses


def run_instances(
        region: str,
        cluster_name: str,  # pylint: disable=unused-argument
        cluster_name_on_cloud: str,
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
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
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
    assert len(running_jobs) == 1, (
        f'Multiple running jobs found for cluster {cluster_name_on_cloud}: '
        f'{running_jobs}')

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
                    'node': node,
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

    # Check if we are running inside a Slurm cluster (only happens with
    # autodown, where the Skylet invokes terminate_instances on the remote
    # cluster). In this case, use local execution instead of SSH.
    # This assumes that the compute node is able to run scancel.
    # TODO(kevin): Validate this assumption.
    if slurm_utils.is_inside_slurm_cluster():
        logger.debug('Running inside a Slurm cluster, using local execution')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
    else:
        ssh_config_dict = provider_config['ssh']
        ssh_host = ssh_config_dict['hostname']
        ssh_port = int(ssh_config_dict['port'])
        ssh_user = ssh_config_dict['user']
        ssh_private_key = ssh_config_dict['private_key']
        ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
        ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)

        client = slurm.SlurmClient(
            ssh_host,
            ssh_port,
            ssh_user,
            ssh_private_key,
            ssh_proxy_command=ssh_proxy_command,
            ssh_proxy_jump=ssh_proxy_jump,
        )
    jobs_state = client.get_jobs_state_by_name(cluster_name_on_cloud)
    if not jobs_state:
        logger.debug(f'Job for cluster {cluster_name_on_cloud} not found, '
                     'it may have been terminated.')
        return
    assert len(jobs_state) == 1, (
        f'Multiple jobs found for cluster {cluster_name_on_cloud}: {jobs_state}'
    )

    job_state = jobs_state[0].strip()
    # Terminal states where scancel is not needed or will fail.
    terminal_states = {
        'COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'PREEMPTED',
        'SPECIAL_EXIT'
    }
    if job_state in terminal_states:
        logger.debug(
            f'Job for cluster {cluster_name_on_cloud} is already in a terminal '
            f'state {job_state}. No action needed.')
        return

    if job_state in ('PENDING', 'CONFIGURING'):
        # For pending/configuring jobs, cancel without signal to avoid hangs.
        client.cancel_jobs_by_name(cluster_name_on_cloud, signal=None)
    elif job_state == 'COMPLETING':
        # Job is already being terminated. No action needed.
        logger.debug(
            f'Job for cluster {cluster_name_on_cloud} is already completing. '
            'No action needed.')
    else:
        # For other states (e.g., RUNNING, SUSPENDED), send a TERM signal.
        client.cancel_jobs_by_name(cluster_name_on_cloud,
                                   signal='TERM',
                                   full=True)


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config
    pass


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config
    pass


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Dict[str, Any],
) -> List[command_runner.SlurmCommandRunner]:
    """Get a command runner for the given cluster."""
    assert cluster_info.provider_config is not None, cluster_info

    if cluster_info.head_instance_id is None:
        # No running job found
        return []

    head_instance = cluster_info.get_head_instance()
    assert head_instance is not None, 'Head instance not found'
    cluster_name_on_cloud = head_instance.tags.get(
        constants.TAG_SKYPILOT_CLUSTER_NAME, None)
    assert cluster_name_on_cloud is not None, cluster_info

    # There can only be one InstanceInfo per instance_id.
    instances = [
        instance_infos[0] for instance_infos in cluster_info.instances.values()
    ]

    # Note: For Slurm, the external IP for all instances is the same,
    # it is the login node's. The internal IP is the private IP of the node.
    ssh_user = cast(str, credentials.pop('ssh_user'))
    ssh_private_key = cast(str, credentials.pop('ssh_private_key'))
    # ssh_proxy_jump is Slurm-specific, it does not exist in the auth section
    # of the cluster yaml.
    ssh_proxy_jump = cluster_info.provider_config.get('ssh', {}).get(
        'proxyjump', None)
    runners = [
        command_runner.SlurmCommandRunner(
            (instance_info.external_ip or '', instance_info.ssh_port),
            ssh_user,
            ssh_private_key,
            sky_dir=_sky_cluster_home_dir(cluster_name_on_cloud),
            skypilot_runtime_dir=_skypilot_runtime_dir(cluster_name_on_cloud),
            job_id=instance_info.tags['job_id'],
            slurm_node=instance_info.tags['node'],
            ssh_proxy_jump=ssh_proxy_jump,
            enable_interactive_auth=True,
            **credentials) for instance_info in instances
    ]

    return runners
