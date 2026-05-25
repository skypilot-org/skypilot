"""ManagedJobRuntime adapter for Slurm v1 managed jobs.

Lives in OSS alongside the Slurm provisioner. Owns the runtime-side
adapter for the Slurm-native managed-jobs path: status reads from
``sacct``/``squeue``, log tailing reads ``--output`` files, and so on.

Log-tail streaming machinery lives in
``sky/provision/slurm/log_streaming.py`` per PLAN.md "File layout";
``download_logs`` stays here (it's a single ``cat`` + write-to-disk).
"""
import datetime
import os
import shlex
import typing
from typing import Any, Dict, List, Optional, Tuple

import colorama

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import slurm
from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants as skylet_constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.utils import command_runner

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# Slurm job state mapping. Sourced from:
# https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
_PENDING_STATES = frozenset({
    'PENDING',
    'CONFIGURING',
    'RESV_DEL_HOLD',
    'REQUEUED',
    'REQUEUE_HOLD',
    'REQUEUE_FED',
    'RESIZING',
})
_RUNNING_STATES = frozenset({
    'RUNNING',
    'COMPLETING',
    'SIGNALING',
    'STAGE_OUT',
    'SUSPENDED',
})
_SUCCEEDED_STATES = frozenset({'COMPLETED'})
_FAILED_STATES = frozenset({
    'FAILED',
    'NODE_FAIL',
    'BOOT_FAIL',
    'TIMEOUT',
    'OUT_OF_MEMORY',
    'DEADLINE',
    'SPECIAL_EXIT',
    'PREEMPTED',
})
_CANCELLED_STATES = frozenset({'CANCELLED', 'REVOKED'})

_TERMINAL_STATES = (_SUCCEEDED_STATES | _FAILED_STATES | _CANCELLED_STATES)


class _Target:
    """Resolved per-handle context for a Slurm v1 managed job."""

    def __init__(
        self,
        job_id: str,
        ssh_config: Dict[str, Any],
        partition: str,
        region: Optional[str],
        log_path: Optional[str],
    ) -> None:
        self.job_id = job_id
        self.ssh_config = ssh_config
        self.partition = partition
        self.region = region
        self.log_path = log_path


def _resolve_slurm_target(handle) -> Optional[_Target]:
    """Extract the Slurm v1 context from a cluster handle.

    Returns ``None`` for handles that don't belong to the Slurm v1
    runtime — never falls back to defaults. Cheap fast-path:
    ``getattr(..., 'has_ray', True)`` treats missing metadata as
    legacy (default-to-legacy posture).
    """
    if handle is None:
        return None
    metadata = getattr(handle, 'provision_runtime_metadata', None)
    has_ray = getattr(metadata, 'has_ray', True)
    if has_ray:
        return None

    try:
        config = global_user_state.get_cluster_yaml_dict(handle.cluster_yaml)
    except (ValueError, TypeError):
        return None

    provider_config = config.get('provider', {}) if config else {}
    if not slurm_instance.is_managed_job_v1_provider_config(provider_config):
        return None

    cached = getattr(handle, 'cached_cluster_info', None)
    if cached is None:
        return None

    head_id = getattr(cached, 'head_instance_id', None)
    instances = getattr(cached, 'instances', None) or {}
    if head_id is None or head_id not in instances:
        return None

    head_infos = instances[head_id]
    if not head_infos:
        return None
    head_info = head_infos[0]
    tags = getattr(head_info, 'tags', None) or {}
    job_id = tags.get('job_id')
    if not job_id:
        return None

    ssh_config = provider_config.get('ssh') or {}
    partition = provider_config.get('partition') or ''
    log_path = provider_config.get('log_path')

    region: Optional[str] = None
    launched_resources = getattr(handle, 'launched_resources', None)
    if launched_resources is not None:
        region = getattr(launched_resources, 'region', None)

    return _Target(job_id=str(job_id),
                   ssh_config=ssh_config,
                   partition=partition,
                   region=region,
                   log_path=log_path)


def _slurm_client_from_target(target: _Target) -> 'slurm.SlurmClient':
    ssh = target.ssh_config
    return slurm.SlurmClient(
        ssh.get('hostname'),
        int(ssh.get('port', 22)),
        ssh.get('user'),
        ssh.get('private_key'),
        ssh_proxy_command=ssh.get('proxycommand'),
        ssh_proxy_jump=ssh.get('proxyjump'),
        identities_only=ssh.get('identities_only', False),
    )


def _login_node_runner(
        ssh_config: Dict[str, Any]) -> 'command_runner.SSHCommandRunner':
    """Build an ``SSHCommandRunner`` for the Slurm login node.

    Mirrors the constructor block in ``instance.py::_create_virtual_instance``
    (around line 523) and ``_create_managed_job_v1`` (around line 1677).
    Copied rather than imported to avoid an import cycle with the
    provisioner module from runtime code paths.
    """
    identities_only = bool(ssh_config.get('identities_only', False))
    return command_runner.SSHCommandRunner(
        (ssh_config['hostname'], int(ssh_config.get('port', 22))),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
    )


def _sacct_get_state(client: 'slurm.SlurmClient', job_id: str) -> Optional[str]:
    """Look up a job's terminal state via ``sacct`` for jobs aged out
    of the ``squeue`` window (default ``MinJobAge=300`` seconds).

    Returns the parent step's state (the first row, which is the job
    itself rather than ``<jobid>.batch`` / ``<jobid>.0``), or ``None``
    if the call fails or returns nothing.
    """
    cmd = (f'sacct -j {job_id} --format=State --parsable2 --noheader '
           '--allocations')
    # pylint: disable=protected-access
    rc, stdout, _ = client._run_slurm_cmd(cmd)
    if rc != 0:
        return None
    for line in stdout.splitlines():
        state = line.strip()
        # Cancelled jobs show as ``CANCELLED by <uid>``; normalize.
        if state.startswith('CANCELLED'):
            return 'CANCELLED'
        if state:
            return state.upper()
    return None


def _sacct_get_single_field(client: 'slurm.SlurmClient', job_id: str,
                            field: str) -> Optional[str]:
    """Fetch a single ``sacct`` field for the allocation (parent row).

    Returns the trimmed field value, or ``None`` on query failure /
    empty output. Empty string and Slurm's ``Unknown`` sentinel both
    map to ``None`` so callers can treat "not available yet" uniformly.
    """
    cmd = (f'sacct -j {job_id} --format={field} --parsable2 --noheader '
           '--allocations')
    # pylint: disable=protected-access
    rc, stdout, _ = client._run_slurm_cmd(cmd)
    if rc != 0:
        return None
    for line in stdout.splitlines():
        value = line.strip()
        if not value or value == 'Unknown':
            return None
        return value
    return None


def _parse_slurm_timestamp(value: Optional[str]) -> Optional[float]:
    """Parse a Slurm sacct ISO timestamp (local time) to a Unix float.

    Slurm emits times in the controller's local timezone formatted as
    ``YYYY-MM-DDTHH:MM:SS`` (no timezone suffix). ``fromisoformat``
    treats the result as a naive ``datetime``; calling ``.timestamp()``
    then interprets it in the *local* timezone of the API server. For
    Phase 2 that's the closest we can get without an explicit TZ flag
    or sacct format override (TODO(slurm-v1): set ``SLURM_TIME_FORMAT``
    or use ``--format=Start%Y-%m-%dT%H:%M:%S%z`` once we standardize
    timezone handling).
    """
    if value is None:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        logger.debug(f'Failed to parse Slurm timestamp {value!r}.')
        return None
    return dt.timestamp()


def _resolve_log_path(
        target: _Target,
        client: Optional['slurm.SlurmClient'] = None) -> Optional[str]:
    """Compute the sbatch ``--output`` path for the v1 job.

    ``_create_managed_job_v1`` writes it to
    ``{workdir or $HOME}/.sky_provision/slurm-{job_id}.out`` — the
    same shape as ``instance._sbatch_log_path``. We recompute it at
    runtime because the path is not persisted into the cluster YAML
    (template intentionally omits skypilot-level workdir; only the
    task-level workdir payload is stored).
    """
    if target.log_path is not None:
        return target.log_path
    if client is None:
        client = _slurm_client_from_target(target)

    workdir_cfg: Optional[str] = None
    if target.region is not None:
        try:
            workdir_cfg = skypilot_config.get_effective_region_config(
                cloud='slurm',
                region=target.region,
                keys=('workdir',),
                default_value=None)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to read slurm.workdir from config: {e}')
            workdir_cfg = None

    if workdir_cfg is not None:
        try:
            remote_env = client.get_env()
            workdir_cfg = slurm_utils.expand_path_vars(workdir_cfg, remote_env)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to expand workdir vars: {e}')

    if workdir_cfg is not None:
        sky_base_dir = workdir_cfg
    else:
        try:
            sky_base_dir = client.get_remote_home_dir()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to resolve remote $HOME for v1 log '
                           f'path: {e}')
            return None

    if not sky_base_dir or not os.path.isabs(sky_base_dir):
        logger.warning(f'Refusing to use non-absolute v1 log base dir '
                       f'{sky_base_dir!r}.')
        return None

    return (f'{sky_base_dir}/'
            f'{slurm_instance.PROVISION_SCRIPTS_DIRECTORY_NAME}/'
            f'slurm-{target.job_id}.out')


def _build_managed_job_streaming_header(num_nodes: int,
                                        min_nodes: Optional[int] = None) -> str:
    """Build the multi-line streaming-start header for a v1 managed job.

    The first line embeds ``LOG_FILE_START_STREAMING_AT`` so saved-log
    replay finds its expected marker.
    """
    dim = colorama.Style.DIM
    reset = colorama.Style.RESET_ALL
    plural = 's' if num_nodes > 1 else ''
    lines = [
        f'{dim}├── {log_lib.LOG_FILE_START_STREAMING_AT}{num_nodes} '
        f'node{plural}.{reset}\n'
    ]
    if min_nodes is not None:
        lines.append(f'{dim}├── Gang scheduling: waiting for '
                     f'{min_nodes} nodes before starting setup/run.'
                     f'{reset}\n')
    lines.append(f'{dim}└── {reset}'
                 f'Job started. Streaming logs... '
                 f'{dim}'
                 f'(Ctrl-C to exit log streaming; job will not be killed)'
                 f'{reset}\n')
    return ''.join(lines)


def _slurm_state_to_job_status(
        state: Optional[str]) -> Optional[job_lib.JobStatus]:
    """Map a Slurm job state to a SkyPilot ``JobStatus``.

    Unknown / future states fall through to ``None`` so the caller
    defers to its default — never silently coerce.
    """
    if state is None:
        return None
    if state in _PENDING_STATES:
        return job_lib.JobStatus.PENDING
    if state in _RUNNING_STATES:
        return job_lib.JobStatus.RUNNING
    if state in _SUCCEEDED_STATES:
        return job_lib.JobStatus.SUCCEEDED
    if state in _FAILED_STATES:
        return job_lib.JobStatus.FAILED
    if state in _CANCELLED_STATES:
        return job_lib.JobStatus.CANCELLED
    logger.warning(f'Unknown Slurm job state {state!r}; deferring to '
                   'caller default.')
    return None


def _returncode_to_job_status(
        returncode: int) -> Tuple[Optional[job_lib.JobStatus], Optional[str]]:
    """Map a caller-supplied returncode to a ``JobStatus``.

    Trusts the caller: a streaming session that observed the previous
    attempt's terminal state is more authoritative than a fresh
    ``sacct`` query, which races against the controller submitting
    attempt N+1 under the same job name.
    """
    if returncode == exceptions.JobExitCode.SUCCEEDED.value:
        return (job_lib.JobStatus.SUCCEEDED, None)
    if returncode == exceptions.JobExitCode.CANCELLED.value:
        return (job_lib.JobStatus.CANCELLED, None)
    # All other terminal codes represent a failed attempt. JobExitCode is
    # a lossy collapse (FAILED, FAILED_SETUP, FAILED_DRIVER, etc. all
    # become FAILED), but the reverse map only needs to flag user-code
    # failure for the controller's restart-and-continue branch.
    return (job_lib.JobStatus.FAILED, None)


def _state_to_job_exit_code(state: Optional[str]) -> int:
    """Map a Slurm state to a ``JobExitCode``."""
    if state is None:
        return exceptions.JobExitCode.FAILED.value
    if state in _SUCCEEDED_STATES:
        return exceptions.JobExitCode.SUCCEEDED.value
    if state in _CANCELLED_STATES:
        return exceptions.JobExitCode.CANCELLED.value
    return exceptions.JobExitCode.FAILED.value


class SlurmManagedJobRuntime:
    """ManagedJobRuntime for the Slurm-native v1 managed-job path."""

    def owns(self, handle) -> bool:
        return _resolve_slurm_target(handle) is not None

    def get_job_status(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,  # pylint: disable=unused-argument
        returncode: Optional[int] = None,
    ) -> Optional[Tuple[Optional[job_lib.JobStatus], Optional[str]]]:
        target = _resolve_slurm_target(handle)
        if target is None:
            return None

        if returncode is not None:
            return _returncode_to_job_status(returncode)

        try:
            client = _slurm_client_from_target(target)
            state = client.get_job_state(target.job_id)
            if state is None:
                # squeue's ``MinJobAge`` window (default 300s) ages jobs
                # out — fall back to sacct for historical terminal state.
                state = _sacct_get_state(client, target.job_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to query Slurm job {target.job_id} '
                           f'state: {e}')
            return (None, f'Slurm query error: {e}')

        status = _slurm_state_to_job_status(state)
        if status is None:
            return None
        return (status, None)

    def get_job_submitted_at(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
    ) -> Optional[float]:
        del cluster_name
        target = _resolve_slurm_target(handle)
        if target is None:
            return None
        try:
            client = _slurm_client_from_target(target)
            # ``Start`` (not ``Submit``): the legacy skylet ``add_job``
            # timestamp tracks "registered to begin", not "got queued".
            # On a busy partition ``Submit`` can be days off ``Start``.
            value = _sacct_get_single_field(client, target.job_id, 'Start')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to query Slurm Start time for job '
                           f'{target.job_id}: {e}')
            return None
        return _parse_slurm_timestamp(value)

    def get_job_ended_at(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
    ) -> Optional[float]:
        del cluster_name
        target = _resolve_slurm_target(handle)
        if target is None:
            return None
        try:
            client = _slurm_client_from_target(target)
            value = _sacct_get_single_field(client, target.job_id, 'End')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to query Slurm End time for job '
                           f'{target.job_id}: {e}')
            return None
        return _parse_slurm_timestamp(value)

    def get_exit_codes(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[List[int]]:
        target = _resolve_slurm_target(handle)
        if target is None:
            return None
        # TODO(slurm-v1): PLAN.md gap #16 calls out that ``sacct
        # --format=ExitCode`` returns one row per srun step
        # (``<jobid>``, ``<jobid>.batch``, ``<jobid>.0``, ...). For now
        # we take the parent allocation row, parse the ``<exit>:<signal>``
        # format, and surface a single exit code. A follow-up should
        # split per-rank codes once we decide which step row maps to
        # which user-visible rank.
        try:
            client = _slurm_client_from_target(target)
            value = _sacct_get_single_field(client, target.job_id, 'ExitCode')
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to query Slurm ExitCode for job '
                         f'{target.job_id}: {e}')
            return None
        if value is None:
            return None
        # ``<exit>:<signal>``. If signal != 0 and exit == 0, the job
        # was killed by signal — surface 128+signal so callers see a
        # non-zero code.
        try:
            exit_str, _, signal_str = value.partition(':')
            exit_code = int(exit_str) if exit_str else 0
            signal_code = int(signal_str) if signal_str else 0
        except ValueError:
            logger.debug(f'Unparseable Slurm ExitCode {value!r}.')
            return None
        if exit_code == 0 and signal_code != 0:
            exit_code = 128 + signal_code
        return [exit_code]

    def download_logs(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        job_id: int,
        task_id: Optional[int],
    ) -> Optional[str]:
        target = _resolve_slurm_target(handle)
        if target is None:
            return None
        client = _slurm_client_from_target(target)
        log_path = _resolve_log_path(target, client)
        if log_path is None:
            return None

        runner = _login_node_runner(target.ssh_config)
        # ``cat`` instead of ``rsync`` so a missing file produces a
        # clean empty stdout + non-zero rc rather than an rsync error;
        # job-not-yet-started maps to an empty saved log.
        cmd = f'cat {shlex.quote(log_path)} 2>/dev/null'
        rc, stdout, stderr = runner.run(cmd,
                                        require_outputs=True,
                                        separate_stderr=True,
                                        stream_logs=False)
        if rc != 0:
            logger.warning(f'Failed to download Slurm v1 logs from '
                           f'{log_path} (rc={rc}): {stderr}')
            return None

        local_dir = os.path.expanduser(
            os.path.join(skylet_constants.SKY_LOGS_DIRECTORY, 'managed_jobs',
                         f'job-id-{job_id}'))
        os.makedirs(local_dir, exist_ok=True)
        log_file = os.path.join(
            local_dir, f'task-{task_id if task_id is not None else 0}.log')

        num_nodes = getattr(handle, 'launched_nodes', 1) or 1
        header = _build_managed_job_streaming_header(num_nodes=num_nodes,
                                                     min_nodes=None)
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(header)
            f.write(stdout)

        logger.info(f'Downloaded Slurm managed job logs to {log_file}')
        return log_file

    def tail_logs(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        *,
        backend: 'backends.CloudVmRayBackend',
        job_id: int,
        task_id: Optional[int],
        job_id_on_cluster: Optional[int],
        worker: Optional[int],
        follow: bool,
        tail: Optional[int],
        tail_offset: Optional[int] = None,
    ) -> Optional[int]:
        del backend, job_id, task_id, job_id_on_cluster, worker
        target = _resolve_slurm_target(handle)
        if target is None:
            return None
        client = _slurm_client_from_target(target)
        log_path = _resolve_log_path(target, client)
        if log_path is None:
            return None

        # Lazy-import the streamer module — its import edge points back
        # at this module's ``_Target`` (under ``TYPE_CHECKING``) and we
        # want to keep the runtime module's import surface unchanged.
        # pylint: disable=import-outside-toplevel
        from sky.provision.slurm import log_streaming

        streamer = log_streaming.make_streamer_from_target(
            target,
            log_path,
            client,
            terminal_states=_TERMINAL_STATES,
            sacct_get_state=_sacct_get_state,
            state_to_job_exit_code=_state_to_job_exit_code,
            follow=follow,
            tail=tail,
            tail_offset=tail_offset,
        )
        return streamer.run()

    def job_group_envs(
        self,
        tasks: List['task_lib.Task'],
        job_id: int,
    ) -> Optional[Dict[str, str]]:
        # No JobGroup on Slurm.
        del tasks, job_id
        return None

    def k8s_dns_addresses_for_task(
        self,
        task: 'task_lib.Task',
        job_id: int,
    ) -> Optional[List[str]]:
        del task, job_id
        return None

    def k8s_dns_addresses_for_handle(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[List[str]]:
        del handle
        return None
