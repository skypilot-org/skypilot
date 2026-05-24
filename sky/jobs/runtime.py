"""ManagedJobRuntime: runtime adapter for managed jobs.

Extension point for runtime-specific behavior in the managed-jobs
lifecycle. Each method takes a cluster handle and returns ``None``
to defer to the default behavior at the call site, or a non-None
result to handle the operation.

Register a runtime with ``register(MyRuntime())``. Multiple runtimes
can be registered and form a dispatch chain: the chain iterates in
registration order and returns the first non-None result. The previous
"last-wins" single-runtime semantics is gone — runtimes coexist now.

Callers use the module-level dispatch (``runtime.get_job_status(...)``,
``runtime.tail_logs(...)``, etc.) — the chain is private to this
module.
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

    def owns(
        self,
        handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    ) -> bool:
        """Cheap pure-function claim check used by chain dispatch.

        Must not perform I/O. Implementations should answer from
        ``handle.provision_runtime_metadata`` + the provider block in
        ``handle.cluster_yaml`` only. Returning False short-circuits
        every other hook for this runtime — so it MUST agree with the
        per-hook resolve checks (a runtime that returns ``owns=True``
        then returns ``None`` from every hook is a bug).
        """
        ...

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


_runtimes: List[ManagedJobRuntime] = []


def register(runtime: ManagedJobRuntime) -> None:
    """Append ``runtime`` to the dispatch chain.

    Last-wins semantics from the pre-chain implementation are gone.
    Two runtimes claiming the same handle is a configuration bug;
    dispatch resolves in registration order and the first claimer
    wins, but a warning is logged when more than one ``owns(handle)``
    returns True for the same handle so the conflict surfaces.
    """
    _runtimes.append(runtime)
    logger.debug('Registered ManagedJobRuntime: %s', type(runtime).__name__)


def replace(old_cls: type, new_runtime: ManagedJobRuntime) -> None:
    """Swap an already-registered runtime in place.

    Use case: an external integration ships an updated runtime and
    wants to upgrade without changing chain order. Without this, the
    only alternative would be append-then-shadow (fragile — relies on
    first-non-None ordering).
    """
    for i, r in enumerate(_runtimes):
        if isinstance(r, old_cls):
            _runtimes[i] = new_runtime
            logger.debug('Replaced ManagedJobRuntime: %s -> %s',
                         old_cls.__name__,
                         type(new_runtime).__name__)
            return
    _runtimes.append(new_runtime)


def is_registered() -> bool:
    """Whether any runtime is currently registered.

    Cheap synchronous check — async callers can guard
    ``asyncio.to_thread(runtime.X, ...)`` on this so they don't pay
    thread-pool overhead when no runtime is installed.
    """
    return bool(_runtimes)


def _is_v1_candidate(handle) -> bool:
    """Cheap pre-filter so the chain skips obvious non-v1 handles.

    Default-to-legacy: missing metadata means a pre-v1 handle from
    before ``ProvisionRuntimeMetadata`` was added to ``ProvisionRecord``
    — those handles exist in production, and treating them as v1
    candidates would mean every status poll for an old AWS/GCP cluster
    fans out to parse YAML inside N runtime claim checks.
    ``getattr(..., 'has_ray', True)`` treats unset as "yes, has ray" →
    not v1.
    """
    if handle is None:
        return False
    metadata = getattr(handle, 'provision_runtime_metadata', None)
    has_ray = getattr(metadata, 'has_ray', True)  # default True == legacy
    return not has_ray


def _claimants(handle) -> List[ManagedJobRuntime]:
    """Iterate runtimes that claim ``handle`` via ``owns()``.

    Backward-compat shim: runtimes that pre-date the chain refactor may
    not implement ``owns()`` yet. For those, fall back to "claims
    everything" — their per-hook resolvers already self-filter by
    returning None for handles they don't recognize.
    """
    claims = []
    for r in _runtimes:
        owns = getattr(r, 'owns', None)
        if owns is None:
            # Backward-compat: pre-chain runtimes self-filter inside
            # each hook via their own ``_resolve_target`` returning None.
            claims.append(r)
            continue
        try:
            if owns(handle):
                claims.append(r)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug('ManagedJobRuntime.owns() raised on %s: %s',
                         type(r).__name__, e)
    if len(claims) > 1:
        logger.warning(
            'Multiple ManagedJobRuntime instances claimed the same '
            'handle: %s. Dispatch will resolve in registration order; '
            'this is usually a configuration bug.',
            [type(r).__name__ for r in claims])
    return claims


# Module-level dispatch. Each function returns ``None`` when no
# runtime claims the handle, so callers fall through to their default.


def get_job_status(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
    returncode: Optional[int] = None,
) -> Optional[Tuple[Optional['job_lib.JobStatus'], Optional[str]]]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.get_job_status(handle, cluster_name, returncode=returncode)
        if result is not None:
            return result
    return None


def get_job_submitted_at(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
) -> Optional[float]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.get_job_submitted_at(handle, cluster_name)
        if result is not None:
            return result
    return None


def get_job_ended_at(
    handle: Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle'],
    cluster_name: str,
) -> Optional[float]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.get_job_ended_at(handle, cluster_name)
        if result is not None:
            return result
    return None


def get_exit_codes(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
) -> Optional[List[int]]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.get_exit_codes(handle)
        if result is not None:
            return result
    return None


def download_logs(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
    job_id: int,
    task_id: Optional[int],
) -> Optional[str]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.download_logs(handle, job_id, task_id)
        if result is not None:
            return result
    return None


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
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.tail_logs(
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
        if result is not None:
            return result
    return None


def job_group_envs(
    tasks: List['task_lib.Task'],
    job_id: int,
) -> Optional[Dict[str, str]]:
    # No handle here — JobGroup envs are runtime-agnostic; iterate
    # all runtimes and return the first non-None.
    for r in _runtimes:
        result = r.job_group_envs(tasks, job_id)
        if result is not None:
            return result
    return None


def k8s_dns_addresses_for_task(
    task: 'task_lib.Task',
    job_id: int,
) -> Optional[List[str]]:
    # No handle here — DNS lookup is task-shaped; iterate all runtimes.
    for r in _runtimes:
        result = r.k8s_dns_addresses_for_task(task, job_id)
        if result is not None:
            return result
    return None


def k8s_dns_addresses_for_handle(
    handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
) -> Optional[List[str]]:
    if not _is_v1_candidate(handle):
        return None
    for r in _claimants(handle):
        result = r.k8s_dns_addresses_for_handle(handle)
        if result is not None:
            return result
    return None
