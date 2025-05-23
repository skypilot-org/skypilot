"""Unit tests for workspace management functionality."""

import os
import tempfile
import unittest
from unittest import mock

from sky import core
from sky import exceptions
from sky import skypilot_config
from sky.skylet import constants


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
    @mock.patch('sky.skypilot_config.get_workspaces')
    @mock.patch('sky.skypilot_config.safe_reload_config')
    @mock.patch('sky.utils.common_utils.dump_yaml')
    def test_internal_update_workspaces_config(self, mock_dump_yaml,
                                               mock_reload, mock_get_workspaces,
                                               mock_to_dict, mock_get_path):
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

        expected_return = new_workspaces.copy()
        mock_get_workspaces.return_value = expected_return

        result = core._update_workspaces_config(new_workspaces)

        # Verify the function called the right methods
        mock_dump_yaml.assert_called_once()
        mock_reload.assert_called_once()
        mock_get_workspaces.assert_called_once()

        # Verify the result
        self.assertEqual(result, expected_return)

    @mock.patch('sky.skypilot_config.get_workspaces')
    @mock.patch('sky.core._update_workspaces_config')
    def test_update_workspace(self, mock_update_workspaces,
                              mock_get_workspaces):
        """Test updating a specific workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

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
        mock_update_workspaces.return_value = expected_workspaces

        result = core.update_workspace('dev', new_config)

        # Verify the internal helper was called with the right args
        mock_update_workspaces.assert_called_once_with(expected_workspaces)
        self.assertEqual(result, expected_workspaces)

    @mock.patch('sky.skypilot_config.get_workspaces')
    @mock.patch('sky.core.update_workspace')
    def test_create_workspace(self, mock_update_workspace, mock_get_workspaces):
        """Test creating a new workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        new_config = {'gcp': {'project_id': 'test-project'}}

        expected_return = {'updated': 'workspaces'}
        mock_update_workspace.return_value = expected_return

        result = core.create_workspace('test', new_config)

        mock_update_workspace.assert_called_once_with('test', new_config)
        self.assertEqual(result, expected_return)

    @mock.patch('sky.skypilot_config.get_workspaces')
    def test_create_workspace_already_exists(self, mock_get_workspaces):
        """Test creating a workspace that already exists should fail."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        new_config = {'gcp': {'project_id': 'test-project'}}

        # Should raise ValueError when workspace already exists
        with self.assertRaises(ValueError) as cm:
            core.create_workspace('dev', new_config)

        self.assertIn("already exists", str(cm.exception))

    @mock.patch('sky.skypilot_config.get_workspaces')
    @mock.patch('sky.core._update_workspaces_config')
    def test_delete_workspace(self, mock_update_workspaces,
                              mock_get_workspaces):
        """Test deleting a workspace."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        expected_workspaces = self.sample_config['workspaces'].copy()
        del expected_workspaces['dev']
        mock_update_workspaces.return_value = expected_workspaces

        result = core.delete_workspace('dev')

        mock_update_workspaces.assert_called_once_with(expected_workspaces)
        self.assertEqual(result, expected_workspaces)

    @mock.patch('sky.skypilot_config.get_workspaces')
    def test_delete_default_workspace_fails(self, mock_get_workspaces):
        """Test deleting the default workspace should fail."""
        mock_get_workspaces.return_value = self.sample_config[
            'workspaces'].copy()

        with self.assertRaises(ValueError) as cm:
            core.delete_workspace(constants.SKYPILOT_DEFAULT_WORKSPACE)

        self.assertIn("Cannot delete the default workspace", str(cm.exception))

    @mock.patch('sky.skypilot_config.get_workspaces')
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


if __name__ == '__main__':
    unittest.main()
