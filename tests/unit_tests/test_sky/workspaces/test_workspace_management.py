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


if __name__ == '__main__':
    unittest.main()
