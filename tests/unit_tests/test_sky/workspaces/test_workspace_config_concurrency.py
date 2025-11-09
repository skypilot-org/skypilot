"""Tests for workspace configuration concurrency control."""
import concurrent.futures
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import filelock
import pytest

from sky import skypilot_config
from sky.utils import common_utils
from sky.utils import yaml_utils
from sky.workspaces import core


class TestWorkspaceConfigConcurrency(unittest.TestCase):
    """Test workspace configuration concurrency control."""

    def setUp(self):
        """Set up test environment."""
        # Create a temporary config file for testing
        self.temp_config_dir = tempfile.mkdtemp()
        self.temp_config_file = os.path.join(self.temp_config_dir,
                                             'config.yaml')

        # Initial config with valid workspace schema
        self.initial_config = {
            'workspaces': {
                'default': {},
                'test': {
                    'gcp': {
                        'project_id': 'test-project'
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

    def test_concurrent_workspace_updates_with_lock(self):
        """Test that concurrent workspace updates are serialized by the lock."""

        def update_workspace(workspace_name: str, config: dict):
            """Helper function to update a workspace."""

            def modifier_fn(workspaces):
                workspaces.update({
                    'default': {},
                    'test': {
                        'gcp': {
                            'project_id': 'test-project'
                        }
                    },
                    workspace_name: config
                })

            return core._update_workspaces_config(modifier_fn)

        # Create multiple threads that try to update workspaces simultaneously
        num_threads = 5
        results = []
        errors = []

        def worker(thread_id):
            try:
                workspace_name = f'workspace_{thread_id}'
                # Use valid workspace config according to schema
                config = {
                    'aws': {
                        'disabled': False
                    },
                    'gcp': {
                        'project_id': f'project-{thread_id}'
                    }
                }
                result = update_workspace(workspace_name, config)
                results.append((thread_id, result))
            except Exception as e:
                errors.append((thread_id, e))

        # Start all threads
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)

        # Start all threads at roughly the same time
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Check that no errors occurred
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")

        # Check that all updates succeeded
        self.assertEqual(len(results), num_threads)

        # Verify final config contains all workspaces
        final_config = yaml_utils.read_yaml(self.temp_config_file)
        final_workspaces = final_config['workspaces']

        # Should have default + test + num_threads new workspaces
        expected_workspace_count = 2 + num_threads
        self.assertEqual(len(final_workspaces), expected_workspace_count)

        # Verify each thread's workspace is present
        for i in range(num_threads):
            workspace_name = f'workspace_{i}'
            self.assertIn(workspace_name, final_workspaces)
            expected_config = {
                'aws': {
                    'disabled': False
                },
                'gcp': {
                    'project_id': f'project-{i}'
                }
            }
            self.assertEqual(final_workspaces[workspace_name], expected_config)

    def test_lock_timeout_handling(self):
        """Test that lock timeout is handled gracefully."""

        # Mock filelock to always timeout
        with mock.patch('filelock.FileLock') as mock_filelock:
            mock_context = mock.MagicMock()
            # Create a proper Timeout exception with lock_file argument
            mock_context.__enter__.side_effect = filelock.Timeout(
                'test_lock_file')
            mock_filelock.return_value = mock_context

            def modifier_fn(workspaces):
                workspaces['test'] = {'aws': {'disabled': True}}

            with self.assertRaises(RuntimeError) as context:
                core._update_workspaces_config(modifier_fn)

            self.assertIn('timeout', str(context.exception).lower())
            self.assertIn('lock', str(context.exception).lower())

    def test_lock_file_creation(self):
        """Test that lock file and directory are created properly."""
        # Get the lock path
        lock_path = skypilot_config.get_skypilot_config_lock_path()

        # Verify the path is expanded and directory exists
        self.assertTrue(os.path.isabs(lock_path))
        self.assertTrue(os.path.exists(os.path.dirname(lock_path)))
        self.assertTrue(lock_path.endswith('.skypilot_config.lock'))

    def test_sequential_updates_preserve_data(self):
        """Test that sequential workspace updates preserve existing data."""

        # First update
        def first_modifier(workspaces):
            workspaces.clear()
            workspaces.update({
                'default': {},
                'workspace1': {
                    'gcp': {
                        'project_id': 'project-1'
                    }
                }
            })

        result1 = core._update_workspaces_config(first_modifier)
        expected_workspaces1 = {
            'default': {},
            'workspace1': {
                'gcp': {
                    'project_id': 'project-1'
                }
            }
        }
        self.assertEqual(result1, expected_workspaces1)

        # Verify first update was written
        config_after_first = yaml_utils.read_yaml(self.temp_config_file)
        self.assertEqual(config_after_first['workspaces'], expected_workspaces1)

        # Second update (should preserve workspace1)
        def second_modifier(workspaces):
            workspaces['workspace2'] = {'azure': {'disabled': False}}

        result2 = core._update_workspaces_config(second_modifier)

        # Verify second update was written and first workspace preserved
        config_after_second = yaml_utils.read_yaml(self.temp_config_file)
        final_workspaces = config_after_second['workspaces']
        self.assertEqual(len(final_workspaces), 3)
        self.assertEqual(final_workspaces['workspace1'],
                         {'gcp': {
                             'project_id': 'project-1'
                         }})
        self.assertEqual(final_workspaces['workspace2'],
                         {'azure': {
                             'disabled': False
                         }})

    def test_process_level_concurrency_simulation(self):
        """Test simulated process-level concurrency using ProcessPoolExecutor."""

        def update_in_process(workspace_config):
            """Function to run in separate process."""
            # This simulates a separate process updating the workspace config
            workspace_name, config = workspace_config

            def modifier_fn(workspaces):
                workspaces.update({
                    'default': {},
                    'test': {
                        'gcp': {
                            'project_id': 'test-project'
                        }
                    },
                    workspace_name: config
                })

            # Re-setup the mocks in the subprocess (this is a limitation
            # of this test approach, but demonstrates the concept)
            with mock.patch('sky.skypilot_config.get_user_config_path',
                            return_value=self.temp_config_file):

                def mock_to_dict():
                    if os.path.exists(self.temp_config_file):
                        return yaml_utils.read_yaml(self.temp_config_file)
                    return {}

                with mock.patch('sky.skypilot_config.to_dict',
                                side_effect=mock_to_dict):
                    return core._update_workspaces_config(modifier_fn)

        # Create test data for multiple "processes" with valid configs
        workspace_configs = [(f'proc_workspace_{i}', {
            'kubernetes': {
                'disabled': False
            },
            'gcp': {
                'project_id': f'proc-project-{i}'
            }
        }) for i in range(3)]

        # Use ThreadPoolExecutor instead of ProcessPoolExecutor for testing
        # (ProcessPoolExecutor would require pickling and complex setup)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(update_in_process, config)
                for config in workspace_configs
            ]

            # Wait for all to complete
            results = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self.fail(f"Process failed with exception: {e}")

        # Verify all updates completed successfully
        self.assertEqual(len(results), 3)

        # Check final state
        final_config = yaml_utils.read_yaml(self.temp_config_file)
        final_workspaces = final_config['workspaces']

        # Should have default + test + 3 process workspaces
        self.assertEqual(len(final_workspaces), 5)

        # Verify each process's workspace exists
        for i in range(3):
            workspace_name = f'proc_workspace_{i}'
            self.assertIn(workspace_name, final_workspaces)


if __name__ == '__main__':
    unittest.main()
