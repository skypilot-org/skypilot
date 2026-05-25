"""Slurm instance provisioning."""

import base64
import importlib.resources
import os
import shlex
import tempfile
import threading
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple

import colorama

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import slurm
from sky.provision import common
from sky.provision import constants
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants as skylet_constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import provision as provision_lib
    from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

PROVISION_SCRIPTS_DIRECTORY_NAME = '.sky_provision'

# -------- v1 (Slurm-native managed jobs) detection and gating -------- #
#
# v1 managed jobs submit a real ``sbatch`` whose script body is the
# user's setup+run command — exiting when user code exits — instead
# of today's ``sleep infinity`` + ``srun --jobid`` pattern. The v1
# branch is selected at provision time by the provider-config marker
# below; the legacy ``_create_virtual_instance`` path stays untouched
# and runs by default.

SLURM_MANAGED_JOB_V1_RUNTIME = 'managed_job_v1'

# Opt-out env var. v1 is on by default; users who rely on the
# long-lived sleep-infinity allocation (e.g. ``sky exec`` against a
# managed-job cluster) can fall back to legacy.
_DISABLE_V1_JOBS_ENV = 'SKYPILOT_SLURM_DISABLE_V1_JOBS'
# Config-file analog: ``slurm.use_v1: false``.
_V1_CONFIG_KEY = ('slurm', 'use_v1')

# Base64-encoded ``git_clone.sh`` for embedding in the sbatch script
# preamble. Reuses the same script as the regular path so token auth,
# SSH key auth, shallow clones, ref-type detection, and incremental
# updates all work identically. The env vars ``GIT_URL``,
# ``GIT_BRANCH``/``GIT_TAG``/``GIT_COMMIT_HASH``, ``GIT_TOKEN``, and
# ``GIT_SSH_KEY`` are already set as task envs by
# ``task.py:_set_git_envs_and_secrets``.
try:
    _git_clone_script = importlib.resources.files('sky.utils').joinpath(
        'git_clone.sh')
    _GIT_CLONE_SCRIPT_B64: Optional[str] = base64.b64encode(
        _git_clone_script.read_bytes()).decode()
except (FileNotFoundError, AttributeError):
    _GIT_CLONE_SCRIPT_B64 = None
    logger.debug('git_clone.sh not found; git workdir support in the v1 '
                 'fast path will be limited')


def is_managed_job_v1_provider_config(provider_config: Dict[str, Any]) -> bool:
    """Whether the provider config selects the Slurm v1 managed-job path."""
    return provider_config.get(
        'skypilot_runtime') == SLURM_MANAGED_JOB_V1_RUNTIME


def is_slurm_managed_jobs_v1_enabled() -> bool:
    """Whether Slurm v1 managed jobs are enabled.

    On by default. Opt out by setting
    ``SKYPILOT_SLURM_DISABLE_V1_JOBS=1`` or
    ``slurm.use_v1: false`` in ``~/.sky/config.yaml``.
    """
    if os.environ.get(_DISABLE_V1_JOBS_ENV) == '1':
        return False
    return bool(skypilot_config.get_nested(_V1_CONFIG_KEY, True))


def _sbatch_log_path(base_dir: str, job_id: str) -> str:
    return f'{base_dir}/{PROVISION_SCRIPTS_DIRECTORY_NAME}/slurm-{job_id}.out'


POLL_INTERVAL_SECONDS = 2
# Default KillWait is 30 seconds, so we add some buffer time here.
_JOB_TERMINATION_TIMEOUT_SECONDS = 60

# sbatch options that SkyPilot controls and must not be overridden by users.
# These are either set dynamically based on the resource spec, or are required
# for SkyPilot's job lifecycle management.
_SBATCH_PROTECTED_OPTIONS = frozenset({
    'job-name',
    'output',
    'error',
    'nodes',
    'wait-all-nodes',
    'no-requeue',
    'cpus-per-task',
    'mem',
    'gres',
    'partition',
})


def _build_custom_sbatch_directives(sbatch_options: Dict[str, Any]) -> str:
    """Build #SBATCH directive lines from user-supplied sbatch_options.

    Args:
        sbatch_options: Dict mapping sbatch option names to values.

    Returns:
        A string of #SBATCH directives, one per line. Protected options
        managed by SkyPilot are skipped with a warning.
    """
    if not sbatch_options:
        return ''

    # Normalize: replace underscores with hyphens (sbatch uses hyphens).
    normalized = {k.replace('_', '-'): v for k, v in sbatch_options.items()}

    # Warn and skip protected options.
    conflicting = set(normalized.keys()) & _SBATCH_PROTECTED_OPTIONS
    if conflicting:
        logger.warning(
            f'{colorama.Fore.YELLOW}Ignoring protected sbatch options '
            f'managed by SkyPilot: {sorted(conflicting)}. Remove them '
            f'from slurm.sbatch_options in ~/.sky/config.yaml.'
            f'{colorama.Style.RESET_ALL}')
        for key in conflicting:
            del normalized[key]

    # Build directive lines.
    lines = []
    for key in sorted(normalized):
        value = normalized[key]
        if value is None or value is False:
            continue
        # Defense in depth: schema validation rejects newlines, but
        # guard here too to prevent script injection.
        str_value = str(value)
        if '\n' in key or '\n' in str_value:
            raise ValueError(
                f'Newline characters are not allowed in sbatch options: '
                f'{key!r}={str_value!r}')
        if key in ('time', 't'):
            slurm_utils.validate_sbatch_time(str_value)
        if value is True:
            lines.append(f'#SBATCH --{key}')
        else:
            lines.append(f'#SBATCH --{key}={value}')
    if not lines:
        return ''
    # Prefix with newline so it slots in after other directives
    # in the provision script f-string.
    return '\n' + '\n'.join(lines)


def _compute_time_directive(sbatch_options: Dict[str, Any],
                            partition_info: 'slurm.SlurmPartition',
                            partition: str) -> str:
    """Compute the auto-generated ``#SBATCH --time=...`` directive.

    Priority: user-supplied > partition MaxTime > partition DefaultTime >
    warn-and-omit. The MaxTime-before-DefaultTime ordering preserves
    longstanding behavior (pre-existing code always emitted
    ``--time={MaxTime}`` and ignored ``DefaultTime``). DefaultTime is
    only consulted when MaxTime is UNLIMITED — emitting
    ``--time=UNLIMITED`` is the #9370 footgun (backfill scheduler
    refuses to schedule ahead of maintenance reservations).

    TODO(kevin): consider preferring DefaultTime over MaxTime. Arguments:
    (1) matches Slurm's own default-resolution order; (2) DefaultTime
    is the more intentional admin signal — MaxTime is usually the
    ceiling, DefaultTime is "what a typical job should get";
    (3) friendlier to the backfill scheduler; (4) less surprising for
    admins who explicitly configured DefaultTime.

    Returns the directive line (no trailing newline), or empty string
    when no auto-generated directive should be emitted (user supplied
    their own, or DefaultTime path, or warn path).
    """
    # Match _build_custom_sbatch_directives' emit criteria: None and False
    # are skipped there (the convention for boolean-shaped options like
    # `exclusive: false`), so we treat them the same way here. Otherwise
    # `time: false` would suppress both the user's directive AND the auto
    # fallback, silently bypassing the safety net.
    user_supplied_time = any(
        sbatch_options.get(k) not in (None, False) for k in ('time', 't'))
    if user_supplied_time:
        return ''
    # MaxTime first: preserve pre-existing behavior for partitions where
    # MaxTime is set.
    if partition_info.maxtime is not None:
        max_time = slurm_utils.format_slurm_duration(partition_info.maxtime)
        return f'#SBATCH --time={max_time}'
    # MaxTime is UNLIMITED / NONE. Fall back to DefaultTime (the #9370
    # fix path) so Slurm doesn't see --time=UNLIMITED.
    if partition_info.default_time is not None:
        return ''
    logger.warning(
        f'Partition {partition!r} has no MaxTime or DefaultTime configured. '
        'Submitting without --time may cause the job to hang behind '
        'maintenance reservations. Set slurm.sbatch_options.time in your '
        'task YAML or in ~/.sky/config.yaml.')
    return ''


def _build_sbatch_directives(sbatch_options: Dict[str, Any],
                             partition_info: 'slurm.SlurmPartition',
                             partition: str) -> str:
    """Combine auto-generated and user-supplied ``#SBATCH`` directives.

    Returns a string with a leading newline so it slots into the sbatch
    script f-string after the pre-existing directives, or empty string
    when nothing to emit.
    """
    user_block = _build_custom_sbatch_directives(sbatch_options)
    auto_time = _compute_time_directive(sbatch_options, partition_info,
                                        partition)
    if not auto_time:
        return user_block
    # user_block is either '' or '\n#SBATCH ...' (leading \n, no trailing).
    return '\n' + auto_time + user_block


def _wait_for_job_nodes(
    client: 'slurm.SlurmClient',
    job_id: str,
    timeout: int,
    partition: str,
    on_pending: Callable[[str, Optional[str], Optional[int]], None],
) -> None:
    """Wait for a Slurm job to have nodes allocated.

    Args:
        client: The Slurm client to use for queries.
        job_id: The Slurm job ID.
        timeout: Maximum time to wait in seconds. If negative, wait
            indefinitely.
        partition: Optional partition name for querying pending job count.
        on_pending: Optional callback invoked when the job is pending or
            configuring. Called with (state, reason, pending_count) where
            reason and pending_count may be None.
    """
    start_time = time.time()
    last_state = None

    while timeout < 0 or time.time() - start_time < timeout:
        state = client.get_job_state(job_id)

        if state != last_state:
            logger.debug(f'Job {job_id} state: {state}')
            last_state = state

        if state is None:
            raise RuntimeError(f'Job {job_id} not found. It may have been '
                               'cancelled or failed.')

        if state in ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'):
            raise RuntimeError(f'Job {job_id} terminated with state {state} '
                               'before nodes were allocated.')

        if state in ('PENDING', 'CONFIGURING') and on_pending is not None:
            try:
                reason = client.get_job_reason(job_id)
                pending_count: Optional[int] = None
                if partition is not None:
                    pending_count = client.get_pending_job_count(
                        partition, exclude_job_id=job_id)
                    if pending_count < 0:
                        pending_count = None
                on_pending(state, reason, pending_count)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to get pending status for job '
                             f'{job_id}: {e}')

        if client.check_job_has_nodes(job_id):
            logger.debug(f'Job {job_id} has nodes allocated')
            return

        time.sleep(2)

    raise TimeoutError(f'Job {job_id} did not get nodes allocated within '
                       f'{timeout} seconds. Last state: {last_state}')


def _sky_cluster_home_dir(base_dir: str, cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot cluster's home directory path on the Slurm cluster.

    This path is assumed to be on a shared NFS mount accessible by all nodes.
    """
    return f'{base_dir}/.sky_clusters/{cluster_name_on_cloud}'


def _sbatch_provision_script_path(base_dir: str,
                                  cluster_name_on_cloud: str) -> str:
    """Returns the path to the sbatch provision script on the login node."""
    # Put sbatch script in $HOME instead of /tmp as there can be
    # multiple login nodes, and different SSH connections
    # can land on different login nodes.
    return os.path.join(base_dir, PROVISION_SCRIPTS_DIRECTORY_NAME,
                        f'{cluster_name_on_cloud}.sh')


def _skypilot_runtime_dir(tmpdir: Optional[str],
                          cluster_name_on_cloud: str) -> str:
    """Returns the SkyPilot runtime directory path on the Slurm cluster."""
    tmp = tmpdir if tmpdir is not None else '/tmp'
    return os.path.join(tmp, cluster_name_on_cloud)


def _enroot_container_name_global_scope(cluster_name_on_cloud: str) -> str:
    """Get enroot container name when container_scope=global."""
    # Not publicly documented, but see:
    # https://github.com/NVIDIA/pyxis/blob/fb9c2d5a08a778346dd398d670deeb5a569904e5/pyxis_slurmstepd.c#L1104
    # Added in commit:
    # https://github.com/NVIDIA/pyxis/commit/a35027cf2ffa45cf702b117d215b1240aa6de22e
    return f'pyxis_{slurm_utils.pyxis_container_name(cluster_name_on_cloud)}'


def _wait_for_job_ready(
    login_node_runner: 'command_runner.SSHCommandRunner',
    client: 'slurm.SlurmClient',
    job_id: str,
    ready_signal: str,
    slurm_log: str,
) -> None:
    """Wait for Slurm job initialization to complete.

    Polls while the job is running. Fails if:
    1. The job exits/fails (state not in PENDING/RUNNING/CONFIGURING)
    2. The ready signal file never appears
    """
    poll_interval_seconds = 1

    while True:
        rc, _, _ = login_node_runner.run(f'test -f {ready_signal}',
                                         require_outputs=True,
                                         stream_logs=False)
        if rc == 0:
            return

        job_state = client.get_job_state(job_id)
        # Job states that indicate the job is still initializing
        # See: https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
        if job_state not in ('PENDING', 'RUNNING', 'CONFIGURING'):
            raise RuntimeError(f'Slurm job {job_id} exited ({job_state}) '
                               'before initialization completed. See sbatch '
                               f'logs for details: {slurm_log}')

        time.sleep(poll_interval_seconds)


@timeline.event
def _create_virtual_instance(
        region: str, cluster_name: str, cluster_name_on_cloud: str,
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
    ssh_key = ssh_config_dict.get('private_key', None)
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
    identities_only = ssh_config_dict.get('identities_only', False)
    partition = slurm_utils.get_partition_from_config(provider_config)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        identities_only=identities_only,
    )

    slurm_cluster = slurm_utils.get_slurm_cluster_from_config(provider_config)

    proctrack_type = slurm_utils.get_proctrack_type(slurm_cluster)
    partition_info = slurm_utils.get_partition_info(slurm_cluster, partition)
    if partition_info is None:
        raise ValueError(f'Partition info for {partition} not found '
                         f'for SLURM cluster {slurm_cluster}')

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

    provision_timeout: int = provider_config['provision_timeout']
    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'Waiting {wait_str} for '
                 f'job to be allocated on partition {partition}')

    num_nodes = config.count
    last_status_msg = None

    def _on_pending(state: str, reason: Optional[str],
                    pending_count: Optional[int]) -> None:
        nonlocal last_status_msg
        del state  # unused
        parts = []
        if reason:
            parts.append(f'pending: {reason}')
        if pending_count is not None and pending_count > 0:
            word = 'other' if pending_count == 1 else 'others'
            parts.append(f'{pending_count} {word} pending')
        if parts:
            msg = f'Launching ({", ".join(parts)})'
        else:
            msg = 'Launching'
        status_msg = ux_utils.spinner_message(msg, cluster_name=cluster_name)
        if status_msg != last_status_msg:
            rich_utils.force_update_status(status_msg)
            last_status_msg = status_msg

    workdir = skypilot_config.get_effective_region_config(cloud='slurm',
                                                          region=region,
                                                          keys=('workdir',),
                                                          default_value=None)
    tmpdir = skypilot_config.get_effective_region_config(cloud='slurm',
                                                         region=region,
                                                         keys=('tmpdir',),
                                                         default_value=None)
    if existing_jobs:
        assert len(existing_jobs) == 1, (
            f'Multiple jobs found with name {cluster_name_on_cloud}: '
            f'{existing_jobs}')

        job_id = existing_jobs[0]
        logger.debug(f'Job with name {cluster_name_on_cloud} already exists '
                     f'(JOBID: {job_id})')

        # Wait for nodes to be allocated (job might be in PENDING state)
        _wait_for_job_nodes(client, job_id, provision_timeout, partition,
                            _on_pending)
        nodes, _ = client.get_job_nodes(job_id)
        # Reset spinner since nodes are now allocated
        rich_utils.force_update_status(
            ux_utils.spinner_message('Launching', cluster_name=cluster_name))
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
    accelerator_type = resources.get('accelerator_type')
    accelerator_count_raw = resources.get('accelerator_count')
    try:
        accelerator_count = int(
            accelerator_count_raw) if accelerator_count_raw is not None else 0
    except (TypeError, ValueError):
        logger.warning(
            f'Invalid accelerator_count value: {accelerator_count_raw!r}. '
            'Defaulting to 0 (no accelerators).')
        accelerator_count = 0

    # To bootstrap things, we need to do it with SSHCommandRunner first.
    # SlurmCommandRunner is for after the virtual instances are created.
    login_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
    )
    remote_home_dir = login_node_runner.get_remote_home_dir()

    # Resolve shell variables (e.g. $USER) in workdir/tmpdir using the
    # remote host's environment.
    if workdir is not None or tmpdir is not None:
        remote_env = client.get_env()
        if workdir is not None:
            workdir = slurm_utils.expand_path_vars(workdir, remote_env)
        if tmpdir is not None:
            tmpdir = slurm_utils.expand_path_vars(tmpdir, remote_env)
        logger.debug(f'Resolved workdir: {workdir}, tmpdir: {tmpdir}')

    # Must be absolute — #SBATCH directives don't expand ~ or $HOME.
    sky_base_dir = workdir if workdir is not None else remote_home_dir
    assert os.path.isabs(sky_base_dir), (
        f'sky_base_dir must be absolute, got: {sky_base_dir}')
    sbatch_log_base_dir = sky_base_dir

    provision_script_path = _sbatch_provision_script_path(
        sky_base_dir, cluster_name_on_cloud)
    provision_scripts_dir = os.path.dirname(provision_script_path)

    skypilot_runtime_dir = _skypilot_runtime_dir(tmpdir, cluster_name_on_cloud)
    sky_cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                                 cluster_name_on_cloud)
    ready_signal = f'{sky_cluster_home_dir}/.sky_sbatch_ready'
    slurm_marker_file = (
        f'{sky_cluster_home_dir}/{slurm_utils.SLURM_MARKER_FILE}')

    # For non-Docker Hub registries, pyxis/enroot requires '#' separator
    # between registry and path. See:
    # https://github.com/NVIDIA/pyxis/wiki/Usage#registry-syntax
    container_image = resources.get('image_id')
    if container_image is not None:
        if container_image.endswith('.sqsh'):
            # Local .sqsh file, use path directly.
            pass
        else:
            parts = container_image.split('/', 1)
            if len(parts) > 1:
                maybe_domain, maybe_path = parts
                is_custom_registry = ('.' in maybe_domain or
                                      ':' in maybe_domain or
                                      maybe_domain == 'localhost')
                if is_custom_registry:
                    container_image = f'{maybe_domain}#{maybe_path}'
    container_name = slurm_utils.pyxis_container_name(cluster_name_on_cloud)

    # Build the appended sbatch directive block (auto-generated --time
    # + user-supplied options from sbatch_options).
    sbatch_options = resources.get('sbatch_options', {}) or {}
    extra_sbatch_directives = _build_sbatch_directives(sbatch_options,
                                                       partition_info,
                                                       partition)

    # Build the sbatch script
    gpu_directive = ''
    if accelerator_count > 0:
        if (accelerator_type is not None and
                accelerator_type.upper() != 'NONE'):
            # Typed GRES: #SBATCH --gres=gpu:<type>:<count>
            gpu_directive = (f'#SBATCH --gres=gpu:{accelerator_type}:'
                             f'{accelerator_count}')
        else:
            # GRES without GPU type: #SBATCH --gres=gpu:<count>
            gpu_directive = f'#SBATCH --gres=gpu:{accelerator_count}'

    # Build container initialization block if container image specified
    container_block = ''
    if container_image is not None:
        # Note: /dev/shm is NOT mounted here because enroot handles it:
        # - If ENROOT_RESTRICT_DEV is set: /dev is restricted but /dev/shm is
        #   explicitly mounted by the 10-devices.sh hook
        # - If ENROOT_RESTRICT_DEV is unset: /dev is not restricted, so
        #   /dev/shm is inherited from the host
        # See:
        # https://github.com/NVIDIA/enroot/blob/main/conf/hooks/10-devices.sh
        host_ccache_dir = '/tmp/ccache_$(id -u)'
        container_ccache_dir = '/var/cache/ccache'
        mount_paths = [
            f'{remote_home_dir}:{remote_home_dir}',
            f'{host_ccache_dir}:{container_ccache_dir}',
        ]
        # When workdir differs from remote_home_dir (e.g. workdir is on
        # NFS at /home/ubuntu while $HOME is /home_local/ubuntu), mount
        # it so the container can access sky_cluster_home_dir.
        if workdir is not None and workdir != remote_home_dir:
            mount_paths.append(f'{workdir}:{workdir}')
        container_mounts = ','.join(mount_paths)
        # Add sudo alias to bashrc since we're already root in the container.
        # This allows scripts with 'sudo' commands to work without modification.
        # For containers, ~ is /root which is isolated inside the container,
        # so modifying bashrc doesn't affect non-containerized sessions.
        container_init_script = """\
set -e
echo "[container-init] Starting..."
INIT_START=$SECONDS
apt-get update
apt-get install -y ca-certificates rsync curl git wget fuse
echo 'alias sudo=""' >> ~/.bashrc
echo "[container-init] Packages installed in $((SECONDS - INIT_START))s"
"""
        container_marker_file = (f'{sky_cluster_home_dir}/'
                                 f'{slurm_utils.SLURM_CONTAINER_MARKER_FILE}')
        container_init_done_dir = (
            f'{sky_cluster_home_dir}/.sky_container_init_done')
        # Run container init, touch per-node "done" marker, then sleep infinity
        # to keep container running. Use --overlap so subsequent sruns can share
        # the allocation. Background with & so sbatch continues.
        container_cmd = shlex.quote(
            f'{container_init_script}'
            f'touch {container_init_done_dir}/$SLURM_PROCID && sleep infinity')
        container_block = (
            f'srun --nodes={num_nodes} mkdir -p {host_ccache_dir}\n'
            f'CONTAINER_START=$SECONDS\n'
            f'echo "[container] Initializing {container_name} on all nodes"\n'
            f'rm -rf {container_init_done_dir}\n'
            f'mkdir -p {container_init_done_dir}\n'
            f'srun --overlap {"--label " if num_nodes > 1 else ""}--unbuffered '
            f'--nodes={num_nodes} --ntasks-per-node=1 '
            f'--container-image={shlex.quote(container_image)} '
            f'--container-name={shlex.quote(container_name)}:create '
            f'--container-mounts="{container_mounts}" '
            f'--container-remap-root '
            f'--no-container-mount-home '
            f'--container-writable '
            f'bash -c {container_cmd} &\n'
            f'CONTAINER_PID=$!\n'
            f'while true; do\n'
            f'  num_ready=$(ls -1 {container_init_done_dir} 2>/dev/null | '
            f'wc -l)\n'
            f'  if [ "$num_ready" -ge "{num_nodes}" ]; then\n'
            f'    break\n'
            f'  fi\n'
            f'  if ! kill -0 $CONTAINER_PID 2>/dev/null; then\n'
            f'    echo "[container] ERROR: Container initialization failed."\n'
            f'    echo "[container] Only $num_ready of {num_nodes}'
            f' node(s) completed initialization."\n'
            f'    wait $CONTAINER_PID\n'
            f'    exit $?\n'
            f'  fi\n'
            f'  sleep 1\n'
            f'done\n'
            f'echo "[container] Ready in $((SECONDS - CONTAINER_START))s"\n'
            f'touch {container_marker_file} {ready_signal}')

    # By default stdout and stderr will be written to $HOME/slurm-%j.out
    # (because we invoke sbatch from $HOME). Redirect elsewhere to not pollute
    # the home directory.
    mem_directive = ''
    if float(resources['memory']) > 0:
        # Memory is in MB to support fractional GB values (e.g. 0.5GB ->
        # 512M), since Slurm's --mem requires integer values per unit.
        # Slurm's M suffix means MiB (1G = 1024M), matching SkyPilot's
        # GB convention.
        mem_in_mb = int(float(resources['memory']) * 1024)
        mem_directive = f'#SBATCH --mem={mem_in_mb}M\n'
    # pylint: disable=line-too-long
    # fmt: off
    provision_script = f"""\
#!/bin/bash
#SBATCH --job-name={cluster_name_on_cloud}
#SBATCH --output={_sbatch_log_path(sbatch_log_base_dir, '%j')}
#SBATCH --error={_sbatch_log_path(sbatch_log_base_dir, '%j')}
#SBATCH --nodes={num_nodes}
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task={int(resources["cpus"])}
{mem_directive}{gpu_directive}{extra_sbatch_directives}

# Cleanup function to remove cluster dirs on job termination.
cleanup() {{
    saved_exit=$?
    # The Skylet is daemonized, so it is not automatically terminated when
    # the Slurm job is terminated, we need to kill it manually.
    echo "Terminating Skylet..."
    if [ -f "{skypilot_runtime_dir}/.sky/skylet_pid" ]; then
        kill $(cat "{skypilot_runtime_dir}/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --nodes={num_nodes} --ntasks-per-node=1 enroot remove -f {shlex.quote(_enroot_container_name_global_scope(cluster_name_on_cloud))} 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --nodes={num_nodes} rm -rf {skypilot_runtime_dir}
    rm -rf {sky_cluster_home_dir}
    exit $saved_exit
}}
# Run cleanup on any exit, including container init failures.
trap cleanup EXIT
# On SIGTERM (job cancellation via scancel), exit 0 so cleanup treats
# it as a graceful shutdown rather than propagating an error code.
trap 'exit 0' TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p {sky_cluster_home_dir}/sky_logs {sky_cluster_home_dir}/sky_workdir {sky_cluster_home_dir}/.sky
# Create sky runtime directory on each node.
srun --nodes={num_nodes} mkdir -p {skypilot_runtime_dir}
# Marker file to indicate we're in a Slurm cluster.
touch {slurm_marker_file}
# Store proctrack type for task executor to read.
echo '{proctrack_type or "unknown"}' > {sky_cluster_home_dir}/{skylet_constants.SLURM_PROCTRACK_TYPE_FILE}
# Suppress login messages.
touch {sky_cluster_home_dir}/.hushlogin
{container_block}
{f'touch {ready_signal}' if container_image is None else ''}
{'sleep infinity' if container_image is None else 'wait'}
"""
    # fmt: on
    # pylint: enable=line-too-long

    cmd = f'mkdir -p {provision_scripts_dir}'
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
        tgt_path = provision_script_path
        login_node_runner.rsync(src_path, tgt_path, up=True, stream_logs=False)

    job_id = client.submit_job(partition, cluster_name_on_cloud, tgt_path)
    logger.debug(f'Successfully submitted Slurm job {job_id} to partition '
                 f'{partition} for cluster {cluster_name_on_cloud} '
                 f'with {num_nodes} nodes')

    _wait_for_job_nodes(client, job_id, provision_timeout, partition,
                        _on_pending)
    nodes, _ = client.get_job_nodes(job_id)
    # Reset spinner since nodes are now allocated
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))
    created_instance_ids = [
        slurm_utils.instance_id(job_id, node) for node in nodes
    ]

    # No timeout for job initialization: once nodes are allocated, the
    # provision has effectively succeeded. Container image pulls and
    # package installation can take a long time for large images, and
    # should not be subject to the provision timeout (which is meant for
    # the Slurm scheduler queue, not for container setup).

    # Wait for the sbatch script to create the cluster's sky directories,
    # to avoid a race condition where post-provision commands try to
    # access the directories before they are created.
    slurm_log = _sbatch_log_path(sbatch_log_base_dir, job_id)

    # Stream logs in background thread for visibility if debug mode
    if env_options.Options.SHOW_DEBUG_INFO.get():

        def _stream_logs():
            login_node_runner.run(f'tail -f {slurm_log} 2>/dev/null',
                                  require_outputs=False,
                                  stream_logs=True)

        log_thread = threading.Thread(target=_stream_logs, daemon=True)
        log_thread.start()

    try:
        _wait_for_job_ready(
            login_node_runner,
            client,
            job_id,
            ready_signal,
            slurm_log,
        )
    except (RuntimeError, exceptions.CommandError) as e:
        _, stdout, _ = login_node_runner.run(f'cat {slurm_log} 2>/dev/null',
                                             require_outputs=True,
                                             stream_logs=False)
        if stdout:
            logger.error(f'=== Slurm job logs ({slurm_log}) ===\n'
                         f'{stdout}'
                         f'=== End of Slurm job logs ===')
        raise e

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

    if is_managed_job_v1_provider_config(provider_config):
        return _query_instances_v1(cluster_name_on_cloud,
                                   provider_config,
                                   non_terminated_only=non_terminated_only)

    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict.get('private_key', None)
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
    identities_only = ssh_config_dict.get('identities_only', False)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        identities_only=identities_only,
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
                nodes, _ = client.get_job_nodes(job_id)
                for node in nodes:
                    instance_id = slurm_utils.instance_id(job_id, node)
                    statuses[instance_id] = (sky_status, None)

        # TODO(kevin): Query sacct too to get more historical job info.
        # squeue only includes completed jobs that finished in the last
        # MinJobAge seconds (default 300s). Or could be earlier if it
        # reaches MaxJobCount first (default 10_000).

    return statuses


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster (Slurm in this case)."""
    if is_managed_job_v1_provider_config(config.provider_config):
        return _create_managed_job_v1(region, cluster_name,
                                      cluster_name_on_cloud, config)
    return _create_virtual_instance(region, cluster_name, cluster_name_on_cloud,
                                    config)


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

    if is_managed_job_v1_provider_config(provider_config):
        info = _get_cluster_info_v1(cluster_name_on_cloud, provider_config)
        if info is not None:
            return info
        # Safe default: an empty ClusterInfo. Mirrors the legacy
        # "no running jobs" branch below — the controller treats this
        # as "no instances yet" rather than crashing.
        return common.ClusterInfo(
            instances={},
            head_instance_id=None,
            provider_name='slurm',
            provider_config=provider_config,
        )

    # The SSH host is the remote machine running slurmctld daemon.
    # Cross-cluster operations are supported by interacting with
    # the current controller. For details, please refer to
    # https://slurm.schedmd.com/multi_cluster.html.
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict.get('private_key', None)
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
    identities_only = ssh_config_dict.get('identities_only', False)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        identities_only=identities_only,
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
            provider_name='slurm',
            provider_config=provider_config,
        )
    assert len(running_jobs) == 1, (
        f'Multiple running jobs found for cluster {cluster_name_on_cloud}: '
        f'{running_jobs}')

    job_id = running_jobs[0]
    # Running jobs should already have nodes allocated
    nodes, node_ips = client.get_job_nodes(job_id)

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
                node_name=slurm_utils.instance_id(job_id, node),
            )
        ] for node, node_ip in zip(nodes, node_ips)
    }

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=slurm_utils.instance_id(job_id, nodes[0]),
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

    if is_managed_job_v1_provider_config(provider_config):
        _terminate_managed_job_v1(cluster_name_on_cloud, provider_config)
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
        ssh_private_key = ssh_config_dict.get('private_key', None)
        ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
        ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
        identities_only = ssh_config_dict.get('identities_only', False)

        client = slurm.SlurmClient(
            ssh_host,
            ssh_port,
            ssh_user,
            ssh_private_key,
            ssh_proxy_command=ssh_proxy_command,
            ssh_proxy_jump=ssh_proxy_jump,
            identities_only=identities_only,
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


def _build_pyxis_args(cluster_name_on_cloud: str) -> str:
    """Build pyxis/enroot container args for srun.

    Uses :exec flag to attach to the already-running container (started with
    sleep infinity in sbatch). Container settings like --container-remap-root,
    --container-writable are preserved from when the container was created.
    """
    container_name = slurm_utils.pyxis_container_name(cluster_name_on_cloud)
    quoted_name = shlex.quote(container_name)
    return f'--container-remap-root --container-name={quoted_name}:exec'


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Dict[str, Any],
) -> List[command_runner.SlurmCommandRunner]:
    """Get a command runner for the given cluster."""
    # For Slurm, we use the login node credentials from provider_config['ssh']
    # instead of `credentials` which is for ssh'ing to the SkyPilot cluster.
    del credentials
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

    provider_config = cluster_info.provider_config

    # Get login node SSH credentials.
    login_node_ssh_config = provider_config['ssh']
    login_node_ssh_hostname = login_node_ssh_config['hostname']
    login_node_ssh_port = int(login_node_ssh_config.get('port', 22))
    login_node_ssh_user = login_node_ssh_config['user']
    login_node_ssh_private_key = login_node_ssh_config.get('private_key', None)
    login_node_ssh_proxy_command = login_node_ssh_config.get(
        'proxycommand', None)
    login_node_ssh_proxy_jump = login_node_ssh_config.get('proxyjump', None)
    login_node_identities_only = login_node_ssh_config.get(
        'identities_only', False)
    # For Slurm, multiple SkyPilot clusters may share the same underlying
    # Slurm login node. By using a fixed ssh_control_name ('__default__'),
    # we ensure that all connections to the same login node reuse the same
    # SSH ControlMaster process, avoiding repeated SSH handshakes.
    #
    # The %C token in ControlPath (see ssh_options_list) ensures that
    # connections to different login nodes use different sockets, avoiding
    # collisions between different Slurm clusters.
    ssh_control_name = command_runner.DEFAULT_SSH_CONTROL_NAME

    client = slurm.SlurmClient(
        login_node_ssh_hostname,
        login_node_ssh_port,
        login_node_ssh_user,
        login_node_ssh_private_key,
        ssh_proxy_command=login_node_ssh_proxy_command,
        ssh_proxy_jump=login_node_ssh_proxy_jump,
        identities_only=login_node_identities_only,
    )
    remote_home_dir = client.get_remote_home_dir()

    slurm_cluster_name = provider_config.get('cluster')
    workdir = skypilot_config.get_effective_region_config(
        cloud='slurm',
        region=slurm_cluster_name,
        keys=('workdir',),
        default_value=None)
    tmpdir = skypilot_config.get_effective_region_config(
        cloud='slurm',
        region=slurm_cluster_name,
        keys=('tmpdir',),
        default_value=None)
    if workdir is not None or tmpdir is not None:
        remote_env = client.get_env()
        if workdir is not None:
            workdir = slurm_utils.expand_path_vars(workdir, remote_env)
        if tmpdir is not None:
            tmpdir = slurm_utils.expand_path_vars(tmpdir, remote_env)

    sky_base_dir = workdir if workdir is not None else remote_home_dir
    assert os.path.isabs(sky_base_dir), (
        f'sky_base_dir must be absolute, got: {sky_base_dir}')
    sky_cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                                 cluster_name_on_cloud)
    container_marker = (
        f'{sky_cluster_home_dir}/{slurm_utils.SLURM_CONTAINER_MARKER_FILE}')
    has_container = client.check_file_exists(container_marker)
    container_args = _build_pyxis_args(
        cluster_name_on_cloud) if has_container else None

    runners = [
        # Note: For Slurm, the external IP for all instances is the same,
        # it is the login node's. The internal IP is the private IP of the node.
        command_runner.SlurmCommandRunner(
            (instance_info.external_ip or '', instance_info.ssh_port),
            login_node_ssh_user,
            login_node_ssh_private_key,
            sky_dir=sky_cluster_home_dir,
            skypilot_runtime_dir=_skypilot_runtime_dir(tmpdir,
                                                       cluster_name_on_cloud),
            job_id=instance_info.tags['job_id'],
            slurm_node=instance_info.tags['node'],
            ssh_proxy_jump=login_node_ssh_proxy_jump,
            ssh_proxy_command=login_node_ssh_proxy_command,
            ssh_control_name=ssh_control_name,
            container_args=container_args,
            enable_interactive_auth=True,
            # Allow ssh-agent and default key fallback for Slurm.
            disable_identities_only=True) for instance_info in instances
    ]

    return runners


# -------- v1 managed-job: predicates, template override, provision -------- #

# Bounded wait used by the v1 ``terminate_instances`` branch to confirm
# that ``scancel`` actually moved the job out of RUNNING before we let
# the controller continue with a possibly-stale state. Slurm's default
# ``KillWait`` is 30s; we use a slightly larger ceiling so a SIGTERM
# followed by the eventual SIGKILL has time to land before we give up.
_V1_SCANCEL_LEAVE_RUNNING_TIMEOUT_SECONDS = 30
_V1_SCANCEL_POLL_INTERVAL_SECONDS = 1


def _slurm_client_from_provider_config(
        provider_config: Dict[str, Any]) -> 'slurm.SlurmClient':
    """Build a ``SlurmClient`` from a v1 provider config's ssh block."""
    ssh_config_dict = provider_config['ssh']
    return slurm.SlurmClient(
        ssh_config_dict['hostname'],
        int(ssh_config_dict['port']),
        ssh_config_dict['user'],
        ssh_config_dict.get('private_key', None),
        ssh_proxy_command=ssh_config_dict.get('proxycommand', None),
        ssh_proxy_jump=ssh_config_dict.get('proxyjump', None),
        identities_only=ssh_config_dict.get('identities_only', False),
    )


def _resolve_v1_job_id(
        client: 'slurm.SlurmClient',
        cluster_name_on_cloud: str,
        cluster_info: Optional[common.ClusterInfo] = None) -> Optional[str]:
    """Resolve the Slurm job_id for a v1 cluster.

    Identity contract (PLAN.md block-ship #4): the primary source is the
    structured ``tags['job_id']`` populated by ``get_cluster_info``;
    fallback is a name-keyed ``squeue`` query, which is safe on the v1
    path because the COMPLETING-drain + existing-job reattach invariants
    in ``_create_managed_job_v1`` guarantee a single live job per name.
    """
    # Primary: structured tag from cached ClusterInfo.
    if cluster_info is not None and cluster_info.head_instance_id is not None:
        head = cluster_info.get_head_instance()
        if head is not None:
            tag = head.tags.get('job_id') if head.tags else None
            if tag:
                return str(tag)
    # Fallback: name-keyed query (single match enforced upstream).
    matches = client.query_jobs(cluster_name_on_cloud, ['pending', 'running'])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    raise RuntimeError(
        f'Multiple Slurm jobs found for v1 cluster {cluster_name_on_cloud}: '
        f'{matches}. Expected at most one — single-job-per-name is a v1 '
        'invariant.')


def _terminate_managed_job_v1(cluster_name_on_cloud: str,
                              provider_config: Dict[str, Any]) -> None:
    """Cancel the v1 Slurm job by job_id and wait briefly for RUNNING exit.

    Uses ``scancel <jobid>`` against the resolved primary identity (per
    PLAN.md block-ship #4), not ``scancel --name=...`` — the latter
    stays on the legacy path. After scancel we poll briefly so the
    controller does not continue with a stale RUNNING reading.
    """
    client = _slurm_client_from_provider_config(provider_config)

    job_id = _resolve_v1_job_id(client, cluster_name_on_cloud)
    if job_id is None:
        logger.debug(f'V1 Slurm job for {cluster_name_on_cloud} not found in '
                     'squeue; assuming already terminated.')
        return

    state = client.get_job_state(job_id)
    if state is None:
        logger.debug(f'V1 Slurm job {job_id} no longer in squeue; assuming '
                     'already terminated.')
        return

    terminal_states = {
        'COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'PREEMPTED',
        'SPECIAL_EXIT', 'BOOT_FAIL', 'OUT_OF_MEMORY', 'DEADLINE', 'REVOKED'
    }
    if state in terminal_states:
        logger.debug(f'V1 Slurm job {job_id} ({cluster_name_on_cloud}) already '
                     f'in terminal state {state}; nothing to do.')
        return
    if state == 'COMPLETING':
        logger.debug(f'V1 Slurm job {job_id} ({cluster_name_on_cloud}) already '
                     'completing; nothing to do.')
        return

    if state in ('PENDING', 'CONFIGURING'):
        # Pending jobs haven't allocated nodes; scancel without signal.
        client.cancel_job_by_id(job_id, signal=None)
    else:
        # RUNNING / SUSPENDED / SIGNALING / STAGE_OUT: send SIGTERM with --full
        # so the batch shell + its srun children receive the signal.
        client.cancel_job_by_id(job_id, signal='TERM', full=True)

    # Wait briefly for the job to leave RUNNING. Without this the
    # controller may read a stale RUNNING state on its next status poll
    # and conclude the cancel "didn't take".
    start = time.time()
    while (time.time() - start < _V1_SCANCEL_LEAVE_RUNNING_TIMEOUT_SECONDS):
        state = client.get_job_state(job_id)
        if state is None:
            # Aged out of squeue; reach for sacct to confirm terminal.
            return
        if state in terminal_states or state == 'COMPLETING':
            return
        time.sleep(_V1_SCANCEL_POLL_INTERVAL_SECONDS)

    logger.warning(
        f'V1 Slurm job {job_id} ({cluster_name_on_cloud}) did not leave '
        f'RUNNING within {_V1_SCANCEL_LEAVE_RUNNING_TIMEOUT_SECONDS}s after '
        'scancel. The controller may briefly observe a stale state.')


# Mapping from upper-case Slurm states (as returned by ``squeue`` /
# ``sacct``) to a SkyPilot ``ClusterStatus``. Terminal states map to
# ``None`` so callers can short-circuit cleanup. Aligned with the
# state space documented at
# https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES — see
# also ``managed_job_runtime._slurm_state_to_job_status``.
_V1_SLURM_STATE_TO_CLUSTER_STATUS: Dict[
    str, Optional[status_lib.ClusterStatus]] = {
        'PENDING': status_lib.ClusterStatus.INIT,
        'CONFIGURING': status_lib.ClusterStatus.INIT,
        'RESV_DEL_HOLD': status_lib.ClusterStatus.INIT,
        'REQUEUED': status_lib.ClusterStatus.INIT,
        'REQUEUE_HOLD': status_lib.ClusterStatus.INIT,
        'REQUEUE_FED': status_lib.ClusterStatus.INIT,
        'RESIZING': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
        'COMPLETING': status_lib.ClusterStatus.UP,
        'SIGNALING': status_lib.ClusterStatus.UP,
        'STAGE_OUT': status_lib.ClusterStatus.UP,
        'SUSPENDED': status_lib.ClusterStatus.UP,
        # Terminal — ``None`` means "no longer an instance".
        'COMPLETED': None,
        'CANCELLED': None,
        'FAILED': None,
        'TIMEOUT': None,
        'NODE_FAIL': None,
        'BOOT_FAIL': None,
        'DEADLINE': None,
        'OUT_OF_MEMORY': None,
        'PREEMPTED': None,
        'REVOKED': None,
        'SPECIAL_EXIT': None,
    }


def _v1_sacct_job_state(client: 'slurm.SlurmClient',
                        job_id_or_name: str) -> Optional[str]:
    """Query ``sacct`` for the parent-row terminal state of a job.

    Used by ``_query_instances_v1`` to surface terminal state for jobs
    that have aged past squeue's ``MinJobAge`` window (default 300s) —
    PLAN.md gap #12. ``--name`` is accepted by sacct, so we can key on
    either job id or cluster_name_on_cloud.

    Returns the upper-cased state of the most recent matching allocation
    row, or ``None`` if sacct returns nothing parseable.
    """
    # Use ``-X`` (= ``--allocations``) to skip step rows. Quoting note:
    # the caller may pass a numeric job id or a cluster name; both are
    # safe to substitute (cluster names are validated against
    # ``CLUSTER_NAME_VALID_REGEX``, and job ids are numeric).
    is_numeric = job_id_or_name.isdigit()
    key_flag = '-j' if is_numeric else '--name'
    cmd = (f'sacct {key_flag} {job_id_or_name} --format=JobID,State '
           '--parsable2 --noheader -X')
    # pylint: disable=protected-access
    rc, stdout, _ = client._run_slurm_cmd(cmd)
    if rc != 0:
        return None
    # Take the *last* row — sacct lists rows oldest-first, and on
    # recovery_strategy reuse the same cluster name can have multiple
    # historical rows. The last one is the most recent attempt.
    last_state: Optional[str] = None
    for line in stdout.splitlines():
        parts = line.split('|')
        if len(parts) < 2:
            continue
        state = parts[1].strip()
        # ``CANCELLED by <uid>`` collapses to ``CANCELLED``.
        if state.startswith('CANCELLED'):
            state = 'CANCELLED'
        if state:
            last_state = state.upper()
    return last_state


def _query_instances_v1(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    *,
    non_terminated_only: bool,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """v1 ``query_instances``: squeue + sacct merge.

    Legacy ``query_instances`` queries ``squeue`` only, so a job that
    aged past ``MinJobAge`` (default 300s) disappears from the result
    map. For v1 managed jobs that's a correctness problem — the
    controller needs to observe terminal state for short-lived jobs to
    decide success/failure. So we additionally consult ``sacct``.

    Returns a ``{instance_id: (cluster_status, reason)}`` map keyed on
    ``slurm_utils.instance_id(job_id, node)`` for live jobs, or on the
    bare ``cluster_name_on_cloud`` for terminal-only sacct surfacing
    (we have no node list to fan out across).
    """
    client = _slurm_client_from_provider_config(provider_config)
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    seen_job_ids: set = set()

    # --- squeue: live jobs (pending/running/completing/...) and recent ---
    # --- terminal jobs within the MinJobAge window. ---
    for state_filter in [
            'pending', 'running', 'completing', 'completed', 'cancelled',
            'failed', 'node_fail'
    ]:
        try:
            job_ids = client.query_jobs(cluster_name_on_cloud, [state_filter])
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'V1 query_instances: squeue query for state '
                         f'{state_filter!r} failed: {e}')
            continue
        for job_id in job_ids:
            seen_job_ids.add(job_id)
            # Re-read the precise state to land on the upper-case label
            # that ``_V1_SLURM_STATE_TO_CLUSTER_STATUS`` keys on.
            state = client.get_job_state(job_id)
            if state is None:
                # Aged out between the two calls — fall through to
                # sacct below.
                continue
            sky_status = _V1_SLURM_STATE_TO_CLUSTER_STATUS.get(state)
            if sky_status is None:
                if non_terminated_only:
                    continue
                try:
                    reason = client.get_job_reason(job_id)
                except Exception:  # pylint: disable=broad-except
                    reason = None
                statuses[job_id] = (None, reason)
                continue
            try:
                nodes, _ = client.get_job_nodes(job_id)
            except Exception as e:  # pylint: disable=broad-except
                # PENDING jobs may not yet have nodes; the empty result
                # below is fine. Other failures we surface as no nodes.
                logger.debug(f'V1 query_instances: get_job_nodes({job_id}) '
                             f'failed: {e}')
                nodes = []
            if not nodes:
                # Surface the job-id keyed entry so the caller sees the
                # cluster is in INIT.
                statuses[job_id] = (sky_status, None)
                continue
            for node in nodes:
                instance_id = slurm_utils.instance_id(job_id, node)
                statuses[instance_id] = (sky_status, None)

    # --- sacct: terminal state for jobs aged past MinJobAge. ---
    # Only consult sacct if squeue didn't already produce a live entry,
    # and only when the caller actually wants terminal state.
    if not non_terminated_only and not statuses:
        sacct_state = _v1_sacct_job_state(client, cluster_name_on_cloud)
        if sacct_state is not None:
            sky_status = _V1_SLURM_STATE_TO_CLUSTER_STATUS.get(sacct_state)
            # sacct's most-recent terminal state is keyed on the cluster
            # name (we have no per-node fanout once the job is gone).
            statuses[cluster_name_on_cloud] = (sky_status, sacct_state)

    return statuses


def _v1_precondition_cleanup(
        login_node_runner: 'command_runner.SSHCommandRunner', sky_base_dir: str,
        cluster_name_on_cloud: str) -> None:
    """Purge residue from a prior v1 attempt under the same cluster name.

    PLAN.md gap #10: if a previous attempt was killed under SIGKILL or
    OOM, its EXIT trap (which would have removed
    ``{sky_base_dir}/.sky_clusters/<cluster_name_on_cloud>``) never
    ran. Submitting attempt N+1 without clearing that residue can leave
    stale log markers, ready-signal files, or container init flags in
    place, confusing the new attempt.

    Best-effort: failures (e.g. NFS hiccup) are logged and tolerated —
    the new attempt's sbatch script will still proceed; we just risk
    seeing the stale residue if cleanup didn't take. The legacy path's
    EXIT trap was the only place this was previously removed.
    """
    cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                             cluster_name_on_cloud)
    cmd = f'rm -rf {shlex.quote(cluster_home_dir)}'
    rc, stdout, stderr = login_node_runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    if rc != 0:
        logger.warning(f'V1 precondition cleanup of {cluster_home_dir} '
                       f'failed (rc={rc}): {stdout}\n{stderr}. Continuing '
                       'with submission; stale residue may be visible to '
                       'the new attempt.')
    else:
        logger.debug(f'V1 precondition cleanup of {cluster_home_dir} ok.')


def _v1_sacct_node_list(client: 'slurm.SlurmClient',
                        job_id: str) -> Optional[List[str]]:
    """Recover the per-job node list from ``sacct`` when squeue is empty.

    Used by ``_get_cluster_info_v1`` as a fallback for jobs that have
    aged out of squeue. ``NodeList`` is on the parent allocation row;
    ``scontrol show hostnames`` expands the compact Slurm hostlist
    notation (e.g. ``node-[01-03]``) into individual node names.
    """
    cmd = (f'sacct -j {job_id} --format=NodeList --parsable2 --noheader -X')
    # pylint: disable=protected-access
    rc, stdout, _ = client._run_slurm_cmd(cmd)
    if rc != 0:
        return None
    nodelist: Optional[str] = None
    for line in stdout.splitlines():
        value = line.strip()
        if not value or value == 'None assigned':
            continue
        nodelist = value
        break
    if nodelist is None:
        return None
    expand_cmd = f'scontrol show hostnames {shlex.quote(nodelist)}'
    rc, stdout, _ = client._run_slurm_cmd(expand_cmd)
    if rc != 0:
        return None
    nodes = [line.strip() for line in stdout.splitlines() if line.strip()]
    return nodes if nodes else None


def _get_cluster_info_v1(
        cluster_name_on_cloud: str,
        provider_config: Dict[str, Any]) -> Optional[common.ClusterInfo]:
    """v1 ``get_cluster_info``: minimal shape, no SSH-port shenanigans.

    Unlike the legacy path's ``InstanceInfo(ssh_port=ssh_port,
    external_ip=ssh_host, ...)`` (which is misleading on v1 — SkyPilot
    never SSHes to the compute node), the v1 shape mirrors K8s v1:
    no external_ip, empty ssh_user override, and the InstanceInfo's
    ``ssh_port`` left at its default. Tags carry the structured
    ``job_id`` / ``node`` / ``TAG_SKYPILOT_CLUSTER_NAME`` so
    ``_resolve_slurm_target`` (managed_job_runtime) finds the right
    job_id without parsing.

    Returns ``None`` (not a ``ClusterInfo``) when listing nodes fails
    even after the sacct fallback — callers should treat that as
    "no instances yet" and fall through to a safe default.
    """
    client = _slurm_client_from_provider_config(provider_config)

    try:
        running_jobs = client.query_jobs(cluster_name_on_cloud, ['running'])
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'V1 get_cluster_info: squeue query failed: {e}')
        return None

    if not running_jobs:
        # No running job — return empty so caller treats as "no instances
        # yet" / cluster torn down.
        return common.ClusterInfo(
            instances={},
            head_instance_id=None,
            provider_name='slurm',
            provider_config=provider_config,
        )
    assert len(running_jobs) == 1, (
        f'Multiple running jobs found for v1 cluster '
        f'{cluster_name_on_cloud}: {running_jobs}. Expected single-job-per'
        '-name (enforced by _create_managed_job_v1).')

    job_id = running_jobs[0]
    nodes: List[str] = []
    try:
        nodes, _ = client.get_job_nodes(job_id)
    except Exception as e:  # pylint: disable=broad-except
        # Fall back to sacct — the job may have just exited and squeue
        # is briefly out of sync, or get_job_nodes' scontrol-show-node
        # invocation hit a transient.
        logger.debug(f'V1 get_cluster_info: get_job_nodes({job_id}) failed: '
                     f'{e}; trying sacct.')
        recovered = _v1_sacct_node_list(client, job_id)
        if recovered:
            nodes = recovered

    if not nodes:
        # Give the caller a None so it can fall through to its safe
        # default rather than emitting an empty-instances ClusterInfo
        # that downstream code might interpret as "definitely no
        # nodes".
        logger.debug(f'V1 get_cluster_info: no nodes resolved for job '
                     f'{job_id}; returning None.')
        return None

    instances: Dict[str, List[common.InstanceInfo]] = {}
    for node in nodes:
        instance_id = slurm_utils.instance_id(job_id, node)
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                # V1 never SSHes to the compute node; leave internal_ip
                # empty (mirrors K8s v1's empty pod_ip handling).
                internal_ip='',
                external_ip=None,
                tags={
                    constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
                    'job_id': str(job_id),
                    'node': node,
                },
                node_name=instance_id,
            )
        ]

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=slurm_utils.instance_id(job_id, nodes[0]),
        provider_name='slurm',
        # V1 has no SSH user — runtime never executes against the node.
        ssh_user='',
        provider_config=provider_config,
    )


def _all_resource_alternatives_are_slurm(task: 'task_lib.Task') -> bool:
    """Whether every resource alternative on the task is Slurm."""
    # pylint: disable=import-outside-toplevel
    from sky import clouds as sky_clouds

    resources = list(task.resources)
    if not resources:
        return False
    return all(resource.cloud is not None and
               resource.cloud.is_same_cloud(sky_clouds.Slurm())
               for resource in resources)


def _get_unsupported_v1_inputs(task: 'task_lib.Task') -> List[str]:
    """Return user inputs that force fallback from the v1 template."""
    unsupported: List[str] = []
    local_file_mounts = task.get_local_to_remote_file_mounts() or {}
    has_local_workdir = (task.workdir is not None and
                         isinstance(task.workdir, str))
    storage_mounts = task.storage_mounts
    if has_local_workdir:
        unsupported.append(f'workdir: {task.workdir!r}')
    if local_file_mounts:
        for dst, src in local_file_mounts.items():
            unsupported.append(f'file_mounts: {src!r} -> {dst!r}')
    if storage_mounts:
        for mnt_path, storage in storage_mounts.items():
            unsupported.append(
                f'storage_mounts: {storage.name!r} -> {mnt_path!r}')
    return unsupported


def will_use_v1_template(
    task: 'task_lib.Task',
    *,
    is_launched_by_jobs_controller: bool = True,
) -> bool:
    """Whether this task will be claimed by the Slurm v1 template.

    Keep this predicate in sync with ``template_override()``.
    """
    if (not is_slurm_managed_jobs_v1_enabled() or
            not is_launched_by_jobs_controller):
        return False
    if not _all_resource_alternatives_are_slurm(task):
        return False
    return not _get_unsupported_v1_inputs(task)


def template_override(
    task: 'task_lib.Task',
    *,
    _extra_launch_context: Dict[str, Any],
    _is_launched_by_jobs_controller: bool,
) -> Optional['provision_lib.TemplateSpec']:
    """Claim v1 Slurm managed-job tasks and return their template spec."""
    # pylint: disable=import-outside-toplevel
    from sky import provision as provision_lib
    from sky import task as task_lib
    from sky.backends import backend_utils

    del _extra_launch_context  # No per-task context consumed yet.

    if not will_use_v1_template(
            task,
            is_launched_by_jobs_controller=_is_launched_by_jobs_controller,
    ):
        unsupported = _get_unsupported_v1_inputs(task)
        if (is_slurm_managed_jobs_v1_enabled() and
                _is_launched_by_jobs_controller and
                _all_resource_alternatives_are_slurm(task) and unsupported):
            logger.warning(
                'Falling back to legacy Slurm managed-jobs path: jobs with '
                'local file mounts, workdir, or storage mounts are not '
                'supported on the v1 fast path. Unsupported inputs:\n  ' +
                '\n  '.join(unsupported) +
                '\nUse a git workdir or cloud storage (s3://, gs://, etc.) '
                'to opt into the v1 fast path.')
        return None

    resources = list(task.resources)
    envs = task_lib.get_plaintext_envs_and_secrets(task.envs_and_secrets)

    resource = resources[0]
    sbatch_options: Dict[str, Any] = {}
    cluster_overrides = resource.cluster_config_overrides
    if cluster_overrides:
        # Surface task-level sbatch_options into the template; cluster /
        # partition level options are merged later by
        # ``Slurm.make_deploy_resources_variables`` and threaded through
        # the standard template variables (``sbatch_options``).
        task_sbatch = cluster_overrides.get(
            'slurm', {}).get('sbatch_options') if isinstance(
                cluster_overrides, dict) else None
        if isinstance(task_sbatch, dict):
            sbatch_options.update(task_sbatch)

    workdir = task.workdir
    workdir_config: Optional[Dict[str, Any]] = None
    if workdir is not None:
        # Local workdirs are blocked by ``will_use_v1_template`` above —
        # this branch only handles the git-dict form.
        assert isinstance(
            workdir,
            dict), (f'Expected git workdir (dict), got {type(workdir)}')
        workdir_config = {'git': workdir}

    file_mounts: Optional[Dict[str, str]] = None
    if task.file_mounts:
        file_mounts = dict(task.file_mounts)

    container_image = resource.extract_docker_image()

    return provision_lib.TemplateSpec(
        template_path='slurm-managed-job-v1.yml.j2',
        variables={
            'setup': task.setup,
            'run': task.run,
            'envs': envs,
            'num_nodes': task.num_nodes,
            'num_gpus_per_node': backend_utils.get_num_gpus_per_node(task),
            'workdir': workdir_config,
            'file_mounts': file_mounts,
            'sbatch_options': sbatch_options or None,
            'container_image': container_image,
        },
    )


def _build_workdir_block(workdir: Optional[Dict[str, Any]]) -> str:
    """Build sbatch preamble commands for the git workdir, or empty."""
    if not workdir:
        return ''
    if 'git' not in workdir:
        return ''
    if _GIT_CLONE_SCRIPT_B64 is None:
        raise RuntimeError(
            'git_clone.sh not found; cannot use git workdir with the v1 '
            'fast path')
    # The git_clone.sh script expects to be invoked with the target
    # workdir as its only positional argument. ``SKY_REMOTE_WORKDIR`` is
    # the canonical destination used elsewhere; expand ~ to $HOME so
    # bash double-quote semantics work.
    remote_workdir = skylet_constants.SKY_REMOTE_WORKDIR.replace('~', '$HOME')
    return (
        '# === Workdir: git clone via git_clone.sh ===\n'
        f"echo '{_GIT_CLONE_SCRIPT_B64}' | base64 -d > /tmp/sky_git_clone.sh\n"
        f'bash /tmp/sky_git_clone.sh "{remote_workdir}"\n'
        'rm -f /tmp/sky_git_clone.sh\n'
        f'cd "{remote_workdir}"\n')


def _build_file_mounts_block(file_mounts: Optional[Dict[str, str]]) -> str:
    """Build sbatch preamble commands for cloud-URI file mounts, or empty."""
    if not file_mounts:
        return ''
    # Lazy imports: ``sky.data.data_utils`` and ``sky.cloud_stores``
    # both transitively import ``sky.clouds``. Importing them at
    # module level creates a cycle when this module is loaded as part
    # of the managed-job controller subprocess's import order (which
    # starts at ``sky.clouds`` itself).
    # pylint: disable=import-outside-toplevel
    from sky import cloud_stores
    from sky.data import data_utils

    commands: List[str] = []
    for remote_path, source in file_mounts.items():
        if not data_utils.is_cloud_store_url(source):
            logger.warning('Slurm v1 fast path: skipping non-cloud-URL '
                           f'file mount {source} -> {remote_path}')
            continue
        try:
            storage = cloud_stores.get_storage_from_path(source)
            if storage.is_directory(source):
                mkdir_cmd = f'mkdir -p {shlex.quote(remote_path)}'
                sync_cmd = storage.make_sync_dir_command(
                    source=source, destination=remote_path)
            else:
                mkdir_cmd = (f'mkdir -p $(dirname {shlex.quote(remote_path)})')
                sync_cmd = storage.make_sync_file_command(
                    source=source, destination=remote_path)
            commands.append(f'{mkdir_cmd} && {sync_cmd}')
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                f'Slurm v1 fast path: cannot generate sync command for '
                f'{source} -> {remote_path}',
                exc_info=True)
    if not commands:
        return ''
    return '# === File Mounts ===\n' + '\n'.join(commands) + '\n'


def _build_env_exports(envs: Optional[Dict[str, str]]) -> str:
    """Render env exports as bash ``export`` lines."""
    if not envs:
        return ''
    lines = ['# === Env vars ===']
    for key, value in envs.items():
        lines.append(f'export {key}={shlex.quote(str(value))}')
    return '\n'.join(lines) + '\n'


def _build_v1_sbatch_script(
    *,
    cluster_name_on_cloud: str,
    num_nodes: int,
    log_path: str,
    resources: Dict[str, Any],
    setup: Optional[str],
    run: Optional[str],
    envs: Dict[str, str],
    workdir: Optional[Dict[str, Any]],
    file_mounts: Optional[Dict[str, str]],
    container_image: Optional[str],
    extra_sbatch_directives: str,
) -> str:
    """Build the v1 sbatch script whose body is the user's setup+run.

    Pattern: a single ``srun --nodes=N --ntasks-per-node=1`` whose body
    is ``bash -c '<setup> && <run>'``. Container support wraps the same
    srun with pyxis flags. No ``sleep infinity``; the job exits when
    user code exits and Slurm marks it COMPLETED / FAILED naturally.
    """
    accelerator_type = resources.get('accelerator_type')
    accelerator_count_raw = resources.get('accelerator_count')
    try:
        accelerator_count = int(
            accelerator_count_raw) if accelerator_count_raw is not None else 0
    except (TypeError, ValueError):
        accelerator_count = 0

    gpu_directive = ''
    if accelerator_count > 0:
        if (accelerator_type is not None and
                accelerator_type.upper() != 'NONE'):
            gpu_directive = (f'#SBATCH --gres=gpu:{accelerator_type}:'
                             f'{accelerator_count}')
        else:
            gpu_directive = f'#SBATCH --gres=gpu:{accelerator_count}'

    mem_directive = ''
    if float(resources.get('memory', 0)) > 0:
        mem_in_mb = int(float(resources['memory']) * 1024)
        mem_directive = f'#SBATCH --mem={mem_in_mb}M'

    cpus = int(resources.get('cpus', 1))

    # Compose the inner user command. ``set -o pipefail`` is the only
    # global shell flag we impose — legacy doesn't add ``-e`` or ``-u``
    # and user setup blocks routinely rely on ``+u`` (sourcing
    # unset-var-tolerant rc files) and ``|| true``-style guards.
    user_setup = (setup or '').strip()
    user_run = (run or '').strip()
    if user_setup and user_run:
        user_script = f'set -o pipefail\n{user_setup}\n{user_run}'
    elif user_setup:
        user_script = f'set -o pipefail\n{user_setup}'
    elif user_run:
        user_script = f'set -o pipefail\n{user_run}'
    else:
        user_script = 'set -o pipefail\n# no setup / run specified'
    quoted_user_script = shlex.quote(user_script)

    # Container args (pyxis/enroot). Per-job container name (suffix with
    # SLURM_JOB_ID) so a half-extracted rootfs from a killed previous
    # attempt cannot collide with this attempt.
    container_args = ''
    if container_image:
        container_name_base = slurm_utils.pyxis_container_name(
            cluster_name_on_cloud)
        container_args = (
            f'--container-image={shlex.quote(container_image)} '
            f'--container-name={shlex.quote(container_name_base)}-'
            f'${{SLURM_JOB_ID}} '
            f'--container-remap-root '
            f'--container-writable ')

    label_flag = '--label ' if num_nodes > 1 else ''
    srun_line = (f'srun --nodes={num_nodes} --ntasks-per-node=1 '
                 f'{label_flag}--unbuffered {container_args}'
                 f'bash -c {quoted_user_script}')

    workdir_block = _build_workdir_block(workdir)
    file_mounts_block = _build_file_mounts_block(file_mounts)
    env_exports = _build_env_exports(envs)

    # pylint: disable=line-too-long
    return (f'#!/bin/bash\n'
            f'#SBATCH --job-name={cluster_name_on_cloud}\n'
            f'#SBATCH --output={log_path}\n'
            f'#SBATCH --error={log_path}\n'
            f'#SBATCH --nodes={num_nodes}\n'
            f'#SBATCH --wait-all-nodes=1\n'
            f'#SBATCH --no-requeue\n'
            f'#SBATCH --cpus-per-task={cpus}\n'
            f'{mem_directive}\n'
            f'{gpu_directive}'
            f'{extra_sbatch_directives}\n'
            f'\n'
            f'{workdir_block}'
            f'{file_mounts_block}'
            f'{env_exports}'
            f'\n'
            f'{srun_line}\n')


def _create_managed_job_v1(
        region: str, cluster_name: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Create a Slurm v1 managed job (single real ``sbatch``).

    Replaces the ``sleep infinity`` + ``srun --jobid`` pattern: the
    sbatch script body IS the user's task. When user code exits, the
    Slurm job finishes; state propagates to ``sacct`` naturally.

    Preserves the legacy prologue invariants (COMPLETING-drain +
    existing-job reattach) so concurrent recovery attempts cannot trip
    over each other.
    """
    provider_config = config.provider_config
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = int(ssh_config_dict['port'])
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict.get('private_key', None)
    ssh_proxy_command = ssh_config_dict.get('proxycommand', None)
    ssh_proxy_jump = ssh_config_dict.get('proxyjump', None)
    identities_only = ssh_config_dict.get('identities_only', False)
    partition = slurm_utils.get_partition_from_config(provider_config)

    client = slurm.SlurmClient(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        identities_only=identities_only,
    )

    # ---- Drain COMPLETING jobs before submitting. Ported verbatim ----
    # ---- from ``_create_virtual_instance``. Without this, recovery ----
    # ---- attempt N+1 races attempt N's teardown and trips the ----
    # ---- ``assert len(jobs_state) == 1`` invariant in ----
    # ---- terminate_instances. ----
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
        raise RuntimeError(f'Found {len(completing_jobs)} jobs still in '
                           'completing state after '
                           f'{_JOB_TERMINATION_TIMEOUT_SECONDS}s. '
                           'This is typically due to non-killable processes '
                           'associated with the job.')

    # ---- Reattach if a previous attempt is already PENDING/RUNNING. ----
    # ---- Without this, a controller restart mid-attempt would submit ----
    # ---- a duplicate job. ----
    existing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        ['pending', 'running'],
    )
    provision_timeout: int = provider_config.get('provision_timeout', -1)

    num_nodes = config.count
    last_status_msg = None

    def _on_pending(state: str, reason: Optional[str],
                    pending_count: Optional[int]) -> None:
        nonlocal last_status_msg
        del state  # unused
        parts = []
        if reason:
            parts.append(f'pending: {reason}')
        if pending_count is not None and pending_count > 0:
            word = 'other' if pending_count == 1 else 'others'
            parts.append(f'{pending_count} {word} pending')
        if parts:
            msg = f'Launching ({", ".join(parts)})'
        else:
            msg = 'Launching'
        status_msg = ux_utils.spinner_message(msg, cluster_name=cluster_name)
        if status_msg != last_status_msg:
            rich_utils.force_update_status(status_msg)
            last_status_msg = status_msg

    if existing_jobs:
        assert len(existing_jobs) == 1, (
            f'Multiple jobs found with name {cluster_name_on_cloud}: '
            f'{existing_jobs}')

        job_id = existing_jobs[0]
        logger.debug(f'V1 job with name {cluster_name_on_cloud} already '
                     f'exists (JOBID: {job_id}); reattaching.')

        _wait_for_job_nodes(client, job_id, provision_timeout, partition,
                            _on_pending)
        nodes, _ = client.get_job_nodes(job_id)
        rich_utils.force_update_status(
            ux_utils.spinner_message('Launching', cluster_name=cluster_name))
        return common.ProvisionRecord(
            provider_name='slurm',
            region=region,
            zone=partition,
            cluster_name=cluster_name_on_cloud,
            head_instance_id=slurm_utils.instance_id(job_id, nodes[0]),
            resumed_instance_ids=[],
            created_instance_ids=[
                slurm_utils.instance_id(job_id, n) for n in nodes
            ],
            runtime_metadata=common.ProvisionRuntimeMetadata(
                has_ray=False,
                has_skylet=False,
                has_job_queue=False,
                ssh_available=False,
                runtime_setup_done=True,
                workdir_synced=True,
                file_mounts_synced=True,
                setup_done=True,
                run_started=True,
            ),
        )

    # ---- Fresh submission ----
    login_node_runner = command_runner.SSHCommandRunner(
        (ssh_host, ssh_port),
        ssh_user,
        ssh_key,
        ssh_proxy_command=ssh_proxy_command,
        ssh_proxy_jump=ssh_proxy_jump,
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
    )
    remote_home_dir = login_node_runner.get_remote_home_dir()

    # Resolve workdir (must be on a shared FS — same contract as legacy).
    workdir_cfg = skypilot_config.get_effective_region_config(
        cloud='slurm', region=region, keys=('workdir',), default_value=None)
    if workdir_cfg is not None:
        remote_env = client.get_env()
        workdir_cfg = slurm_utils.expand_path_vars(workdir_cfg, remote_env)

    sky_base_dir = workdir_cfg if workdir_cfg is not None else remote_home_dir
    assert os.path.isabs(sky_base_dir), (
        f'sky_base_dir must be absolute, got: {sky_base_dir}')
    log_path = _sbatch_log_path(sky_base_dir, '%j')

    provision_script_path = _sbatch_provision_script_path(
        sky_base_dir, cluster_name_on_cloud)
    provision_scripts_dir = os.path.dirname(provision_script_path)

    # Precondition cleanup: a previous attempt killed under SIGKILL
    # would have skipped its EXIT trap and left
    # ``.sky_clusters/<cluster_name_on_cloud>`` behind. The legacy path
    # tolerated this because its sbatch preamble recreated everything
    # before user code ran. V1's sbatch script writes nothing under
    # ``.sky_clusters`` today, but we still purge the directory so any
    # historical residue (logs, ready markers, partial container init
    # state) doesn't poison a fresh attempt — PLAN.md gap #10.
    _v1_precondition_cleanup(login_node_runner, sky_base_dir,
                             cluster_name_on_cloud)

    # Read the persisted v1 execution payload from the provider config.
    setup = provider_config.get('setup')
    run = provider_config.get('run')
    envs = provider_config.get('envs') or {}
    workdir_payload = provider_config.get('workdir')
    file_mounts_payload = provider_config.get('file_mounts')
    container_image = provider_config.get('container_image')
    sbatch_options = provider_config.get('sbatch_options') or {}

    # Build user-supplied + auto-generated sbatch directives. We still
    # consult partition info to compute a sensible ``--time`` default.
    slurm_cluster = slurm_utils.get_slurm_cluster_from_config(provider_config)
    partition_info = slurm_utils.get_partition_info(slurm_cluster, partition)
    if partition_info is None:
        raise ValueError(f'Partition info for {partition} not found '
                         f'for SLURM cluster {slurm_cluster}')
    extra_sbatch_directives = _build_sbatch_directives(sbatch_options,
                                                       partition_info,
                                                       partition)

    resources = config.node_config

    sbatch_script = _build_v1_sbatch_script(
        cluster_name_on_cloud=cluster_name_on_cloud,
        num_nodes=num_nodes,
        log_path=log_path,
        resources=resources,
        setup=setup,
        run=run,
        envs=envs,
        workdir=workdir_payload,
        file_mounts=file_mounts_payload,
        container_image=container_image,
        extra_sbatch_directives=extra_sbatch_directives,
    )

    cmd = f'mkdir -p {provision_scripts_dir}'
    rc, stdout, stderr = login_node_runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    subprocess_utils.handle_returncode(
        rc,
        cmd,
        'Failed to create provision scripts directory on login node.',
        stderr=f'{stdout}\n{stderr}')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=True) as f:
        f.write(sbatch_script)
        f.flush()
        login_node_runner.rsync(f.name,
                                provision_script_path,
                                up=True,
                                stream_logs=False)

    job_id = client.submit_job(partition, cluster_name_on_cloud,
                               provision_script_path)
    logger.debug(f'Submitted v1 Slurm job {job_id} to partition {partition} '
                 f'for cluster {cluster_name_on_cloud} with {num_nodes} nodes')

    _wait_for_job_nodes(client, job_id, provision_timeout, partition,
                        _on_pending)
    nodes, _ = client.get_job_nodes(job_id)
    rich_utils.force_update_status(
        ux_utils.spinner_message('Launching', cluster_name=cluster_name))

    created_instance_ids = [
        slurm_utils.instance_id(job_id, node) for node in nodes
    ]

    return common.ProvisionRecord(
        provider_name='slurm',
        region=region,
        zone=partition,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=slurm_utils.instance_id(job_id, nodes[0]),
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
        runtime_metadata=common.ProvisionRuntimeMetadata(
            has_ray=False,
            has_skylet=False,
            has_job_queue=False,
            ssh_available=False,
            runtime_setup_done=True,
            workdir_synced=True,
            file_mounts_synced=True,
            setup_done=True,
            run_started=True,
        ),
    )
