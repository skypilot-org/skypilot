"""Plugin lifecycle hooks for the SkyPilot API server.

Plugins can register callbacks here to react to events emitted by core code
(e.g. a volume being deleted). Hooks are best-effort: any exception raised
by a callback is logged and swallowed so it cannot affect the originating
operation.
"""
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from sky import sky_logging

if TYPE_CHECKING:
    from sky import admin_policy
    from sky import dag as dag_lib
    from sky import models
    from sky.server.requests import request_names
    from sky.utils import config_utils

logger = sky_logging.init_logger(__name__)

VolumeDeletedHook = Callable[[str, 'models.VolumeConfig'], None]
ManagedJobSubmittedHook = Callable[
    [List[int], 'dag_lib.Dag', Optional[str], str], None]
AdminPolicyAppliedHook = Callable[[
    'dag_lib.Dag', 'config_utils.Config',
    'request_names.AdminPolicyRequestName',
    Optional['admin_policy.RequestOptions']
], None]

_VOLUME_DELETED_HOOKS: Dict[str, VolumeDeletedHook] = {}
_MANAGED_JOB_SUBMITTED_HOOKS: Dict[str, ManagedJobSubmittedHook] = {}
_ADMIN_POLICY_APPLIED_HOOKS: Dict[str, AdminPolicyAppliedHook] = {}


def register_volume_deleted_hook(hook_id: str, fn: VolumeDeletedHook) -> None:
    """Register a callback to run after a volume is removed from the database.

    The callback receives the volume name and its ``VolumeConfig``. It is
    invoked whether the deletion path is normal or ``purge=True``.

    Args:
        hook_id: A stable, unique identifier for this hook (e.g.
            ``"my_plugin.on_volume_deleted"``). Re-registering with the
            same ID replaces the previous callback so a plugin loaded twice
            in the same process does not fire its hook twice.
        fn: The callback to invoke.
    """
    if hook_id in _VOLUME_DELETED_HOOKS:
        logger.debug(
            f'Replacing existing volume-deleted hook for id {hook_id!r}.')
    _VOLUME_DELETED_HOOKS[hook_id] = fn


def fire_volume_deleted(name: str, config: 'models.VolumeConfig') -> None:
    """Invoke all registered volume-deleted callbacks.

    Each callback runs in isolation; an exception in one does not affect
    other callbacks or the caller.
    """
    # Iterate over a snapshot so a hook that registers another hook (or a
    # concurrent register call) cannot mutate the dict mid-iteration.
    for hook_id, fn in list(_VOLUME_DELETED_HOOKS.items()):
        try:
            fn(name, config)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'volume-deleted hook {hook_id!r} raised for {name!r}: {e}')


def register_managed_job_submitted_hook(hook_id: str,
                                        fn: ManagedJobSubmittedHook) -> None:
    """Register a callback to run after managed job IDs are assigned.

    Callbacks run synchronously in the launch worker, before the controller
    task is launched. They receive the full job ID list, the submitted DAG, the
    optional file-mounts blob ID, and the submitting user's hash. Callbacks
    should return promptly and treat the job IDs and DAG as read-only.

    Args:
        hook_id: A stable, unique identifier. Re-registering with the same ID
            replaces the previous callback.
        fn: The callback to invoke.
    """
    if hook_id in _MANAGED_JOB_SUBMITTED_HOOKS:
        logger.debug(
            f'Replacing existing managed-job-submitted hook for id {hook_id!r}.'
        )
    _MANAGED_JOB_SUBMITTED_HOOKS[hook_id] = fn


def has_managed_job_submitted_hooks() -> bool:
    """Whether any managed job submission callbacks are registered."""
    return bool(_MANAGED_JOB_SUBMITTED_HOOKS)


def fire_managed_job_submitted(job_ids: List[int], dag: 'dag_lib.Dag',
                               file_mounts_blob_id: Optional[str],
                               user_hash: str) -> None:
    """Invoke submitted callbacks, isolating callback failures from launch."""
    # Snapshot the registry so callbacks can register hooks during dispatch.
    for hook_id, fn in list(_MANAGED_JOB_SUBMITTED_HOOKS.items()):
        try:
            fn(list(job_ids), dag, file_mounts_blob_id, user_hash)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'managed-job-submitted hook {hook_id!r} raised '
                           f'for jobs {job_ids!r}: {e}')


def register_admin_policy_applied_hook(hook_id: str,
                                       fn: AdminPolicyAppliedHook) -> None:
    """Register a callback to run after the server applies the admin policy.

    Callbacks run synchronously in the request worker each time the server
    resolves the effective request for a DAG (``admin_policy_utils.apply``),
    whether or not an admin policy is configured. They receive the effective
    DAG, the effective SkyPilot config, the request name and the request
    options. The config is the full resolved config and may hold credentials.
    Callbacks should return promptly and treat their arguments as read-only.
    They do not run for client-side policy application, nor for requests the
    policy rejects.

    Args:
        hook_id: A stable, unique identifier. Re-registering with the same ID
            replaces the previous callback.
        fn: The callback to invoke.
    """
    if hook_id in _ADMIN_POLICY_APPLIED_HOOKS:
        logger.debug(
            f'Replacing existing admin-policy-applied hook for id {hook_id!r}.')
    _ADMIN_POLICY_APPLIED_HOOKS[hook_id] = fn


def fire_admin_policy_applied(
        dag: 'dag_lib.Dag', config: 'config_utils.Config',
        request_name: 'request_names.AdminPolicyRequestName',
        request_options: Optional['admin_policy.RequestOptions']) -> None:
    """Invoke applied callbacks, isolating callback failures from the request.
    """
    # Snapshot the registry so callbacks can register hooks during dispatch.
    for hook_id, fn in list(_ADMIN_POLICY_APPLIED_HOOKS.items()):
        try:
            fn(dag, config, request_name, request_options)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'admin-policy-applied hook {hook_id!r} raised '
                           f'for request {request_name.value!r}: {e}')
