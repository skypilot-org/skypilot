"""Tests for the durable server-side autodown reconciler."""

import contextlib
import dataclasses
import os
import pickle
import threading
import time
from typing import Callable, Dict, List, Optional, Union

import pytest
import sqlalchemy

from sky import global_user_state
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.schemas.generated import autostopv1_pb2
from sky.server import autodown
from sky.skylet import constants
from sky.utils import locks
from sky.utils.db import db_utils


@dataclasses.dataclass(frozen=True)
class _Handle:
    cluster_name: str


class _AcquireContext:

    def __init__(self, lock: threading.Lock):
        self._lock = lock

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self._lock.release()


class _DistributedLock:

    def __init__(self):
        self._lock = threading.Lock()
        self.force_unlock_calls = 0

    def acquire(self, blocking: bool = True):
        if not self._lock.acquire(blocking=blocking):
            raise locks.LockTimeout('busy')
        return _AcquireContext(self._lock)

    def force_unlock(self):
        self.force_unlock_calls += 1


StatusResult = Union[autostopv1_pb2.IsAutostoppingResponse, BaseException]
TeardownEffect = Callable[[_Handle], None]


class _Backend:

    def __init__(self):
        self.status_results: Dict[str, StatusResult] = {}
        self.status_calls: List[str] = []
        self.teardown_calls: List[str] = []
        self.teardown_expected_hashes: List[Optional[str]] = []
        self.teardown_effect: Optional[TeardownEffect] = None

    def get_durable_autodown_status(
            self, handle: _Handle) -> autostopv1_pb2.IsAutostoppingResponse:
        self.status_calls.append(handle.cluster_name)
        result = self.status_results[handle.cluster_name]
        if isinstance(result, BaseException):
            raise result
        return result

    def teardown_no_lock(self,
                         handle: _Handle,
                         terminate: bool,
                         expected_cluster_hash: Optional[str] = None) -> None:
        assert terminate is True
        self.teardown_calls.append(handle.cluster_name)
        self.teardown_expected_hashes.append(expected_cluster_hash)
        if self.teardown_effect is not None:
            self.teardown_effect(handle)


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    manager = db_utils.DatabaseManager(
        'state',
        global_user_state.create_table,
        # pylint: disable=protected-access
        post_init_fn=lambda _: global_user_state._sqlite_supports_returning(),
    )
    monkeypatch.setattr(global_user_state, '_db_manager', manager)
    monkeypatch.setattr(global_user_state, 'initialize_and_get_db',
                        manager.get_engine)
    return manager.get_engine()


def _insert_cluster(engine,
                    cluster_name: str,
                    cluster_hash: str,
                    workspace: str = 'workspace-a') -> None:
    with engine.begin() as connection:
        connection.execute(global_user_state.cluster_table.insert().values(
            name=cluster_name,
            cluster_hash=cluster_hash,
            handle=pickle.dumps(_Handle(cluster_name)),
            status='UP',
            metadata='{}',
            workspace=workspace,
        ))


def _delete_cluster(engine, cluster_name: str) -> None:
    with engine.begin() as connection:
        connection.execute(global_user_state.cluster_table.delete().where(
            global_user_state.cluster_table.c.name == cluster_name))


def _create_intent(
    engine,
    cluster_name: str = 'cluster',
    cluster_hash: str = 'hash',
    *,
    state: global_user_state.AutodownIntentState = (
        global_user_state.AutodownIntentState.CONFIGURING),
    idle_minutes: int = 15,
    to_down: bool = True,
    execution_strategy: str = (
        TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value),
    with_cluster: bool = True,
):
    intent = global_user_state.create_or_replace_autodown_intent(
        cluster_name=cluster_name,
        cluster_hash=cluster_hash,
        idle_minutes=idle_minutes,
        to_down=to_down,
        execution_strategy=execution_strategy,
        user_hash='user-hash',
        workspace='workspace-a',
    )
    assert intent is not None
    if state is not global_user_state.AutodownIntentState.CONFIGURING:
        assert global_user_state.compare_and_swap_autodown_intent(
            cluster_name=cluster_name,
            cluster_hash=cluster_hash,
            generation=intent.generation,
            expected_states={intent.state},
            expected_attempt_count=intent.attempt_count,
            new_state=state,
        )
        intent = global_user_state.get_autodown_intent(cluster_name)
        assert intent is not None
    if with_cluster:
        _insert_cluster(engine, cluster_name, cluster_hash)
    return intent


def _status(intent,
            durable_state,
            *,
            capability: bool = True,
            cluster_hash: Optional[str] = None,
            generation: Optional[int] = None,
            error_summary: Optional[str] = None):
    response = autostopv1_pb2.IsAutostoppingResponse(
        supports_durable_autodown=capability,
        durable_execution_state=durable_state,
    )
    if cluster_hash is None:
        cluster_hash = intent.cluster_hash
    if generation is None:
        generation = intent.generation
    response.cluster_hash = cluster_hash
    response.generation = generation
    if error_summary is not None:
        response.error_summary = error_summary
    return response


@pytest.fixture()
def reconciler(tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)
    backend = _Backend()
    monkeypatch.setattr(autodown.cloud_vm_ray_backend, 'CloudVmRayBackend',
                        lambda: backend)

    distributed_locks: Dict[str, _DistributedLock] = {}

    def get_lock(lock_id, timeout=None):
        del timeout
        return distributed_locks.setdefault(lock_id, _DistributedLock())

    monkeypatch.setattr(autodown.locks, 'get_lock', get_lock)

    def refresh_cluster_record(cluster_name, **kwargs):
        assert kwargs['cluster_lock_already_held'] is True
        assert kwargs['retry_if_missing'] is False
        return global_user_state.get_cluster_from_name(cluster_name,
                                                       include_user_info=False,
                                                       summary_response=True)

    monkeypatch.setattr(autodown.backend_utils, 'refresh_cluster_record',
                        refresh_cluster_record)
    return engine, backend, distributed_locks


@pytest.mark.parametrize(('durable_state', 'expected_state'), [
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED,
     global_user_state.AutodownIntentState.ARMED),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
     global_user_state.AutodownIntentState.PREPARING),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
     global_user_state.AutodownIntentState.READY),
])
def test_configuring_status_mapping(reconciler, durable_state, expected_state):
    engine, backend, _ = reconciler
    intent = _create_intent(engine)
    backend.status_results[intent.cluster_name] = _status(intent, durable_state)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is expected_state
    assert backend.teardown_calls == []


@pytest.mark.parametrize(('durable_state', 'expected_state'), [
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
     global_user_state.AutodownIntentState.PREPARING),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
     global_user_state.AutodownIntentState.READY),
])
def test_armed_status_mapping(reconciler, durable_state, expected_state):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.ARMED)
    backend.status_results[intent.cluster_name] = _status(intent, durable_state)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is expected_state


def test_old_skylet_capability_is_non_destructive(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine)
    backend.status_results[intent.cluster_name] = _status(
        intent,
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
        capability=False)

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(intent.cluster_name) == intent
    assert backend.teardown_calls == []


def test_server_required_with_error_summary_becomes_ready(reconciler, caplog):
    engine, backend, _ = reconciler
    intent = _create_intent(engine)
    backend.status_results[intent.cluster_name] = _status(
        intent,
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
        error_summary='bounded head teardown failure')

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.READY
    assert 'bounded head teardown failure' not in caplog.text


def test_transient_head_failure_leaves_armed_intent_for_polling(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.ARMED)
    backend.status_results[intent.cluster_name] = RuntimeError(
        'provider body with token=secret')

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(intent.cluster_name) == intent
    assert backend.teardown_calls == []


def test_predecessor_generation_claim_cannot_map_newer_configuring_intent(
        reconciler):
    engine, backend, _ = reconciler
    predecessor = _create_intent(engine)
    current = global_user_state.create_or_replace_autodown_intent(
        cluster_name=predecessor.cluster_name,
        cluster_hash=predecessor.cluster_hash,
        idle_minutes=30,
        to_down=True,
        execution_strategy=predecessor.execution_strategy,
        user_hash=predecessor.user_hash,
        workspace=predecessor.workspace,
        expected_cluster_hash=predecessor.cluster_hash,
        expected_generation=predecessor.generation,
        expected_states={predecessor.state},
    )
    assert current is not None
    backend.status_results[current.cluster_name] = _status(
        current,
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
        generation=predecessor.generation)

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(
        current.cluster_name) == current
    assert backend.teardown_calls == []


@pytest.mark.parametrize('mismatch', ['hash', 'generation'])
def test_untrusted_skylet_status_is_non_destructive(reconciler, mismatch):
    engine, backend, _ = reconciler
    intent = _create_intent(engine)
    kwargs = {}
    if mismatch == 'hash':
        kwargs['cluster_hash'] = 'other-hash'
    elif mismatch == 'generation':
        kwargs['generation'] = intent.generation + 1
    backend.status_results[intent.cluster_name] = _status(
        intent, autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
        **kwargs)

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(intent.cluster_name) == intent
    assert backend.teardown_calls == []


@pytest.mark.parametrize('idle_minutes,to_down', [(-1, True), (15, False)])
def test_manual_cancellation_finalizes_configuring_intent(
        reconciler, idle_minutes, to_down):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, idle_minutes=idle_minutes, to_down=to_down)
    backend.status_results[intent.cluster_name] = _status(
        intent, autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.CANCELLED


@pytest.mark.parametrize(('durable_state', 'expected_state'), [
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
     global_user_state.AutodownIntentState.PREPARING),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
     global_user_state.AutodownIntentState.READY),
])
@pytest.mark.parametrize('idle_minutes,to_down', [(-1, True), (15, False)])
def test_remote_teardown_claim_wins_over_configuring_cancellation(
        reconciler, durable_state, expected_state, idle_minutes, to_down):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, idle_minutes=idle_minutes, to_down=to_down)
    backend.status_results[intent.cluster_name] = _status(intent, durable_state)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is expected_state


@pytest.mark.parametrize('state', [
    global_user_state.AutodownIntentState.CONFIGURING,
    global_user_state.AutodownIntentState.READY,
])
def test_name_reuse_cancels_stale_hash_without_touching_new_cluster(
        reconciler, state):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, state=state, with_cluster=False)
    _insert_cluster(engine, intent.cluster_name, 'replacement-hash')

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.CANCELLED
    replacement = global_user_state.get_cluster_from_name(
        intent.cluster_name, include_user_info=False, summary_response=True)
    assert replacement is not None
    assert replacement['cluster_hash'] == 'replacement-hash'
    assert backend.status_calls == []
    assert backend.teardown_calls == []


def test_missing_configuring_down_intent_succeeds_as_tombstone(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, with_cluster=False)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.status_calls == []


def test_missing_configuring_cancellation_is_cancelled(reconciler):
    engine, _, _ = reconciler
    intent = _create_intent(engine, idle_minutes=-1, with_cluster=False)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.CANCELLED


def test_runpod_like_head_disappearance_succeeds_armed_intent(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.ARMED)
    backend.status_results[intent.cluster_name] = RuntimeError(
        'head disappeared before state could be observed')

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(intent.cluster_name) == intent
    _delete_cluster(engine, intent.cluster_name)
    autodown.reconcile_autodown_intents(now=101)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.status_calls == [intent.cluster_name]


def test_preparing_waits_for_grace_then_becomes_ready(reconciler, monkeypatch):
    engine, _, _ = reconciler
    intent = _create_intent(
        engine, state=global_user_state.AutodownIntentState.PREPARING)
    with engine.begin() as connection:
        connection.execute(
            global_user_state.autodown_intent_table.update().where(
                global_user_state.autodown_intent_table.c.cluster_name ==
                intent.cluster_name).values(updated_at=100))
    monkeypatch.setattr(
        autodown.server_constants,
        'AUTODOWN_RECONCILER_PREPARING_GRACE_SECONDS',
        10,
    )

    autodown.reconcile_autodown_intents(now=109)
    before_grace = global_user_state.get_autodown_intent(intent.cluster_name)
    assert before_grace is not None
    assert before_grace.state is global_user_state.AutodownIntentState.PREPARING

    autodown.reconcile_autodown_intents(now=110)
    after_grace = global_user_state.get_autodown_intent(intent.cluster_name)
    assert after_grace is not None
    assert after_grace.state is global_user_state.AutodownIntentState.READY


@pytest.mark.parametrize(('state', 'expected_state'), [
    (global_user_state.AutodownIntentState.PREPARING,
     global_user_state.AutodownIntentState.READY),
    (global_user_state.AutodownIntentState.READY,
     global_user_state.AutodownIntentState.SUCCEEDED),
])
def test_claimed_local_state_wins_over_manual_cancellation(
        reconciler, monkeypatch, state, expected_state):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, state=state, idle_minutes=-1)
    monkeypatch.setattr(
        autodown.server_constants,
        'AUTODOWN_RECONCILER_PREPARING_GRACE_SECONDS',
        0,
    )
    backend.teardown_effect = lambda handle: _delete_cluster(
        engine, handle.cluster_name)

    autodown.reconcile_autodown_intents(now=intent.updated_at)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is expected_state


@pytest.mark.parametrize('state', [
    global_user_state.AutodownIntentState.PREPARING,
    global_user_state.AutodownIntentState.READY,
])
def test_provider_disappearance_finalizes_without_teardown(
        reconciler, monkeypatch, state):
    engine, backend, _ = reconciler
    intent = _create_intent(engine, state=state)

    def provider_gone(cluster_name, **kwargs):
        del kwargs
        _delete_cluster(engine, cluster_name)
        return None

    monkeypatch.setattr(autodown.backend_utils, 'refresh_cluster_record',
                        provider_gone)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == []


def test_teardown_uses_workspace_and_preserves_system_user(
        reconciler, monkeypatch):
    engine, backend, distributed_locks = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.READY)
    active_workspaces = []
    system_user = 'system-user'
    monkeypatch.setenv(constants.USER_ID_ENV_VAR, system_user)

    @contextlib.contextmanager
    def workspace_context(workspace):
        active_workspaces.append(workspace)
        yield

    monkeypatch.setattr(autodown.skypilot_config, 'local_active_workspace_ctx',
                        workspace_context)

    def teardown(handle):
        assert active_workspaces[-1] == 'workspace-a'
        assert os.environ[constants.USER_ID_ENV_VAR] == system_user
        _delete_cluster(engine, handle.cluster_name)

    backend.teardown_effect = teardown

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert active_workspaces == ['workspace-a']
    assert all(
        lock.force_unlock_calls == 0 for lock in distributed_locks.values())


def test_retry_backoff_is_fenced_and_persisted_error_is_generic(
        reconciler, monkeypatch):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.READY)
    monkeypatch.setattr(autodown.server_constants,
                        'AUTODOWN_RECONCILER_RETRY_BASE_SECONDS', 5)
    monkeypatch.setattr(autodown.server_constants,
                        'AUTODOWN_RECONCILER_RETRY_MAX_SECONDS', 20)

    attempts = 0

    def teardown(handle):
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise RuntimeError('provider body token=very-secret')
        _delete_cluster(engine, handle.cluster_name)

    backend.teardown_effect = teardown

    autodown.reconcile_autodown_intents(now=100)

    retry = global_user_state.get_autodown_intent(intent.cluster_name)
    assert retry is not None
    assert retry.state is global_user_state.AutodownIntentState.RETRY_WAIT
    assert retry.attempt_count == 1
    assert retry.next_retry_at == 105
    assert retry.last_error == 'RuntimeError: Autodown reconciliation failed.'
    assert 'secret' not in retry.last_error

    autodown.reconcile_autodown_intents(now=104)
    assert backend.teardown_calls == [intent.cluster_name]

    autodown.reconcile_autodown_intents(now=105)
    retry = global_user_state.get_autodown_intent(intent.cluster_name)
    assert retry is not None
    assert retry.attempt_count == 2
    assert retry.next_retry_at == 115

    autodown.reconcile_autodown_intents(now=115)
    retry = global_user_state.get_autodown_intent(intent.cluster_name)
    assert retry is not None
    assert retry.attempt_count == 3
    assert retry.next_retry_at == 135

    autodown.reconcile_autodown_intents(now=135)
    succeeded = global_user_state.get_autodown_intent(intent.cluster_name)
    assert succeeded is not None
    assert succeeded.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == [intent.cluster_name] * 4


def test_executing_restart_retries_teardown_idempotently(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(
        engine, state=global_user_state.AutodownIntentState.EXECUTING)
    backend.teardown_effect = lambda handle: _delete_cluster(
        engine, handle.cluster_name)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == [intent.cluster_name]


def test_executing_restart_after_cluster_row_removal_finalizes_tombstone(
        reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(
        engine,
        state=global_user_state.AutodownIntentState.EXECUTING,
        with_cluster=False)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == []


def test_stale_generation_snapshot_cannot_teardown_replacement(
        reconciler, monkeypatch):
    engine, backend, _ = reconciler
    stale = _create_intent(engine,
                           state=global_user_state.AutodownIntentState.READY)
    replacement = global_user_state.create_or_replace_autodown_intent(
        cluster_name=stale.cluster_name,
        cluster_hash=stale.cluster_hash,
        idle_minutes=30,
        to_down=True,
        execution_strategy=stale.execution_strategy,
        user_hash=stale.user_hash,
        workspace=stale.workspace,
        expected_cluster_hash=stale.cluster_hash,
        expected_generation=stale.generation,
        expected_states={stale.state},
    )
    assert replacement is not None
    monkeypatch.setattr(global_user_state, 'list_due_autodown_intents',
                        lambda now, limit, start_after: [stale])

    autodown.reconcile_autodown_intents(now=100)

    assert global_user_state.get_autodown_intent(
        stale.cluster_name) == (replacement)
    assert backend.teardown_calls == []


def test_cluster_hash_is_revalidated_after_teardown_claim(
        reconciler, monkeypatch):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.READY)
    real_cas = global_user_state.compare_and_swap_autodown_intent

    def claim_then_replace_cluster(**kwargs):
        transitioned = real_cas(**kwargs)
        if (transitioned and kwargs['new_state'] is
                global_user_state.AutodownIntentState.EXECUTING):
            _delete_cluster(engine, intent.cluster_name)
            _insert_cluster(engine, intent.cluster_name, 'replacement-hash')
        return transitioned

    monkeypatch.setattr(global_user_state, 'compare_and_swap_autodown_intent',
                        claim_then_replace_cluster)

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.CANCELLED
    replacement = global_user_state.get_cluster_from_name(
        intent.cluster_name, include_user_info=False, summary_response=True)
    assert replacement is not None
    assert replacement['cluster_hash'] == 'replacement-hash'
    assert backend.teardown_calls == []


def test_replacement_during_teardown_refresh_cancels_stale_intent(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.READY)

    def replace_during_internal_refresh(handle):
        _delete_cluster(engine, handle.cluster_name)
        _insert_cluster(engine, handle.cluster_name, 'replacement-hash')

    backend.teardown_effect = replace_during_internal_refresh

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.CANCELLED
    replacement = global_user_state.get_cluster_from_name(
        intent.cluster_name, include_user_info=False, summary_response=True)
    assert replacement is not None
    assert replacement['cluster_hash'] == 'replacement-hash'
    assert backend.teardown_expected_hashes == [intent.cluster_hash]


def test_duplicate_sweeps_call_teardown_at_most_once(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(engine,
                            state=global_user_state.AutodownIntentState.READY)
    teardown_started = threading.Event()
    allow_teardown = threading.Event()

    def teardown(handle):
        teardown_started.set()
        assert allow_teardown.wait(timeout=5)
        _delete_cluster(engine, handle.cluster_name)

    backend.teardown_effect = teardown
    first = threading.Thread(target=autodown.reconcile_autodown_intents,
                             kwargs={'now': 100})
    second = threading.Thread(target=autodown.reconcile_autodown_intents,
                              kwargs={'now': 100})

    first.start()
    assert teardown_started.wait(timeout=5)
    second.start()
    time.sleep(0.05)
    allow_teardown.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert backend.teardown_calls == [intent.cluster_name]


def test_polling_cursor_wraps_without_starving_stable_armed_intents(
        reconciler, monkeypatch):
    engine, backend, _ = reconciler
    intents = [
        _create_intent(engine,
                       name,
                       f'hash-{name}',
                       state=global_user_state.AutodownIntentState.ARMED)
        for name in ('alpha', 'beta', 'gamma')
    ]
    for intent in intents:
        backend.status_results[intent.cluster_name] = _status(
            intent, autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED)
    monkeypatch.setattr(autodown, '_polling_cursor', None, raising=False)

    autodown.reconcile_autodown_intents(now=100, batch_size=2)
    autodown.reconcile_autodown_intents(now=101, batch_size=2)

    assert backend.status_calls == ['alpha', 'beta', 'gamma', 'alpha']


def test_actionable_teardown_runs_before_slow_head_polling(
        reconciler, monkeypatch):
    engine, backend, _ = reconciler
    ready = _create_intent(engine,
                           'ready',
                           'hash-ready',
                           state=global_user_state.AutodownIntentState.READY)
    armed = _create_intent(engine,
                           'armed',
                           'hash-armed',
                           state=global_user_state.AutodownIntentState.ARMED)
    backend.status_results[armed.cluster_name] = _status(
        armed, autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED)
    call_order = []
    original_status = backend.get_durable_autodown_status

    def status(handle):
        call_order.append('poll')
        return original_status(handle)

    def teardown(handle):
        call_order.append('teardown')
        _delete_cluster(engine, handle.cluster_name)

    monkeypatch.setattr(backend, 'get_durable_autodown_status', status)
    backend.teardown_effect = teardown

    autodown.reconcile_autodown_intents(now=100)

    assert call_order == ['teardown', 'poll']
    current = global_user_state.get_autodown_intent(ready.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED


@pytest.mark.parametrize('target_state', [
    global_user_state.AutodownIntentState.READY,
    global_user_state.AutodownIntentState.EXECUTING,
])
def test_actionable_cursor_does_not_starve_teardown_behind_preparing(
        reconciler, monkeypatch, target_state):
    engine, backend, _ = reconciler
    for name in ('alpha', 'beta'):
        _create_intent(engine,
                       name,
                       f'hash-{name}',
                       state=global_user_state.AutodownIntentState.PREPARING)
    target = _create_intent(
        engine,
        'gamma',
        'hash-gamma',
        state=target_state,
    )
    backend.teardown_effect = lambda handle: _delete_cluster(
        engine, handle.cluster_name)
    monkeypatch.setattr(autodown, '_actionable_cursor', None, raising=False)

    autodown.reconcile_autodown_intents(now=100, batch_size=2)
    assert backend.teardown_calls == []

    autodown.reconcile_autodown_intents(now=101, batch_size=2)

    current = global_user_state.get_autodown_intent(target.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == ['gamma']


def test_one_intent_failure_does_not_block_other_intents(reconciler):
    engine, backend, _ = reconciler
    alpha = _create_intent(engine,
                           'alpha',
                           'hash-alpha',
                           state=global_user_state.AutodownIntentState.READY)
    beta = _create_intent(engine,
                          'beta',
                          'hash-beta',
                          state=global_user_state.AutodownIntentState.READY)

    def teardown(handle):
        if handle.cluster_name == alpha.cluster_name:
            raise RuntimeError('provider secret')
        _delete_cluster(engine, handle.cluster_name)

    backend.teardown_effect = teardown

    autodown.reconcile_autodown_intents(now=100)

    alpha_current = global_user_state.get_autodown_intent(alpha.cluster_name)
    beta_current = global_user_state.get_autodown_intent(beta.cluster_name)
    assert alpha_current is not None
    assert beta_current is not None
    assert alpha_current.state is (
        global_user_state.AutodownIntentState.RETRY_WAIT)
    assert beta_current.state is global_user_state.AutodownIntentState.SUCCEEDED
    assert backend.teardown_calls == ['alpha', 'beta']


def test_legacy_head_credentials_intent_is_never_server_torn_down(reconciler):
    engine, backend, _ = reconciler
    intent = _create_intent(
        engine,
        state=global_user_state.AutodownIntentState.READY,
        execution_strategy=(
            TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS.value))

    autodown.reconcile_autodown_intents(now=100)

    current = global_user_state.get_autodown_intent(intent.cluster_name)
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.READY
    assert backend.teardown_calls == []
