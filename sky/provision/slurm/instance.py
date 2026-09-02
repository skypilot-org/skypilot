"""Slurm instance provisioning."""

import json
import math
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

import colorama

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import slurm
from sky.provision import common
from sky.provision import constants
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

PROVISION_SCRIPTS_DIRECTORY_NAME = '.sky_provision'
SNAPSHOT_DIRECTORY_NAME = '.sky_snapshots'
SNAPSHOT_MANIFEST_FILENAME = 'manifest.json'
SNAPSHOT_JOB_DB_FILENAME = 'jobs.db'
SNAPSHOT_GENERATIONS_DIRECTORY_NAME = 'generations'
SNAPSHOT_MANIFEST_VERSION = 1
_CONTAINER_KEEPER_STEP_NAME = 'sky-container-keeper'
_SKYLET_KEEPER_STEP_NAME = 'sky-skylet-keeper'


def _sbatch_log_path(base_dir: str, job_id: str) -> str:
    return f'{base_dir}/{PROVISION_SCRIPTS_DIRECTORY_NAME}/slurm-{job_id}.out'


POLL_INTERVAL_SECONDS = 2
# Default KillWait is 30 seconds, so we add some buffer time here.
_JOB_TERMINATION_TIMEOUT_SECONDS = 60
# How long to give the batch script's TERM trap to run cleanup and exit
# before escalating to a plain scancel. Matches Slurm's default KillWait,
# the grace Slurm itself gives between SIGTERM and SIGKILL.
_TERMINATION_GRACE_PERIOD_SECONDS = 30
# Workloads get longer than Slurm's default 30-second KillWait to finish their
# TERM handlers before the stop path escalates individual steps to KILL.
_WORKLOAD_STEP_TERM_GRACE_PERIOD_SECONDS = 40
_WORKLOAD_STEP_DRAIN_TIMEOUT_SECONDS = 120
_WORKLOAD_KEEPER_STEP_NAMES = frozenset({
    _CONTAINER_KEEPER_STEP_NAME,
    _SKYLET_KEEPER_STEP_NAME,
})

# Terminal states where scancel is not needed or will fail.
_TERMINAL_JOB_STATES = {
    'COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'PREEMPTED',
    'SPECIAL_EXIT'
}
# States where the job holds an allocation that a graceful termination is
# not guaranteed to release, so teardown must verify the job exits and
# escalate if it does not.
_ESCALATION_JOB_STATES = {'RUNNING', 'SUSPENDED'}
# States that mean the job is on its way out: terminal, or COMPLETING
# (Slurm is already tearing it down).
_EXITING_JOB_STATES = _TERMINAL_JOB_STATES | {'COMPLETING'}

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

_PYXIS_MOUNT_PATH_PATTERN = re.compile(
    r'/(?:[A-Za-z0-9._~+@%=/\-]|\$[A-Za-z_][A-Za-z0-9_]*)*')


def _validate_pyxis_mount_path(path: str, field: str) -> None:
    """Validate a path before interpolating it into --container-mounts."""
    if not (isinstance(path, str) and
            _PYXIS_MOUNT_PATH_PATTERN.fullmatch(path)):
        raise ValueError(
            f'Invalid Pyxis container mount {field} path {path!r}. Paths must '
            'be absolute and contain only safe POSIX path characters or '
            'simple $VARNAME expansions.')


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


def _snapshot_dir(base_dir: str, cluster_name_on_cloud: str) -> str:
    """Returns the shared directory for a Slurm container snapshot."""
    # TODO(kevin): Verify that base_dir is on a shared filesystem (e.g., NFS)
    # visible to every allocated node before using it for snapshots.
    return f'{base_dir}/{SNAPSHOT_DIRECTORY_NAME}/{cluster_name_on_cloud}'


def _snapshot_manifest_path(snapshot_dir: str) -> str:
    return f'{snapshot_dir}/{SNAPSHOT_MANIFEST_FILENAME}'


def _snapshot_generation_dir(snapshot_dir: str, generation: str) -> str:
    return (f'{snapshot_dir}/{SNAPSHOT_GENERATIONS_DIRECTORY_NAME}/'
            f'{generation}')


def _snapshot_rank_path(generation_dir: str, rank: int) -> str:
    return f'{generation_dir}/rank{rank}.sqsh'


def _snapshot_job_db_path(generation_dir: str) -> str:
    return f'{generation_dir}/{SNAPSHOT_JOB_DB_FILENAME}'


def _run_on_login_node(
        login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
        cmd: str,
        failure_message: str,
        tolerate_returncodes: Tuple[int, ...] = (),
) -> Tuple[int, str]:
    """Run a command on the login node and return (returncode, stdout).

    Raises on failure unless the exit code is in `tolerate_returncodes`,
    which is for commands that use dedicated exit codes to report state
    (e.g. `test -f`).
    """
    rc, stdout, stderr = login_node_runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    if rc not in tolerate_returncodes:
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           failure_message,
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)
    return rc, stdout


def _validate_snapshot_manifest(
        manifest: Any,
        expected_num_nodes: Optional[int] = None) -> Dict[str, Any]:
    """Validate a Slurm container snapshot manifest.

    The manifest stores only facts about the snapshot. The paths of the
    files it references are always derived from its generation (see
    `_manifest_paths`), so no path read from shared storage is ever
    trusted or embedded into a command.
    """
    if not isinstance(manifest, dict):
        raise RuntimeError('Slurm container snapshot manifest must be a JSON '
                           'object.')
    if manifest.get('version') != SNAPSHOT_MANIFEST_VERSION:
        raise RuntimeError(
            'Unsupported Slurm container snapshot manifest version: '
            f'{manifest.get("version")!r}.')
    generation = manifest.get('generation')
    if (not isinstance(generation, str) or
            re.fullmatch(r'[0-9a-f]{32}', generation) is None):
        raise RuntimeError('Slurm container snapshot manifest has an invalid '
                           f'generation: {generation!r}.')
    image_id = manifest.get('image_id')
    if not isinstance(image_id, str) or not image_id:
        raise RuntimeError('Slurm container snapshot manifest has no image ID.')
    nodes = manifest.get('nodes')
    if (not isinstance(nodes, list) or not nodes or
            not all(isinstance(node, str) and node for node in nodes)):
        raise RuntimeError('Slurm container snapshot manifest has an invalid '
                           f'source node list: {nodes!r}.')
    if expected_num_nodes is not None and len(nodes) != expected_num_nodes:
        raise RuntimeError(
            'Slurm container snapshot node count does not match the cluster: '
            f'manifest has {len(nodes)}, cluster requires '
            f'{expected_num_nodes}.')
    if not isinstance(manifest.get('has_job_db'), bool):
        raise RuntimeError('Slurm container snapshot manifest has no job '
                           'database entry.')
    return manifest


def _read_snapshot_manifest(
        login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
        snapshot_dir: str,
        expected_num_nodes: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Read a snapshot manifest from the Slurm cluster's shared storage."""
    manifest_path = _snapshot_manifest_path(snapshot_dir)
    missing_exit_code = 44
    cmd = (f'test -f {shlex.quote(manifest_path)} || '
           f'exit {missing_exit_code}; cat {shlex.quote(manifest_path)}')
    rc, stdout = _run_on_login_node(
        login_node_runner,
        cmd,
        'Failed to read Slurm container snapshot manifest.',
        tolerate_returncodes=(missing_exit_code,))
    if rc == missing_exit_code:
        return None
    try:
        manifest = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError('Slurm container snapshot manifest is not valid '
                           f'JSON: {e}') from e
    return _validate_snapshot_manifest(manifest, expected_num_nodes)


def _manifest_paths(
        snapshot_dir: str,
        manifest: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    """Derive the per-rank snapshot paths and job db path of a manifest."""
    generation_dir = _snapshot_generation_dir(snapshot_dir,
                                              manifest['generation'])
    rank_paths = [
        _snapshot_rank_path(generation_dir, rank)
        for rank in range(len(manifest['nodes']))
    ]
    job_db_path = (_snapshot_job_db_path(generation_dir)
                   if manifest['has_job_db'] else None)
    return rank_paths, job_db_path


def _write_snapshot_manifest(
        login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
        snapshot_dir: str, manifest: Dict[str, Any]) -> None:
    """Atomically write a validated snapshot manifest to shared storage."""
    _validate_snapshot_manifest(manifest)
    manifest_path = _snapshot_manifest_path(snapshot_dir)
    remote_tmp_path = f'{manifest_path}.tmp'
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as f:
        json.dump(manifest, f, sort_keys=True)
        f.write('\n')
        f.flush()
        login_node_runner.rsync(f.name,
                                remote_tmp_path,
                                up=True,
                                stream_logs=False)
    _run_on_login_node(
        login_node_runner,
        f'mv -f {shlex.quote(remote_tmp_path)} {shlex.quote(manifest_path)}',
        'Failed to publish Slurm container snapshot manifest.')


def _remove_snapshot_path_best_effort(
    login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
    path: str,
    description: str,
) -> None:
    """Remove an unreferenced snapshot path without masking the main result."""
    try:
        _run_on_login_node(login_node_runner, f'rm -rf -- {shlex.quote(path)}',
                           f'Failed to remove {description}.')
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to remove {description}: '
                       f'{common_utils.format_exception(e, use_bracket=True)}')
        logger.debug('Full exception details:', exc_info=True)


def _validate_snapshot_files(
        login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
        snapshot_dir: str, manifest: Dict[str, Any]) -> None:
    """Raise if a file referenced by a snapshot manifest is missing."""
    rank_paths, job_db_path = _manifest_paths(snapshot_dir, manifest)
    labeled_paths = [
        (f'rank {rank}', path) for rank, path in enumerate(rank_paths)
    ]
    if job_db_path is not None:
        labeled_paths.append(('job database', job_db_path))
    missing = []
    for label, path in labeled_paths:
        rc, _ = _run_on_login_node(
            login_node_runner,
            f'test -f {shlex.quote(path)}',
            f'Failed to inspect the Slurm container snapshot ({label}).',
            tolerate_returncodes=(1,))
        if rc == 1:
            missing.append(f'{label}: {path}')
    if missing:
        raise RuntimeError('Slurm container snapshot is incomplete; missing ' +
                           ', '.join(missing) + '.')


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


def _enroot_container_name_job_scope(cluster_name_on_cloud: str,
                                     job_id: str) -> str:
    """Get enroot container name when container_scope=job (the default)."""
    return (f'pyxis_{job_id}_'
            f'{slurm_utils.pyxis_container_name(cluster_name_on_cloud)}')


def _make_slurm_client(provider_config: Dict[str, Any]) -> 'slurm.SlurmClient':
    ssh_config = provider_config['ssh']
    return slurm.SlurmClient(
        ssh_config['hostname'],
        int(ssh_config['port']),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        identities_only=ssh_config.get('identities_only', False),
        slurm_user=provider_config.get('slurm_user'),
    )


def _make_login_node_runner(
    provider_config: Dict[str,
                          Any]) -> command_runner.SlurmLoginNodeCommandRunner:
    ssh_config = provider_config['ssh']
    identities_only = ssh_config.get('identities_only', False)
    return command_runner.SlurmLoginNodeCommandRunner(
        (ssh_config['hostname'], int(ssh_config['port'])),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
        slurm_user=provider_config.get('slurm_user'),
    )


def _resolve_sky_base_dir(client: 'slurm.SlurmClient',
                          provider_config: Dict[str, Any]) -> str:
    """Resolve the shared base directory used for Slurm cluster state."""
    sky_base_dir = provider_config.get('sky_base_dir')
    if sky_base_dir is not None:
        if not isinstance(sky_base_dir, str) or not os.path.isabs(sky_base_dir):
            raise RuntimeError('Slurm sky_base_dir must be an absolute path, '
                               f'got {sky_base_dir!r}.')
        return sky_base_dir

    slurm_cluster = slurm_utils.get_slurm_cluster_from_config(provider_config)
    return slurm_utils.resolve_sky_base_dir(slurm_cluster, client)


def _resolve_skypilot_runtime_dir(client: 'slurm.SlurmClient',
                                  provider_config: Dict[str, Any],
                                  cluster_name_on_cloud: str) -> str:
    """Resolve the node-local SkyPilot runtime directory for a cluster."""
    slurm_cluster = slurm_utils.get_slurm_cluster_from_config(provider_config)
    tmpdir = skypilot_config.get_effective_region_config(cloud='slurm',
                                                         region=slurm_cluster,
                                                         keys=('tmpdir',),
                                                         default_value=None)
    if tmpdir is not None:
        # Resolve shell variables (e.g. $USER) in tmpdir using the remote
        # host's environment.
        tmpdir = slurm_utils.expand_path_vars(tmpdir, client.get_env())
        logger.debug(f'Resolved tmpdir: {tmpdir}')
    return _skypilot_runtime_dir(tmpdir, cluster_name_on_cloud)


def _stop_skylet_script(skypilot_runtime_dir: str) -> str:
    """Script that stops Skylet and keeps the keeper from restarting it."""
    skylet_start_file = (
        f'{skypilot_runtime_dir}/{skylet_constants.SKYLET_START_FILE}')
    skylet_pid_file = f'{skypilot_runtime_dir}/.sky/skylet_pid'
    # Remove the start spec first so the keeper cannot restart Skylet.
    return (
        f'rm -f -- {shlex.quote(skylet_start_file)}; '
        f'if [ -f {shlex.quote(skylet_pid_file)} ]; then '
        f'skylet_pid="$(cat {shlex.quote(skylet_pid_file)})"; '
        'if kill -0 "$skylet_pid" 2>/dev/null; then kill "$skylet_pid"; fi; '
        'fi')


def _srun_on_node(job_id: str, node: str, script: str) -> str:
    """Build an srun command that runs a script on one allocated node."""
    return (f'srun --unbuffered --overlap --jobid={shlex.quote(job_id)} '
            f'--nodelist={shlex.quote(node)} --nodes=1 --ntasks=1 '
            f'bash -c {shlex.quote(script)}')


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
    partition = slurm_utils.get_partition_from_config(provider_config)
    client = _make_slurm_client(provider_config)

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
    login_node_runner = _make_login_node_runner(provider_config)
    remote_home_dir = login_node_runner.get_remote_home_dir()

    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    sbatch_log_base_dir = sky_base_dir

    provision_script_path = _sbatch_provision_script_path(
        sky_base_dir, cluster_name_on_cloud)
    provision_scripts_dir = os.path.dirname(provision_script_path)

    skypilot_runtime_dir = _resolve_skypilot_runtime_dir(
        client, provider_config, cluster_name_on_cloud)
    sky_cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                                 cluster_name_on_cloud)
    snapshot_dir = _snapshot_dir(sky_base_dir, cluster_name_on_cloud)
    snapshot_manifest_path = _snapshot_manifest_path(snapshot_dir)
    snapshot_manifest = _read_snapshot_manifest(login_node_runner,
                                                snapshot_dir,
                                                expected_num_nodes=num_nodes)
    if snapshot_manifest is not None:
        _validate_snapshot_files(login_node_runner, snapshot_dir,
                                 snapshot_manifest)
    ready_signal = f'{sky_cluster_home_dir}/.sky_sbatch_ready'

    # For non-Docker Hub registries, pyxis/enroot requires '#' separator
    # between registry and path. See:
    # https://github.com/NVIDIA/pyxis/wiki/Usage#registry-syntax
    container_image = resources.get('image_id')
    original_container_image = container_image
    if snapshot_manifest is not None:
        if container_image is None:
            raise RuntimeError('A Slurm container snapshot exists for this '
                               'cluster, but its cluster record has no '
                               'container image.')
        if snapshot_manifest['image_id'] != container_image:
            raise RuntimeError(
                'Slurm container snapshot image does not match the cluster '
                f'record: manifest has {snapshot_manifest["image_id"]!r}, '
                f'cluster has {container_image!r}.')
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
            # Share only skylet state between the host and container.
            # Mounting the full runtime dir exposes the host venv.
            # The container then skips its own venv.
            # The host python symlink is invalid inside the container.
            f'{skypilot_runtime_dir}/.sky:{skypilot_runtime_dir}/.sky',
        ]
        # The cluster state directory may be outside the remote home directory.
        if sky_base_dir != remote_home_dir:
            mount_paths.append(f'{sky_base_dir}:{sky_base_dir}')
        for volume_mount in resources.get('volume_mounts', []) or []:
            dst_path = volume_mount['path']
            volume_config = volume_mount['volume_config']
            host_path = volume_config['config']['host_path']
            _validate_pyxis_mount_path(host_path, 'source')
            _validate_pyxis_mount_path(dst_path, 'destination')
            mount = f'{host_path}:{dst_path}'
            # Fail closed: anything but an explicit 'rw' mounts read-only.
            if volume_config['config'].get('mode') != 'rw':
                mount += ':ro'
            mount_paths.append(mount)
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
        pyxis_args = (f'--container-name={shlex.quote(container_name)}:create '
                      f'--container-mounts="{container_mounts}" '
                      f'--container-remap-root '
                      f'--no-container-mount-home '
                      f'--container-writable')
        global_enroot_name = _enroot_container_name_global_scope(
            cluster_name_on_cloud)
        if snapshot_manifest is None:
            container_cmd = shlex.quote(
                f'{container_init_script}'
                f'touch {container_init_done_dir}/$SLURM_PROCID && '
                'sleep infinity')
            container_launch_block = (
                f'CONTAINER_PIDS=()\n'
                f'srun --overlap '
                f'--job-name={_CONTAINER_KEEPER_STEP_NAME} '
                f'{"--label " if num_nodes > 1 else ""}--unbuffered '
                f'--nodes={num_nodes} --ntasks-per-node=1 '
                f'--container-image={shlex.quote(container_image)} '
                f'{pyxis_args} bash -c {container_cmd} &\n'
                f'CONTAINER_PIDS+=("$!")')
        else:
            remove_stale_container_script = f"""\
while IFS= read -r candidate; do
    if [ "$candidate" = {shlex.quote(global_enroot_name)} ]; then
        enroot remove -f "$candidate"
    fi
done < <(enroot list)
"""
            snapshot_rank_paths, snapshot_job_db_path = _manifest_paths(
                snapshot_dir, snapshot_manifest)
            restore_lines = [
                'mapfile -t SKY_NODES < <(scontrol show hostnames '
                '"$SLURM_JOB_NODELIST")',
                f'if [ "${{#SKY_NODES[@]}}" -ne "{num_nodes}" ]; then',
                '  echo "[container] ERROR: Allocation node count does not '
                'match snapshot."',
                '  exit 1',
                'fi',
            ]
            if snapshot_job_db_path is not None:
                restored_job_db_path = (f'{skypilot_runtime_dir}/.sky/jobs.db')
                restored_job_db_dir = os.path.dirname(restored_job_db_path)
                restore_job_db_script = (
                    f'mkdir -p {shlex.quote(restored_job_db_dir)}; '
                    f'cp -f {shlex.quote(snapshot_job_db_path)} '
                    f'{shlex.quote(restored_job_db_path)}')
                restore_lines.append(
                    'srun --overlap --unbuffered --nodes=1 --ntasks=1 '
                    '-w "${SKY_NODES[0]}" bash -c '
                    f'{shlex.quote(restore_job_db_script)}')
            restore_lines.extend([
                f'srun --overlap --unbuffered --nodes={num_nodes} '
                f'--ntasks-per-node=1 bash -c '
                f'{shlex.quote(remove_stale_container_script)}',
                'CONTAINER_PIDS=()',
            ])
            for rank, snapshot_path in enumerate(snapshot_rank_paths):
                container_cmd = shlex.quote(
                    f'touch {container_init_done_dir}/{rank} && '
                    'sleep infinity')
                restore_lines.extend([
                    f'echo "[container] Restoring rank {rank} on '
                    f'${{SKY_NODES[{rank}]}}"',
                    f'srun --overlap '
                    f'--job-name={_CONTAINER_KEEPER_STEP_NAME} '
                    f'--unbuffered --nodes=1 --ntasks=1 '
                    f'-w "${{SKY_NODES[{rank}]}}" '
                    f'--container-image={shlex.quote(snapshot_path)} '
                    f'{pyxis_args} bash -c {container_cmd} &',
                    'CONTAINER_PIDS+=("$!")',
                ])
            container_launch_block = '\n'.join(restore_lines)
        container_ready_script = f"""\
global_target={shlex.quote(global_enroot_name)}
job_target="pyxis_${{SLURM_JOB_ID}}_"{shlex.quote(container_name)}
for ((attempt = 1; attempt <= 30; attempt++)); do
    container_pid=
    while read -r name pid rest; do
        if [ "$name" = "$global_target" ] || [ "$name" = "$job_target" ]; then
            container_pid=$pid
        fi
    done < <(enroot list -f)
    case "$container_pid" in
        ''|*[!0-9]*) ;;
        *)
            if kill -0 "$container_pid" 2>/dev/null; then
                exit 0
            fi
            ;;
    esac
    sleep 1
done
echo "[container] ERROR: Container is not running as $global_target or $job_target." >&2
exit 1
"""
        assert original_container_image is not None
        snapshot_restore_complete_block = ''
        if snapshot_manifest is not None:
            snapshot_restore_complete_block = (
                f'if ! rm -rf -- {shlex.quote(snapshot_dir)}; then\n'
                '  echo "[container] ERROR: Failed to consume snapshot." '
                '>&2\n'
                '  exit 1\n'
                'fi\n')
        container_block = (
            f'srun --nodes={num_nodes} mkdir -p {host_ccache_dir}\n'
            f'CONTAINER_START=$SECONDS\n'
            f'echo "[container] Initializing {container_name} on all nodes"\n'
            f'rm -rf {container_init_done_dir}\n'
            f'mkdir -p {container_init_done_dir}\n'
            f'{container_launch_block}\n'
            f'while true; do\n'
            f'  for container_pid in "${{CONTAINER_PIDS[@]}}"; do\n'
            f'    if ! kill -0 "$container_pid" 2>/dev/null; then\n'
            f'      wait "$container_pid"\n'
            f'      container_rc=$?\n'
            f'      if [ "$container_rc" -eq 0 ]; then container_rc=1; fi\n'
            f'      echo "[container] ERROR: Container initialization '
            f'failed with exit code $container_rc."\n'
            f'      exit "$container_rc"\n'
            f'    fi\n'
            f'  done\n'
            f'  shopt -s nullglob\n'
            f'  ready_markers=({container_init_done_dir}/*)\n'
            f'  num_ready=${{#ready_markers[@]}}\n'
            f'  if [ "$num_ready" -ge "{num_nodes}" ]; then break; fi\n'
            f'  sleep 1\n'
            f'done\n'
            f'srun --overlap --unbuffered --nodes={num_nodes} '
            f'--ntasks-per-node=1 bash -c '
            f'{shlex.quote(container_ready_script)}'
            f' || exit 1\n'
            f'echo "[container] Ready in $((SECONDS - CONTAINER_START))s"\n'
            f'printf \'%s\\n\' {shlex.quote(original_container_image)} > '
            f'{container_marker_file}\n'
            f'{snapshot_restore_complete_block}'
            f'touch {ready_signal}')

    # sbatch batch script ── lives as long as the allocation
    #   └─ keeper srun client (host side, never enters the container)
    #        └─ keeper step ── outer loop restarts the step
    #             └─ run the start spec FOREGROUND ── inner loop restarts skylet
    #                  └─ skylet (the step cgroup owns it)
    #
    # attempt_skylet (any shape, possibly in-container)
    #   └─ writes <runtime_dir>/.sky/skylet_start ──► read by the keeper loop
    #        (runtime dir = the same host path in both shapes)
    #
    # A nohup'd skylet dies here: proctrack/cgroup reaps whatever a short-lived
    # step leaves behind. The keeper stays out of the workload container because
    # the Slurm CLIs, munge, and slurm.conf exist only on the host.
    keeper_start_file = (
        f'{skypilot_runtime_dir}/{skylet_constants.SKYLET_START_FILE}')
    # A spec written in the container inherits HOME=/root.
    # The keeper restores the host HOME before running it.
    keeper_loop = (f'while true; do '
                   f'if [ -f {keeper_start_file} ]; then '
                   f'HOME={sky_cluster_home_dir} bash {keeper_start_file}; '
                   f'fi; '
                   f'sleep 5; done')
    skylet_keeper_block = (
        'SKY_HEAD_NODE=$(scontrol show hostnames "$SLURM_JOB_NODELIST" '
        '| head -n1)\n'
        f'( while true; do '
        f'srun --overlap --jobid=$SLURM_JOB_ID --nodes=1 --ntasks=1 '
        f'--job-name={_SKYLET_KEEPER_STEP_NAME} '
        f'--nodelist=$SKY_HEAD_NODE '
        f'bash -c {shlex.quote(keeper_loop)}; '
        f'sleep 5; done ) &')

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
#SBATCH --cpus-per-task={math.ceil(float(resources["cpus"]))}
{mem_directive}{gpu_directive}{extra_sbatch_directives}

# Cleanup function to remove cluster dirs on job termination.
cleanup() {{
    saved_exit=$?
    # Prevent the keeper from restarting Skylet during cleanup.
    rm -f "{skypilot_runtime_dir}/{skylet_constants.SKYLET_START_FILE}"
    echo "Terminating Skylet..."
    if [ -f "{skypilot_runtime_dir}/.sky/skylet_pid" ]; then
        kill $(cat "{skypilot_runtime_dir}/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --overlap --nodes={num_nodes} --ntasks-per-node=1 enroot remove -f {shlex.quote(_enroot_container_name_global_scope(cluster_name_on_cloud))} 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --overlap --nodes={num_nodes} rm -rf {skypilot_runtime_dir}
    # A stop publishes the snapshot manifest before cancellation. Keep the
    # logs referenced by the jobs database that start will restore.
    if [ -f {shlex.quote(snapshot_manifest_path)} ]; then
        find {shlex.quote(sky_cluster_home_dir)} -mindepth 1 -maxdepth 1 \\
            ! -name sky_logs \\
            -exec rm -rf -- {{}} +
    else
        rm -rf -- {shlex.quote(sky_cluster_home_dir)}
    fi
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
srun --nodes={num_nodes} mkdir -p {skypilot_runtime_dir}/.sky
# Marker file to indicate we're in a Slurm cluster.
srun --nodes={num_nodes} touch {skypilot_runtime_dir}/.sky/{slurm_utils.SLURM_MARKER_FILE}
# Store proctrack type for task executor to read.
echo '{proctrack_type or "unknown"}' > {sky_cluster_home_dir}/{skylet_constants.SLURM_PROCTRACK_TYPE_FILE}
# Suppress login messages.
touch {sky_cluster_home_dir}/.hushlogin
{container_block}
{f'touch {ready_signal}' if container_image is None else ''}
# Host-side keeper step that starts skylet and restarts it if it dies.
{skylet_keeper_block}
{'sleep infinity' if container_image is None else 'wait -n "${CONTAINER_PIDS[@]}"'}
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

    client = _make_slurm_client(provider_config)

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

    non_terminated_statuses = {
        instance_id: status_and_reason
        for instance_id, status_and_reason in statuses.items()
        if status_and_reason[0] is not None
    }
    if non_terminated_statuses:
        return non_terminated_statuses
    login_node_runner = _make_login_node_runner(provider_config)
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    snapshot_dir = _snapshot_dir(sky_base_dir, cluster_name_on_cloud)
    manifest = _read_snapshot_manifest(login_node_runner, snapshot_dir)
    if manifest is not None:
        return {
            f'snapshot-rank-{rank}': (status_lib.ClusterStatus.STOPPED, None)
            for rank in range(len(manifest['nodes']))
        }

    return statuses


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster (Slurm in this case)."""
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
        slurm_user=provider_config.get('slurm_user'),
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
    """Snapshot and stop a container-backed Slurm virtual instance."""
    assert provider_config is not None, cluster_name_on_cloud
    if worker_only:
        logger.warning(
            'worker_only=True is not supported for Slurm, this is a no-op.')
        return

    client = _make_slurm_client(provider_config)
    login_node_runner = _make_login_node_runner(provider_config)
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    snapshot_dir = _snapshot_dir(sky_base_dir, cluster_name_on_cloud)

    running_jobs = client.query_jobs(cluster_name_on_cloud,
                                     ['running', 'suspended'])
    if not running_jobs:
        manifest = _read_snapshot_manifest(login_node_runner, snapshot_dir)
        if manifest is not None:
            logger.debug(f'Cluster {cluster_name_on_cloud} is already stopped.')
            return
        states = client.get_jobs_state_by_name(cluster_name_on_cloud)
        state_text = ', '.join(state.strip() for state in states) or 'missing'
        raise RuntimeError(
            f'Cannot stop Slurm cluster {cluster_name_on_cloud!r}: expected '
            f'one running allocation, found state {state_text}.')
    if len(running_jobs) != 1:
        raise RuntimeError(f'Multiple running jobs found for cluster '
                           f'{cluster_name_on_cloud}: {running_jobs}')
    job_id = running_jobs[0]
    nodes, _ = client.get_job_nodes(job_id)

    sky_cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                                 cluster_name_on_cloud)
    container_marker = (
        f'{sky_cluster_home_dir}/{slurm_utils.SLURM_CONTAINER_MARKER_FILE}')
    rc, stdout = _run_on_login_node(
        login_node_runner,
        f'cat {shlex.quote(container_marker)}',
        'Failed to read the Slurm container marker.',
        tolerate_returncodes=(1,))
    if rc != 0:
        raise exceptions.NotSupportedError(
            'Stopping Slurm clusters is supported only for containers '
            'launched with Pyxis. The running cluster has no container '
            'snapshot metadata.')
    image_id = stdout.strip()
    # An empty marker identifies a Pyxis cluster without the image metadata
    # required to create a restorable snapshot.
    if not image_id:
        raise exceptions.NotSupportedError(
            'The running Slurm cluster has no container snapshot metadata. '
            'Relaunch the cluster before stopping it.')
    previous_manifest = _read_snapshot_manifest(login_node_runner, snapshot_dir)

    cluster_info = get_cluster_info('', cluster_name_on_cloud, provider_config)
    command_runners = get_command_runners(cluster_info)
    if not command_runners:
        raise RuntimeError('Cannot stop Slurm cluster because its head node '
                           'command runner is unavailable.')
    cancel_jobs_code = job_lib.JobLibCodeGen.cancel_jobs(None, cancel_all=True)
    rc, stdout, stderr = command_runners[0].run_driver(cancel_jobs_code,
                                                       require_outputs=True,
                                                       stream_logs=False)
    subprocess_utils.handle_returncode(
        rc,
        cancel_jobs_code,
        'Failed to cancel jobs before snapshotting the Slurm container.',
        stderr=f'{stdout}\n{stderr}',
        stream_logs=False)
    _drain_slurm_workload_steps(client, job_id)

    skypilot_runtime_dir = _resolve_skypilot_runtime_dir(
        client, provider_config, cluster_name_on_cloud)
    _run_on_login_node(
        login_node_runner,
        _srun_on_node(job_id, nodes[0],
                      _stop_skylet_script(skypilot_runtime_dir)),
        'Failed to stop Skylet before snapshotting the Slurm container.')

    global_enroot_name = _enroot_container_name_global_scope(
        cluster_name_on_cloud)
    job_enroot_name = _enroot_container_name_job_scope(cluster_name_on_cloud,
                                                       job_id)

    def _find_enroot_container_script(node: str) -> str:
        return f"""\
enroot_name=''
while IFS= read -r candidate; do
    if [ "$candidate" = {shlex.quote(global_enroot_name)} ] || [ "$candidate" = {shlex.quote(job_enroot_name)} ]; then
        if [ -n "$enroot_name" ]; then
            echo "Multiple matching Pyxis containers found" >&2
            exit 1
        fi
        enroot_name="$candidate"
    fi
done < <(enroot list)
if [ -z "$enroot_name" ]; then
    echo "Pyxis container not found on node {node}" >&2
    exit 1
fi
"""

    def _check_node_container(node: str) -> None:
        check_script = 'set -e\n' + _find_enroot_container_script(node)
        _run_on_login_node(
            login_node_runner, _srun_on_node(job_id, node, check_script),
            f'Failed to verify the Slurm container on node {node} before '
            'preparing its snapshot.')

    subprocess_utils.run_in_parallel(_check_node_container, nodes)

    generation = uuid.uuid4().hex
    generation_dir = _snapshot_generation_dir(snapshot_dir, generation)
    staging_dir = f'{snapshot_dir}/.staging-{generation}'
    _run_on_login_node(
        login_node_runner,
        (f'mkdir -p {shlex.quote(os.path.dirname(generation_dir))} && '
         f'mkdir {shlex.quote(staging_dir)}'),
        'Failed to prepare Slurm container snapshot directory.')

    # The directory to delete if the snapshot fails partway. Cleared before
    # publishing starts: the manifest rename may complete remotely even if
    # SSH loses its acknowledgement, so from that point the generation must
    # be kept for any manifest that now references it.
    cleanup_dir_on_failure: Optional[str] = staging_dir
    try:
        runtime_job_db_path = f'{skypilot_runtime_dir}/.sky/jobs.db'
        staging_job_db_path = _snapshot_job_db_path(staging_dir)
        backup_job_db_code = (
            'import sqlite3, sys; '
            'source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", '
            'uri=True); '
            'destination = sqlite3.connect(sys.argv[2]); '
            'source.backup(destination); '
            'destination.close(); source.close()')
        backup_job_db_script = (
            f'export {skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY}='
            f'{shlex.quote(skypilot_runtime_dir)} && '
            f'if [ -f {shlex.quote(runtime_job_db_path)} ]; then '
            f'{skylet_constants.SKY_SLURM_PYTHON_CMD} -c '
            f'{shlex.quote(backup_job_db_code)} '
            f'{shlex.quote(runtime_job_db_path)} '
            f'{shlex.quote(staging_job_db_path)}; fi')
        _run_on_login_node(
            login_node_runner,
            _srun_on_node(job_id, nodes[0], backup_job_db_script),
            'Failed to snapshot the Slurm cluster job database.')
        rc, _ = _run_on_login_node(
            login_node_runner,
            f'test -f {shlex.quote(staging_job_db_path)}',
            'Failed to inspect the Slurm cluster job database snapshot.',
            tolerate_returncodes=(1,))
        has_job_db = rc == 0

        def _export_node(rank_and_node: Tuple[int, str]) -> None:
            rank, node = rank_and_node
            staging_snapshot_path = _snapshot_rank_path(staging_dir, rank)
            export_script = f"""\
set -e
{_find_enroot_container_script(node)}\
sync
enroot export -f -o {shlex.quote(staging_snapshot_path)} "$enroot_name"
"""
            _run_on_login_node(
                login_node_runner, _srun_on_node(job_id, node, export_script),
                f'Failed to snapshot Slurm container rank {rank} on node '
                f'{node}.')

        subprocess_utils.run_in_parallel(_export_node, list(enumerate(nodes)))
        manifest = {
            'version': SNAPSHOT_MANIFEST_VERSION,
            'generation': generation,
            'image_id': image_id,
            'created_at': time.time(),
            'has_job_db': has_job_db,
            'nodes': nodes,
        }
        _run_on_login_node(
            login_node_runner,
            (f'test ! -e {shlex.quote(generation_dir)} && '
             f'mv -- {shlex.quote(staging_dir)} {shlex.quote(generation_dir)}'),
            'Failed to commit the Slurm container snapshot generation.')
        cleanup_dir_on_failure = generation_dir
        _validate_snapshot_files(login_node_runner, snapshot_dir, manifest)
        # Keep the previous generation reachable until its replacement is
        # complete and the manifest can switch to it atomically.
        cleanup_dir_on_failure = None
        _write_snapshot_manifest(login_node_runner, snapshot_dir, manifest)
    except Exception:
        if cleanup_dir_on_failure is not None:
            _remove_snapshot_path_best_effort(
                login_node_runner, cleanup_dir_on_failure,
                'incomplete Slurm snapshot generation')
        raise

    if previous_manifest is not None:
        _remove_snapshot_path_best_effort(
            login_node_runner,
            _snapshot_generation_dir(snapshot_dir,
                                     previous_manifest['generation']),
            'previous Slurm snapshot generation')
    _cancel_slurm_job(client,
                      cluster_name_on_cloud,
                      inside_slurm_cluster=False,
                      pre_batch_cancel=lambda: _cleanup_slurm_allocation(
                          client,
                          login_node_runner,
                          cluster_name_on_cloud,
                          provider_config,
                          job_id,
                          nodes,
                          preserve_logs=True))


def _drain_slurm_workload_steps(client: 'slurm.SlurmClient',
                                job_id: str) -> None:
    """Stop every non-infrastructure step before snapshotting an allocation."""
    deadline = time.monotonic() + _WORKLOAD_STEP_DRAIN_TIMEOUT_SECONDS
    term_sent_at: Dict[str, float] = {}
    kill_sent: Set[str] = set()
    infrastructure_step_ids = {f'{job_id}.batch', f'{job_id}.extern'}

    while True:
        active_steps = client.list_job_steps(job_id)
        workload_steps = [
            step for step in active_steps
            if step.step_id not in infrastructure_step_ids and
            step.name not in _WORKLOAD_KEEPER_STEP_NAMES
        ]
        if not workload_steps:
            return

        now = time.monotonic()
        for step in workload_steps:
            if step.step_id not in term_sent_at:
                logger.debug(f'Signalling Slurm workload step {step.step_id} '
                             f'({step.name}) with TERM before snapshotting.')
                client.signal_job_step(job_id, step.step_id, 'TERM')
                term_sent_at[step.step_id] = now
            elif (step.step_id not in kill_sent and
                  now - term_sent_at[step.step_id] >=
                  _WORKLOAD_STEP_TERM_GRACE_PERIOD_SECONDS):
                logger.warning(f'Slurm workload step {step.step_id} '
                               f'({step.name}) did not exit after TERM; '
                               'escalating to KILL.')
                client.signal_job_step(job_id, step.step_id, 'KILL')
                kill_sent.add(step.step_id)

        if now >= deadline:
            remaining = ', '.join(
                f'{step.step_id} ({step.name})' for step in workload_steps)
            raise RuntimeError(
                'Cannot snapshot the Slurm cluster because workload steps '
                f'are still running after '
                f'{_WORKLOAD_STEP_DRAIN_TIMEOUT_SECONDS}s: {remaining}')
        time.sleep(
            min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def _wait_for_job_states(client: 'slurm.SlurmClient', job_name: str,
                         states: Set[str], timeout: float) -> bool:
    """Wait until every job with this name is in `states` or gone.

    Returns False if the timeout expires first. Transient query failures
    are tolerated until the deadline: this is only called after the
    termination signals were already delivered, so one failed poll must
    not fail the whole teardown.
    """
    deadline = time.time() + timeout
    while True:
        try:
            jobs_state: Optional[List[str]] = client.get_jobs_state_by_name(
                job_name)
        except exceptions.CommandError as e:
            logger.debug(f'Failed to query the state of job {job_name}, '
                         f'retrying: {e}')
            jobs_state = None
        if jobs_state is not None and all(
                state.strip() in states for state in jobs_state):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)


def _cleanup_slurm_allocation(
    client: 'slurm.SlurmClient',
    login_node_runner: 'command_runner.SlurmLoginNodeCommandRunner',
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    job_id: str,
    nodes: List[str],
    preserve_logs: bool = False,
) -> None:
    """Remove per-node state while the Slurm allocation is still active."""
    skypilot_runtime_dir = _resolve_skypilot_runtime_dir(
        client, provider_config, cluster_name_on_cloud)
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    sky_cluster_home_dir = _sky_cluster_home_dir(sky_base_dir,
                                                 cluster_name_on_cloud)
    global_enroot_name = _enroot_container_name_global_scope(
        cluster_name_on_cloud)
    job_enroot_name = _enroot_container_name_job_scope(cluster_name_on_cloud,
                                                       job_id)
    cleanup_node_script = f"""\
set -e
{_stop_skylet_script(skypilot_runtime_dir)}
if command -v enroot > /dev/null; then
    container_exists() {{
        while IFS= read -r existing; do
            if [ "$existing" = "$1" ]; then
                return 0
            fi
        done < <(enroot list)
        return 1
    }}
    for enroot_name in {shlex.quote(global_enroot_name)} {shlex.quote(job_enroot_name)}; do
        removed=false
        for ((attempt = 1; attempt <= 30; attempt++)); do
            if ! container_exists "$enroot_name"; then
                removed=true
                break
            fi
            if enroot remove -f "$enroot_name" && ! container_exists "$enroot_name"; then
                removed=true
                break
            fi
            sleep 1
        done
        if [ "$removed" != true ]; then
            echo "Failed to remove Enroot container $enroot_name" >&2
            exit 1
        fi
    done
fi
rm -rf -- {shlex.quote(skypilot_runtime_dir)}
"""
    cleanup_node_cmd = (
        f'srun --unbuffered --overlap --jobid={shlex.quote(job_id)} '
        f'--nodes={len(nodes)} --ntasks-per-node=1 '
        f'bash -c {shlex.quote(cleanup_node_script)}')
    _run_on_login_node(
        login_node_runner, cleanup_node_cmd,
        'Failed to clean up the Slurm allocation before cancellation.')

    if preserve_logs:
        # A stop keeps the logs referenced by the jobs database that start
        # will restore.
        remove_shared_state_cmd = (f'find {shlex.quote(sky_cluster_home_dir)} '
                                   '-mindepth 1 -maxdepth 1 '
                                   '! -name sky_logs '
                                   '-exec rm -rf -- {} +')
    else:
        remove_shared_state_cmd = (
            f'rm -rf -- {shlex.quote(sky_cluster_home_dir)}')
    _run_on_login_node(
        login_node_runner, remove_shared_state_cmd,
        'Failed to clean up shared Slurm cluster state before cancellation.')


def _cancel_slurm_job(
    client: 'slurm.SlurmClient',
    cluster_name_on_cloud: str,
    inside_slurm_cluster: bool,
    pre_batch_cancel: Optional[Callable[[], None]] = None,
) -> None:
    """Cancel a Slurm virtual-instance allocation and verify it exits."""
    jobs_state = client.get_jobs_state_by_name(cluster_name_on_cloud)
    if not jobs_state:
        logger.debug(f'Job for cluster {cluster_name_on_cloud} not found, '
                     'it may have been terminated.')
        return
    assert len(jobs_state) == 1, (
        f'Multiple jobs found for cluster {cluster_name_on_cloud}: {jobs_state}'
    )

    job_state = jobs_state[0].strip()
    if job_state in _TERMINAL_JOB_STATES:
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
    elif job_state not in _ESCALATION_JOB_STATES or inside_slurm_cluster:
        # Transient states (e.g. STAGING_OUT, SIGNALING): the job is not
        # holding a steady allocation; a graceful signal is sufficient.
        # Autodown (running inside the cluster): the Skylet performing this
        # teardown lives inside a job step's cgroup, so the step-level TERM
        # below would kill the terminating process itself; and autodown only
        # fires on idle clusters, which have no task steps that could block
        # the batch script's cleanup.
        client.cancel_jobs_by_name(cluster_name_on_cloud,
                                   signal='TERM',
                                   full=True)
    else:
        # For RUNNING/SUSPENDED jobs, TERM all job steps first. This stops
        # processes that hold the container rootfs busy while preserving the
        # allocation for the node cleanup. Signal the batch script only after
        # that cleanup finishes.
        # Both scancel invocations are needed: without --full, scancel signals
        # all job steps but not the batch script; with --full, it signals the
        # batch script and its child processes.
        client.cancel_jobs_by_name(cluster_name_on_cloud, signal='TERM')
        if pre_batch_cancel is not None:
            try:
                pre_batch_cancel()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    'Failed to clean up the Slurm allocation before '
                    'cancellation; proceeding with cancellation. Details: '
                    f'{common_utils.format_exception(e, use_bracket=True)}')
                logger.debug('Full exception details:', exc_info=True)
        client.cancel_jobs_by_name(cluster_name_on_cloud,
                                   signal='TERM',
                                   full=True)
        # Graceful termination is not guaranteed to bring the job down: if
        # any step survives the TERM, it keeps the allocation busy, which
        # blocks the batch script's cleanup srun from starting a step and
        # leaves the job RUNNING indefinitely. Verify the job exits within
        # a grace period, and escalate to a plain scancel (Slurm's own
        # TERM -> KillWait -> KILL sequence) if it does not.
        if _wait_for_job_states(client, cluster_name_on_cloud,
                                _EXITING_JOB_STATES,
                                _TERMINATION_GRACE_PERIOD_SECONDS):
            return
        logger.warning(
            f'Job for cluster {cluster_name_on_cloud} did not exit within '
            f'{_TERMINATION_GRACE_PERIOD_SECONDS}s of the termination '
            'signal. Escalating to a full cancellation.')
        client.cancel_jobs_by_name(cluster_name_on_cloud)
        # The plain scancel moves the job to COMPLETING while Slurm runs
        # its TERM -> KillWait -> KILL sequence, so here require the job to
        # actually leave the queue: a job wedged in COMPLETING (typically
        # non-killable processes) still holds the allocation and must be
        # reported, not treated as success.
        if not _wait_for_job_states(client, cluster_name_on_cloud,
                                    _TERMINAL_JOB_STATES,
                                    _JOB_TERMINATION_TIMEOUT_SECONDS):
            raise RuntimeError(
                f'Slurm job for cluster {cluster_name_on_cloud} is still '
                f'running {_JOB_TERMINATION_TIMEOUT_SECONDS}s after scancel. '
                'The allocation may be leaked; check squeue and cancel the '
                'job manually if needed.')


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
    inside_slurm_cluster = slurm_utils.is_inside_slurm_cluster()
    if inside_slurm_cluster:
        logger.debug('Running inside a Slurm cluster, using local execution')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
    else:
        client = _make_slurm_client(provider_config)
    pre_batch_cancel = None
    if not inside_slurm_cluster:
        running_jobs = client.query_jobs(cluster_name_on_cloud,
                                         ['running', 'suspended'])
        if len(running_jobs) > 1:
            raise RuntimeError(f'Multiple running jobs found for cluster '
                               f'{cluster_name_on_cloud}: {running_jobs}')
        if len(running_jobs) == 1:
            job_id = running_jobs[0]
            nodes, _ = client.get_job_nodes(job_id)
            login_node_runner = _make_login_node_runner(provider_config)
            pre_batch_cancel = lambda: _cleanup_slurm_allocation(
                client, login_node_runner, cluster_name_on_cloud,
                provider_config, job_id, nodes)
    _cancel_slurm_job(client,
                      cluster_name_on_cloud,
                      inside_slurm_cluster,
                      pre_batch_cancel=pre_batch_cancel)
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    snapshot_dir = _snapshot_dir(sky_base_dir, cluster_name_on_cloud)
    if inside_slurm_cluster:
        if os.path.exists(snapshot_dir):
            shutil.rmtree(snapshot_dir)
    else:
        _run_on_login_node(_make_login_node_runner(provider_config),
                           f'rm -rf -- {shlex.quote(snapshot_dir)}',
                           'Failed to remove Slurm container snapshot.')


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

    slurm_user = provider_config.get('slurm_user')
    client = slurm.SlurmClient(
        login_node_ssh_hostname,
        login_node_ssh_port,
        login_node_ssh_user,
        login_node_ssh_private_key,
        ssh_proxy_command=login_node_ssh_proxy_command,
        ssh_proxy_jump=login_node_ssh_proxy_jump,
        identities_only=login_node_identities_only,
        slurm_user=slurm_user,
    )
    skypilot_runtime_dir = _resolve_skypilot_runtime_dir(
        client, provider_config, cluster_name_on_cloud)
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
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
            skypilot_runtime_dir=skypilot_runtime_dir,
            job_id=instance_info.tags['job_id'],
            slurm_node=instance_info.tags['node'],
            ssh_proxy_jump=login_node_ssh_proxy_jump,
            ssh_proxy_command=login_node_ssh_proxy_command,
            ssh_control_name=ssh_control_name,
            container_args=container_args,
            slurm_user=slurm_user,
            enable_interactive_auth=True,
            # Allow ssh-agent and default key fallback for Slurm.
            disable_identities_only=True) for instance_info in instances
    ]

    return runners
