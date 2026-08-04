"""Unit tests for per-resource cluster read-permission checks.

Covers ``workspaces_core.check_cluster_read_permission``, the read-side
counterpart of ``check_cluster_write_permission``: it gates reading an
existing cluster's logs/state by that cluster's own workspace (READ), rather
than the caller's active workspace.
"""

import unittest
from unittest import mock

from sky import exceptions
from sky import models
from sky.workspaces import core as workspaces_core


class TestCheckClusterReadPermission(unittest.TestCase):
    """Test check_cluster_read_permission."""

    def setUp(self):
        self.user = models.User(id='user1', name='alice')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_denied_when_cannot_read_cluster_workspace(self, mock_get_ws,
                                                       mock_check):
        mock_get_ws.return_value = 'ws-b'
        mock_check.return_value = False

        with self.assertRaises(exceptions.PermissionDeniedError):
            workspaces_core.check_cluster_read_permission(
                self.user, 'other-users-cluster')

        # Gate on the *cluster's* workspace with the READ action.
        mock_get_ws.assert_called_once_with('other-users-cluster')
        mock_check.assert_called_once_with('user1', 'ws-b', action='read')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_allowed_when_can_read_cluster_workspace(self, mock_get_ws,
                                                     mock_check):
        mock_get_ws.return_value = 'ws-a'
        mock_check.return_value = True

        workspaces_core.check_cluster_read_permission(self.user, 'my-cluster')
        mock_check.assert_called_once_with('user1', 'ws-a', action='read')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_unknown_cluster_is_not_rejected(self, mock_get_ws, mock_check):
        mock_get_ws.return_value = None

        workspaces_core.check_cluster_read_permission(self.user, 'nonexistent')
        mock_check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
