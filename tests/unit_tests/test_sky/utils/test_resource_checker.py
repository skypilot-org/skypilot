"""Tests for resource checker utilities for active resources protection."""

import unittest.mock as mock

import pytest

from sky import exceptions
from sky.utils import resource_checker


class TestResourceChecker:
    """Test cases for resource checker functionality."""

    @pytest.fixture
    def sample_clusters(self):
        """Sample cluster data."""
        return [{
            'name': 'cluster-1',
            'user_hash': 'user123',
            'workspace': 'default',
            'status': 'UP'
        }, {
            'name': 'cluster-2',
            'user_hash': 'user456',
            'workspace': 'production',
            'status': 'UP'
        }, {
            'name': 'cluster-3',
            'user_hash': 'sa-service123',
            'workspace': 'default',
            'status': 'UP'
        }]

    @pytest.fixture
    def sample_managed_jobs(self):
        """Sample managed job data."""
        return [{
            'job_id': 'job-001',
            'user_hash': 'user123',
            'workspace': 'default',
            'status': 'RUNNING'
        }, {
            'job_id': 'job-002',
            'user_hash': 'sa-service123',
            'workspace': 'production',
            'status': 'RUNNING'
        }, {
            'job_id': 'job-003',
            'user_hash': 'user456',
            'workspace': 'default',
            'status': 'SUBMITTED'
        }]

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_no_resources(
            self, mock_queue, mock_get_clusters):
        """Test resource check passes when user has no active resources."""
        # Setup mocks - no resources
        mock_get_clusters.return_value = []
        mock_queue.return_value = []

        # Should not raise any exception
        resource_checker.check_no_active_resources_for_users([('user123',
                                                               'delete')])

        # Verify both checks were called
        mock_get_clusters.assert_called_once()
        mock_queue.assert_called_once_with(refresh=False,
                                           skip_finished=True,
                                           all_users=True)

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_with_clusters(
            self, mock_queue, mock_get_clusters, sample_clusters):
        """Test resource check fails when user has active clusters."""
        # Setup mocks - user has clusters
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = []

        # Should raise ValueError for user with clusters
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "1 active cluster(s): cluster-1" in error_message
        assert "Please terminate these resources first" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_with_jobs(
            self, mock_queue, mock_get_clusters, sample_managed_jobs):
        """Test resource check fails when user has active managed jobs."""
        # Setup mocks - user has jobs
        mock_get_clusters.return_value = []
        mock_queue.return_value = sample_managed_jobs

        # Should raise ValueError for user with jobs
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "1 active managed job(s): job-001" in error_message
        assert "Please terminate these resources first" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_with_mixed_resources(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test resource check fails when user has both clusters and jobs."""
        # Setup mocks - user has both clusters and jobs
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs

        # Should raise ValueError for user with both types of resources
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "1 active cluster(s): cluster-1" in error_message
        assert "1 active managed job(s): job-001" in error_message
        assert "Please terminate these resources first" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_service_account(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test resource check for service account user."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs

        # Should raise ValueError for service account with resources
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([
                ('sa-service123', 'delete')
            ])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'sa-service123'" in error_message
        assert "1 active cluster(s): cluster-3" in error_message
        assert "1 active managed job(s): job-002" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_multiple_users(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test resource check for multiple users with mixed results."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs

        # Should raise ValueError with details for multiple users
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([
                ('user123', 'delete'),
                ('user456', 'delete'),
                ('userclean', 'delete')  # This user has no resources
            ])

        error_message = str(exc_info.value)
        assert "Cannot proceed due to active resources in 2 user(s)" in error_message
        assert "Cannot delete user 'user123'" in error_message
        assert "Cannot delete user 'user456'" in error_message
        # userclean should not appear in errors since it has no resources

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_jobs_controller_down(
            self, mock_queue, mock_get_clusters, sample_clusters):
        """Test resource check when jobs controller is down."""
        # Setup mocks - jobs controller throws ClusterNotUpError
        mock_get_clusters.return_value = sample_clusters
        mock_queue.side_effect = exceptions.ClusterNotUpError(
            "Jobs controller down")

        # Should still check clusters and raise error for those
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "1 active cluster(s): cluster-1" in error_message
        # Should not mention jobs since controller is down

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_workspaces(self, mock_queue,
                                                      mock_get_clusters,
                                                      sample_clusters,
                                                      sample_managed_jobs):
        """Test resource check for workspaces."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs

        # Should raise ValueError for workspace with resources
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_workspaces([
                ('production', 'delete')
            ])

        error_message = str(exc_info.value)
        assert "Cannot delete workspace 'production'" in error_message
        assert "1 active cluster(s): cluster-2" in error_message
        assert "1 active managed job(s): job-002" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_empty_operations(self, mock_queue,
                                                        mock_get_clusters):
        """Test resource check with empty operations list."""
        # Should not make any calls if operations list is empty
        resource_checker.check_no_active_resources_for_users([])
        resource_checker.check_no_active_resources_for_workspaces([])

        # Verify no API calls were made
        mock_get_clusters.assert_not_called()
        mock_queue.assert_not_called()

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_update_operation(self, mock_queue,
                                                        mock_get_clusters,
                                                        sample_clusters):
        """Test resource check for update operations (not just delete)."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = []

        # Should raise ValueError even for update operations with active resources
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'update')])

        error_message = str(exc_info.value)
        assert "Cannot update user 'user123'" in error_message
        assert "1 active cluster(s): cluster-1" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_mixed_operations(self, mock_queue,
                                                        mock_get_clusters,
                                                        sample_clusters,
                                                        sample_managed_jobs):
        """Test resource check with mixed update and delete operations."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs

        # Should handle mixed operations correctly
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([
                ('user123', 'delete'), ('user456', 'update')
            ])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "Cannot update user 'user456'" in error_message
