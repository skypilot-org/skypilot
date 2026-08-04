"""Unit tests for RBAC hardening helpers in sky/server/server.py.

Covers:
- ``_staging_user_hash`` (bind download staging dir to the caller).
- ``_reject_cluster_read_for_unauthorized_sync`` (cluster read-by-name gate).
- ``complete_cluster_name`` (accessible-workspace-filtered completion).
"""

import asyncio
from unittest import mock

import fastapi
import pytest

from sky import exceptions
from sky import models
from sky.server import server


def _request_with_user(user):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.auth_user = user
    return request


class TestStagingUserHash:

    def test_uses_auth_user_id_over_body(self):
        req = _request_with_user(models.User(id='alice-id', name='alice'))
        # Even if the body claims another user's hash, the caller's own id wins.
        assert server._staging_user_hash(req, 'bob-id') == 'alice-id'

    def test_falls_back_to_body_when_no_auth(self):
        req = _request_with_user(None)
        assert server._staging_user_hash(req, 'bob-id') == 'bob-id'


class TestRejectClusterReadSync:

    @mock.patch('sky.workspaces.core.check_cluster_read_permission')
    def test_no_auth_user_is_unscoped(self, mock_check):
        # No server identity -> preserve behavior, do not gate.
        server._reject_cluster_read_for_unauthorized_sync(None, 'some-cluster')
        mock_check.assert_not_called()

    @mock.patch('sky.workspaces.core.check_cluster_read_permission')
    def test_none_cluster_name_is_noop(self, mock_check):
        server._reject_cluster_read_for_unauthorized_sync(
            models.User(id='alice-id', name='alice'), None)
        mock_check.assert_not_called()

    @mock.patch('sky.workspaces.core.check_cluster_read_permission')
    def test_permission_denied_becomes_404(self, mock_check):
        mock_check.side_effect = exceptions.PermissionDeniedError('nope')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server._reject_cluster_read_for_unauthorized_sync(
                models.User(id='alice-id', name='alice'), 'w2-cluster')
        # 404 (not 403) to avoid existence disclosure.
        assert exc_info.value.status_code == 404

    @mock.patch('sky.workspaces.core.check_cluster_read_permission')
    def test_allowed_does_not_raise(self, mock_check):
        mock_check.return_value = None
        server._reject_cluster_read_for_unauthorized_sync(
            models.User(id='alice-id', name='alice'), 'my-cluster')
        mock_check.assert_called_once()


class TestCompleteClusterNameScoping:

    @mock.patch('sky.global_user_state.'
                'get_cluster_names_and_workspaces_start_with')
    def test_filters_to_accessible_workspaces(self, mock_names):
        mock_names.return_value = [('c-a', 'ws-a'), ('c-b', 'ws-b')]
        req = _request_with_user(models.User(id='alice-id', name='alice'))
        with mock.patch(
                'sky.users.permission.permission_service.'
                'get_accessible_workspace_names',
                return_value={'ws-a'}):
            result = asyncio.run(server.complete_cluster_name(req, ''))
        # Only the cluster in the accessible workspace is suggested.
        assert result == ['c-a']

    @mock.patch('sky.global_user_state.'
                'get_cluster_names_and_workspaces_start_with')
    def test_no_auth_user_returns_all(self, mock_names):
        mock_names.return_value = [('c-a', 'ws-a'), ('c-b', 'ws-b')]
        req = _request_with_user(None)
        result = asyncio.run(server.complete_cluster_name(req, ''))
        assert result == ['c-a', 'c-b']
