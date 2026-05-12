"""ManagedJobRuntime: runtime adapter for managed jobs.

Extension point for runtime-specific behavior in the managed-jobs
lifecycle. Each method takes a cluster handle and returns ``None``
to defer to the default behavior at the call site, or a non-None
result to handle the operation.

Register a runtime with ``register(MyRuntime())``. Callers use the
module-level dispatch (``runtime.get_job_status(...)``,
``runtime.tail_logs(...)``, etc.) — the registered instance is
private to this module.
"""
import typing
from typing import Dict, List, Optional, Protocol, Tuple

from sky import sky_logging

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend
    from sky.skylet import job_lib

logger = sky_logging.init_logger(__name__)


class ManagedJobRuntime(Protocol):
    """Runtime adapter Protocol.

    Every method returns ``None`` to defer to the default behavior at
    the call site, or a non-None result to handle the operation. All
    methods are synchronous; async call sites wrap with
    ``asyncio.to_thread``.
    """

    # pylint: disable=unnecessary-ellipsis

    def get_job_status(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
        returncode: Optional[int] = None,
    ) -> Optional[Tuple[Optional['job_lib.JobStatus'], Optional[str]]]:
        """Query job status from the underlying runtime."""
        ...

    def get_job_submitted_at(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
    ) -> Optional[float]:
        """Return the job's submitted-at timestamp, or None to defer to
        the call site's default (``managed_job_utils.get_job_timestamp``
        over skylet)."""
        ...

    def get_job_ended_at(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
        cluster_name: str,
    ) -> Optional[float]:
        """Return the job's ended-at timestamp, or None to defer to the
        call site's default (``managed_job_utils.get_job_timestamp``
        over skylet)."""
        ...

    def get_exit_codes(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[List[int]]:
        """Retrieve per-node exit codes (sorted by node index)."""
        ...

    def download_logs(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        job_id: int,
        task_id: Optional[int],
    ) -> Optional[str]:
        """Download logs to a local file. Returns the path or None."""
        ...

    def cleanup(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[bool]:
        """Clean up runtime-specific resources. Returns True / None."""
        ...

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
        """Tail logs to stdout. Returns an exit code, or None to defer
        to the call site's default (``backend.tail_logs``)."""
        ...

    def job_group_envs(
        self,
        tasks: List['task_lib.Task'],
        job_id: int,
    ) -> Optional[Dict[str, str]]:
        """Return extra env vars to inject into all JobGroup tasks.

        Called once before launching any task in a JobGroup. Returns a
        dict of env vars merged into every task, or ``None`` to skip.
        """
        ...

    def k8s_dns_addresses_for_task(
        self,
        task: 'task_lib.Task',
        job_id: int,
    ) -> Optional[List[str]]:
        """Return K8s DNS names for this task's nodes, or ``None``."""
        ...

    def k8s_dns_addresses_for_handle(
        self,
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    ) -> Optional[List[str]]:
        """Return K8s DNS names for this handle's nodes, or ``None``."""
        ...


_current: Optional[ManagedJobRuntime] = None


def register(runtime: ManagedJobRuntime) -> None:
    """Install ``runtime`` as the active managed-job runtime.

    Last registration wins.
    """
    global _current  # pylint: disable=global-statement
    _current = runtime
    logger.debug('Registered ManagedJobRuntime: %s', type(runtime).__name__)


def is_registered() -> bool:
    """Whether a runtime is currently registered.

    Cheap synchronous check — async callers can guard
    ``asyncio.to_thread(runtime.X, ...)`` on this so they don't pay
    thread-pool overhead when no plugin is installed.
    """
    return _current is not None


# Module-level dispatch. Each function returns ``None`` when no
# runtime is registered, so callers fall through to their default.


def get_job_status(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
    returncode: Optional[int] = None,
) -> Optional[Tuple[Optional['job_lib.JobStatus'], Optional[str]]]:
    if _current is None:
        return None
    return _current.get_job_status(handle, cluster_name, returncode=returncode)


def get_job_submitted_at(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
) -> Optional[float]:
    if _current is None:
        return None
    return _current.get_job_submitted_at(handle, cluster_name)


def get_job_ended_at(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
) -> Optional[float]:
    if _current is None:
        return None
    return _current.get_job_ended_at(handle, cluster_name)


def get_exit_codes(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
) -> Optional[List[int]]:
    if _current is None:
        return None
    return _current.get_exit_codes(handle)


def download_logs(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    job_id: int,
    task_id: Optional[int],
) -> Optional[str]:
    if _current is None:
        return None
    return _current.download_logs(handle, job_id, task_id)


def cleanup(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',) -> Optional[bool]:
    if _current is None:
        return None
    return _current.cleanup(handle)


def tail_logs(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    *,
    backend: 'backends.CloudVmRayBackend',
    job_id: int,
    task_id: Optional[int],
    job_id_on_cluster: Optional[int],
    worker: Optional[int] = None,
    follow: bool,
    tail: Optional[int],
    tail_offset: Optional[int] = None,
) -> Optional[int]:
    if _current is None:
        return None
    return _current.tail_logs(
        handle,
        backend=backend,
        job_id=job_id,
        task_id=task_id,
        job_id_on_cluster=job_id_on_cluster,
        worker=worker,
        follow=follow,
        tail=tail,
        tail_offset=tail_offset,
    )


def job_group_envs(
    tasks: List['task_lib.Task'],
    job_id: int,
) -> Optional[Dict[str, str]]:
    if _current is None:
        return None
    return _current.job_group_envs(tasks, job_id)


def k8s_dns_addresses_for_task(
    task: 'task_lib.Task',
    job_id: int,
) -> Optional[List[str]]:
    if _current is None:
        return None
    return _current.k8s_dns_addresses_for_task(task, job_id)


def k8s_dns_addresses_for_handle(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
) -> Optional[List[str]]:
    if _current is None:
        return None
    return _current.k8s_dns_addresses_for_handle(handle)
