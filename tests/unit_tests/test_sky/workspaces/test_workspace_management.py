"""Unit tests for workspace management functionality."""

import os
import tempfile
import unittest
from unittest import mock

from sky.skylet import constants
from sky.workspaces import core


class TestWorkspaceManagement(unittest.TestCase):
    """Test workspace management functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Sample config
        self.sample_config = {
            'workspaces': {
                'default': {},
                'dev': {
                    'gcp': {
                        'project_id': 'dev-project'
                    }
                },
                'prod': {
                    'aws': {
                        'disabled': True
                    },
                    'gcp': {
                        'project_id': 'prod-project'
                    }
                }
            }
        }

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)

    @mock.patch('sky.skypilot_config.get_user_config_path')
    @mock.patch('sky.skypilot_config.to_dict')
    @mock.patch('sky.utils.common_utils.dump_yaml')
    @mock.patch('sky.skypilot_config.reload_config')
    def test_internal_update_workspaces_config(self, mock_reload_config,
                                               mock_dump_yaml, mock_to_dict,
                                               mock_get_path):
        """Test the internal helper for updating workspaces configuration."""
        mock_get_path.return_value = self.config_path
        mock_to_dict.return_value = self.sample_config.copy()

        new_workspaces = {
            'default': {},
            'staging': {
                'gcp': {
                    'project_id': 'staging-project'
                }
            }
        }

        def modifier_fn(workspaces: dict) -> None:
            """Modifier function that replaces workspaces content."""
            workspaces.clear()
            workspaces.update(new_workspaces)

        result = core._update_workspaces_config(modifier_fn)

        # Verify the function called the right methods
        mock_dump_yaml.assert_called_once()
        mock_to_dict.assert_called_once()
        mock_get_path.assert_called_once()
        mock_reload_config.assert_called_once()

        # Verify the result
        self.assertEqual(result, new_workspaces)

    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch(
        'sky.utils.resource_checker.check_no_active_resources_for_workspaces')
    @mock.patch('sky.utils.schemas.get_config_schema')
    @mock.patch('sky.utils.common_utils.validate_schema')
    @mock.patch('sky.check.check')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    def test_update_workspace(self, mock_update_workspaces_config,
                              mock_sky_check, mock_validate_schema,
                              mock_get_schema, mock_check_resources,
                              mock_get_workspaces):
        """Test updating a specific workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()
        mock_check_resources.return_value = None
        mock_get_schema.return_value = {
            'properties': {
                'workspaces': {
                    'additionalProperties': {}
                }
            }
        }
        mock_validate_schema.return_value = None

        new_config = {
            'gcp': {
                'project_id': 'new-dev-project'
            },
            'aws': {
                'disabled': False
            }
        }

        expected_workspaces = self.sample_config['workspaces'].copy()
        expected_workspaces['dev'] = new_config
        mock_update_workspaces_config.return_value = expected_workspaces

        result = core.update_workspace('dev', new_config)

        # Verify the internal helper was called with a function
        mock_update_workspaces_config.assert_called_once()
        self.assertEqual(result, expected_workspaces)

    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.utils.schemas.get_config_schema')
    @mock.patch('sky.utils.common_utils.validate_schema')
    @mock.patch('sky.check.check')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    def test_create_workspace(self, mock_update_workspaces_config,
                              mock_sky_check, mock_validate_schema,
                              mock_get_schema, mock_get_workspaces):
        """Test creating a new workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()
        mock_get_schema.return_value = {
            'properties': {
                'workspaces': {
                    'additionalProperties': {}
                }
            }
        }
        mock_validate_schema.return_value = None

        new_config = {'gcp': {'project_id': 'staging-project'}}

        expected_return = {'updated': 'workspaces'}
        mock_update_workspaces_config.return_value = expected_return

        result = core.create_workspace('staging', new_config)

        # Verify that _update_workspaces_config was called with a function
        mock_update_workspaces_config.assert_called_once()
        self.assertEqual(result, expected_return)

    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.utils.schemas.get_config_schema')
    @mock.patch('sky.utils.common_utils.validate_schema')
    @mock.patch('sky.check.check')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    def test_create_workspace_already_exists(
            self, mock_update_workspaces_config, mock_sky_check,
            mock_validate_schema, mock_get_schema, mock_get_workspaces):
        """Test creating a workspace that already exists should fail."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()
        mock_get_schema.return_value = {
            'properties': {
                'workspaces': {
                    'additionalProperties': {}
                }
            }
        }
        mock_validate_schema.return_value = None

        # Mock _update_workspaces_config to simulate the ValueError being raised
        # inside the modifier function
        def side_effect(modifier_fn):
            # Simulate calling the modifier function with existing workspaces
            workspaces = self.sample_config['workspaces'].copy()
            modifier_fn(workspaces)  # This should raise ValueError

        mock_update_workspaces_config.side_effect = side_effect

        new_config = {'gcp': {'project_id': 'test-project'}}

        # Should raise ValueError when workspace already exists
        with self.assertRaises(ValueError) as cm:
            core.create_workspace('dev', new_config)

        self.assertIn("already exists", str(cm.exception))

    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch(
        'sky.utils.resource_checker.check_no_active_resources_for_workspaces')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    def test_delete_workspace(self, mock_update_workspaces_config,
                              mock_check_resources, mock_get_workspaces):
        """Test deleting a workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()
        mock_check_resources.return_value = None

        expected_workspaces = self.sample_config['workspaces'].copy()
        del expected_workspaces['dev']
        mock_update_workspaces_config.return_value = expected_workspaces

        result = core.delete_workspace('dev')

        # Verify the internal helper was called with a function
        mock_update_workspaces_config.assert_called_once()
        self.assertEqual(result, expected_workspaces)

    @mock.patch('sky.workspaces.core.get_workspaces')
    def test_delete_default_workspace_fails(self, mock_get_workspaces):
        """Test deleting the default workspace should fail."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        with self.assertRaises(ValueError) as cm:
            core.delete_workspace(constants.SKYPILOT_DEFAULT_WORKSPACE)

        self.assertIn("Cannot delete the default workspace", str(cm.exception))

    @mock.patch('sky.workspaces.core.get_workspaces')
    def test_delete_nonexistent_workspace_fails(self, mock_get_workspaces):
        """Test deleting a non-existent workspace should fail."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        with self.assertRaises(ValueError) as cm:
            core.delete_workspace('nonexistent')

        self.assertIn("does not exist", str(cm.exception))

    def test_create_workspace_invalid_name(self):
        """Test creating a workspace with invalid name should fail."""
        with self.assertRaises(ValueError) as cm:
            core.create_workspace('', {})

        self.assertIn("non-empty string", str(cm.exception))

    # Tests for new functions
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_no_changes(self, mock_get_users):
        """Test comparing identical workspace configurations."""
        mock_get_users.return_value = []

        current_config = {
            'gcp': {
                'project_id': 'test-project'
            },
            'aws': {
                'disabled': True
            }
        }
        new_config = current_config.copy()

        result = core._compare_workspace_configs(current_config, new_config)

        # Should detect no changes
        self.assertTrue(result['only_user_access_changes'])
        self.assertFalse(result['private_changed'])
        self.assertFalse(result['private_old'])
        self.assertFalse(result['private_new'])
        self.assertFalse(result['allowed_users_changed'])
        self.assertEqual(result['removed_users'], [])
        self.assertEqual(result['added_users'], [])

    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_private_changed_to_public(
            self, mock_get_users):
        """Test changing workspace from private to public."""
        mock_get_users.side_effect = lambda config: (['user1', 'user2']
                                                     if config.get('private')
                                                     else [])

        current_config = {
            'private': True,
            'allowed_users': ['user1', 'user2'],
            'gcp': {
                'project_id': 'test-project'
            }
        }
        new_config = {'private': False, 'gcp': {'project_id': 'test-project'}}

        result = core._compare_workspace_configs(current_config, new_config)

        # Should detect private change and user access changes only
        self.assertTrue(result['only_user_access_changes'])
        self.assertTrue(result['private_changed'])
        self.assertTrue(result['private_old'])
        self.assertFalse(result['private_new'])
        self.assertTrue(result['allowed_users_changed'])
        self.assertEqual(set(result['allowed_users_old']), {'user1', 'user2'})
        self.assertEqual(set(result['allowed_users_new']), set())
        self.assertEqual(set(result['removed_users']), {'user1', 'user2'})
        self.assertEqual(set(result['added_users']), set())

    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_private_changed_to_private(
            self, mock_get_users):
        """Test changing workspace from public to private."""
        mock_get_users.side_effect = lambda config: (['user1', 'user2']
                                                     if config.get('private')
                                                     else [])

        current_config = {
            'private': False,
            'gcp': {
                'project_id': 'test-project'
            }
        }
        new_config = {
            'private': True,
            'allowed_users': ['user1', 'user2'],
            'gcp': {
                'project_id': 'test-project'
            }
        }

        result = core._compare_workspace_configs(current_config, new_config)

        # Should detect private change and user access changes only
        self.assertTrue(result['only_user_access_changes'])
        self.assertTrue(result['private_changed'])
        self.assertFalse(result['private_old'])
        self.assertTrue(result['private_new'])
        self.assertTrue(result['allowed_users_changed'])
        self.assertEqual(result['allowed_users_old'], [])
        self.assertEqual(set(result['allowed_users_new']), {'user1', 'user2'})
        self.assertEqual(set(result['removed_users']), set())
        self.assertEqual(set(result['added_users']), {'user1', 'user2'})

    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_allowed_users_changed(
            self, mock_get_users):
        """Test changing allowed users without changing private setting."""
        mock_get_users.side_effect = lambda config: config.get(
            'allowed_users', [])

        current_config = {
            'private': True,
            'allowed_users': ['user1', 'user2', 'user3'],
            'gcp': {
                'project_id': 'test-project'
            }
        }
        new_config = {
            'private': True,
            'allowed_users': ['user1', 'user4'],
            'gcp': {
                'project_id': 'test-project'
            }
        }

        result = core._compare_workspace_configs(current_config, new_config)

        # Should detect user access changes only
        self.assertTrue(result['only_user_access_changes'])
        self.assertFalse(result['private_changed'])
        self.assertTrue(result['private_old'])
        self.assertTrue(result['private_new'])
        self.assertTrue(result['allowed_users_changed'])
        self.assertEqual(set(result['allowed_users_old']),
                         {'user1', 'user2', 'user3'})
        self.assertEqual(set(result['allowed_users_new']), {'user1', 'user4'})
        self.assertEqual(set(result['removed_users']), {'user2', 'user3'})
        self.assertEqual(set(result['added_users']), {'user4'})

    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_non_user_access_changes(
            self, mock_get_users):
        """Test detecting non-user-access configuration changes."""
        mock_get_users.return_value = []

        current_config = {
            'gcp': {
                'project_id': 'test-project'
            },
            'aws': {
                'disabled': True
            }
        }
        new_config = {
            'gcp': {
                'project_id': 'new-project'
            },  # Changed project
            'aws': {
                'disabled': True
            }
        }

        result = core._compare_workspace_configs(current_config, new_config)

        # Should detect non-user-access changes
        self.assertFalse(result['only_user_access_changes'])
        self.assertFalse(result['private_changed'])
        self.assertFalse(result['allowed_users_changed'])

    @mock.patch('sky.workspaces.utils.get_workspace_users')
    def test_compare_workspace_configs_wildcard_users(self, mock_get_users):
        """Test handling wildcard users in allowed_users."""
        mock_get_users.return_value = ['*']

        current_config = {'private': False, 'allowed_users': ['*']}
        new_config = {'private': True, 'allowed_users': ['user1']}

        # Mock get_workspace_users to return different values for different configs
        def mock_get_users_side_effect(config):
            if config.get('allowed_users') == ['*']:
                return ['*']
            else:
                return config.get('allowed_users', [])

        mock_get_users.side_effect = mock_get_users_side_effect

        result = core._compare_workspace_configs(current_config, new_config)

        # Should handle wildcard correctly (convert to empty set)
        self.assertTrue(result['allowed_users_changed'])
        self.assertEqual(result['removed_users'], [])  # Empty set from wildcard
        self.assertEqual(result['added_users'], ['user1'])

    @mock.patch(
        'sky.utils.resource_checker.check_users_workspaces_active_resources')
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_private_to_public(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when changing from private to public (should allow)."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': True,
            'private_old': True,
            'private_new': False,
            'allowed_users_changed': True,
            'removed_users': ['user1']
        }

        # Should not raise any exception
        core._validate_workspace_config_changes('test-workspace', {}, {})

        # Should not call resource checker since private->public is always allowed
        mock_check_resources.assert_not_called()

    @mock.patch(
        'sky.utils.resource_checker.check_users_workspaces_active_resources')
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_public_to_private_success(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when changing from public to private (resources belong to allowed users)."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': True,
            'private_old': False,
            'private_new': True,
            'allowed_users_new': ['user1', 'user2']
        }

        # Mock that all resources belong to allowed users
        mock_check_resources.return_value = ('', [])

        # Should not raise any exception
        core._validate_workspace_config_changes('test-workspace', {}, {})

        # Should call resource checker with new allowed users
        mock_check_resources.assert_called_once_with(['user1', 'user2'],
                                                     ['test-workspace'])

    @mock.patch(
        'sky.utils.resource_checker.check_users_workspaces_active_resources')
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_public_to_private_failure(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when changing from public to private (unauthorized resources exist)."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': True,
            'private_old': False,
            'private_new': True,
            'allowed_users_new': ['user1']
        }

        # Mock that some resources don't belong to allowed users
        mock_check_resources.return_value = (
            '2 active cluster(s): cluster-1, cluster-2', ['unauthorized-user'])

        # Should raise ValueError
        with self.assertRaises(ValueError) as cm:
            core._validate_workspace_config_changes('test-workspace', {}, {})

        error_message = str(cm.exception)
        self.assertIn("Cannot change workspace 'test-workspace' to private",
                      error_message)
        self.assertIn("unauthorized-user", error_message)
        self.assertIn("2 active cluster(s): cluster-1, cluster-2",
                      error_message)

    @mock.patch(
        'sky.utils.resource_checker.check_users_workspaces_active_resources')
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_public_to_private_multiple_users(
            self, mock_compare_configs, mock_check_resources):
        """Test validation with multiple unauthorized users."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': True,
            'private_old': False,
            'private_new': True,
            'allowed_users_new': ['user1']
        }

        # Mock multiple unauthorized users
        mock_check_resources.return_value = (
            '3 active cluster(s): cluster-1, cluster-2, cluster-3',
            ['user2', 'user3'])

        # Should raise ValueError with proper plural form
        with self.assertRaises(ValueError) as cm:
            core._validate_workspace_config_changes('test-workspace', {}, {})

        error_message = str(cm.exception)
        self.assertIn("because the users", error_message)  # Plural form
        self.assertIn("user2", error_message)
        self.assertIn("user3", error_message)

    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_removed_users(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when users are removed from allowed_users."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': False,
            'allowed_users_changed': True,
            'removed_users': ['user2', 'user3']
        }

        # Should not raise any exception
        core._validate_workspace_config_changes('test-workspace', {}, {})

        # Should call resource checker for removed users
        expected_operations = [('user2', 'remove'), ('user3', 'remove')]
        mock_check_resources.assert_called_once_with(expected_operations)

    @mock.patch(
        'sky.utils.resource_checker.check_no_active_resources_for_workspaces')
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_non_user_access_changes(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when non-user-access configuration changes are made."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': False,
        }

        # Should not raise any exception
        core._validate_workspace_config_changes('test-workspace', {}, {})

        # Should call workspace resource checker
        mock_check_resources.assert_called_once_with([('test-workspace',
                                                       'update')])

    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    @mock.patch('sky.workspaces.core._compare_workspace_configs')
    def test_validate_workspace_config_changes_no_removed_users(
            self, mock_compare_configs, mock_check_resources):
        """Test validation when allowed_users changed but no users were removed."""
        mock_compare_configs.return_value = {
            'only_user_access_changes': True,
            'private_changed': False,
            'allowed_users_changed': True,
            'removed_users': []  # No removed users
        }

        # Should not raise any exception
        core._validate_workspace_config_changes('test-workspace', {}, {})

        # Should not call resource checker since no users were removed
        mock_check_resources.assert_not_called()


if __name__ == '__main__':
    unittest.main()
