"""ManagedJobRuntime adapter for Slurm v1 managed jobs.

Lives in OSS alongside the Slurm provisioner. Owns the runtime-side
adapter for the Slurm-native managed-jobs path: status reads from
``sacct``/``squeue``, log tailing reads ``--output`` files, and so on.

Phase 1 ships ``get_job_status`` only — every other Protocol method
returns ``None`` to defer to the call site's legacy default. Follow-up
phases will replace those stubs with Slurm-native implementations
(timestamps via ``sacct``, exit codes via ``sacct ExitCode``, log
download/tail via SSH to the login node).
"""
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.adaptors import slurm
from sky.provision.slurm import instance as slurm_instance
from sky.skylet import job_lib

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


class _Target:
    """Resolved per-handle context for a Slurm v1 managed job."""

    def __init__(
        self,
        job_id: str,
        ssh_config: Dict[str, Any],
        partition: str,
        log_path: Optional[str],
    ) -> None:
        self.job_id = job_id
        self.ssh_config = ssh_config
        self.partition = partition
        self.log_path = log_path


def _resolve_slurm_target(handle) -> Optional[_Target]:
    """Extract the Slurm v1 context from a cluster handle.

    Returns ``None`` for handles that don't belong to the Slurm v1
    runtime — never falls back to defaults. Cheap fast-path matches
    the K8s v1 posture: ``getattr(..., 'has_ray', True)`` treats
    missing metadata as legacy.
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

    return _Target(job_id=str(job_id),
                   ssh_config=ssh_config,
                   partition=partition,
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
        # TODO(slurm-v1 phase 2): query ``sacct -j <jobid> --format=Start``
        # and parse the Unix timestamp. Mirrors skylet ``add_job``
        # semantics ("registered to begin"), not "got queued".
        del handle, cluster_name
        return None

    def get_job_ended_at(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
    ) -> Optional[float]:
        # TODO(slurm-v1 phase 2): query ``sacct -j <jobid> --format=End``.
        del handle, cluster_name
        return None

    def get_exit_codes(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[List[int]]:
        # TODO(slurm-v1 phase 2): query ``sacct -j <jobid>
        # --format=ExitCode --parsable2`` — one row per srun step.
        del handle
        return None

    def download_logs(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        job_id: int,
        task_id: Optional[int],
    ) -> Optional[str]:
        # TODO(slurm-v1 phase 2): SSH to the login node and scp the
        # sbatch ``--output`` file into ``~/.sky/managed_jobs/...``.
        del handle, job_id, task_id
        return None

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
        # TODO(slurm-v1 phase 2): live-tail the sbatch ``--output`` file
        # over SSH; see ``log_streaming.SlurmLogStreamer`` (planned).
        del (handle, backend, job_id, task_id, job_id_on_cluster, worker,
             follow, tail, tail_offset)
        return None

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
