"""Demonstration test showing the race condition fix for workspace updates."""
import concurrent.futures
import os
import tempfile
import time
import unittest
from unittest import mock

from sky.utils import yaml_utils
from sky.workspaces import core


class TestWorkspaceRaceConditionDemo(unittest.TestCase):
    """Demonstrate that the race condition in workspace updates is fixed."""

    def setUp(self):
        """Set up test environment."""
        # Create a temporary config file for testing
        self.temp_config_dir = tempfile.mkdtemp()
        self.temp_config_file = os.path.join(self.temp_config_dir,
                                             'config.yaml')

        # Initial config with just default workspace using valid schema
        self.initial_config = {
            'workspaces': {
                'default': {
                    'aws': {
                        'disabled': False
                    }
                }
            }
        }
        yaml_utils.dump_yaml(self.temp_config_file, self.initial_config)

        # Patch the config path to use our temporary file
        self.config_path_patcher = mock.patch(
            'sky.skypilot_config.get_user_config_path',
            return_value=self.temp_config_file)
        self.config_path_patcher.start()

        # Mock skypilot_config.to_dict to read from our temp file
        def mock_to_dict():
            if os.path.exists(self.temp_config_file):
                return yaml_utils.read_yaml(self.temp_config_file)
            return {}

        self.to_dict_patcher = mock.patch('sky.skypilot_config.to_dict',
                                          side_effect=mock_to_dict)
        self.to_dict_patcher.start()

    def tearDown(self):
        """Clean up test environment."""
        self.config_path_patcher.stop()
        self.to_dict_patcher.stop()
        # Clean up temp files
        if os.path.exists(self.temp_config_file):
            os.remove(self.temp_config_file)
        os.rmdir(self.temp_config_dir)

    def test_concurrent_different_workspace_updates_no_data_loss(self):
        """Test that concurrent updates to different workspaces don't lose data.
        
        This test demonstrates the race condition that would have occurred
        without proper locking:
        
        1. Process A reads workspaces = {default: {...}}
        2. Process B reads workspaces = {default: {...}}  
        3. Process A modifies to {default: {...}, workspace_a: {...}}
        4. Process B modifies to {default: {...}, workspace_b: {...}}
        5. Process A writes its version
        6. Process B writes its version, overwriting A's changes
        
        Result without locking: Only workspace_b would exist
        Result with locking: Both workspace_a and workspace_b exist
        """

        def update_workspace_thread(workspace_name: str, config: dict):
            """Simulate a process updating a workspace."""

            def modifier_fn(workspaces):
                # Simulate some processing time to increase chance of race condition
                time.sleep(0.01)
                workspaces[workspace_name] = config

            return core._update_workspaces_config(modifier_fn)

        # Create two threads that update different workspaces simultaneously
        # Using valid workspace configurations
        workspace_configs = [
            ('workspace_alpha', {
                'gcp': {
                    'project_id': 'alpha-project',
                    'disabled': False
                }
            }),
            ('workspace_beta', {
                'azure': {
                    'disabled': False
                },
                'aws': {
                    'disabled': True
                }
            }),
        ]

        # Start both updates concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update_workspace_thread, name, config)
                for name, config in workspace_configs
            ]

            # Wait for both to complete
            results = []
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results.append(result)

        # Verify both updates succeeded and no data was lost
        final_config = yaml_utils.read_yaml(self.temp_config_file)
        final_workspaces = final_config['workspaces']

        # Should have default + workspace_alpha + workspace_beta = 3 workspaces
        self.assertEqual(len(final_workspaces), 3)

        # Verify all workspaces exist with correct configurations
        self.assertIn('default', final_workspaces)
        self.assertEqual(final_workspaces['default'],
                         {'aws': {
                             'disabled': False
                         }})

        self.assertIn('workspace_alpha', final_workspaces)
        self.assertEqual(
            final_workspaces['workspace_alpha'],
            {'gcp': {
                'project_id': 'alpha-project',
                'disabled': False
            }})

        self.assertIn('workspace_beta', final_workspaces)
        self.assertEqual(final_workspaces['workspace_beta'], {
            'azure': {
                'disabled': False
            },
            'aws': {
                'disabled': True
            }
        })

    def test_mixed_operations_concurrent_safety(self):
        """Test that mixed create/update/delete operations are safe concurrently."""

        # First, create some initial workspaces using valid schema
        initial_workspaces = {
            'default': {
                'aws': {
                    'disabled': False
                }
            },
            'workspace_1': {
                'gcp': {
                    'project_id': 'workspace1-project'
                }
            },
            'workspace_2': {
                'azure': {
                    'disabled': False
                }
            },
            'workspace_3': {
                'aws': {
                    'disabled': False
                },
                'gcp': {
                    'project_id': 'workspace3-project'
                }
            },
        }

        def setup_modifier(workspaces):
            workspaces.clear()
            workspaces.update(initial_workspaces)

        core._update_workspaces_config(setup_modifier)

        # Define concurrent operations
        def create_workspace():

            def modifier(workspaces):
                workspaces['new_workspace'] = {
                    'kubernetes': {
                        'disabled': False
                    }
                }

            return core._update_workspaces_config(modifier)

        def update_workspace():

            def modifier(workspaces):
                if 'workspace_1' in workspaces:
                    workspaces['workspace_1'] = {
                        'gcp': {
                            'project_id': 'updated-project'
                        },
                        'aws': {
                            'disabled': False
                        }
                    }

            return core._update_workspaces_config(modifier)

        def delete_workspace():

            def modifier(workspaces):
                if 'workspace_3' in workspaces:
                    del workspaces['workspace_3']

            return core._update_workspaces_config(modifier)

        # Run all operations concurrently
        operations = [create_workspace, update_workspace, delete_workspace]

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(op) for op in operations]

            # Wait for all operations to complete
            for future in concurrent.futures.as_completed(futures):
                future.result()  # This will raise any exceptions

        # Verify final state is consistent
        final_config = yaml_utils.read_yaml(self.temp_config_file)
        final_workspaces = final_config['workspaces']

        # Should have: default, workspace_1 (updated), workspace_2, new_workspace
        # workspace_3 should be deleted
        expected_workspaces = 4
        self.assertEqual(len(final_workspaces), expected_workspaces)

        # Verify specific results
        self.assertIn('default', final_workspaces)
        self.assertIn('workspace_1', final_workspaces)
        self.assertIn('workspace_2', final_workspaces)
        self.assertIn('new_workspace', final_workspaces)
        self.assertNotIn('workspace_3', final_workspaces)

        # Verify the content of updated workspace
        self.assertEqual(final_workspaces['workspace_1'], {
            'gcp': {
                'project_id': 'updated-project'
            },
            'aws': {
                'disabled': False
            }
        })

        # Verify the new workspace
        self.assertEqual(final_workspaces['new_workspace'],
                         {'kubernetes': {
                             'disabled': False
                         }})


if __name__ == '__main__':
    unittest.main()
