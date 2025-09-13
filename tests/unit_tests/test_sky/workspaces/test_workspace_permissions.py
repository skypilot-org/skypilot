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
        # Create users with duplicate names to test conflict resolution
        self.user4 = models.User(id='user4', name='Alice')  # Same name as user1
        self.user5 = models.User(id='user5', name=None)  # User with no name
        self.all_users = [
            self.user1, self.user2, self.user3, self.user4, self.user5
        ]

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
        private_config = {'private': True, 'allowed_users': ['Bob', 'Charlie']}
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user2', 'user3']  # user IDs for Bob and Charlie
        self.assertEqual(set(users), set(expected_users),
                         "Private workspace should return specific user IDs")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_with_unknown_user(self, mock_get_users):
        """Test private workspace with unknown user."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Bob', 'UnknownUser']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user2']  # Only Bob's user ID
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

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_with_user_ids(self, mock_get_users):
        """Test private workspace using user IDs directly."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['user1', 'user2']}
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1', 'user2']
        self.assertEqual(set(users), set(expected_users),
                         "Private workspace should accept user IDs directly")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_mixed_names_and_ids(self, mock_get_users):
        """Test private workspace with mix of user names and IDs."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['user1', 'Bob', 'user3']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1', 'user2', 'user3']
        self.assertEqual(
            set(users), set(expected_users),
            "Private workspace should handle mix of names and IDs")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_duplicate_user_names_raises_error(
            self, mock_get_users):
        """Test private workspace with duplicate user names raises ValueError."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['Alice']}

        with self.assertRaises(ValueError) as context:
            workspaces_utils.get_workspace_users(private_config)

        self.assertIn('User \'Alice\' has multiple IDs', str(context.exception))
        self.assertIn('user1, user4', str(context.exception))
        self.assertIn('Please specify the user ID instead',
                      str(context.exception))

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_empty_allowed_users(self, mock_get_users):
        """Test private workspace with empty allowed_users list."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': []}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace with empty allowed_users should return empty list"
        )

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_no_allowed_users_key(self, mock_get_users):
        """Test private workspace without allowed_users key."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace without allowed_users should return empty list")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_only_unknown_users(self, mock_get_users):
        """Test private workspace with only unknown users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['UnknownUser1', 'UnknownUser2']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace with only unknown users should return empty list"
        )

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_user_with_no_name(self, mock_get_users):
        """Test private workspace with user that has no name."""
        mock_get_users.return_value = self.all_users
        # Try to use the ID of a user with no name
        private_config = {'private': True, 'allowed_users': ['user5']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, ['user5'],
            "Private workspace should accept user ID even if user has no name")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_unique_user_name(self, mock_get_users):
        """Test private workspace with unique user name (no duplicates)."""
        mock_get_users.return_value = [self.user2,
                                       self.user3]  # Only Bob and Charlie
        private_config = {'private': True, 'allowed_users': ['Bob']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, ['user2'],
            "Private workspace should resolve unique user name to ID")

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.workspaces.utils.logger')
    def test_private_workspace_logs_warning_for_unknown_user(
            self, mock_logger, mock_get_users):
        """Test that warning is logged for unknown users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Bob', 'UnknownUser']
        }

        users = workspaces_utils.get_workspace_users(private_config)

        # Should return Bob's ID
        self.assertEqual(users, ['user2'])

        # Should log warning for unknown user
        mock_logger.warning.assert_called_once_with(
            "User 'UnknownUser' not found in all users")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_preserves_order(self, mock_get_users):
        """Test that private workspace preserves order of allowed users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Charlie', 'Bob', 'user1']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user3', 'user2', 'user1']  # Charlie, Bob, user1
        self.assertEqual(
            users, expected_users,
            "Private workspace should preserve order of allowed users")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_empty_all_users_list(self, mock_get_users):
        """Test behavior when get_all_users returns empty list."""
        mock_get_users.return_value = []
        private_config = {'private': True, 'allowed_users': ['Alice', 'Bob']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [], "Should return empty list when no users exist in system")


if __name__ == '__main__':
    unittest.main()
