"""Multi-row policy writes commit once, and no reader sees one half done.

Each test runs on SQLite, and on PostgreSQL when SKYPILOT_TEST_PG_URL is set.
"""
# pylint: disable=protected-access,redefined-outer-name
import contextlib
import copy
import os
import time
from unittest import mock

import casbin
import pytest
import sqlalchemy
import sqlalchemy_adapter

from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.users import permission
from sky.utils.db import migration_utils
from sky.workspaces import core

_PG_URL = os.environ.get('SKYPILOT_TEST_PG_URL')
_PG_SCHEMA = 'rbac_policy_writes_test'


@pytest.fixture(params=[
    'sqlite',
    pytest.param('postgres',
                 marks=[
                     pytest.mark.skipif(
                         not _PG_URL, reason='SKYPILOT_TEST_PG_URL is not set'),
                     pytest.mark.xdist_group('rbac_policy_writes_pg')
                 ])
])
def engine(request, tmp_path):
    """A global_user_state database migrated by the real migrations."""
    if request.param == 'sqlite':
        url = sqlalchemy.engine.make_url(f'sqlite:///{tmp_path}/state.db')
    else:
        pg_url = sqlalchemy.engine.make_url(_PG_URL).set(
            drivername='postgresql+psycopg2')
        admin = sqlalchemy.create_engine(pg_url)
        with admin.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS {_PG_SCHEMA} CASCADE'))
            conn.execute(sqlalchemy.text(f'CREATE SCHEMA {_PG_SCHEMA}'))
        admin.dispose()
        url = pg_url.update_query_dict(
            {'options': f'-csearch_path={_PG_SCHEMA}'})
    migration_utils.safe_alembic_upgrade(
        sqlalchemy.create_engine(url),
        migration_utils.GLOBAL_USER_STATE_DB_NAME,
        migration_utils.GLOBAL_USER_STATE_VERSION)
    engine = sqlalchemy.create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def service(engine, monkeypatch):
    """A PermissionService over `engine`, built as an API-server process does.
    """
    monkeypatch.setattr(permission.global_user_state, 'initialize_and_get_db',
                        lambda: engine)
    monkeypatch.setattr(permission, '_policy_lock', contextlib.nullcontext)
    monkeypatch.setattr(permission, '_enforcer_instance', None)
    monkeypatch.setattr(permission.rbac, 'get_default_role', lambda: 'user')
    service = permission.PermissionService()
    service._lazy_initialize()
    return service


def _is_policy_write(statement):
    return (statement.lstrip().upper().startswith(
        ('INSERT', 'DELETE', 'UPDATE')) and 'casbin_rule' in statement)


@contextlib.contextmanager
def _policy_commits(engine):
    """Counts committed transactions that wrote to casbin_rule."""
    counts = {'commits': 0}

    def _after(conn, cursor, statement, *args):  # pylint: disable=unused-argument
        if _is_policy_write(statement):
            conn.info['wrote_policy'] = True

    def _commit(conn):
        if conn.info.pop('wrote_policy', False):
            counts['commits'] += 1

    def _rollback(conn):
        conn.info.pop('wrote_policy', None)

    events = (('after_cursor_execute', _after), ('commit', _commit),
              ('rollback', _rollback))
    for name, fn in events:
        sqlalchemy.event.listen(engine, name, fn)
    try:
        yield counts
    finally:
        for name, fn in events:
            sqlalchemy.event.remove(engine, name, fn)


@contextlib.contextmanager
def _seen_by_reader(engine, query):
    """What another connection reads after each policy write statement."""
    reader = sqlalchemy.create_engine(engine.url)
    seen = []

    def _after(conn, cursor, statement, *args):  # pylint: disable=unused-argument
        if _is_policy_write(statement):
            with reader.connect() as reader_conn:
                seen.append({tuple(row) for row in reader_conn.execute(query)})

    sqlalchemy.event.listen(engine, 'after_cursor_execute', _after)
    try:
        yield seen
    finally:
        sqlalchemy.event.remove(engine, 'after_cursor_execute', _after)
        reader.dispose()


def _rows(engine, ptype, where=''):
    width = 2 if ptype == 'g' else 3
    fields = ', '.join(f'v{i}' for i in range(width))
    with engine.connect() as conn:
        return {
            tuple(row) for row in conn.execute(
                sqlalchemy.text(f'SELECT {fields} FROM casbin_rule '
                                f'WHERE ptype = \'{ptype}\' {where}'))
        }


def _insert(engine, ptype, *rules):
    """A write by something other than PermissionService."""
    with engine.begin() as conn:
        for rule in rules:
            fields = ', '.join(f'v{i}' for i in range(len(rule)))
            values = ', '.join(f'\'{v}\'' for v in rule)
            conn.execute(
                sqlalchemy.text(f'INSERT INTO casbin_rule (ptype, {fields}) '
                                f'VALUES (\'{ptype}\', {values})'))


def _members(engine, workspace_name):
    return {
        user for user, _, _ in _rows(
            engine, 'p', f'AND v1 = \'{workspace_name}\' AND v2 = \'*\'')
    }


_MEMBERS_OF_WS = sqlalchemy.text(
    'SELECT v0 FROM casbin_rule WHERE ptype = \'p\' AND v1 = \'ws\' '
    'AND v2 = \'*\'')
_ALICE_ROLES = sqlalchemy.text(
    'SELECT v1 FROM casbin_rule WHERE ptype = \'g\' AND v0 = \'alice\'')


def test_member_update_commits_once_and_keeps_staying_members(service, engine):
    service.update_workspace_policy('ws', ['alice', 'bob'])

    with _policy_commits(engine) as counts, \
            _seen_by_reader(engine, _MEMBERS_OF_WS) as seen:
        service.update_workspace_policy('ws', ['bob', 'carol'])

    # Before the commit another connection still sees the old members.
    assert seen and all(members == {('alice',), ('bob',)} for members in seen)
    assert counts['commits'] == 1
    assert _members(engine, 'ws') == {'bob', 'carol'}
    assert service.enforcer.enforce('carol', 'ws', '*')
    assert not service.enforcer.enforce('alice', 'ws', '*')


def test_unchanged_members_write_nothing(service, engine):
    service.update_workspace_policy('ws', ['alice', 'bob'])

    with _policy_commits(engine) as counts:
        service.update_workspace_policy('ws', ['bob', 'alice'])

    assert counts['commits'] == 0


def test_member_sync_leaves_other_rows_alone(service, engine):
    _insert(engine, 'p', ('user', '/workspaces/ws', 'POST'),
            ('alice', 'other', '*'))
    service.sync_workspace_members({'ws': ['bob']})
    assert _rows(engine, 'p') == {('user', '/workspaces/ws', 'POST'),
                                  ('alice', 'other', '*'), ('bob', 'ws', '*')}


def test_full_sync_removes_members_of_unconfigured_workspaces(service, engine):
    _insert(engine, 'p', ('alice', 'gone', '*'), ('*', 'gone', '*'),
            ('user', '/admin/*', '*'), ('carol', 'unresolved', '*'))
    service.sync_workspace_members({'ws': ['bob']},
                                   all_workspaces=['ws', 'unresolved'])
    # A role's blocklist rule and an unresolved configured workspace stay.
    assert _rows(engine, 'p') == {('bob', 'ws', '*'), ('user', '/admin/*', '*'),
                                  ('carol', 'unresolved', '*')}


def test_role_change_commits_once_and_never_leaves_no_role(service, engine):
    service.update_role('alice', 'user')

    with _policy_commits(engine) as counts, \
            _seen_by_reader(engine, _ALICE_ROLES) as seen:
        service.update_role('alice', 'admin')

    assert seen and all(roles == {('user',)} for roles in seen)
    assert counts['commits'] == 1
    assert _rows(engine, 'g', 'AND v0 = \'alice\'') == {('alice', 'admin')}
    assert service.get_user_roles('alice') == ['admin']


def test_role_change_drops_every_extra_role(service, engine):
    _insert(engine, 'g', ('alice', 'admin'), ('alice', 'user'),
            ('alice', 'user'))
    service.update_role('alice', 'viewer')
    assert service.get_user_roles('alice') == ['viewer']


def test_batch_role_update_commits_once(service, engine):
    users = [f'u{i}' for i in range(50)]
    service.update_roles({user: 'user' for user in users})

    with _policy_commits(engine) as counts:
        service.update_roles({user: 'viewer' for user in users})

    assert counts['commits'] == 1
    assert _rows(engine, 'g') == {(user, 'viewer') for user in users}


def test_add_workspace_policy_adds_only_missing_members(service, engine):
    service.add_workspace_policy('ws', ['alice'])
    with _policy_commits(engine) as counts:
        service.add_workspace_policy('ws', ['alice', 'bob', 'bob'])
    assert counts['commits'] == 1
    assert _rows(engine, 'p') == {('alice', 'ws', '*'), ('bob', 'ws', '*')}


def test_writes_land_in_the_enforcers_own_database(tmp_path, monkeypatch):
    # An enforcer set up outside `_lazy_initialize`, on its own database.
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/casbin.db')
    service = permission.PermissionService()
    service.enforcer = casbin.SyncedEnforcer(
        os.path.join(os.path.dirname(permission.__file__), 'model.conf'),
        sqlalchemy_adapter.Adapter(engine))
    monkeypatch.setattr(permission.global_user_state, 'initialize_and_get_db',
                        mock.Mock(side_effect=AssertionError('wrong database')))
    monkeypatch.setattr(permission, '_policy_lock', contextlib.nullcontext)

    service.update_role('alice', 'user')
    service.update_workspace_policy('ws', ['alice'])

    assert service.get_user_roles('alice') == ['user']
    assert service.enforcer.enforce('alice', 'ws', '*')
    assert _members(engine, 'ws') == {'alice'}


def test_a_failed_reload_after_a_write_keeps_the_write(service, engine,
                                                       monkeypatch):
    service.update_workspace_policy('ws', ['alice'])
    monkeypatch.setattr(service, '_load_policy_no_lock',
                        mock.Mock(side_effect=RuntimeError('reload failed')))

    service.update_workspace_policy('ws', ['bob'])

    assert _members(engine, 'ws') == {'bob'}


def test_member_check_sees_a_write_made_elsewhere(service, engine, monkeypatch):
    # Another process, an older replica or manual SQL: nothing in this
    # process is told about the write.
    monkeypatch.setenv('IS_SKYPILOT_SERVER', '1')
    assert not service.check_workspace_permission('alice', 'ws')

    _insert(engine, 'p', ('alice', 'ws', '*'))
    assert service.check_workspace_permission('alice', 'ws')

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text('DELETE FROM casbin_rule WHERE v0 = \'alice\''))
    assert not service.check_workspace_permission('alice', 'ws')


def test_new_user_resync_adds_the_missing_grant(service, engine, monkeypatch):
    service.add_workspace_policy('ws-a', ['alice'])
    workspaces = {
        name: {
            'private': True,
            'allowed_users': ['alice']
        } for name in ('ws-a', 'ws-b')
    }
    monkeypatch.setenv('IS_SKYPILOT_SERVER', '1')
    monkeypatch.setattr(skypilot_config, 'get_nested',
                        lambda *a, **k: copy.deepcopy(workspaces))
    monkeypatch.setattr(skypilot_config, 'reload_config', lambda: None)
    monkeypatch.setattr(skypilot_config, 'get_skypilot_config_lock',
                        lambda *a, **k: contextlib.nullcontext())
    resolver = mock.Mock()
    resolver.entries_for.return_value = ['alice']
    with mock.patch('sky.users.resolver.UserResolver', return_value=resolver):
        service.resync_workspace_policies_for_new_user('alice')
    assert _rows(engine, 'p') == {('alice', 'ws-a', '*'),
                                  ('alice', 'ws-b', '*')}


def _startup_patches(monkeypatch, service, rules_complete):
    monkeypatch.setattr(
        permission.rbac, 'get_role_permissions', lambda **_: {
            'user': {
                'permissions': {
                    'blocklist': [{
                        'path': '/a',
                        'method': 'POST'
                    }, {
                        'path': '/b',
                        'method': 'POST'
                    }]
                }
            }
        })
    monkeypatch.setattr(permission.rbac, 'get_workspace_policy_permissions',
                        lambda: {'default': ['*']})
    monkeypatch.setattr(service, '_get_plugin_rbac_rules', lambda: {})
    monkeypatch.setattr(service, '_plugin_rbac_rules_complete',
                        lambda: rules_complete)
    monkeypatch.setattr(permission.global_user_state, 'add_or_update_user',
                        mock.Mock())
    monkeypatch.setattr(permission.global_user_state, 'get_all_users',
                        lambda: [models.User(id='bob', name='bob')])


def test_startup_sync_commits_once_and_matches_expected(service, engine,
                                                        monkeypatch):
    _startup_patches(monkeypatch, service, rules_complete=True)
    _insert(engine, 'p', ('user', '/a', 'POST'), ('user', '/old', 'POST'),
            ('alice', 'gone', '*'))

    with _policy_commits(engine) as counts:
        service._maybe_initialize_policies()

    assert counts['commits'] == 1
    assert _rows(engine, 'p') == {('user', '/a', 'POST'),
                                  ('user', '/b', 'POST'), ('*', 'default', '*')}
    roles = dict(_rows(engine, 'g'))
    assert roles['bob'] == 'user'
    for user_id, role in permission._system_user_roles().items():
        assert roles[user_id] == role


@pytest.mark.parametrize('rules_complete', [True, False])
def test_startup_keeps_role_rules_when_plugin_rules_failed(
        service, engine, monkeypatch, rules_complete):
    _startup_patches(monkeypatch, service, rules_complete)
    plugin_rule = ('user', '/plugins/api/thing', 'POST')
    _insert(engine, 'p', plugin_rule, ('alice', 'gone', '*'))

    service._maybe_initialize_policies()

    rows = _rows(engine, 'p')
    # Without every plugin's rules, a missing rule may be one of theirs.
    assert (plugin_rule in rows) is not rules_complete
    assert ('alice', 'gone', '*') not in rows


def _add_user_row(engine, user_id):
    users = global_user_state.user_table
    with engine.begin() as conn:
        conn.execute(sqlalchemy.insert(users).values(id=user_id, name=user_id))


def _user_row_exists(engine, user_id):
    users = global_user_state.user_table
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.select(
                users.c.id).where(users.c.id == user_id)).first() is not None


def test_delete_user_keeps_the_record(service, engine):
    _add_user_row(engine, 'bob')
    service.update_role('bob', 'admin')
    service.delete_user('bob')
    assert _user_row_exists(engine, 'bob')
    assert _rows(engine, 'g', 'AND v0 = \'bob\'') == set()


_LEGACY_CACHE_WRITES = {
    'update_workspace_policy': lambda service: service.update_workspace_policy(
        'ws', ['bob']),
    'add_workspace_policy': lambda service: service.add_workspace_policy(
        'ws', ['bob']),
    'remove_workspace_policy': lambda service: service.remove_workspace_policy(
        'ws'),
    'update_role': lambda service: service.update_role('alice', 'admin'),
    'add_user_if_not_exists': lambda service: service.add_user_if_not_exists(
        'carol'),
    'delete_user': lambda service: service.delete_user('alice'),
    'delete_user_and_record': lambda service: service.delete_user_and_record(
        'alice'),
}


@pytest.mark.parametrize('write', sorted(_LEGACY_CACHE_WRITES))
@pytest.mark.parametrize('clear_fails', [False, True])
def test_every_policy_write_clears_older_servers_member_cache(
        service, engine, monkeypatch, write, clear_fails):
    """Older servers trust a cached member check for an hour; during a rolling
    update or after a rollback only this clear keeps them current. A failed
    clear must not fail the committed write."""
    _add_user_row(engine, 'alice')
    service.update_role('alice', 'user')
    service.add_workspace_policy('ws', ['alice'])
    versions = []

    def clear(prefix):
        versions.append((prefix, _rows(engine, 'g') | _rows(engine, 'p')))
        if clear_fails:
            raise RuntimeError('cache down')

    monkeypatch.setattr(permission.kv_cache, 'delete_cache_entries_by_prefix',
                        clear)
    before = _rows(engine, 'g') | _rows(engine, 'p')
    _LEGACY_CACHE_WRITES[write](service)
    after = _rows(engine, 'g') | _rows(engine, 'p')

    assert after != before
    # After the commit: the clear saw the written rows.
    assert versions and versions[-1] == ('perm:ws:', after)


def test_startup_clears_older_servers_member_cache_even_if_unchanged(
        service, monkeypatch):
    """A rollback right after this server starts must not find entries an
    older server cached before the upgrade."""
    _startup_patches(monkeypatch, service, rules_complete=True)
    service._maybe_initialize_policies()
    cleared = []
    monkeypatch.setattr(permission.kv_cache, 'delete_cache_entries_by_prefix',
                        cleared.append)
    service._maybe_initialize_policies()  # Nothing left to change.
    assert cleared == ['perm:ws:']


def test_legacy_member_cache_clear_drops_only_member_checks(
        service, monkeypatch, tmp_path):
    kv_engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/kv_cache.db')
    permission.kv_cache.create_table(kv_engine)
    monkeypatch.setattr(permission.kv_cache._db_manager, '_engine', kv_engine)
    expires = time.time() + 3600
    for key in ('perm:ws:ws:alice', 'perm:ws:other:bob', 'other:perm:ws:x'):
        permission.kv_cache.add_or_update_cache_entry(key, '1', expires)

    service.add_workspace_policy('ws', ['alice'])

    assert permission.kv_cache.get_cache_entry('perm:ws:ws:alice') is None
    assert permission.kv_cache.get_cache_entry('perm:ws:other:bob') is None
    assert permission.kv_cache.get_cache_entry('other:perm:ws:x') == '1'


def test_delete_user_and_record_is_one_transaction(service, engine):
    _add_user_row(engine, 'bob')
    service.update_role('bob', 'admin')
    service.add_workspace_policy('private', ['bob'])

    def _fail_policy_delete(conn, cursor, statement, *args):  # pylint: disable=unused-argument
        if statement.lstrip().upper().startswith('DELETE') and ('casbin_rule'
                                                                in statement):
            raise RuntimeError('policy delete failed')

    sqlalchemy.event.listen(engine, 'before_cursor_execute',
                            _fail_policy_delete)
    try:
        with pytest.raises(Exception, match='policy delete failed'):
            service.delete_user_and_record('bob')
    finally:
        sqlalchemy.event.remove(engine, 'before_cursor_execute',
                                _fail_policy_delete)
    assert _user_row_exists(engine, 'bob')
    assert _rows(engine, 'g', 'AND v0 = \'bob\'') == {('bob', 'admin')}

    service.delete_user_and_record('bob')
    assert not _user_row_exists(engine, 'bob')
    assert _rows(engine, 'g', 'AND v0 = \'bob\'') == set()
    assert _rows(engine, 'p', 'AND v0 = \'bob\'') == set()

    # A user recreated with the same id starts clean.
    service.add_user_if_not_exists('bob')
    assert service.get_user_roles('bob') == ['user']
    assert not service.enforcer.enforce('bob', 'private', '*')


@pytest.fixture
def store(service, monkeypatch):
    """An in-memory config store behind the real config save path, with the
    real policy writes on `service`."""
    store = {'workspaces': {}}

    def get_nested(keys, default_value=None, **kwargs):
        del kwargs
        value = store
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default_value
            value = value[key]
        return copy.deepcopy(value)

    def save(config):
        store.clear()
        store.update(copy.deepcopy(dict(config)))

    monkeypatch.setattr(skypilot_config, 'get_nested', get_nested)
    monkeypatch.setattr(skypilot_config, '_get_loaded_config', lambda: store)
    monkeypatch.setattr(skypilot_config, 'to_dict',
                        lambda: copy.deepcopy(store))
    monkeypatch.setattr(skypilot_config, 'reload_config', lambda: None)
    monkeypatch.setattr(skypilot_config, 'update_api_server_config_no_lock',
                        save)
    monkeypatch.setattr(skypilot_config, 'get_skypilot_config_lock',
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(core.resource_checker,
                        'check_no_active_resources_for_workspaces',
                        lambda *a, **k: None)
    monkeypatch.setattr(core, '_validate_workspace_config_changes_with_lock',
                        lambda *a, **k: None)

    def get_workspace_users(config, resolver=None):
        del resolver
        users = config.get('allowed_users', [])
        if 'ambiguous' in users:
            raise ValueError('ambiguous matches several users')
        return users if config.get('private') else ['*']

    monkeypatch.setattr(core.workspaces_utils, 'get_workspace_users',
                        get_workspace_users)
    monkeypatch.setattr(core.user_resolver, 'UserResolver', mock.Mock)
    monkeypatch.setattr(core.permission, 'permission_service', service)
    return store


def _private(*users):
    return {'private': True, 'allowed_users': list(users)}


def test_retried_config_save_repairs_a_failed_policy_write(
        store, service, engine, monkeypatch):
    core.update_config({'workspaces': {'ws': _private('alice', 'bob')}})
    assert _members(engine, 'ws') == {'alice', 'bob'}

    new_config = {'workspaces': {'ws': _private('bob')}}
    sync = service.sync_workspace_members
    monkeypatch.setattr(service, 'sync_workspace_members',
                        mock.Mock(side_effect=RuntimeError('lock timeout')))
    with pytest.raises(RuntimeError):
        core.update_config(new_config)
    # The config was saved; its policy write was not.
    assert store['workspaces']['ws'] == _private('bob')
    assert _members(engine, 'ws') == {'alice', 'bob'}

    monkeypatch.setattr(service, 'sync_workspace_members', sync)
    core.update_config(copy.deepcopy(new_config))
    assert _members(engine, 'ws') == {'bob'}


def test_config_save_writes_every_workspace_in_one_transaction(
        store, engine, monkeypatch):
    core.update_config(
        {'workspaces': {f'ws{i}': _private('alice') for i in range(5)}})
    locks_taken = []

    @contextlib.contextmanager
    def _counting_lock():
        locks_taken.append(1)
        yield

    monkeypatch.setattr(permission, '_policy_lock', _counting_lock)
    new_workspaces = {f'ws{i}': _private('bob') for i in range(1, 6)}
    with _policy_commits(engine) as counts:
        core.update_config({'workspaces': new_workspaces})

    assert len(locks_taken) == 1
    assert counts['commits'] == 1
    assert _members(engine, 'ws0') == set()
    for name in new_workspaces:
        assert _members(engine, name) == {'bob'}
    assert _members(engine, 'default') == {'*'}


def test_unresolvable_untouched_workspace_keeps_its_members(store, engine):
    core.update_config({'workspaces': {'x': _private('alice')}})
    store['workspaces']['x'] = _private('ambiguous')

    core.update_config(
        {'workspaces': {
            'x': _private('ambiguous'),
            'y': _private('bob')
        }})

    assert _members(engine, 'x') == {'alice'}
    assert _members(engine, 'y') == {'bob'}


def test_unresolvable_changed_workspace_fails_before_saving(store, engine):
    core.update_config({'workspaces': {'x': _private('alice')}})

    with pytest.raises(ValueError, match='ambiguous'):
        core.update_config({'workspaces': {'x': _private('ambiguous')}})

    assert store['workspaces']['x'] == _private('alice')
    assert _members(engine, 'x') == {'alice'}


def test_retried_save_removes_members_of_a_deleted_workspace(
        store, service, engine, monkeypatch):
    core.update_config(
        {'workspaces': {
            'old': _private('alice'),
            'ws': _private('bob')
        }})
    new_config = {'workspaces': {'ws': _private('bob')}}
    sync = service.sync_workspace_members
    monkeypatch.setattr(service, 'sync_workspace_members',
                        mock.Mock(side_effect=RuntimeError('lock timeout')))
    with pytest.raises(RuntimeError):
        core.update_config(new_config)
    assert _members(engine, 'old') == {'alice'}

    monkeypatch.setattr(service, 'sync_workspace_members', sync)
    core.update_config(copy.deepcopy(new_config))
    assert _members(engine, 'old') == set()
    assert _members(engine, 'ws') == {'bob'}
