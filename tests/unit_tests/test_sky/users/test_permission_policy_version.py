"""The casbin policy is reloaded only when its stored version moved.

Every service here is built through `_lazy_initialize` on a database migrated
by the real global_user_state migrations, so the triggers are the real ones.
Each test runs on SQLite, and on PostgreSQL when SKYPILOT_TEST_PG_URL is set.
"""
# pylint: disable=protected-access,redefined-outer-name
import contextlib
import os
import threading
import time
from unittest import mock

import pytest
import sqlalchemy

from sky.users import permission
from sky.utils.db import migration_utils

_PG_URL = os.environ.get('SKYPILOT_TEST_PG_URL')
# Own schema, so other PostgreSQL tests resetting `public` do not interfere.
_PG_SCHEMA = 'rbac_policy_version_test'


def _pg_url():
    # Pin the driver: the same URL also serves psycopg2.connect() elsewhere.
    return sqlalchemy.engine.make_url(_PG_URL).set(
        drivername='postgresql+psycopg2')


def _migrate(engine):
    migration_utils.safe_alembic_upgrade(
        engine, migration_utils.GLOBAL_USER_STATE_DB_NAME,
        migration_utils.GLOBAL_USER_STATE_VERSION)


@pytest.fixture(params=[
    'sqlite',
    pytest.param('postgres',
                 marks=[
                     pytest.mark.skipif(
                         not _PG_URL, reason='SKYPILOT_TEST_PG_URL is not set'),
                     pytest.mark.xdist_group('rbac_policy_version_pg')
                 ])
])
def engine(request, tmp_path):
    """A migrated global_user_state database."""
    if request.param == 'sqlite':
        url = f'sqlite:///{tmp_path}/state.db'
    else:
        admin = sqlalchemy.create_engine(_pg_url())
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS {_PG_SCHEMA} CASCADE'))
            conn.execute(sqlalchemy.text(f'CREATE SCHEMA {_PG_SCHEMA}'))
        admin.dispose()
        url = _pg_url().update_query_dict(
            {'options': f'-csearch_path={_PG_SCHEMA}'})
    # Migrate first: an engine connected before can answer from a stale schema.
    _migrate(sqlalchemy.create_engine(url))
    engine = sqlalchemy.create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def new_service(engine, monkeypatch):
    """Builds services the way each API-server process does.

    Each call resets the shared instance first, so every service gets its own
    enforcer, as two processes would.
    """
    monkeypatch.setattr(permission.global_user_state, 'initialize_and_get_db',
                        lambda: engine)
    monkeypatch.setattr(permission, '_policy_lock', contextlib.nullcontext)
    monkeypatch.setattr(permission, '_enforcer_instance', None)

    def _new():
        permission._enforcer_instance = None
        service = permission.PermissionService()
        service._lazy_initialize()
        return service

    return _new


def _execute(engine, statement):
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(statement))


def _grant(engine, user, role):
    """A write by something other than PermissionService."""
    _execute(
        engine, f'INSERT INTO casbin_rule (ptype, v0, v1) '
        f'VALUES (\'g\', \'{user}\', \'{role}\')')


def _stored_version(engine):
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.text(
                'SELECT version FROM rbac_policy_version')).scalar()


@contextlib.contextmanager
def _count_statements(engine):
    """Counts version reads and policy loads issued on the engine."""
    counts = {'version': 0, 'load': 0}

    def _before(conn, cursor, statement, *args):  # pylint: disable=unused-argument
        if 'FROM rbac_policy_version' in statement:
            counts['version'] += 1
        elif 'FROM casbin_rule' in statement and 'SELECT' in statement:
            counts['load'] += 1

    sqlalchemy.event.listen(engine, 'before_cursor_execute', _before)
    try:
        yield counts
    finally:
        sqlalchemy.event.remove(engine, 'before_cursor_execute', _before)


def test_change_by_another_enforcer_is_seen(new_service):
    a, b = new_service(), new_service()
    assert a.enforcer is not b.enforcer
    assert b.get_user_roles('alice') == []

    a.update_role('alice', 'viewer')
    assert b.get_user_roles('alice') == ['viewer']
    a.update_role('alice', 'user')
    assert b.get_user_roles('alice') == ['user']


def test_write_during_the_load_is_seen_by_the_next_call(new_service, engine,
                                                        monkeypatch):
    service = new_service()
    service.get_user_roles('carol')
    _grant(engine, 'other', 'user')  # Forces the next call to reload.
    adapter = service._policy_version.adapter
    original = adapter.load_policy

    def load_then_write(model):
        original(model)
        monkeypatch.setattr(adapter, 'load_policy', original)
        # Commits after the rows were read, before the load is recorded.
        _grant(engine, 'carol', 'viewer')

    monkeypatch.setattr(adapter, 'load_policy', load_then_write)
    service.get_user_roles('carol')
    assert service.get_user_roles('carol') == ['viewer']


def test_foreign_sql_write_is_seen(new_service, engine):
    service = new_service()
    assert service.get_user_roles('dave') == []
    _grant(engine, 'dave', 'viewer')
    assert service.get_user_roles('dave') == ['viewer']


def test_unchanged_policy_is_not_loaded_again(new_service, engine):
    service = new_service()
    service.get_user_roles('alice')
    with _count_statements(engine) as counts:
        service.get_user_roles('alice')
    assert counts == {'version': 1, 'load': 0}


@pytest.mark.parametrize('break_version', [
    'DELETE FROM rbac_policy_version',
    'ALTER TABLE rbac_policy_version RENAME TO rbac_policy_version_gone',
])
def test_unreadable_version_loads_every_time(new_service, engine,
                                             break_version):
    service = new_service()
    service.get_user_roles('alice')  # Holds a real version from here.
    _execute(engine, break_version)
    with _count_statements(engine) as counts:
        service.get_user_roles('alice')
        service.get_user_roles('alice')
    assert counts['load'] == 2


def test_reloads_never_record_a_version_newer_than_the_model(
        new_service, engine, monkeypatch):
    """An older build is never recorded as current.
    """
    service = new_service()
    service.get_user_roles('alice')
    state = service._policy_version
    a_swapping, b_swapped, release_b = (threading.Event(), threading.Event(),
                                        threading.Event())
    real_swap = permission._swap_enforcer

    def swap(synced, fresh, *args):
        if threading.current_thread().name == 'older':
            a_swapping.set()
            # Without the reload lock, the newer reload swaps first.
            b_swapped.wait(timeout=1)
            real_swap(synced, fresh, *args)
        else:
            real_swap(synced, fresh, *args)
            b_swapped.set()
            # Without the lock, the older reload swaps and records in here.
            release_b.wait(timeout=5)

    monkeypatch.setattr(permission, '_swap_enforcer', swap)
    _grant(engine, 'x', 'user')
    older = threading.Thread(target=service._load_policy_no_lock, name='older')
    older.start()
    assert a_swapping.wait(timeout=5)
    _grant(engine, 'y', 'viewer')
    newer = threading.Thread(target=service._load_policy_no_lock, name='newer')
    newer.start()
    older.join(timeout=10)
    release_b.set()
    newer.join(timeout=10)

    assert state.loaded == _stored_version(engine)
    assert service.enforcer.get_roles_for_user('y') == ['viewer']


@pytest.mark.parametrize('writer', ['foreign_reload', 'own_update_role'])
def test_local_write_during_a_reload_is_kept(new_service, engine, monkeypatch,
                                             writer):
    """A write this process makes while another thread reloads stays in memory.

    Paths that never reload, such as `roles_in_memory`, read that model.
    """
    service = new_service()
    service.update_role('alice', 'user')
    service.get_user_roles('alice')
    adapter = service._policy_version.adapter
    original = adapter.load_policy
    paused, resume = threading.Event(), threading.Event()
    readers = []

    def paused_load(model):
        original(model)
        if threading.current_thread().name == 'reader':
            paused.set()
            resume.wait(timeout=5)

    def start_reader():
        reader = threading.Thread(target=service.get_user_roles,
                                  args=('zed',),
                                  name='reader')
        readers.append(reader)
        reader.start()
        assert paused.wait(timeout=5)

    monkeypatch.setattr(adapter, 'load_policy', paused_load)
    if writer == 'foreign_reload':
        # Another writer moves the version; the local write lands mid-reload.
        _grant(engine, 'zed', 'user')
        start_reader()
        service.enforcer.remove_filtered_grouping_policy(0, 'alice')
        service.enforcer.add_grouping_policy('alice', 'admin')
        resume.set()
    else:
        # The first commit of update_role starts a reload before its second.
        synced_class = permission.casbin.SyncedEnforcer
        real_remove = synced_class.remove_filtered_grouping_policy

        def remove_then_reload(self, *args):
            removed = real_remove(self, *args)
            start_reader()
            threading.Timer(0.2, resume.set).start()
            return removed

        monkeypatch.setattr(synced_class, 'remove_filtered_grouping_policy',
                            remove_then_reload)
        service.update_role('alice', 'admin')
    for reader in readers:
        reader.join(timeout=10)

    assert service.roles_in_memory('alice') == ['admin']


class _WriteFirst:
    """A write lock whose first acquire by `thread` lets `write` run first."""

    def __init__(self, real, thread, write):
        self.real, self.thread, self.write = real, thread, write

    def __enter__(self):
        if threading.current_thread().name == self.thread and self.write:
            writer = threading.Thread(target=self.write)
            self.write = None
            writer.start()
            writer.join(timeout=10)
        return self.real.__enter__()

    def __exit__(self, *args):
        return self.real.__exit__(*args)


def test_local_write_just_before_the_swap_lock_is_kept(new_service, engine,
                                                       monkeypatch):
    """The swap decides on a version read while it holds the write lock."""
    service = new_service()
    service.update_role('alice', 'user')
    service.get_user_roles('alice')
    _grant(engine, 'zed', 'user')  # Forces a reload.
    synced = service.enforcer

    def demote():
        synced.remove_filtered_grouping_policy(0, 'alice')
        synced.add_grouping_policy('alice', 'admin')

    monkeypatch.setattr(synced, '_wl',
                        _WriteFirst(synced._wl, 'reloader', demote))
    reloader = threading.Thread(target=service._load_policy_no_lock,
                                name='reloader')
    reloader.start()
    reloader.join(timeout=10)
    assert not reloader.is_alive()

    assert service.roles_in_memory('alice') == ['admin']


def test_write_during_the_in_place_load_is_seen_next(new_service, engine,
                                                     monkeypatch):
    """The in-place load records the version read before it, not after."""
    service = new_service()
    service.get_user_roles('carol')
    _grant(engine, 'zed', 'user')  # Forces a reload.
    adapter = service._policy_version.adapter
    original = adapter.load_policy
    after_load = [
        # During the build: sends the swap down the in-place branch.
        lambda: _grant(engine, 'other', 'user'),
        # During the in-place load, after its rows were read.
        lambda: _grant(engine, 'carol', 'viewer'),
    ]

    def load_then_write(model):
        original(model)
        if after_load:
            after_load.pop(0)()

    monkeypatch.setattr(adapter, 'load_policy', load_then_write)
    service._load_policy_no_lock()
    assert not after_load
    assert service.get_user_roles('carol') == ['viewer']


def test_concurrent_stale_readers_load_once(new_service, engine, monkeypatch):
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'x', 'user')
    adapter = service._policy_version.adapter
    original = adapter.load_policy
    loads, loading = [], threading.Event()

    def slow_load(model):
        loads.append(threading.current_thread().name)
        loading.set()
        time.sleep(0.3)
        original(model)

    monkeypatch.setattr(adapter, 'load_policy', slow_load)
    first = threading.Thread(target=service._load_policy_no_lock, name='a')
    second = threading.Thread(target=service._load_policy_no_lock, name='b')
    first.start()
    assert loading.wait(timeout=5)
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert loads == ['a']


def test_unknown_casbin_internals_reload_in_place(new_service, engine,
                                                  monkeypatch):
    """If casbin renames its inner enforcer, reload as before, never skip."""
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'frank', 'viewer')
    synced = service.enforcer
    monkeypatch.delattr(synced, '_e')
    reload = mock.Mock()
    monkeypatch.setattr(synced, 'load_policy', reload)

    service._load_policy_no_lock()

    reload.assert_called_once_with()


@pytest.mark.parametrize('disable', ['DISABLE', 'ENABLE REPLICA'])
def test_disabled_trigger_reloads_every_time(new_service, engine, disable):
    is_sqlite = engine.dialect.name == 'sqlite'
    if is_sqlite and disable != 'DISABLE':
        pytest.skip('SQLite has no replica-only triggers')
    with mock.patch.object(permission.logger, 'warning') as warning:
        if is_sqlite:
            # SQLite checks its triggers once, when the enforcer is built.
            _execute(engine,
                     'DROP TRIGGER casbin_rule_bump_policy_version_insert')
        service = new_service()
        service.get_user_roles('alice')
        if not is_sqlite:
            _execute(
                engine, f'ALTER TABLE casbin_rule {disable} TRIGGER '
                'casbin_rule_bump_policy_version')
        with _count_statements(engine) as counts:
            service.get_user_roles('alice')
            service.get_user_roles('alice')
        assert counts['load'] == 2
        # A write the trigger does not count is still seen.
        _grant(engine, 'alice', 'viewer')
        assert service.get_user_roles('alice') == ['viewer']
    warning.assert_called_once()


def test_reload_does_not_block_readers(new_service, engine, monkeypatch):
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'user')
    adapter = service._policy_version.adapter
    original = adapter.load_policy
    loading = threading.Event()

    def slow_load(model):
        original(model)
        loading.set()
        time.sleep(1)

    monkeypatch.setattr(adapter, 'load_policy', slow_load)
    reload = threading.Thread(target=service._load_policy_no_lock)
    reload.start()
    assert loading.wait(timeout=5)
    started = time.monotonic()
    service.enforcer.get_roles_for_user('alice')
    service.enforcer.enforce('alice', '/x', 'GET')
    elapsed = time.monotonic() - started
    reload.join(timeout=10)
    assert elapsed < 0.5


def test_swapped_enforcer_serves_every_read(new_service, engine):
    service = new_service()
    service.get_user_roles('erin')
    _grant(engine, 'erin', 'viewer')
    _execute(
        engine, 'INSERT INTO casbin_rule (ptype, v0, v1, v2) '
        'VALUES (\'p\', \'viewer\', \'/swapped\', \'GET\')')

    service._load_policy_no_lock()

    enforcer = service.enforcer
    assert enforcer.enforce('erin', '/swapped', 'GET')
    assert enforcer.get_roles_for_user('erin') == ['viewer']
    assert ['viewer', '/swapped', 'GET'] in enforcer.get_policy()
