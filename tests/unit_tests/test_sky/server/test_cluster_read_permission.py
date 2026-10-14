"""A cluster's jobs and logs are readable only from its own workspace.

Real state DB and casbin policy; the handlers are called directly and stop at
the executor, which is mocked.
"""
import asyncio
import json
import os
from unittest import mock

import casbin
import fastapi
import pytest
import sqlalchemy_adapter

from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.server import server
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.utils import locks
from sky.utils.db import db_utils
from sky.utils.db import kv_cache


class _Handle:
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None
    instance_ids = None


@pytest.fixture
def clusters(tmp_path, monkeypatch):
    """alice is the only member of private ws-a and of private ws-ro, which
    non-members may read. Clusters a-1 and ro-1 live in them."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    # Another test in the same worker may leak ENV_VAR_SKYPILOT_CONFIG into
    # os.environ. It takes priority over ENV_VAR_GLOBAL_CONFIG in
    # reload_config(), so the live reload on the read path would load that
    # config instead of ours and lose ws-ro. Clear it for this test.
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    config = tmp_path / 'config.yaml'
    config.write_text(
        json.dumps({
            'workspaces': {
                'ws-a': {
                    'private': True,
                    'allowed_users': ['alice']
                },
                'ws-ro': {
                    'private': True,
                    'allowed_users': ['alice'],
                    'read_access': 'all'
                },
            }
        }))
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
    for user_id in ['admin', 'alice', 'bob']:
        service.enforcer.add_grouping_policy(
            user_id, 'admin' if user_id == 'admin' else 'user')
    for user_id, workspace in [('*', 'default'), ('alice', 'ws-a'),
                               ('alice', 'ws-ro')]:
        service.enforcer.add_policy(user_id, workspace, '*')

    log = tmp_path / 'provision.log'
    log.write_text('provisioned\n')
    for name, workspace in [('a-1', 'ws-a'), ('ro-1', 'ws-ro')]:
        with skypilot_config.local_active_workspace_ctx(workspace):
            global_user_state.add_or_update_cluster(cluster_name=name,
                                                    cluster_handle=_Handle(),
                                                    requested_resources=set(),
                                                    ready=True,
                                                    provision_log_path=str(log))

    for name in ['schedule_request_async', 'prepare_request_async']:
        monkeypatch.setattr(executor, name, mock.AsyncMock())
    monkeypatch.setattr(executor, 'check_request_thread_executor_available',
                        lambda: None)
    monkeypatch.setattr(executor, 'execute_request_in_coroutine', mock.Mock())
    monkeypatch.setattr(server.stream_utils, 'stream_response_for_long_request',
                        mock.Mock())
    monkeypatch.setattr(server.stream_utils, 'log_streamer', mock.Mock())
    monkeypatch.setattr(
        server.bs, 'get_blob_storage',
        lambda: mock.Mock(download_tmp_dir=lambda _: str(tmp_path / 'dl')))


def _request(user_id):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.request_id = 'request-id'
    request.state.auth_user = models.User(id=user_id, name=user_id)
    return request


_ENV = {constants.USER_ID_ENV_VAR: 'x', constants.USER_ENV_VAR: 'x'}

_ENDPOINTS = {
    '/queue': lambda r, c: server.queue(
        r, payloads.QueueBody(cluster_name=c, env_vars=_ENV)),
    '/job_status': lambda r, c: server.job_status(
        r, payloads.JobStatusBody(cluster_name=c, job_ids=None, env_vars=_ENV)),
    '/logs': lambda r, c: server.logs(
        r, payloads.ClusterJobBody(cluster_name=c, job_id=None, env_vars=_ENV),
        fastapi.BackgroundTasks()),
    '/download_logs': lambda r, c: server.download_logs(
        r,
        payloads.ClusterJobsDownloadLogsBody(
            cluster_name=c, job_ids=None, env_vars=_ENV)),
    '/hook_logs': lambda r, c: server.hook_logs(
        r, payloads.HookLogsBody(cluster_name=c, env_vars=_ENV),
        fastapi.BackgroundTasks()),
}


def _status(endpoint, user_id, cluster, worker=None):
    """HTTP status the handler answers with; 200 if it was let through."""
    try:
        if endpoint == '/provision_logs':
            server.provision_logs(_request(user_id),
                                  payloads.ProvisionLogsBody(
                                      cluster_name=cluster,
                                      worker=worker,
                                      env_vars=_ENV),
                                  follow=False)
        else:
            asyncio.run(_ENDPOINTS[endpoint](_request(user_id), cluster))
    except fastapi.HTTPException as e:
        return e.status_code, e.detail
    return 200, None


_ALL = sorted(_ENDPOINTS) + ['/provision_logs']
_DENIED = {endpoint: 403 for endpoint in _ENDPOINTS}
_DENIED['/provision_logs'] = 404


@pytest.mark.usefixtures('clusters')
class TestClusterReadPermission:
    """Only readers of a cluster's workspace get its jobs and logs."""

    @pytest.mark.parametrize('endpoint', _ALL)
    def test_non_member_is_denied(self, endpoint):
        assert _status(endpoint, 'bob', 'a-1')[0] == _DENIED[endpoint]

    @pytest.mark.parametrize('endpoint', _ALL)
    def test_member_is_allowed(self, endpoint):
        assert _status(endpoint, 'alice', 'a-1') == (200, None)

    @pytest.mark.parametrize('endpoint', _ALL)
    def test_read_only_workspace_is_readable(self, endpoint):
        assert _status(endpoint, 'bob', 'ro-1') == (200, None)

    @pytest.mark.parametrize('endpoint', _ALL)
    def test_admin_is_allowed(self, endpoint):
        assert _status(endpoint, 'admin', 'a-1') == (200, None)

    def test_provision_logs_denial_matches_missing_cluster(self):
        assert (_status('/provision_logs', 'bob',
                        'a-1') == _status('/provision_logs', 'bob', 'missing'))

    def test_provision_logs_history_is_checked(self):
        global_user_state.remove_cluster('a-1', terminate=True)
        assert _status('/provision_logs', 'bob', 'a-1')[0] == 404
        assert _status('/provision_logs', 'alice', 'a-1') == (200, None)

    def test_provision_logs_reads_cluster_row_once(self, monkeypatch):
        workspace = mock.Mock(wraps=global_user_state.get_cluster_workspace)
        monkeypatch.setattr(global_user_state, 'get_cluster_workspace',
                            workspace)
        assert _status('/provision_logs', 'bob', 'a-1')[0] == 404
        workspace.assert_not_called()

    def test_provision_logs_worker_is_checked(self):
        denied = _status('/provision_logs', 'bob', 'a-1', worker=1)
        assert denied == _status('/provision_logs', 'bob', 'missing', worker=1)
        assert denied[0] == 404
        # Past the check: the handle has no instance ids recorded.
        assert _status('/provision_logs', 'alice', 'a-1', worker=1)[0] == 400

    @pytest.mark.parametrize('endpoint', _ALL)
    def test_read_access_change_applies_without_restart(self, endpoint,
                                                        tmp_path):
        assert _status(endpoint, 'bob', 'a-1')[0] == _DENIED[endpoint]
        assert _status(endpoint, 'bob', 'ro-1') == (200, None)
        config = tmp_path / 'config.yaml'
        workspaces = json.loads(config.read_text())['workspaces']
        workspaces['ws-a']['read_access'] = 'all'
        workspaces['ws-ro']['read_access'] = 'allowed_users'
        config.write_text(json.dumps({'workspaces': workspaces}))
        assert _status(endpoint, 'bob', 'a-1') == (200, None)
        assert _status(endpoint, 'bob', 'ro-1')[0] == _DENIED[endpoint]

    @pytest.mark.parametrize('endpoint', _ALL)
    @pytest.mark.parametrize('user_id,cluster,reloads', [
        ('alice', 'a-1', 0),
        ('admin', 'a-1', 0),
        ('bob', 'a-1', 1),
        ('bob', 'ro-1', 1),
    ])
    def test_only_non_members_reload_config(self, endpoint, user_id, cluster,
                                            reloads, monkeypatch):
        reload = mock.Mock(wraps=skypilot_config.safe_reload_config)
        monkeypatch.setattr(skypilot_config, 'safe_reload_config', reload)
        _status(endpoint, user_id, cluster)
        assert reload.call_count == reloads
