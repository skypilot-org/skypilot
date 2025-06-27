"""Tests for resource_checker module."""

import unittest
from unittest import mock

from sky.utils import resource_checker
from sky.skylet import constants


class TestResourceChecker(unittest.TestCase):
    """Test cases for resource checker utilities."""

    def test_check_no_active_resources_for_users_empty(self):
        """Test resource check with no active resources for users."""
        with mock.patch.object(
            resource_checker, '_check_active_resources'
        ) as mock_check:
            resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            mock_check.assert_called_once()
            
            # Verify the filter function works correctly
            args, kwargs = mock_check.call_args
            user_operations, filter_factory, resource_type = args
            
            self.assertEqual(user_operations, [('user-123', 'delete')])
            self.assertEqual(resource_type, 'user')
            
            # Test the filter function
            user_filter = filter_factory('user-123')
            
            # Should match resources with the correct user_hash
            self.assertTrue(user_filter({'user_hash': 'user-123'}))
            self.assertFalse(user_filter({'user_hash': 'other-user'}))
            self.assertFalse(user_filter({}))

    def test_check_no_active_resources_for_workspaces_empty(self):
        """Test resource check with no active resources for workspaces."""
        with mock.patch.object(
            resource_checker, '_check_active_resources'
        ) as mock_check:
            resource_checker.check_no_active_resources_for_workspaces([('my-workspace', 'delete')])
            mock_check.assert_called_once()
            
            # Verify the filter function works correctly
            args, kwargs = mock_check.call_args
            workspace_operations, filter_factory, resource_type = args
            
            self.assertEqual(workspace_operations, [('my-workspace', 'delete')])
            self.assertEqual(resource_type, 'workspace')
            
            # Test the filter function
            workspace_filter = filter_factory('my-workspace')
            
            # Should match resources with the correct workspace
            self.assertTrue(workspace_filter({'workspace': 'my-workspace'}))
            self.assertFalse(workspace_filter({'workspace': 'other-workspace'}))
            # Should default to SKYPILOT_DEFAULT_WORKSPACE when workspace is missing
            self.assertTrue(workspace_filter({}))  # Uses default workspace
            
    def test_check_active_resources_with_clusters(self):
        """Test resource check with active clusters."""
        mock_clusters = [
            {'name': 'cluster1', 'user_hash': 'user-123'},
            {'name': 'cluster2', 'user_hash': 'user-456'},
        ]
        mock_jobs = []
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            
            error_msg = str(exc_info.exception)
            self.assertIn('Cannot delete user \'user-123\'', error_msg)
            self.assertIn('1 active cluster(s): cluster1', error_msg)
            self.assertIn('Please terminate these resources first', error_msg)

    def test_check_active_resources_with_jobs(self):
        """Test resource check with active managed jobs."""
        mock_clusters = []
        mock_jobs = [
            {'job_id': 1, 'user_hash': 'user-123'},
            {'job_id': 2, 'user_hash': 'user-456'},
        ]
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            
            error_msg = str(exc_info.exception)
            self.assertIn('Cannot delete user \'user-123\'', error_msg)
            self.assertIn('1 active managed job(s): 1', error_msg)
            self.assertIn('Please terminate these resources first', error_msg)

    def test_check_active_resources_with_both_clusters_and_jobs(self):
        """Test resource check with both active clusters and jobs."""
        mock_clusters = [
            {'name': 'cluster1', 'user_hash': 'user-123'},
            {'name': 'cluster2', 'user_hash': 'user-123'},
        ]
        mock_jobs = [
            {'job_id': 1, 'user_hash': 'user-123'},
        ]
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            
            error_msg = str(exc_info.exception)
            self.assertIn('Cannot delete user \'user-123\'', error_msg)
            self.assertIn('2 active cluster(s): cluster1, cluster2', error_msg)
            self.assertIn('1 active managed job(s): 1', error_msg)
            self.assertIn('Please terminate these resources first', error_msg)

    def test_check_active_resources_workspace_with_default_workspace(self):
        """Test workspace resource check with default workspace."""
        mock_clusters = [
            {'name': 'cluster1', 'workspace': constants.SKYPILOT_DEFAULT_WORKSPACE},
            {'name': 'cluster2'},  # No workspace field, should default
        ]
        mock_jobs = [
            {'job_id': 1, 'workspace': constants.SKYPILOT_DEFAULT_WORKSPACE},
            {'job_id': 2},  # No workspace field, should default
        ]
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_workspaces([
                    (constants.SKYPILOT_DEFAULT_WORKSPACE, 'delete')])
            
            error_msg = str(exc_info.exception)
            self.assertIn(f'Cannot delete workspace \'{constants.SKYPILOT_DEFAULT_WORKSPACE}\'', error_msg)
            self.assertIn('2 active cluster(s): cluster1, cluster2', error_msg)
            self.assertIn('2 active managed job(s): 1, 2', error_msg)

    def test_check_multiple_users_with_mixed_resources(self):
        """Test checking multiple users with some having resources and others not."""
        mock_clusters = [
            {'name': 'cluster1', 'user_hash': 'user-123'},
        ]
        mock_jobs = [
            {'job_id': 1, 'user_hash': 'user-456'},
        ]
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            user_operations = [
                ('user-123', 'delete'),  # Has cluster
                ('user-456', 'delete'),  # Has job
                ('user-789', 'delete'),  # Has nothing
            ]
            
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_users(user_operations)
            
            error_msg = str(exc_info.exception)
            self.assertIn('Cannot proceed due to active resources in 2 user(s)', error_msg)
            self.assertIn('Cannot delete user \'user-123\'', error_msg)
            self.assertIn('Cannot delete user \'user-456\'', error_msg)
            self.assertNotIn('user-789', error_msg)  # Should not appear as it has no resources

    def test_check_no_resources_passes_silently(self):
        """Test that check passes silently when no resources exist."""
        mock_clusters = []
        mock_jobs = []
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', return_value=mock_jobs):
            
            # Should not raise any exception
            resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            resource_checker.check_no_active_resources_for_workspaces([('my-workspace', 'delete')])

    def test_check_empty_operations_list(self):
        """Test that empty operations list does nothing."""
        # Should not make any calls to get clusters/jobs
        with mock.patch('sky.global_user_state.get_clusters') as mock_clusters, \
             mock.patch('sky.jobs.server.core.queue') as mock_jobs:
            
            resource_checker.check_no_active_resources_for_users([])
            resource_checker.check_no_active_resources_for_workspaces([])
            
            mock_clusters.assert_not_called()
            mock_jobs.assert_not_called()

    def test_jobs_server_cluster_not_up_error(self):
        """Test handling of ClusterNotUpError from jobs server."""
        from sky import exceptions
        
        mock_clusters = [
            {'name': 'cluster1', 'user_hash': 'user-123'},
        ]
        
        with mock.patch('sky.global_user_state.get_clusters', return_value=mock_clusters), \
             mock.patch('sky.jobs.server.core.queue', 
                       side_effect=exceptions.ClusterNotUpError('Jobs controller not up')):
            
            # Should still raise error due to cluster, but handle the jobs error gracefully
            with self.assertRaises(ValueError) as exc_info:
                resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])
            
            error_msg = str(exc_info.exception)
            self.assertIn('1 active cluster(s): cluster1', error_msg)
            # Should not mention jobs since the controller is not up


if __name__ == '__main__':
    unittest.main()