"""Tests for durable, generation-fenced autodown intents."""

import concurrent.futures
import importlib
import threading
from unittest import mock

from alembic import command as alembic_command
import pytest
import sqlalchemy
from sqlalchemy.dialects import postgresql

from sky import global_user_state
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.skylet import constants
from sky.utils.db import db_utils
from sky.utils.db import migration_utils


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


def _create_intent(cluster_name: str = 'cluster', cluster_hash: str = 'hash'):
    return global_user_state.create_or_replace_autodown_intent(
        cluster_name=cluster_name,
        cluster_hash=cluster_hash,
        idle_minutes=15,
        to_down=True,
        execution_strategy=(
            TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value),
        user_hash='user-hash',
        workspace='workspace-a',
    )


def _replace_intent(
        intent,
        cluster_hash: str = 'replacement-hash',
        idle_minutes: int = 15,
        to_down: bool = True,
        execution_strategy: str = (
            TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value),
        user_hash: str = 'user-hash',
        workspace: str = 'workspace-a'):
    return global_user_state.create_or_replace_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=cluster_hash,
        idle_minutes=idle_minutes,
        to_down=to_down,
        execution_strategy=execution_strategy,
        user_hash=user_hash,
        workspace=workspace,
        expected_cluster_hash=intent.cluster_hash,
        expected_generation=intent.generation,
        expected_states={intent.state},
    )


def _transition(intent, expected_states, new_state):
    return global_user_state.compare_and_swap_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states=expected_states,
        expected_attempt_count=intent.attempt_count,
        new_state=new_state,
    )


def _row_count(engine, table):
    with engine.connect() as connection:
        return connection.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()).select_from(table)).scalar()


def _schema_version(engine):
    with engine.connect() as connection:
        return connection.execute(
            sqlalchemy.text(
                'SELECT version_num FROM alembic_version_state_db')).scalar()


def test_fresh_db_uses_the_patched_database_manager(tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)

    assert global_user_state.initialize_and_get_db.__self__ is (
        global_user_state._db_manager)
    assert global_user_state._db_manager.get_engine() is engine


def test_intent_states_are_explicit_and_stable():
    assert {state.value for state in global_user_state.AutodownIntentState} == {
        'CONFIGURING',
        'ARMED',
        'PREPARING',
        'READY',
        'EXECUTING',
        'RETRY_WAIT',
        'SUCCEEDED',
        'CANCELLED',
    }


def test_create_replaces_intent_and_increments_generation(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    first = _create_intent(cluster_hash='old-hash')
    assert first is not None
    assert _create_intent(cluster_hash='unfenced-hash') is None
    assert global_user_state.get_autodown_intent('cluster') == first

    replacement = _replace_intent(
        first,
        cluster_hash='new-hash',
        idle_minutes=30,
        to_down=False,
        execution_strategy=TeardownExecutionStrategy.SERVER_ONLY.value,
        user_hash='other-user',
        workspace='workspace-b',
    )

    assert replacement is not None
    assert first.generation == 1
    assert replacement.generation == 2
    assert replacement.cluster_hash == 'new-hash'
    assert replacement.state is (
        global_user_state.AutodownIntentState.CONFIGURING)
    assert replacement.idle_minutes == 30
    assert replacement.to_down is False
    assert replacement.execution_strategy == 'server_only'
    assert replacement.user_hash == 'other-user'
    assert replacement.workspace == 'workspace-b'
    assert replacement.attempt_count == 0
    assert replacement.next_retry_at is None
    assert replacement.last_error is None
    assert isinstance(replacement.created_at, int)
    assert replacement.updated_at == replacement.created_at
    assert global_user_state.get_autodown_intent('cluster') == replacement


def test_replace_requires_a_complete_expected_current_fence(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    current = _create_intent()
    assert current is not None

    with pytest.raises(ValueError, match='expected-current fence'):
        global_user_state.create_or_replace_autodown_intent(
            cluster_name=current.cluster_name,
            cluster_hash='new-hash',
            idle_minutes=15,
            to_down=True,
            execution_strategy=(
                TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK.value),
            user_hash='user-hash',
            workspace='workspace-a',
            expected_cluster_hash=current.cluster_hash,
        )

    assert global_user_state.get_autodown_intent('cluster') == current


def test_concurrent_first_insert_is_insert_if_absent(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    start_barrier = threading.Barrier(6)

    def create_concurrently(index):
        start_barrier.wait()
        return _create_intent(cluster_hash=f'hash-{index}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        intents = list(executor.map(create_concurrently, range(6)))

    created = [intent for intent in intents if intent is not None]
    assert len(created) == 1
    assert created[0].generation == 1
    current_intent = global_user_state.get_autodown_intent('cluster')
    assert current_intent is not None
    assert current_intent == created[0]


def test_get_autodown_intents_batches_names_and_omits_missing(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    alpha = _create_intent('alpha', 'hash-alpha')
    beta = _create_intent('beta', 'hash-beta')
    assert alpha is not None
    assert beta is not None
    monkeypatch.setattr(global_user_state,
                        '_AUTODOWN_INTENT_IN_QUERY_CHUNK_SIZE', 1)

    intents = global_user_state.get_autodown_intents(
        ['beta', 'missing', 'alpha'])

    assert intents == {'alpha': alpha, 'beta': beta}


def test_postgres_first_insert_uses_returned_row_not_rowcount():
    engine = mock.Mock()
    engine.dialect.name = 'postgresql'
    session = mock.Mock()
    result = mock.Mock()
    result.rowcount = -1
    result.scalar_one_or_none.return_value = 'cluster'
    session.execute.return_value = result

    inserted = global_user_state._insert_autodown_intent_if_absent(
        session,
        engine,
        {
            'cluster_name': 'cluster',
            'cluster_hash': 'hash',
            'generation': 1,
            'state': global_user_state.AutodownIntentState.CONFIGURING.value,
            'idle_minutes': 15,
            'to_down': 1,
            'execution_strategy': TeardownExecutionStrategy.SERVER_ONLY.value,
            'user_hash': None,
            'workspace': None,
            'attempt_count': 0,
            'next_retry_at': None,
            'last_error': None,
            'created_at': 1,
            'updated_at': 1,
        },
    )

    assert inserted is True
    statement = session.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert 'ON CONFLICT' in compiled
    assert 'RETURNING' in compiled


def test_concurrent_replacements_accept_only_one_expected_generation(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    current = _create_intent()
    assert current is not None
    start_barrier = threading.Barrier(6)

    def replace_concurrently(index):
        start_barrier.wait()
        return _replace_intent(current, cluster_hash=f'hash-{index}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        replacements = list(executor.map(replace_concurrently, range(6)))

    accepted = [intent for intent in replacements if intent is not None]
    assert len(accepted) == 1
    assert accepted[0].generation == 2
    assert global_user_state.get_autodown_intent('cluster') == accepted[0]


def test_delayed_replacement_cannot_overwrite_newer_or_cancelled_intent(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    original = _create_intent(cluster_hash='old-hash')
    assert original is not None
    current = _replace_intent(original, cluster_hash='new-hash')
    assert current is not None

    assert _replace_intent(original, cluster_hash='delayed-hash') is None
    assert global_user_state.cancel_autodown_intent(
        cluster_name=current.cluster_name,
        cluster_hash=current.cluster_hash,
        generation=current.generation,
        expected_attempt_count=current.attempt_count,
    )
    cancelled = global_user_state.get_autodown_intent('cluster')
    assert cancelled is not None

    assert _replace_intent(
        current,
        cluster_hash='after-cancellation-hash',
    ) is None
    assert global_user_state.get_autodown_intent('cluster') == cancelled


def test_restore_claimed_predecessor_replaces_only_its_configuring_successor(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    configuring = _create_intent(cluster_hash='cluster-hash')
    assert configuring is not None
    assert _transition(
        configuring,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.ARMED,
    )
    predecessor = global_user_state.get_autodown_intent('cluster')
    assert predecessor is not None
    replacement = _replace_intent(predecessor,
                                  cluster_hash='cluster-hash',
                                  idle_minutes=-1,
                                  to_down=False)
    assert replacement is not None

    assert global_user_state.restore_predecessor_autodown_intent(
        replacement,
        predecessor,
        global_user_state.AutodownIntentState.PREPARING,
    )

    restored = global_user_state.get_autodown_intent('cluster')
    assert restored is not None
    assert restored.generation == predecessor.generation
    assert restored.state is global_user_state.AutodownIntentState.PREPARING
    assert restored.idle_minutes == predecessor.idle_minutes
    assert restored.to_down is predecessor.to_down
    assert restored.execution_strategy == predecessor.execution_strategy
    assert not global_user_state.restore_predecessor_autodown_intent(
        replacement,
        predecessor,
        global_user_state.AutodownIntentState.PREPARING,
    )
    assert global_user_state.get_autodown_intent('cluster') == restored


def test_compare_and_swap_rejects_old_cluster_hash(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    old_intent = _create_intent(cluster_hash='old-hash')
    assert old_intent is not None
    current_intent = _replace_intent(old_intent, cluster_hash='new-hash')
    assert current_intent is not None

    changed = global_user_state.compare_and_swap_autodown_intent(
        cluster_name=current_intent.cluster_name,
        cluster_hash=old_intent.cluster_hash,
        generation=current_intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        expected_attempt_count=current_intent.attempt_count,
        new_state=global_user_state.AutodownIntentState.ARMED,
    )

    assert changed is False
    assert global_user_state.get_autodown_intent('cluster') == current_intent


def test_compare_and_swap_rejects_stale_generation(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    stale_intent = _create_intent(cluster_hash='same-hash')
    assert stale_intent is not None
    current_intent = _replace_intent(stale_intent, cluster_hash='same-hash')
    assert current_intent is not None

    changed = global_user_state.compare_and_swap_autodown_intent(
        cluster_name=current_intent.cluster_name,
        cluster_hash=current_intent.cluster_hash,
        generation=stale_intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        expected_attempt_count=current_intent.attempt_count,
        new_state=global_user_state.AutodownIntentState.ARMED,
    )

    assert changed is False
    assert global_user_state.get_autodown_intent('cluster') == current_intent


def test_compare_and_swap_requires_expected_state(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    intent = _create_intent()

    assert _transition(
        intent,
        {global_user_state.AutodownIntentState.ARMED},
        global_user_state.AutodownIntentState.READY,
    ) is False
    assert _transition(
        intent,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.ARMED,
    ) is True
    armed_intent = global_user_state.get_autodown_intent('cluster')
    assert armed_intent is not None
    assert armed_intent.state is global_user_state.AutodownIntentState.ARMED


def test_due_actionable_intents_are_filtered_and_deterministic(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    alpha = _create_intent('alpha', 'hash-alpha')
    charlie = _create_intent('charlie', 'hash-charlie')
    beta = _create_intent('beta', 'hash-beta')
    future = _create_intent('future', 'hash-future')
    armed = _create_intent('armed', 'hash-armed')
    terminal = _create_intent('terminal', 'hash-terminal')

    for intent in (alpha, charlie):
        assert _transition(
            intent,
            {global_user_state.AutodownIntentState.CONFIGURING},
            global_user_state.AutodownIntentState.PREPARING,
        )
    assert _transition(
        armed,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.ARMED,
    )
    assert _transition(
        terminal,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.SUCCEEDED,
    )
    for intent, next_retry_at in ((beta, 200), (future, 300)):
        assert global_user_state.record_autodown_intent_retry(
            cluster_name=intent.cluster_name,
            cluster_hash=intent.cluster_hash,
            generation=intent.generation,
            expected_states={
                global_user_state.AutodownIntentState.CONFIGURING,
            },
            expected_attempt_count=0,
            next_retry_at=next_retry_at,
            error=RuntimeError('temporary failure'),
        )

    due = global_user_state.list_due_autodown_intents(now=250)

    assert [intent.cluster_name for intent in due] == [
        'alpha',
        'charlie',
        'beta',
    ]
    wrapped = global_user_state.list_due_autodown_intents(
        now=250, limit=2, start_after=(0, 'charlie'))
    assert [intent.cluster_name for intent in wrapped] == ['beta', 'alpha']


def test_polling_intents_are_bounded_filtered_and_deterministic(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    charlie = _create_intent('charlie', 'hash-charlie')
    alpha = _create_intent('alpha', 'hash-alpha')
    beta = _create_intent('beta', 'hash-beta')
    ready = _create_intent('ready', 'hash-ready')
    terminal = _create_intent('terminal', 'hash-terminal')

    assert _transition(
        beta,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.ARMED,
    )
    assert _transition(
        ready,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.READY,
    )
    assert _transition(
        terminal,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.CANCELLED,
    )

    polling = global_user_state.list_polling_autodown_intents(limit=2)

    assert [intent.cluster_name for intent in polling] == ['alpha', 'beta']
    assert [intent.state for intent in polling] == [
        global_user_state.AutodownIntentState.CONFIGURING,
        global_user_state.AutodownIntentState.ARMED,
    ]
    assert charlie.cluster_name not in {
        intent.cluster_name for intent in polling
    }
    wrapped = global_user_state.list_polling_autodown_intents(
        limit=2, start_after='beta')
    assert [intent.cluster_name for intent in wrapped] == ['charlie', 'alpha']


def test_retry_wait_without_deadline_is_not_due(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    intent = _create_intent()
    assert intent is not None
    assert _transition(
        intent,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.RETRY_WAIT,
    )

    assert global_user_state.list_due_autodown_intents(now=100) == []


def test_cancellation_tombstone_survives_cluster_row_removal(
        tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)
    assert global_user_state._db_manager.get_engine() is engine
    with engine.begin() as connection:
        connection.execute(global_user_state.cluster_table.insert().values(
            name='cluster',
            cluster_hash='cluster-hash',
            handle=b'',
            status='INIT',
            metadata='{}',
        ))
    intent = _create_intent(cluster_hash='cluster-hash')
    assert intent is not None
    assert _row_count(engine, global_user_state.cluster_table) == 1
    assert _row_count(engine, global_user_state.autodown_intent_table) == 1

    assert global_user_state.cancel_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_attempt_count=intent.attempt_count,
    ) is True
    global_user_state.remove_cluster('cluster', terminate=True)

    tombstone = global_user_state.get_autodown_intent('cluster')
    assert tombstone is not None
    assert tombstone.state is global_user_state.AutodownIntentState.CANCELLED
    assert _row_count(engine, global_user_state.cluster_table) == 0
    assert _row_count(engine, global_user_state.autodown_intent_table) == 1


def test_cluster_autostop_update_requires_matching_hash(tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(global_user_state.cluster_table.insert().values(
            name='cluster',
            cluster_hash='current-hash',
            handle=b'',
            status='INIT',
            metadata='{}',
            autostop=-1,
            to_down=0,
        ))

    assert not global_user_state.set_cluster_autostop_value_if_hash_matches(
        'cluster', 'stale-hash', 15, True)
    with engine.connect() as connection:
        row = connection.execute(
            sqlalchemy.select(global_user_state.cluster_table).where(
                global_user_state.cluster_table.c.name == 'cluster')).one()
    assert (row.autostop, row.to_down) == (-1, 0)

    assert global_user_state.set_cluster_autostop_value_if_hash_matches(
        'cluster', 'current-hash', 15, True)
    with engine.connect() as connection:
        row = connection.execute(
            sqlalchemy.select(global_user_state.cluster_table).where(
                global_user_state.cluster_table.c.name == 'cluster')).one()
    assert (row.autostop, row.to_down) == (15, 1)


def test_retry_metadata_is_fenced_incremented_and_bounded(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    stale_intent = _create_intent(cluster_hash='old-hash')
    assert stale_intent is not None
    intent = _replace_intent(stale_intent, cluster_hash='new-hash')
    assert intent is not None

    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=stale_intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        expected_attempt_count=0,
        next_retry_at=100,
        error=RuntimeError('stale failure'),
    ) is False
    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        expected_attempt_count=0,
        next_retry_at=200,
        error=RuntimeError('x' * 2000 + '\nsecond line must not persist'),
    ) is True

    retried = global_user_state.get_autodown_intent('cluster')
    assert retried is not None
    assert retried.state is global_user_state.AutodownIntentState.RETRY_WAIT
    assert retried.attempt_count == 1
    assert retried.next_retry_at == 200
    assert retried.last_error.startswith('RuntimeError: ')
    assert len(retried.last_error) <= 1024
    assert '\n' not in retried.last_error

    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.RETRY_WAIT,
        },
        expected_attempt_count=1,
        next_retry_at=300,
        error=ValueError('try again'),
    ) is True
    retried_again = global_user_state.get_autodown_intent('cluster')
    assert retried_again is not None
    assert retried_again.attempt_count == 2
    assert retried_again.next_retry_at == 300
    assert retried_again.last_error == 'ValueError: try again'


def test_retry_same_observed_attempt_can_only_be_recorded_once(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    intent = _create_intent()
    assert intent is not None
    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        expected_attempt_count=0,
        next_retry_at=100,
        error=RuntimeError('initial failure'),
    )

    first_writer_changed = global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.RETRY_WAIT,
        },
        expected_attempt_count=1,
        next_retry_at=200,
        error=RuntimeError('first writer'),
    )
    second_writer_changed = global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.RETRY_WAIT,
        },
        expected_attempt_count=1,
        next_retry_at=300,
        error=RuntimeError('second writer'),
    )

    assert first_writer_changed is True
    assert second_writer_changed is False
    retried = global_user_state.get_autodown_intent('cluster')
    assert retried is not None
    assert retried.attempt_count == 2
    assert retried.next_retry_at == 200
    assert retried.last_error == 'RuntimeError: first writer'


def test_state_claim_rejects_stale_retry_attempt(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    intent = _create_intent()
    assert intent is not None
    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states={intent.state},
        expected_attempt_count=0,
        next_retry_at=100,
        error=RuntimeError('attempt one'),
    )
    observed = global_user_state.get_autodown_intent('cluster')
    assert observed is not None
    assert observed.attempt_count == 1
    assert global_user_state.record_autodown_intent_retry(
        cluster_name=observed.cluster_name,
        cluster_hash=observed.cluster_hash,
        generation=observed.generation,
        expected_states={observed.state},
        expected_attempt_count=observed.attempt_count,
        next_retry_at=200,
        error=RuntimeError('attempt two'),
    )

    assert global_user_state.compare_and_swap_autodown_intent(
        cluster_name=observed.cluster_name,
        cluster_hash=observed.cluster_hash,
        generation=observed.generation,
        expected_states={
            global_user_state.AutodownIntentState.RETRY_WAIT,
        },
        expected_attempt_count=observed.attempt_count,
        new_state=global_user_state.AutodownIntentState.PREPARING,
    ) is False
    current = global_user_state.get_autodown_intent('cluster')
    assert current is not None
    assert current.state is global_user_state.AutodownIntentState.RETRY_WAIT
    assert current.attempt_count == 2
    assert current.next_retry_at == 200


def test_leaving_retry_wait_clears_retry_metadata_and_orders_immediately(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    retrying = _create_intent('cluster', 'hash-cluster')
    other = _create_intent('zzz', 'hash-zzz')
    assert retrying is not None
    assert other is not None
    assert global_user_state.record_autodown_intent_retry(
        cluster_name=retrying.cluster_name,
        cluster_hash=retrying.cluster_hash,
        generation=retrying.generation,
        expected_states={retrying.state},
        expected_attempt_count=retrying.attempt_count,
        next_retry_at=300,
        error=RuntimeError('temporary'),
    )
    retrying = global_user_state.get_autodown_intent('cluster')
    assert retrying is not None
    assert _transition(
        retrying,
        {global_user_state.AutodownIntentState.RETRY_WAIT},
        global_user_state.AutodownIntentState.PREPARING,
    )
    assert _transition(
        other,
        {global_user_state.AutodownIntentState.CONFIGURING},
        global_user_state.AutodownIntentState.PREPARING,
    )

    claimed = global_user_state.get_autodown_intent('cluster')
    assert claimed is not None
    assert claimed.next_retry_at is None
    assert claimed.last_error is None
    assert [
        intent.cluster_name
        for intent in global_user_state.list_due_autodown_intents(now=1000,
                                                                  limit=2)
    ] == [
        'cluster',
        'zzz',
    ]


def test_invalid_states_are_rejected_before_writing(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    intent = _create_intent()

    with pytest.raises(ValueError, match='BROKEN'):
        global_user_state.compare_and_swap_autodown_intent(
            cluster_name=intent.cluster_name,
            cluster_hash=intent.cluster_hash,
            generation=intent.generation,
            expected_states={
                global_user_state.AutodownIntentState.CONFIGURING,
            },
            expected_attempt_count=intent.attempt_count,
            new_state='BROKEN',
        )

    assert global_user_state.get_autodown_intent('cluster') == intent


def test_migration_021_schema_version_and_downgrade(tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)
    migration_module = importlib.import_module(
        'sky.schemas.db.global_user_state.021_add_autodown_intents')

    assert migration_utils.GLOBAL_USER_STATE_VERSION == '021'
    assert migration_module.revision == '021'
    assert migration_module.down_revision == '020'
    inspector = sqlalchemy.inspect(engine)
    assert 'autodown_intents' in inspector.get_table_names()
    assert inspector.get_pk_constraint(
        'autodown_intents')['constrained_columns'] == ['cluster_name']
    assert inspector.get_foreign_keys('autodown_intents') == []
    assert {
        column['name'] for column in inspector.get_columns('autodown_intents')
    } == {
        'cluster_name',
        'cluster_hash',
        'generation',
        'state',
        'idle_minutes',
        'to_down',
        'execution_strategy',
        'user_hash',
        'workspace',
        'attempt_count',
        'next_retry_at',
        'last_error',
        'created_at',
        'updated_at',
    }

    alembic_config = migration_utils.get_alembic_config(
        engine, migration_utils.GLOBAL_USER_STATE_DB_NAME)
    alembic_command.downgrade(alembic_config, '020')
    assert 'autodown_intents' not in sqlalchemy.inspect(
        engine).get_table_names()

    assert _schema_version(engine) == '020'
    alembic_command.upgrade(alembic_config, '021')
    assert 'autodown_intents' in sqlalchemy.inspect(engine).get_table_names()
    assert _schema_version(engine) == '021'
