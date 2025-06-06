"""Unit tests for workspace permissions."""

import unittest
from unittest import mock

from sky import models
from sky.workspaces import utils as workspaces_utils


class TestWorkspacePermissions(unittest.TestCase):
    """Test workspace permission functionality."""

    def setUp(self):
        """Set up test environment."""
        # Create mock users
        self.user1 = models.User(id='user1', name='Alice')
        self.user2 = models.User(id='user2', name='Bob')
        self.user3 = models.User(id='user3', name='Charlie')
        self.all_users = [self.user1, self.user2, self.user3]

    @mock.patch('sky.global_user_state.get_all_users')
    def test_public_workspace_config(self, mock_get_users):
        """Test that public workspace config returns wildcard."""
        mock_get_users.return_value = self.all_users
        public_config = {'private': False}
        users = workspaces_utils.get_workspace_users(public_config)
        self.assertEqual(users, ['*'],
                         "Public workspace should return ['*'] for all users")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_config(self, mock_get_users):
        """Test that private workspace config returns specific users."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['Alice', 'Bob']}
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1', 'user2']  # user IDs for Alice and Bob
        self.assertEqual(set(users), set(expected_users),
                         "Private workspace should return specific user IDs")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_with_unknown_user(self, mock_get_users):
        """Test private workspace with unknown user."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Alice', 'UnknownUser']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1']  # Only Alice's user ID
        self.assertEqual(
            users, expected_users,
            "Unknown users should be ignored in private workspace")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_workspace_default_public(self, mock_get_users):
        """Test that workspace without 'private' key defaults to public."""
        mock_get_users.return_value = self.all_users
        default_config = {}
        users = workspaces_utils.get_workspace_users(default_config)
        self.assertEqual(
            users, ['*'],
            "Workspace without 'private' key should default to public")


if __name__ == '__main__':
    unittest.main()
