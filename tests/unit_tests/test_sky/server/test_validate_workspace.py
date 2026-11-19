# pylint: disable=protected-access,redefined-outer-name,missing-class-docstring
"""`/validate` resolves the active workspace like executor requests do.

The handler runs inline in the API server rather than through the executor, so
without its own resolution it always ran in the literal 'default' workspace.
Real state DB, config file and casbin policy; the handler is called directly.
"""
import asyncio
import json
import os
import threading
from typing import Any, Dict, List, Optional
from unittest import mock

import casbin
import fastapi
import pytest
import sqlalchemy_adapter

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.clouds import cloud as sky_cloud
from sky.server import constants as server_constants
from sky.server import server
from sky.server import versions
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import workspace_access
from sky.skylet import constants
from sky.users import permission
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import context
from sky.utils import locks
from sky.utils.db import db_utils
from sky.workspaces import core as workspaces_core

_TASK = 'run: echo hi\n'
_INSTANCE_TYPE_TASK = 'resources:\n  instance_type: m5.2xlarge\nrun: echo hi\n'
_NEW_CLIENT = server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION
_OLD_CLIENT = _NEW_CLIENT - 1
_PREFIX = server_constants.REQUEST_NAME_PREFIX


class _Env:
    """A server with private workspaces and users who belong to some of them."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.service = permission.permission_service
        self.seen: List[str] = []
        self.users: List[str] = []

    def configure(self,
                  workspaces: Dict[str, Any],
                  extra: Optional[Dict[str, Any]] = None,
                  reload: bool = True) -> None:
        config = self.tmp_path / 'config.yaml'
        config.write_text(
            json.dumps({
                **(extra or {}), 'workspaces': workspaces
            }))
        self.monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG,
                                str(config))
        if reload:
            skypilot_config.reload_config()

    def add_user(self,
                 user_id: str,
                 member_of: List[str],
                 preferred: Optional[str] = None) -> None:
        global_user_state.add_or_update_user(
            models.User(id=user_id, name=user_id))
        if preferred is not None:
            global_user_state.set_user_preferred_workspace(user_id, preferred)
        self.service.enforcer.add_grouping_policy(user_id, 'user')
        for workspace in member_of:
            self.service.enforcer.add_policy(user_id, workspace, '*')

    def validate(self,
                 task: str = _TASK,
                 user: Optional[str] = 'alice',
                 api_version: Optional[int] = _NEW_CLIENT,
                 override_config: Optional[Dict[str, Any]] = None,
                 report_user_in_env: Optional[str] = None,
                 extra_env: Optional[Dict[str, str]] = None,
                 drop_user_env: bool = False) -> Optional[Any]:
        """Calls the handler; returns the 400 detail, or None if it passed."""
        request = mock.MagicMock(spec=fastapi.Request)
        request.state = mock.MagicMock()
        request.state.auth_user = (models.User(id=user, name=user)
                                   if user is not None else None)
        reported = report_user_in_env or user or 'nobody'
        body = payloads.ValidateBody(dag=task,
                                     request_options=None,
                                     env_vars={
                                         constants.USER_ID_ENV_VAR: reported,
                                         constants.USER_ENV_VAR: reported,
                                         **(extra_env or {}),
                                     },
                                     override_skypilot_config=override_config or
                                     {})
        if drop_user_env:
            body.env_vars.clear()
        self.monkeypatch.setattr(versions, 'get_remote_api_version',
                                 lambda: api_version)
        workspace_access.set_request_endpoint('/validate', 'POST')
        try:
            asyncio.run(server.validate(body, request))
        except fastapi.HTTPException as e:
            assert e.status_code == 400
            return e.detail
        return None

    def record_workspace(self):
        """Records the active workspace `Dag.validate` runs under."""

        def _record(*args, **kwargs):
            del args, kwargs
            self.seen.append(skypilot_config.get_active_workspace())
            self.users.append(common_utils.get_current_user().id)

        return mock.patch('sky.dag.Dag.validate', side_effect=_record)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    state_db = db_utils.DatabaseManager('state', global_user_state.create_table)
    monkeypatch.setattr(global_user_state, '_db_manager', state_db)
    service = permission.PermissionService()
    service.enforcer = casbin.SyncedEnforcer(
        os.path.join(os.path.dirname(permission.__file__), 'model.conf'),
        sqlalchemy_adapter.Adapter(state_db.get_engine(),
                                   db_class=sqlalchemy_adapter.CasbinRule))
    # What a service built by `initialize()` already holds.
    service._build_viewer_allowlist_no_lock()
    monkeypatch.setattr(permission, 'permission_service', service)
    # The server hijacks os.environ so that a request's env vars (and thus its
    # current user) are contextual; see `context_utils.hijack_sys_attrs`.
    monkeypatch.setattr(os, 'environ', context.ContextualEnviron(os.environ))
    return _Env(tmp_path, monkeypatch)


def _private(*members: str, **extra: Any) -> Dict[str, Any]:
    return {'private': True, 'allowed_users': list(members), **extra}


def _two_workspaces(env: _Env) -> None:
    """Private `default` has no members; alice is in ws-a, ws-b; bob in ws-a."""
    env.configure({
        'default': _private(),
        'ws-a': _private('alice', 'bob'),
        'ws-b': _private('alice'),
    })
    env.add_user('alice', ['ws-a', 'ws-b'])
    env.add_user('bob', ['ws-a'])


class TestWorkspaceResolution:

    def test_preferred_workspace(self, env):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        with env.record_workspace():
            assert env.validate(user='alice') is None
        assert env.seen == ['ws-b']

    def test_single_membership(self, env):
        _two_workspaces(env)
        with env.record_workspace():
            assert env.validate(user='bob') is None
        assert env.seen == ['ws-a']

    def test_accessible_default_is_kept(self, env):
        env.configure({'ws-a': _private('alice')})
        env.add_user('alice', ['ws-a', 'default'])
        with env.record_workspace():
            assert env.validate(user='alice') is None
        assert env.seen == ['default']

    def test_read_only_user_resolves_at_read_level(self, env):
        """/validate is a read endpoint, so a read-only user can validate."""
        env.configure({
            'default': _private(),
            'ws-ro': _private('alice', read_access='all'),
        })
        env.add_user('alice', ['ws-ro'])
        env.add_user('bob', [])
        with env.record_workspace():
            assert env.validate(user='bob') is None
        assert env.seen == ['ws-ro']
        assert workspace_access.for_current_request() == 'read'

    def test_user_reported_by_client_without_auth(self, env):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        with env.record_workspace():
            assert env.validate(user=None, report_user_in_env='alice') is None
        assert env.seen == ['ws-b']

    def test_admin_policy_and_validation_see_it(self, env):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        policy_saw: List[str] = []
        real_apply = admin_policy_utils.apply

        def _apply(dag, *args, **kwargs):
            policy_saw.append(
                skypilot_config.resolved_config().get('active_workspace'))
            return real_apply(dag, *args, **kwargs)

        with env.record_workspace(), \
             mock.patch.object(admin_policy_utils, 'apply', _apply):
            assert env.validate(user='alice') is None
        assert policy_saw == ['ws-b']
        assert env.seen == ['ws-b']

    def test_rejects_a_malformed_user_id_without_auth(self, env):
        detail = env.validate(user=None, report_user_in_env='bad id')
        assert detail is not None
        error = exceptions.deserialize_exception(detail)
        assert isinstance(error, exceptions.InvalidUserIdError)

    def test_rejects_a_missing_user_id_without_auth(self, env):
        detail = env.validate(user=None, drop_user_env=True)
        assert detail is not None
        error = exceptions.deserialize_exception(detail)
        assert isinstance(error, exceptions.InvalidUserIdError)

    def test_runs_as_the_authenticated_user(self, env):
        """The client's own report of who it is does not count over the auth."""
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        env.add_user('mallory', [])
        with env.record_workspace():
            assert env.validate(user='alice',
                                report_user_in_env='mallory') is None
        assert env.seen == ['ws-b']
        assert env.users == ['alice']

    def test_sees_config_changed_since_the_last_request(self, env):
        """A change made by another process is in effect on the next call."""
        env.configure({'default': _private(), 'ws-a': _private('alice')})
        env.add_user('alice', ['ws-a', 'ws-b'], preferred='ws-b')
        with env.record_workspace():
            assert env.validate(user='alice') is None
            env.configure(
                {
                    'default': _private(),
                    'ws-a': _private('alice'),
                    'ws-b': _private('alice'),
                },
                reload=False)
            assert env.validate(user='alice') is None
        assert env.seen == ['ws-a', 'ws-b']

    def test_workspace_does_not_leak_to_the_next_request(self, env):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        with env.record_workspace():
            assert env.validate(user='alice') is None
            assert env.validate(user='bob') is None
        assert env.seen == ['ws-b', 'ws-a']
        assert not skypilot_config.is_active_workspace_set()


class TestClientEnvIsSanitized:

    def test_server_only_env_vars_are_not_visible_while_validating(
            self, env, monkeypatch):
        """Client env vars are only visible once the server-only ones are
        stripped, including to the DAG loader."""
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        # The API server hijacks os.environ to make it contextual.
        monkeypatch.setattr(os, 'environ',
                            context.ContextualEnviron(os.environ))
        seen: Dict[str, Optional[str]] = {}

        def _record(*args, **kwargs):
            del args, kwargs
            for name in (constants.ENV_VAR_DB_CONNECTION_URI,
                         constants.SKYPILOT_SERVER_ENV_VAR_PREFIX + 'FLAG'):
                seen[name] = os.environ.get(name)

        injected = {
            constants.ENV_VAR_DB_CONNECTION_URI: 'postgresql://evil',
            constants.SKYPILOT_SERVER_ENV_VAR_PREFIX + 'FLAG': '1',
        }
        with mock.patch('sky.dag.Dag.validate', side_effect=_record):
            assert env.validate(user='alice', extra_env=injected) is None
        assert seen == {name: None for name in injected}


class TestConcurrentRequests:

    def test_another_request_does_not_reset_the_resolved_workspace(self, env):
        """A new request resets only its own thread's workspace, so a
        validation in another thread keeps its own."""
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        entered = threading.Event()
        other_done = threading.Event()
        seen: List[str] = []

        def _validate_in_another_thread():
            with skypilot_config.local_active_workspace_ctx('ws-a'):
                entered.set()
                other_done.wait(timeout=30)
                seen.append(skypilot_config.get_active_workspace())

        thread = threading.Thread(target=_validate_in_another_thread)
        thread.start()
        assert entered.wait(timeout=30)
        with _request({}):
            pass
        other_done.set()
        thread.join(timeout=30)
        assert seen == ['ws-a']


_NESTED = {'kubernetes': {'autoscaler': 'gke'}}


def _request(override: Dict[str, Any], request_id: str = 'request-id'):
    """A launch by alice, run on this thread (unlike `_Env.validate`)."""
    body = payloads.RequestBody(env_vars={
        constants.USER_ID_ENV_VAR: 'alice',
        constants.USER_ENV_VAR: 'alice',
    },
                                override_skypilot_config=override)
    body.client_api_version = _NEW_CLIENT
    body.workspace_access = 'write'
    return executor.override_request_env_and_config(
        body, request_id,
        _PREFIX + request_names.RequestName.CLUSTER_LAUNCH.value)


class TestNestedOverride:
    """A config override inside a request keeps the request's workspace."""

    def test_keeps_the_resolved_workspace(self, env):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        with _request(dict(_NESTED)):
            assert skypilot_config.get_active_workspace() == 'ws-b'
            with skypilot_config.override_skypilot_config(dict(_NESTED)):
                assert skypilot_config.get_active_workspace() == 'ws-b'
            assert skypilot_config.get_active_workspace() == 'ws-b'

    def test_a_named_workspace_wins_inside_only(self, env):
        _two_workspaces(env)
        with skypilot_config.local_active_workspace_ctx('ws-a'):
            with skypilot_config.override_skypilot_config(
                {'active_workspace': 'ws-b'}):
                assert skypilot_config.get_active_workspace() == 'ws-b'
            assert skypilot_config.get_active_workspace() == 'ws-a'

    def test_an_empty_override_keeps_it_too(self, env):
        env.configure(
            {
                'default': _private(),
                'ws-a': _private('alice'),
                'ws-b': _private('alice'),
            },
            extra={'active_workspace': 'ws-b'})
        with skypilot_config.local_active_workspace_ctx('ws-a'):
            with skypilot_config.override_skypilot_config(None):
                assert skypilot_config.get_active_workspace() == 'ws-a'

    @pytest.mark.parametrize('workspace,error', [
        ('ws-b', RuntimeError),
        ('no-such-workspace', ValueError),
    ],
                             ids=['block-raises', 'unknown-workspace-raises'])
    def test_restored_when_the_block_fails(self, env, workspace, error):
        _two_workspaces(env)
        with skypilot_config.local_active_workspace_ctx('ws-a'):
            with pytest.raises(error):
                with skypilot_config.override_skypilot_config(
                    {'active_workspace': workspace}):
                    raise RuntimeError('boom')
            assert skypilot_config.get_active_workspace() == 'ws-a'

    def test_keeps_the_override_flag(self, env):
        del env
        with skypilot_config.override_skypilot_config(dict(_NESTED)):
            with skypilot_config.override_skypilot_config(dict(_NESTED)):
                pass
            assert skypilot_config._is_config_overridden()

    @pytest.mark.parametrize('request_id,expected', [
        ('request-id', 'ws-b'),
        ('skypilot-status-refresh-daemon', 'default'),
    ],
                             ids=['resolved', 'daemon'])
    def test_a_request_drops_a_leftover_workspace(self, env, request_id,
                                                  expected):
        """Even with an empty override, which the override itself skips. A
        daemon skips the resolver and runs in the config's workspace."""
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        skypilot_config._active_workspace_context.workspace = 'ws-a'
        try:
            with _request({}, request_id):
                assert skypilot_config.get_active_workspace() == expected
        finally:
            skypilot_config.clear_active_workspace()


class TestResolverIsSkipped:

    @pytest.fixture
    def resolver(self):
        with mock.patch.object(
                workspaces_core,
                'resolve_workspace_for_user',
                wraps=workspaces_core.resolve_workspace_for_user) as resolver:
            yield resolver

    def test_workspace_set_by_the_client(self, env, resolver):
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        with env.record_workspace():
            env.validate(user='alice',
                         override_config={'active_workspace': 'ws-a'})
        resolver.assert_not_called()

    def test_workspace_set_by_the_server(self, env, resolver):
        env.configure(
            {
                'default': _private(),
                'ws-a': _private('alice'),
                'ws-b': _private('alice'),
            },
            extra={'active_workspace': 'ws-a'})
        env.add_user('alice', ['ws-a', 'ws-b'], preferred='ws-b')
        with env.record_workspace():
            assert env.validate(user='alice') is None
        resolver.assert_not_called()
        assert env.seen == ['ws-a']

    @pytest.mark.parametrize('api_version', [_OLD_CLIENT, None])
    def test_old_client(self, env, resolver, api_version):
        # An old client skips the resolver and runs in 'default', so it gets
        # the same permission check as any other request there.
        _two_workspaces(env)
        global_user_state.set_user_preferred_workspace('alice', 'ws-b')
        detail = env.validate(user='alice', api_version=api_version)
        resolver.assert_not_called()
        assert detail['type'] == 'PermissionDeniedError'
        assert '\'default\'' in detail['message']


class TestResolutionErrors:

    def test_ambiguous_reaches_the_client(self, env):
        _two_workspaces(env)
        detail = env.validate(user='alice')
        assert detail['type'] == 'WorkspaceAmbiguousError'
        error = exceptions.deserialize_exception(detail)
        assert isinstance(error, exceptions.WorkspaceAmbiguousError)
        assert error.accessible == ['ws-a', 'ws-b']
        assert 'You belong to multiple workspaces: ws-a, ws-b.' in str(error)
        assert 'sky workspace use <name>' in str(error)

    @pytest.mark.parametrize('request_name,access,picked', [
        (_PREFIX + request_names.RequestName.CLUSTER_STATUS.value, 'read',
         'ws-b'),
        (_PREFIX + request_names.RequestName.ENABLED_CLOUDS.value, 'read',
         'ws-b'),
        (request_names.AdminPolicyRequestName.VALIDATE.value, 'read', None),
        (_PREFIX + request_names.RequestName.OPTIMIZE.value, 'read', None),
        (_PREFIX + request_names.RequestName.CLUSTER_LAUNCH.value, 'write',
         None),
    ])
    def test_reads_use_the_first_workspace_by_name(self, env, request_name,
                                                   access, picked):
        """A read by alice, who chose neither of her workspaces, runs in the
        first one by name, unless it prepares a write. A write never picks."""
        # The names sort the other way round from the ids.
        env.configure({
            'default': _private(),
            'ws-a': _private('alice', name='zeta'),
            'ws-b': _private('alice', name='alpha'),
        })
        env.add_user('alice', ['ws-a', 'ws-b'])
        body = payloads.RequestBody(env_vars={
            constants.USER_ID_ENV_VAR: 'alice',
            constants.USER_ENV_VAR: 'alice',
        })
        body.client_api_version = _NEW_CLIENT
        body.workspace_access = access
        context_manager = executor.override_request_env_and_config(
            body, 'request-id', request_name)
        if picked is None:
            with pytest.raises(exceptions.WorkspaceAmbiguousError):
                with context_manager:
                    pass
            return
        with context_manager:
            assert skypilot_config.get_active_workspace() == picked

    def test_no_access_reaches_the_client(self, env):
        env.configure({'default': _private(), 'ws-a': _private('alice')})
        env.add_user('alice', ['ws-a'])
        env.add_user('bob', [])
        detail = env.validate(user='bob')
        assert detail['type'] == 'NoWorkspaceAccessError'
        error = exceptions.deserialize_exception(detail)
        assert isinstance(error, exceptions.NoWorkspaceAccessError)
        assert 'no accessible workspaces' in str(error)


class TestPrivateDefaultWorkspace:
    """`default` is private and the user is not in it.

    A task that names no cloud makes the resources check the enabled clouds of
    the active workspace. For `default`, which the user cannot write, that
    lookup used to fail with "Workspace 'default' not found".
    """

    @pytest.fixture(autouse=True)
    def enabled_cloud(self, env):
        """AWS is enabled in `ws-a` and `ws-b`, with no catalog download."""
        _two_workspaces(env)
        for workspace in ['ws-a', 'ws-b']:
            with skypilot_config.local_active_workspace_ctx(workspace):
                global_user_state.set_enabled_clouds(
                    ['AWS'], sky_cloud.CloudCapability.COMPUTE, workspace)
                global_user_state.set_allowed_clouds(
                    sky_check._get_workspace_allowed_clouds(workspace),
                    workspace)
        with mock.patch.object(clouds.AWS,
                               'instance_type_exists',
                               return_value=True):
            yield

    @pytest.mark.parametrize('user,preferred', [('alice', 'ws-b'),
                                                ('bob', None)])
    def test_instance_type_without_a_cloud(self, env, user, preferred):
        if preferred is not None:
            global_user_state.set_user_preferred_workspace(user, preferred)
        assert env.validate(task=_INSTANCE_TYPE_TASK, user=user) is None

    def test_ambiguous_user_is_not_told_the_workspace_is_missing(self, env):
        detail = env.validate(task=_INSTANCE_TYPE_TASK, user='alice')
        assert detail['type'] == 'WorkspaceAmbiguousError'


class TestReadOnlyWorkspaceColdCache:
    """The only workspace the user can see is read-only, and its enabled-clouds
    cache is cold."""

    def test_validates_without_rewriting_the_cache(self, env):
        env.configure({
            'default': _private(),
            'ws-ro': _private('alice', read_access='all'),
        })
        env.add_user('alice', ['ws-ro'])
        env.add_user('bob', [])

        def _probe_clouds(func, combinations, *args, **kwargs):
            """Stands in for the credential probes: only AWS is usable."""
            del func, args, kwargs
            return [(capability, cloud_tuple[0] == repr(clouds.AWS()), None)
                    for cloud_tuple, capability, _ in combinations]

        with mock.patch.object(clouds.AWS,
                               'instance_type_exists',
                               return_value=True), \
                mock.patch.object(sky_check.subprocess_utils,
                                  'run_in_parallel',
                                  side_effect=_probe_clouds):
            assert env.validate(task=_INSTANCE_TYPE_TASK, user='bob') is None
        # Answered for bob, but nothing was stored for the workspace.
        assert not global_user_state.get_cached_enabled_clouds(
            sky_cloud.CloudCapability.COMPUTE, 'ws-ro')
        assert not global_user_state.get_allowed_clouds('ws-ro')
        assert not global_user_state.get_cached_check_results('ws-ro')


class TestSharedLogHandler:
    """`/validate` runs in a thread of the API server process, which other
    requests share: it must not rebuild the process-wide log handler at the
    validating client's log level. Before the fix, a `SKYPILOT_DEBUG=1`
    validate (every `sky jobs launch`) left the handler at DEBUG, and later
    in-process requests such as the `sky jobs logs` stream carried the
    server's debug lines."""

    def test_debug_client_keeps_the_shared_handler(self, env):
        # pylint: disable=protected-access
        _two_workspaces(env)
        handler = sky_logging._default_handler
        level = handler.level
        with env.record_workspace():
            assert env.validate(user='bob', extra_env={'SKYPILOT_DEBUG': '1'
                                                      }) is None
        assert sky_logging._default_handler is handler
        assert handler.level == level
