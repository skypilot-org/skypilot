"""Regression tests: sync handlers must see workspaces created after boot.

Sync FastAPI handlers run in the API-server HTTP process and never go through
the executor's ``reload_for_new_request`` pipeline. They see the workspace
config this process loaded at boot plus the request-scoped
``_load_workspaces()`` memo, which nothing in the HTTP process clears. A
workspace created via ``/workspaces/create`` after startup (an executor-side
write) is therefore invisible to them until something reloads the process.

Unlike the handler tests that mock ``refresh_workspace_state_for_sync_handler``
and only assert it is *called*, these tests drive the real config loader, the
real ``_load_workspaces()`` memo and the real refresh helper, so they fail if
any link in that chain stops working — not just if the call is removed.

The scenario mirrors what was observed in production (SkyPilot 0.13.0, 8
uvicorn workers): ``sky workspace use <new>`` returned 404 "does not exist",
and ``ssh <cluster>`` into that workspace closed with "Cluster not found",
indefinitely, from every worker that had not happened to serve a request
that reloads config.
"""
from unittest import mock

import fastapi
import pytest
import yaml

from sky import clouds
from sky import models
from sky import skypilot_config
from sky.server import server
from sky.server.requests import payloads
from sky.users import server as users_server
from sky.utils import annotations
from sky.utils import status_lib
from sky.workspaces import core as workspaces_core

_NEW_WS = 'ws-created-after-boot'


@pytest.fixture
def stale_workspace_config(tmp_path, monkeypatch):
    """Load a server config with only ``default``, prime the memo, then add a
    workspace on disk WITHOUT reloading — the state an HTTP process is in
    after an executor-side ``/workspaces/create``.

    Yields the config path. Restores the previously loaded config on exit.
    """
    config_path = tmp_path / 'config.yaml'

    def _write(workspaces):
        config_path.write_text(yaml.safe_dump({'workspaces': workspaces}))

    _write({'default': {}})
    # Internal-file mode: reload_config() reads exactly this file, no DB.
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       str(config_path))
    prev_config = skypilot_config._get_loaded_config()  # pylint: disable=protected-access
    skypilot_config.reload_config()
    annotations.clear_request_level_cache()

    # Boot-time read: this primes the request-scoped memo.
    assert _NEW_WS not in workspaces_core._load_workspaces()  # pylint: disable=protected-access

    # "Executor writes the new workspace." The file moves forward, this
    # process's loaded config and memo do not.
    _write({'default': {}, _NEW_WS: {}})
    # Precondition for both tests below: the process is really stale. If this
    # ever fails, the tests no longer model the bug and need revisiting.
    assert _NEW_WS not in workspaces_core._load_workspaces()  # pylint: disable=protected-access

    yield config_path

    annotations.clear_request_level_cache()
    skypilot_config._set_loaded_config(prev_config)  # pylint: disable=protected-access


def _fake_request(auth_user):
    req = mock.MagicMock(spec=fastapi.Request)
    req.state.auth_user = auth_user
    return req


def _body(preferred):
    body = mock.MagicMock(spec=payloads.UserPreferredWorkspaceBody)
    body.preferred = preferred
    return body


# POST /users/me/workspace ----------------------------------------------------


def test_post_users_me_workspace_sees_workspace_created_after_boot(
        stale_workspace_config):
    """`sky workspace use <new>` must not 404 for a workspace that exists."""
    del stale_workspace_config  # fixture side effects only
    user = models.User(id='alice', name='alice')
    with mock.patch.object(workspaces_core, 'check_workspace_permission'), \
            mock.patch.object(workspaces_core.global_user_state,
                              'set_user_preferred_workspace') as set_pref:
        resp = users_server.set_user_preferred_workspace(
            _fake_request(user), _body(_NEW_WS))

    assert resp == {'preferred': _NEW_WS}
    set_pref.assert_called_once_with('alice', _NEW_WS)
    # And the process is no longer stale for whatever runs next.
    assert _NEW_WS in workspaces_core._load_workspaces()  # pylint: disable=protected-access


def test_post_users_me_workspace_control_without_refresh_is_404(
        stale_workspace_config):
    """Control: with the refresh stubbed out, the same request 404s.

    This pins the mechanism the test above guards. If this control ever
    passes (no 404), the stale state is being cleared by something else and
    the regression test above has lost its teeth.
    """
    del stale_workspace_config
    user = models.User(id='alice', name='alice')
    with mock.patch.object(users_server.server_common,
                           'refresh_workspace_state_for_sync_handler'), \
            mock.patch.object(workspaces_core, 'check_workspace_permission'), \
            mock.patch.object(workspaces_core.global_user_state,
                              'set_user_preferred_workspace'):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            users_server.set_user_preferred_workspace(_fake_request(user),
                                                      _body(_NEW_WS))
    assert exc_info.value.status_code == 404
    assert 'does not exist' in exc_info.value.detail


# /kubernetes-pod-ssh-proxy cluster lookup -----------------------------------


def _status_filtered_like_get_clusters(record):
    """Stand-in for ``core.status`` that applies the same workspace filter
    ``backend_utils.get_clusters()`` does (``WHERE workspace IN
    accessible``), against the REAL accessible-workspace resolution — so a
    stale ``_load_workspaces()`` memo hides the cluster exactly as in
    production, minus the database."""

    def _status(*args, **kwargs):
        del args, kwargs
        accessible = workspaces_core.get_accessible_workspace_names()
        return [record] if record['workspace'] in accessible else []

    return _status


@pytest.mark.asyncio
async def test_ssh_proxy_lookup_sees_cluster_in_workspace_created_after_boot(
        stale_workspace_config):
    """`ssh <cluster>` into a post-boot workspace must not be refused as
    "Cluster not found"."""
    del stale_workspace_config
    handle = mock.MagicMock()
    handle.launched_resources.cloud = clouds.Kubernetes()
    record = {
        'status': status_lib.ClusterStatus.UP,
        'handle': handle,
        'workspace': _NEW_WS,
    }
    # The API-server process resolves clusters as the server user, which has
    # access to every workspace it knows about; the bug is in *which
    # workspaces it knows about*, so let the ACL step pass everything through.
    with mock.patch.object(workspaces_core,
                           '_accessible_workspace_names_for_user',
                           side_effect=lambda uid, names, action: names), \
            mock.patch.object(server.core, 'status',
                              side_effect=_status_filtered_like_get_clusters(
                                  record)) as status:
        result = await server._get_cluster_and_validate(  # pylint: disable=protected-access
            'cluster-in-new-ws', clouds.Kubernetes)

    assert result is handle
    # First lookup missed (stale), refresh, second lookup hit.
    assert status.call_count == 2
    assert _NEW_WS in workspaces_core._load_workspaces()  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_ssh_proxy_lookup_control_without_refresh_is_not_found(
        stale_workspace_config):
    """Control for the test above: stub the refresh and the lookup 404s."""
    del stale_workspace_config
    handle = mock.MagicMock()
    handle.launched_resources.cloud = clouds.Kubernetes()
    record = {
        'status': status_lib.ClusterStatus.UP,
        'handle': handle,
        'workspace': _NEW_WS,
    }
    with mock.patch.object(server.common,
                           'refresh_workspace_state_for_sync_handler'), \
            mock.patch.object(workspaces_core,
                              '_accessible_workspace_names_for_user',
                              side_effect=lambda uid, names, action: names), \
            mock.patch.object(server.core, 'status',
                              side_effect=_status_filtered_like_get_clusters(
                                  record)):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server._get_cluster_and_validate(  # pylint: disable=protected-access
                'cluster-in-new-ws', clouds.Kubernetes)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'Cluster cluster-in-new-ws not found'
