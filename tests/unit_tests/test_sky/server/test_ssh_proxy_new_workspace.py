"""SSH into a cluster in a workspace created after the server loaded its config.

The SSH proxy looks the cluster up in the API server process, which does not
reload the workspace config per request. Real state DB, server config and
casbin policy; only the websocket is mocked.
"""
import json
import os
from unittest import mock

import casbin
import fastapi
import pytest
import pytest_asyncio
import sqlalchemy_adapter

from sky import clouds
from sky import global_user_state
from sky import models
from sky import resources as resources_lib
from sky import skypilot_config
from sky.backends import cloud_vm_ray_backend
from sky.server import server
from sky.skylet import constants
from sky.users import permission
from sky.utils import common
from sky.utils import context
from sky.utils import locks
from sky.utils.db import db_utils
from sky.utils.db import kv_cache


def _add_cluster(name: str, workspace: str) -> None:
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name=name,
        cluster_name_on_cloud=f'{name}-on-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=resources_lib.Resources(infra='k8s/my-ctx',
                                                   instance_type='2CPU--4GB'))
    with skypilot_config.local_active_workspace_ctx(workspace):
        global_user_state.add_or_update_cluster(cluster_name=name,
                                                cluster_handle=handle,
                                                requested_resources=set(),
                                                ready=True)


async def _ssh(user_id: str, cluster_name: str):
    """Returns the handle, or None with the websocket closed."""
    websocket = mock.MagicMock(spec=fastapi.WebSocket)
    websocket.close = mock.AsyncMock()
    websocket.state = mock.MagicMock()
    websocket.state.auth_user = models.User(id=user_id, name=user_id)
    # pylint: disable-next=protected-access
    handle = await server._validate_cluster_for_ssh_proxy_ws(
        websocket, cluster_name, clouds.Kubernetes)
    return handle, websocket.close


@pytest_asyncio.fixture
async def new_workspace(tmp_path, monkeypatch):
    """The server has served an SSH session into c-default, then private
    ws-new (member: alice) and its cluster c-new were created."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    # The proxy has no request context, so lookups run as the server's own
    # identity. Drop any user an earlier test left in the process globals.
    monkeypatch.setenv(constants.USER_ID_ENV_VAR, common.SERVER_ID)
    # pylint: disable-next=protected-access
    monkeypatch.delitem(context._PROCESS_GLOBAL_VARS, 'user', raising=False)
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    config = tmp_path / 'config.yaml'
    config.write_text(json.dumps({'workspaces': {}}))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(config))
    skypilot_config.reload_config()
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
    monkeypatch.setattr(permission, 'permission_service', service)
    for user_id in ['alice', 'bob']:
        service.enforcer.add_grouping_policy(user_id, 'user')
    service.enforcer.add_policy('*', 'default', '*')
    # Owner of the clusters below; status drops clusters with no owner record.
    global_user_state.add_or_update_user(
        models.User(id=common.SERVER_ID, name='server'))

    _add_cluster('c-default', 'default')
    handle, _ = await _ssh('alice', 'c-default')
    assert handle is not None

    config.write_text(
        json.dumps({
            'workspaces': {
                'ws-new': {
                    'private': True,
                    'allowed_users': ['alice']
                }
            }
        }))
    service.enforcer.add_policy('alice', 'ws-new', '*')
    _add_cluster('c-new', 'ws-new')


@pytest.mark.usefixtures('new_workspace')
class TestSSHProxyNewWorkspace:
    """Members reach clusters in a workspace the process has not loaded."""

    @pytest.mark.asyncio
    async def test_member_reaches_cluster(self):
        handle, close = await _ssh('alice', 'c-new')
        assert handle is not None
        close.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_member_is_denied(self):
        handle, close = await _ssh('bob', 'c-new')
        assert handle is None
        close.assert_awaited_once()
        assert close.await_args.kwargs['code'] == 1008
        assert 'not found' not in close.await_args.kwargs['reason']

    @pytest.mark.asyncio
    async def test_missing_cluster_is_not_found(self):
        handle, close = await _ssh('alice', 'ghost')
        assert handle is None
        close.assert_awaited_once_with(code=1008,
                                       reason='Cluster ghost not found')

    @pytest.mark.asyncio
    async def test_reloads_config_only_on_miss(self, monkeypatch):
        reload = mock.Mock(wraps=skypilot_config.safe_reload_config)
        monkeypatch.setattr(skypilot_config, 'safe_reload_config', reload)
        await _ssh('alice', 'c-default')
        assert reload.call_count == 0
        await _ssh('alice', 'c-new')
        assert reload.call_count == 1
        # The reload is process-wide, so the next session needs none.
        await _ssh('alice', 'c-new')
        assert reload.call_count == 1
