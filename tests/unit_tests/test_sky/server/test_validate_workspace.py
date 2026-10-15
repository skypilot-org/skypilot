# pylint: disable=protected-access,redefined-outer-name,missing-class-docstring
"""`/validate` resolves the active workspace like executor requests do.

The handler runs inline in the API server rather than through the executor, so
without its own resolution it always ran in the literal 'default' workspace.
Real state DB, config file and casbin policy; the handler is called directly.
"""
import asyncio
import json
import os
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
from sky import skypilot_config
from sky.clouds import cloud as sky_cloud
from sky.server import constants as server_constants
from sky.server import server
from sky.server import versions
from sky.server.requests import payloads
from sky.server.requests import workspace_access
from sky.skylet import constants
from sky.users import permission
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import context
from sky.utils import locks
from sky.utils.db import db_utils
from sky.utils.db import kv_cache
from sky.workspaces import core as workspaces_core

_TASK = 'run: echo hi\n'
_INSTANCE_TYPE_TASK = 'resources:\n  instance_type: m5.2xlarge\nrun: echo hi\n'
_NEW_CLIENT = server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION
_OLD_CLIENT = _NEW_CLIENT - 1


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
                 report_user_in_env: Optional[str] = None) -> Optional[Any]:
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
                                     },
                                     override_skypilot_config=override_config or
                                     {})
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
    monkeypatch.setattr(
        kv_cache, '_db_manager',
        db_utils.DatabaseManager('kv_cache', kv_cache.create_table))
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
    """Private `default` has no members; alice is in `ws-a`, `ws-b`, bob in `ws-a`."""
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
        with pytest.raises(exceptions.InvalidUserIdError):
            env.validate(user=None, report_user_in_env='bad id')

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
