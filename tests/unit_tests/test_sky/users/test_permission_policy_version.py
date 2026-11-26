"""The casbin policy is reloaded only when its stored version moved.

Every service here is built through `_lazy_initialize` on a database migrated
by the real global_user_state migrations, so the triggers are the real ones.
Each test runs on SQLite, and on PostgreSQL when SKYPILOT_TEST_PG_URL is set.
"""
# pylint: disable=protected-access,redefined-outer-name
import contextlib
import os
import subprocess
import sys
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
        write = threading.Thread(target=service._add_user_if_not_exists_no_lock,
                                 args=('newbie', 'admin'))
        write.start()
        # Unguarded, the write finishes here, into the model about to go.
        write.join(timeout=0.5)
        resume.set()
        write.join(timeout=10)
        for reader in readers:
            reader.join(timeout=10)
        assert service.roles_in_memory('newbie') == ['admin']
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
    """A write lock whose first acquire by `thread` lets `write` run first.

    Waits only briefly for it: a write that is made to wait for the reload
    finishes after the swap instead.
    """

    def __init__(self, real, thread, write):
        self.real, self.thread, self.write = real, thread, write
        self.writer = None

    def __enter__(self):
        if threading.current_thread().name == self.thread and self.write:
            self.writer = threading.Thread(target=self.write)
            self.write = None
            self.writer.start()
            self.writer.join(timeout=0.5)
        return self.real.__enter__()

    def __exit__(self, *args):
        return self.real.__exit__(*args)


def test_local_write_just_before_the_swap_lock_is_kept(new_service, engine,
                                                       monkeypatch):
    """A local write waits for the reload instead of landing in the old model.
    """
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'zed', 'user')  # Forces a reload.
    synced = service.enforcer
    lock = _WriteFirst(
        synced._wl, 'reloader',
        lambda: service._add_user_if_not_exists_no_lock('newbie', 'admin'))
    monkeypatch.setattr(synced, '_wl', lock)
    reloader = threading.Thread(target=service._load_policy_no_lock,
                                name='reloader')
    reloader.start()
    reloader.join(timeout=10)
    lock.writer.join(timeout=10)
    assert not reloader.is_alive() and not lock.writer.is_alive()

    assert service.roles_in_memory('newbie') == ['admin']


def test_slow_database_does_not_hold_the_write_lock(new_service, engine,
                                                    monkeypatch):
    """Readers, some on the event loop, never wait on the database."""
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    state = service._policy_version
    adapter = state.adapter
    original_load, original_read = adapter.load_policy, state.read
    built, release = threading.Event(), threading.Event()

    def load(model):
        original_load(model)
        if threading.current_thread().name == 'reloader':
            built.set()

    def read():
        if threading.current_thread().name == 'reloader' and built.is_set():
            release.wait(timeout=5)  # A slow database, after the build.
        return original_read()

    monkeypatch.setattr(adapter, 'load_policy', load)
    monkeypatch.setattr(state, 'read', read)
    reloader = threading.Thread(target=service._load_policy_no_lock,
                                name='reloader')
    reloader.start()
    assert built.wait(timeout=5)
    time.sleep(0.3)  # Lets the reloader reach the swap.
    started = time.monotonic()
    service.roles_in_memory('alice')
    elapsed = time.monotonic() - started
    release.set()
    reloader.join(timeout=10)

    assert elapsed < 0.5
    assert service.roles_in_memory('alice') == ['viewer']


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


# The background refresh: a poller publishes the stored version to a file, and
# each serving process's watcher reloads when it moved.


@pytest.fixture
def published(tmp_path, monkeypatch):
    """Points watchers at a file; returns a function that publishes to it."""
    path = str(tmp_path / 'policy_version')
    monkeypatch.setenv(permission._POLICY_VERSION_FILE_ENV, path)

    def _publish(value, age=0.0):
        permission._publish_policy_version(path, str(value))
        if age:
            with open(path, encoding='utf-8') as f:
                value, beat = f.read().split()
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f'{value} {float(beat) - age}\n')

    _publish.path = path
    return _publish


def _poller(service):
    service._poll_engine = permission._poller_engine(
        service._policy_version.engine)
    return service


_EXPORT = ('/users/export', 'GET')


@pytest.mark.parametrize('before,after', [('admin', 'user'), ('user', 'admin')])
def test_watcher_applies_a_role_changed_elsewhere(new_service, engine,
                                                  published, before, after):
    _execute(
        engine, 'INSERT INTO casbin_rule (ptype, v0, v1, v2) '
        f'VALUES (\'p\', \'user\', \'{_EXPORT[0]}\', \'{_EXPORT[1]}\')')
    a = new_service()
    a.update_role('alice', before)
    b = new_service()
    b.update_role('alice', after)
    # Nothing on the request path reloads: `a` still holds the old role.
    assert a.roles_in_memory('alice') == [before]
    assert a.check_endpoint_permission('alice', *_EXPORT) == (before == 'user')

    published(_poller(b)._poll_policy_version_once())
    a._watch_policy_once()

    assert a.roles_in_memory('alice') == [after]
    assert a.check_endpoint_permission('alice', *_EXPORT) == (after == 'user')


def test_watcher_reads_no_database_while_the_version_holds(
        new_service, engine, published):
    service = new_service()
    service.get_user_roles('alice')
    published(service._policy_version.loaded)
    with _count_statements(engine) as counts:
        service._watch_policy_once()
    assert counts == {'version': 0, 'load': 0}

    _grant(engine, 'alice', 'viewer')
    published(_stored_version(engine))
    with _count_statements(engine) as counts:
        service._watch_policy_once()
    assert counts['load'] == 1
    assert service.roles_in_memory('alice') == ['viewer']


def test_poller_publishes_a_number_or_why_not(new_service, engine):
    service = _poller(new_service())
    assert service._poll_policy_version_once() == str(_stored_version(engine))

    service._poll_engine = sqlalchemy.create_engine(
        'sqlite:////nonexistent-dir/state.db')
    with mock.patch.object(permission.logger, 'warning') as warning:
        assert service._poll_policy_version_once() == permission._READ_ERROR
        assert service._poll_policy_version_once() == permission._READ_ERROR
    warning.assert_called_once()

    service = _poller(new_service())
    if engine.dialect.name == 'sqlite':
        _execute(engine, 'DROP TRIGGER casbin_rule_bump_policy_version_insert')
        service = _poller(new_service())  # SQLite checks once, at build.
    else:
        _execute(
            engine, 'ALTER TABLE casbin_rule DISABLE TRIGGER '
            'casbin_rule_bump_policy_version')
    assert service._poll_policy_version_once() == permission._UNVERSIONED


def test_watcher_keeps_its_model_on_a_read_error(new_service, engine,
                                                 published):
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    published(permission._READ_ERROR)
    with _count_statements(engine) as counts:
        service._watch_policy_once()
    assert counts == {'version': 0, 'load': 0}
    assert service.roles_in_memory('alice') == []

    # Only the poller may be failing: past the stale bound, read it here.
    service._error_since -= permission._POLLER_STALE_SECONDS + 1
    service._watch_policy_once()
    assert service.roles_in_memory('alice') == ['viewer']

    # A recovered poller restarts the clock for the next stretch of errors.
    published(_stored_version(engine))
    service._watch_policy_once()
    _grant(engine, 'alice', 'admin')
    published(permission._READ_ERROR)
    service._watch_policy_once()
    assert service.roles_in_memory('alice') == ['viewer']


def test_watcher_reloads_an_unversioned_policy_once_a_minute(
        new_service, engine, published):
    service = new_service()
    service.get_user_roles('alice')
    _execute(engine, 'DELETE FROM rbac_policy_version')
    _grant(engine, 'alice', 'viewer')
    published(permission._UNVERSIONED)
    # Built off the write lock like any other reload, not loaded in place.
    in_place = mock.Mock(side_effect=AssertionError('in-place load'))
    with _count_statements(engine) as counts, mock.patch.object(
            permission.casbin.SyncedEnforcer, 'load_policy', in_place):
        service._watch_policy_once()
        service._watch_policy_once()
    assert counts['load'] == 1
    assert service.roles_in_memory('alice') == ['viewer']


def test_watcher_uses_the_published_version_if_its_own_read_fails(
        new_service, engine, published, monkeypatch):
    """A worker that cannot read the version converges on the poller's."""
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    published(_stored_version(engine))
    monkeypatch.setattr(service._policy_version, 'read', lambda: None)
    service._watch_policy_once()
    assert service.roles_in_memory('alice') == ['viewer']
    # Labelled with the published version, so the next tick has nothing to do.
    with _count_statements(engine) as counts:
        service._watch_policy_once()
    assert counts['load'] == 0


@pytest.mark.parametrize('missing', ['stale', 'no_file', 'no_env'])
def test_watcher_reads_the_database_without_a_live_poller(
        new_service, engine, published, monkeypatch, missing):
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    if missing == 'stale':
        published(_stored_version(engine),
                  age=permission._POLLER_STALE_SECONDS + 1)
    elif missing == 'no_env':
        monkeypatch.delenv(permission._POLICY_VERSION_FILE_ENV)
    with mock.patch.object(permission.logger, 'warning') as warning:
        service._watch_policy_once()
        service._watch_policy_once()
    warning.assert_called_once()
    assert service.roles_in_memory('alice') == ['viewer']

    monkeypatch.setenv(permission._POLICY_VERSION_FILE_ENV, published.path)
    published(_stored_version(engine))
    with mock.patch.object(permission.logger, 'info') as info:
        service._watch_policy_once()
    info.assert_called_once()


def test_watcher_survives_a_failed_reload(new_service, engine, published,
                                          monkeypatch):
    service = new_service()
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    published(_stored_version(engine))
    real = service._load_policy_no_lock
    monkeypatch.setattr(service, '_load_policy_no_lock',
                        mock.Mock(side_effect=RuntimeError('db down')))
    with mock.patch.object(permission.logger, 'warning') as warning:
        service._watch_policy_once()
        service._watch_policy_once()
    warning.assert_called_once()

    monkeypatch.setattr(service, '_load_policy_no_lock', real)
    with mock.patch.object(permission.logger, 'info') as info:
        service._watch_policy_once()
    info.assert_called_once()
    assert service.roles_in_memory('alice') == ['viewer']


def test_refresh_never_takes_the_policy_lock(new_service, engine, published,
                                             monkeypatch):
    service = _poller(new_service())
    service.get_user_roles('alice')
    _grant(engine, 'alice', 'viewer')
    monkeypatch.setattr(permission, '_policy_lock',
                        mock.Mock(side_effect=AssertionError('policy lock')))
    published(service._poll_policy_version_once())
    service._watch_policy_once()
    assert service.roles_in_memory('alice') == ['viewer']


def test_poller_gives_up_on_a_slow_database(new_service, engine, monkeypatch):
    if engine.dialect.name != 'postgresql':
        pytest.skip('statement_timeout is PostgreSQL only')
    monkeypatch.setattr(permission, '_POLLER_DB_TIMEOUT_SECONDS', 1)
    service = _poller(new_service())
    result = []
    with engine.connect() as holder:
        holder.execute(
            sqlalchemy.text('LOCK TABLE rbac_policy_version IN ACCESS '
                            'EXCLUSIVE MODE'))
        # In a thread, so a poll that never gives up fails here, not hangs.
        poll = threading.Thread(
            target=lambda: result.append(service._poll_policy_version_once()),
            daemon=True)
        poll.start()
        poll.join(timeout=10)
        holder.rollback()
    assert result == [permission._READ_ERROR]


def test_refresh_threads_start_once(new_service, engine, monkeypatch):
    service = new_service()
    thread = mock.Mock()
    monkeypatch.setattr(permission.threading, 'Thread', thread)
    with mock.patch.dict(os.environ):
        os.environ.pop(permission._POLICY_VERSION_FILE_ENV, None)
        for _ in range(2):
            service.start_policy_version_poller()
            service.start_policy_watcher()
        assert thread.call_count == 2

        path = os.environ[permission._POLICY_VERSION_FILE_ENV]
        assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
        assert permission._read_published_policy_version() == str(
            _stored_version(engine))


def test_import_starts_no_refresh_thread():
    code = ('import threading, sky.users.permission; '
            'assert not [t for t in threading.enumerate() '
            'if t.name.startswith("rbac-policy")]')
    subprocess.run([sys.executable, '-c', code], check=True, timeout=120)
