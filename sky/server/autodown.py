"""Durable server-side cluster autodown reconciliation."""

import time
from typing import Any, cast, Dict, Optional, Protocol, Tuple

from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.schemas.generated import autostopv1_pb2
from sky.server import constants as server_constants
from sky.skylet import constants
from sky.utils import locks
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

_POLLING_STATES = {
    global_user_state.AutodownIntentState.CONFIGURING,
    global_user_state.AutodownIntentState.ARMED,
}
_ACTIONABLE_STATES = {
    global_user_state.AutodownIntentState.PREPARING,
    global_user_state.AutodownIntentState.READY,
    global_user_state.AutodownIntentState.EXECUTING,
    global_user_state.AutodownIntentState.RETRY_WAIT,
}
_STRICT_EXECUTION_STRATEGIES = {
    TeardownExecutionStrategy.SERVER_ONLY.value,
    TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value,
}
_GENERIC_FAILURE = 'Autodown reconciliation failed.'

# These cursors only schedule fair bounded reads. Durable CAS fences and the
# shared cluster lock remain authoritative for every transition and mutation.
_polling_cursor: Optional[str] = None
_actionable_cursor: Optional[Tuple[int, str]] = None


class _ReconciliationBackend(Protocol):
    """Provider operations used by one reconciliation sweep."""

    def get_durable_autodown_status(
        self, handle: cloud_vm_ray_backend.CloudVmRayResourceHandle
    ) -> autostopv1_pb2.IsAutostoppingResponse:
        ...

    def teardown_no_lock(self,
                         handle: cloud_vm_ray_backend.CloudVmRayResourceHandle,
                         terminate: bool,
                         expected_cluster_hash: Optional[str] = None) -> None:
        ...


def _same_fence(
    current: Optional[global_user_state.AutodownIntent],
    observed: global_user_state.AutodownIntent,
    expected_state: Optional[global_user_state.AutodownIntentState] = None
) -> bool:
    if current is None:
        return False
    if expected_state is None:
        expected_state = observed.state
    return (current.cluster_name == observed.cluster_name and
            current.cluster_hash == observed.cluster_hash and
            current.generation == observed.generation and
            current.state is expected_state and
            current.attempt_count == observed.attempt_count)


def _transition(intent: global_user_state.AutodownIntent,
                new_state: global_user_state.AutodownIntentState) -> bool:
    return global_user_state.compare_and_swap_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={intent.state},
        expected_attempt_count=intent.attempt_count,
        new_state=new_state,
    )


def _is_manual_cancellation(intent: global_user_state.AutodownIntent) -> bool:
    return intent.idle_minutes < 0 or not intent.to_down


def _finish_missing_polling_intent(
        intent: global_user_state.AutodownIntent) -> None:
    terminal_state = global_user_state.AutodownIntentState.SUCCEEDED
    if (intent.state is global_user_state.AutodownIntentState.CONFIGURING and
            _is_manual_cancellation(intent)):
        terminal_state = global_user_state.AutodownIntentState.CANCELLED
    _transition(intent, terminal_state)


def _valid_status_identity(
        intent: global_user_state.AutodownIntent,
        response: autostopv1_pb2.IsAutostoppingResponse) -> bool:
    if (not response.supports_durable_autodown or
            not response.HasField('cluster_hash') or
            not response.HasField('generation')):
        return False
    return (response.cluster_hash == intent.cluster_hash and
            response.generation == intent.generation)


def _poll_intent(intent: global_user_state.AutodownIntent,
                 cluster_record: Optional[Dict[str, Any]],
                 backend: _ReconciliationBackend) -> None:
    if (intent.state not in _POLLING_STATES or
            intent.execution_strategy not in _STRICT_EXECUTION_STRATEGIES):
        return
    current = global_user_state.get_autodown_intent(intent.cluster_name)
    if not _same_fence(current, intent):
        return
    assert current is not None
    intent = current
    if cluster_record is None:
        _finish_missing_polling_intent(intent)
        return
    if cluster_record['cluster_hash'] != intent.cluster_hash:
        _transition(intent, global_user_state.AutodownIntentState.CANCELLED)
        return

    try:
        response = backend.get_durable_autodown_status(
            cast(cloud_vm_ray_backend.CloudVmRayResourceHandle,
                 cluster_record['handle']))
    except Exception:  # pylint: disable=broad-except
        logger.info('Durable autodown status poll deferred for cluster %r.',
                    intent.cluster_name)
        return
    if not _valid_status_identity(intent, response):
        logger.info(
            'Durable autodown status is unavailable or incompatible '
            'for cluster %r.', intent.cluster_name)
        return

    durable_state = response.durable_execution_state
    if durable_state == (
            autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED):
        # A skylet teardown claim is irreversible; recovery must finish that
        # generation even if cancellation reached the server concurrently.
        _transition(intent, global_user_state.AutodownIntentState.PREPARING)
    elif durable_state == (
            autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED):
        _transition(intent, global_user_state.AutodownIntentState.READY)
    elif (intent.state is global_user_state.AutodownIntentState.CONFIGURING and
          _is_manual_cancellation(intent)):
        _transition(intent, global_user_state.AutodownIntentState.CANCELLED)
    elif (intent.state is global_user_state.AutodownIntentState.CONFIGURING and
          durable_state == autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED):
        _transition(intent, global_user_state.AutodownIntentState.ARMED)


def _retry_delay_seconds(attempt_count: int) -> int:
    exponent = min(attempt_count, 30)
    return min(
        server_constants.AUTODOWN_RECONCILER_RETRY_MAX_SECONDS,
        server_constants.AUTODOWN_RECONCILER_RETRY_BASE_SECONDS * (2**exponent),
    )


def _record_retry(intent: global_user_state.AutodownIntent, now: int) -> None:
    global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={intent.state},
        expected_attempt_count=intent.attempt_count,
        next_retry_at=now + _retry_delay_seconds(intent.attempt_count),
        # Provider exceptions can contain response bodies and credentials.
        error=RuntimeError(_GENERIC_FAILURE),
    )


def _get_cluster_record(
        intent: global_user_state.AutodownIntent) -> Optional[Dict[str, Any]]:
    return global_user_state.get_cluster_from_name(intent.cluster_name,
                                                   include_user_info=False,
                                                   summary_response=True)


def _reconcile_under_lock(intent: global_user_state.AutodownIntent,
                          backend: _ReconciliationBackend, now: int) -> None:
    current = global_user_state.get_autodown_intent(intent.cluster_name)
    if not _same_fence(current, intent):
        return
    assert current is not None
    if (current.state not in _ACTIONABLE_STATES or
            current.execution_strategy not in _STRICT_EXECUTION_STRATEGIES):
        return

    cluster_record = _get_cluster_record(current)
    if cluster_record is None:
        _transition(current, global_user_state.AutodownIntentState.SUCCEEDED)
        return
    if cluster_record['cluster_hash'] != current.cluster_hash:
        _transition(current, global_user_state.AutodownIntentState.CANCELLED)
        return

    try:
        cluster_record = backend_utils.refresh_cluster_record(
            current.cluster_name,
            force_refresh_statuses=set(status_lib.ClusterStatus),
            cluster_lock_already_held=True,
            include_user_info=False,
            summary_response=True,
            retry_if_missing=False,
        )
    except Exception:  # pylint: disable=broad-except
        logger.info('Durable autodown provider check deferred for cluster %r.',
                    current.cluster_name)
        if current.state is not global_user_state.AutodownIntentState.PREPARING:
            _record_retry(current, now)
        return
    if cluster_record is None:
        _transition(current, global_user_state.AutodownIntentState.SUCCEEDED)
        return
    if cluster_record['cluster_hash'] != current.cluster_hash:
        _transition(current, global_user_state.AutodownIntentState.CANCELLED)
        return

    # Provider checks may block; re-read the complete intent fence before any
    # state transition or provider mutation.
    refreshed_intent = global_user_state.get_autodown_intent(
        current.cluster_name)
    if not _same_fence(refreshed_intent, current):
        return
    assert refreshed_intent is not None
    current = refreshed_intent

    if current.state is global_user_state.AutodownIntentState.PREPARING:
        grace_seconds = (
            server_constants.AUTODOWN_RECONCILER_PREPARING_GRACE_SECONDS)
        if now - current.updated_at >= grace_seconds:
            _transition(current, global_user_state.AutodownIntentState.READY)
        return

    if current.state in {
            global_user_state.AutodownIntentState.READY,
            global_user_state.AutodownIntentState.RETRY_WAIT,
    }:
        if not _transition(current,
                           global_user_state.AutodownIntentState.EXECUTING):
            return
        claimed = global_user_state.get_autodown_intent(current.cluster_name)
        if not _same_fence(
                claimed,
                current,
                expected_state=global_user_state.AutodownIntentState.EXECUTING):
            return
        assert claimed is not None
        current = claimed

    if current.state is not global_user_state.AutodownIntentState.EXECUTING:
        return

    # The state claim and exact-intent read above can race with name reuse by a
    # writer outside the cluster lock. Re-read the incarnation at the provider
    # mutation boundary and use only that freshly fenced handle.
    cluster_record = _get_cluster_record(current)
    if cluster_record is None:
        _transition(current, global_user_state.AutodownIntentState.SUCCEEDED)
        return
    if cluster_record['cluster_hash'] != current.cluster_hash:
        _transition(current, global_user_state.AutodownIntentState.CANCELLED)
        return
    handle = cast(cloud_vm_ray_backend.CloudVmRayResourceHandle,
                  cluster_record['handle'])
    try:
        backend.teardown_no_lock(handle,
                                 terminate=True,
                                 expected_cluster_hash=current.cluster_hash)
    except Exception:  # pylint: disable=broad-except
        # Teardown may have removed the durable cluster row before a later
        # cleanup step failed. Resolve that crash boundary before retrying.
        cluster_after_failure = _get_cluster_record(current)
        if cluster_after_failure is None:
            _transition(current,
                        global_user_state.AutodownIntentState.SUCCEEDED)
        elif cluster_after_failure['cluster_hash'] != current.cluster_hash:
            _transition(current,
                        global_user_state.AutodownIntentState.CANCELLED)
        else:
            _record_retry(current, now)
        return

    cluster_after_teardown = _get_cluster_record(current)
    if cluster_after_teardown is None:
        _transition(current, global_user_state.AutodownIntentState.SUCCEEDED)
    elif cluster_after_teardown['cluster_hash'] != current.cluster_hash:
        _transition(current, global_user_state.AutodownIntentState.CANCELLED)
    else:
        _record_retry(current, now)


def _reconcile_actionable_intent(intent: global_user_state.AutodownIntent,
                                 backend: _ReconciliationBackend,
                                 now: int) -> None:
    if (intent.state not in _ACTIONABLE_STATES or
            intent.execution_strategy not in _STRICT_EXECUTION_STRATEGIES):
        return
    workspace = intent.workspace or constants.SKYPILOT_DEFAULT_WORKSPACE
    # Keep the daemon's system identity; only the workspace selects the
    # server-owned provider credential scope for this intent.
    with skypilot_config.local_active_workspace_ctx(workspace):
        lock = locks.get_lock(
            backend_utils.cluster_status_lock_id(intent.cluster_name),
            timeout=server_constants.AUTODOWN_RECONCILER_LOCK_TIMEOUT_SECONDS)
        try:
            with lock.acquire():
                _reconcile_under_lock(intent, backend, now)
        except locks.LockTimeout:
            return


def reconcile_autodown_intents(now: Optional[int] = None,
                               batch_size: Optional[int] = None) -> None:
    """Run one bounded reconciliation sweep.

    Polling and actionable queues each receive a bounded slice so a large
    stable ARMED population cannot starve provider teardown recovery.
    """
    global _actionable_cursor, _polling_cursor

    if now is None:
        now = int(time.time())
    if batch_size is None:
        batch_size = server_constants.AUTODOWN_RECONCILER_BATCH_SIZE
    batch_size = max(
        1, min(batch_size, server_constants.AUTODOWN_RECONCILER_BATCH_SIZE))

    due_intents = global_user_state.list_due_autodown_intents(
        now=now, limit=batch_size, start_after=_actionable_cursor)
    polling_intents = global_user_state.list_polling_autodown_intents(
        limit=batch_size, start_after=_polling_cursor)
    if due_intents:
        last_due = due_intents[-1]
        _actionable_cursor = (last_due.next_retry_at or
                              0, last_due.cluster_name)
    if polling_intents:
        _polling_cursor = polling_intents[-1].cluster_name
    if not due_intents and not polling_intents:
        return

    backend = cast(_ReconciliationBackend,
                   cloud_vm_ray_backend.CloudVmRayBackend())
    polling_records = global_user_state.get_clusters_from_names(
        [intent.cluster_name for intent in polling_intents],
        include_user_info=False,
    )
    for intent in due_intents:
        try:
            _reconcile_actionable_intent(intent, backend, now)
        except Exception:  # pylint: disable=broad-except
            logger.info('Durable autodown action deferred for cluster %r.',
                        intent.cluster_name)
    for intent in polling_intents:
        try:
            _poll_intent(intent, polling_records.get(intent.cluster_name),
                         backend)
        except Exception:  # pylint: disable=broad-except
            logger.info('Durable autodown polling deferred for cluster %r.',
                        intent.cluster_name)
