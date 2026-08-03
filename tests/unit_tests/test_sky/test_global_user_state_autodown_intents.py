"""Tests for durable, generation-fenced autodown intents."""

import concurrent.futures
import importlib
import threading

from alembic import command as alembic_command
import pytest
import sqlalchemy

from sky import global_user_state
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.skylet import constants
from sky.utils.db import db_utils
from sky.utils.db import migration_utils


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            # pylint: disable=protected-access
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )
    return global_user_state.initialize_and_get_db()


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


def _transition(intent, expected_states, new_state):
    return global_user_state.compare_and_swap_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
        expected_states=expected_states,
        new_state=new_state,
    )


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
    replacement = global_user_state.create_or_replace_autodown_intent(
        cluster_name='cluster',
        cluster_hash='new-hash',
        idle_minutes=30,
        to_down=False,
        execution_strategy=TeardownExecutionStrategy.SERVER_ONLY.value,
        user_hash='other-user',
        workspace='workspace-b',
    )

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


def test_concurrent_replacements_allocate_unique_generations(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    start_barrier = threading.Barrier(6)

    def create_concurrently(index):
        start_barrier.wait()
        return _create_intent(cluster_hash=f'hash-{index}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        intents = list(executor.map(create_concurrently, range(6)))

    assert sorted(intent.generation for intent in intents) == list(range(1, 7))
    current_intent = global_user_state.get_autodown_intent('cluster')
    assert current_intent is not None
    assert current_intent.generation == 6


def test_compare_and_swap_rejects_old_cluster_hash(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    old_intent = _create_intent(cluster_hash='old-hash')
    current_intent = _create_intent(cluster_hash='new-hash')

    changed = global_user_state.compare_and_swap_autodown_intent(
        cluster_name=current_intent.cluster_name,
        cluster_hash=old_intent.cluster_hash,
        generation=current_intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
        new_state=global_user_state.AutodownIntentState.ARMED,
    )

    assert changed is False
    assert global_user_state.get_autodown_intent('cluster') == current_intent


def test_compare_and_swap_rejects_stale_generation(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    stale_intent = _create_intent(cluster_hash='same-hash')
    current_intent = _create_intent(cluster_hash='same-hash')

    changed = global_user_state.compare_and_swap_autodown_intent(
        cluster_name=current_intent.cluster_name,
        cluster_hash=current_intent.cluster_hash,
        generation=stale_intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
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
            next_retry_at=next_retry_at,
            error=RuntimeError('temporary failure'),
        )

    due = global_user_state.list_due_autodown_intents(now=250)

    assert [intent.cluster_name for intent in due] == [
        'alpha',
        'charlie',
        'beta',
    ]


def test_cancellation_tombstone_survives_cluster_row_removal(
        tmp_path, monkeypatch):
    engine = _fresh_db(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(global_user_state.cluster_table.insert().values(
            name='cluster',
            cluster_hash='cluster-hash',
            handle=b'',
            status='INIT',
            metadata='{}',
        ))
    intent = _create_intent(cluster_hash='cluster-hash')

    assert global_user_state.cancel_autodown_intent(
        cluster_name=intent.cluster_name,
        cluster_hash=intent.cluster_hash,
        generation=intent.generation,
    ) is True
    global_user_state.remove_cluster('cluster', terminate=True)

    tombstone = global_user_state.get_autodown_intent('cluster')
    assert tombstone is not None
    assert tombstone.state is global_user_state.AutodownIntentState.CANCELLED
    assert global_user_state.get_cluster_from_name('cluster') is None


def test_retry_metadata_is_fenced_incremented_and_bounded(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    stale_intent = _create_intent(cluster_hash='old-hash')
    intent = _create_intent(cluster_hash='new-hash')

    assert global_user_state.record_autodown_intent_retry(
        cluster_name=intent.cluster_name,
        cluster_hash=stale_intent.cluster_hash,
        generation=intent.generation,
        expected_states={
            global_user_state.AutodownIntentState.CONFIGURING,
        },
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
        next_retry_at=300,
        error=ValueError('try again'),
    ) is True
    retried_again = global_user_state.get_autodown_intent('cluster')
    assert retried_again is not None
    assert retried_again.attempt_count == 2
    assert retried_again.next_retry_at == 300
    assert retried_again.last_error == 'ValueError: try again'


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
