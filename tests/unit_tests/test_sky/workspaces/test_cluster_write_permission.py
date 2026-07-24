"""Unit tests for per-resource cluster write-permission checks.

Covers ``workspaces_core.check_cluster_write_permission``, which gates a
mutating operation on an *existing* cluster by that cluster's own workspace
(rather than the caller's active workspace). See
https://github.com/skypilot-org/skypilot/issues/8072.
"""

import unittest
from unittest import mock

from sky import exceptions
from sky import models
from sky.workspaces import core as workspaces_core


class TestCheckClusterWritePermission(unittest.TestCase):
    """Test check_cluster_write_permission."""

    def setUp(self):
        self.user = models.User(id='user1', name='alice')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_denied_when_not_member_of_cluster_workspace(
            self, mock_get_ws, mock_check):
        """A user who cannot access the cluster's workspace is rejected."""
        mock_get_ws.return_value = 'ws-b'
        mock_check.return_value = False  # not a member of ws-b

        with self.assertRaises(exceptions.PermissionDeniedError):
            workspaces_core.check_cluster_write_permission(
                self.user, 'other-users-cluster')

        # The check must consult the *cluster's* workspace, not the active one.
        mock_get_ws.assert_called_once_with('other-users-cluster')
        mock_check.assert_called_once_with('user1', 'ws-b')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_allowed_when_member_of_cluster_workspace(self, mock_get_ws,
                                                      mock_check):
        """A member of the cluster's workspace passes."""
        mock_get_ws.return_value = 'ws-a'
        mock_check.return_value = True

        # Should not raise.
        workspaces_core.check_cluster_write_permission(self.user, 'my-cluster')
        mock_check.assert_called_once_with('user1', 'ws-a')

    @mock.patch('sky.users.permission.permission_service.'
                'check_workspace_permission')
    @mock.patch('sky.global_user_state.get_cluster_workspace')
    def test_unknown_cluster_is_not_rejected(self, mock_get_ws, mock_check):
        """A missing cluster leaves not-found handling to the caller.

        There is no workspace to enforce, so the permission service must not
        be consulted and no error is raised here.
        """
        mock_get_ws.return_value = None

        workspaces_core.check_cluster_write_permission(self.user, 'nonexistent')
        mock_check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
