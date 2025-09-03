"""Tests for resource checker utilities for active resources protection."""

import unittest.mock as mock

import pytest

from sky import exceptions
from sky import models
from sky.utils import resource_checker


class TestResourceChecker:
    """Test cases for resource checker functionality."""

    @pytest.fixture
    def sample_user(self):
        """Sample user data."""
        return models.User(id='user123', name='alice@company.com')

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
        }, {
            'name': 'cluster-4',
            'workspace': 'default',
            'status': 'UP'
        }, {
            'name': 'cluster-5',
            'user_hash': None,
            'workspace': 'default',
            'status': 'UP'
        }, {
            'name': 'cluster-6',
            'user_hash': '',
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
        }, {
            'job_id': 'job-004',
            'workspace': 'default',
            'status': 'RUNNING'
        }, {
            'job_id': 'job-005',
            'user_hash': None,
            'workspace': 'default',
            'status': 'RUNNING'
        }, {
            'job_id': 'job-006',
            'user_hash': '',
            'workspace': 'default',
            'status': 'RUNNING'
        }]

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_no_resources(
            self, mock_queue, mock_get_clusters):
        """Test resource check passes when user has no active resources."""
        # Setup mocks - no resources
        mock_get_clusters.return_value = []
        mock_queue.return_value = [], 0, {}, 0

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
        mock_queue.return_value = [], 0, {}, 0

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
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        # Should raise ValueError for user with jobs
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "1 active managed job(s): job-001" in error_message
        assert "Please terminate these resources first" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_user')
    @mock.patch('sky.jobs.server.core.queue')
    def test_check_no_active_resources_for_users_with_mixed_resources(
            self, mock_queue, mock_get_user, mock_get_clusters, sample_clusters,
            sample_managed_jobs, sample_user):
        """Test resource check fails when user has both clusters and jobs."""
        # Setup mocks - user has both clusters and jobs
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0
        mock_get_user.return_value = sample_user

        # Should raise ValueError for user with both types of resources
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([('user123',
                                                                   'delete')])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'alice@company.com'" in error_message
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
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

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
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

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
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

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
        mock_queue.return_value = [], 0, {}, 0

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
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        # Should handle mixed operations correctly
        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_no_active_resources_for_users([
                ('user123', 'delete'), ('user456', 'update')
            ])

        error_message = str(exc_info.value)
        assert "Cannot delete user 'user123'" in error_message
        assert "Cannot update user 'user456'" in error_message

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_all_authorized(
            self, mock_get_all_users, mock_queue, mock_get_clusters,
            sample_clusters, sample_managed_jobs):
        """Test when all active resources belong to authorized users."""
        # Setup mocks - filter to only include resources from specified workspaces
        workspace_clusters = [
            c for c in sample_clusters
            if c['workspace'] in ['default', 'production']
        ]
        workspace_jobs = [
            j for j in sample_managed_jobs
            if j['workspace'] in ['default', 'production']
        ]

        mock_get_clusters.return_value = workspace_clusters
        mock_queue.return_value = workspace_jobs, 0, {}, 0

        # All users who have resources in these workspaces
        authorized_users = ['user123', 'user456', 'sa-service123']
        workspaces = ['default', 'production']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should return empty results since all resources belong to authorized users
        assert error_summary == ''
        assert missed_users == []

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_unauthorized_users(
            self, mock_get_all_users, mock_queue, mock_get_clusters,
            sample_clusters, sample_managed_jobs):
        """Test when some active resources belong to unauthorized users."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        # Mock user data for name resolution
        class MockUser:

            def __init__(self, user_id, name):
                self.id = user_id
                self.name = name

        mock_get_all_users.return_value = [
            MockUser('user123', 'alice@company.com'),
            MockUser('user456', 'bob@company.com'),
            MockUser('sa-service123', 'service-account-1')
        ]

        # Only authorize some users, excluding others
        authorized_users = ['user123']  # Missing user456 and sa-service123
        workspaces = ['default', 'production']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should identify unauthorized resources and users
        assert '2 active cluster(s): cluster-2, cluster-3' in error_summary
        assert '2 active managed job(s): job-002, job-003' in error_summary
        assert 'bob@company.com' in missed_users
        assert 'service-account-1' in missed_users
        assert len(missed_users) == 2

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_no_resources(
            self, mock_get_all_users, mock_queue, mock_get_clusters):
        """Test when there are no active resources in specified workspaces."""
        # Setup mocks - no resources
        mock_get_clusters.return_value = []
        mock_queue.return_value = [], 0, {}, 0

        authorized_users = ['user123', 'user456']
        workspaces = ['empty-workspace']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should return empty results
        assert error_summary == ''
        assert missed_users == []

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_clusters_only(
            self, mock_get_all_users, mock_queue, mock_get_clusters,
            sample_clusters):
        """Test when only clusters exist (no managed jobs)."""
        # Setup mocks - only clusters
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = [], 0, {}, 0

        # Mock user data
        class MockUser:

            def __init__(self, user_id, name):
                self.id = user_id
                self.name = name

        mock_get_all_users.return_value = [
            MockUser('user456', 'bob@company.com')
        ]

        authorized_users = ['user123']  # Missing user456
        workspaces = ['default', 'production']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should only report cluster issues
        assert '2 active cluster(s): cluster-2, cluster-3' in error_summary
        assert 'managed job' not in error_summary
        assert 'bob@company.com' in missed_users

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_jobs_only(
            self, mock_get_all_users, mock_queue, mock_get_clusters,
            sample_managed_jobs):
        """Test when only managed jobs exist (no clusters)."""
        # Setup mocks - only jobs
        mock_get_clusters.return_value = []
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        # Mock user data
        class MockUser:

            def __init__(self, user_id, name):
                self.id = user_id
                self.name = name

        mock_get_all_users.return_value = [
            MockUser('sa-service123', 'service-account-1')
        ]

        authorized_users = ['user123']  # Missing sa-service123
        workspaces = ['production']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should only report job issues
        assert '1 active managed job(s): job-002' in error_summary
        assert 'cluster' not in error_summary
        assert 'service-account-1' in missed_users

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_for_workspaces_multiple_workspaces(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test getting active resources for multiple workspaces."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        workspaces = ['default', 'production']

        clusters, jobs = resource_checker._get_active_resources_for_workspaces(
            workspaces)

        # Should return all resources from specified workspaces
        assert len(clusters) == 6  # All clusters are in default or production
        assert len(jobs) == 6  # All jobs are in default or production

        # Verify workspace filtering
        for cluster in clusters:
            assert cluster['workspace'] in workspaces
        for job in jobs:
            assert job['workspace'] in workspaces

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_for_workspaces_single_workspace(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test getting active resources for a single workspace."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        workspaces = ['default']

        clusters, jobs = resource_checker._get_active_resources_for_workspaces(
            workspaces)

        # Should return only resources from default workspace
        expected_clusters = [
            c for c in sample_clusters if c['workspace'] == 'default'
        ]
        expected_jobs = [
            j for j in sample_managed_jobs if j['workspace'] == 'default'
        ]

        assert len(clusters) == len(expected_clusters)
        assert len(jobs) == len(expected_jobs)

        # Verify all returned resources are from default workspace
        for cluster in clusters:
            assert cluster['workspace'] == 'default'
        for job in jobs:
            assert job['workspace'] == 'default'

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_for_workspaces_empty_list(
            self, mock_queue, mock_get_clusters):
        """Test getting active resources for empty workspace list."""
        # Should return empty lists without making API calls
        clusters, jobs = resource_checker._get_active_resources_for_workspaces(
            [])

        assert clusters == []
        assert jobs == []

        # Verify no API calls were made
        mock_get_clusters.assert_not_called()
        mock_queue.assert_not_called()

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_for_workspaces_nonexistent_workspace(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test getting active resources for non-existent workspace."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        workspaces = ['nonexistent-workspace']

        clusters, jobs = resource_checker._get_active_resources_for_workspaces(
            workspaces)

        # Should return empty lists since no resources match
        assert clusters == []
        assert jobs == []

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_user_without_name(
            self, mock_get_all_users, mock_queue, mock_get_clusters,
            sample_clusters):
        """Test handling users without names (fallback to user ID)."""
        # Setup mocks - only clusters
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = [], 0, {}, 0

        # Mock user data with some users having no name
        class MockUser:

            def __init__(self, user_id, name=None):
                self.id = user_id
                self.name = name

        mock_get_all_users.return_value = [
            MockUser('user456', None),  # No name, should fallback to ID
            MockUser('sa-service123', 'service-account-1')
        ]

        authorized_users = ['user123']  # Missing user456 and sa-service123
        workspaces = ['default', 'production']

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should use user ID when name is not available
        assert 'user456' in missed_users  # Fallback to ID
        assert 'service-account-1' in missed_users  # Use name when available

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_for_workspaces_jobs_controller_down(
            self, mock_queue, mock_get_clusters, sample_clusters):
        """Test handling when jobs controller is down."""
        # Setup mocks - jobs controller throws exception
        mock_get_clusters.return_value = sample_clusters
        mock_queue.side_effect = exceptions.ClusterNotUpError(
            "Jobs controller down")

        workspaces = ['default']

        clusters, jobs = resource_checker._get_active_resources_for_workspaces(
            workspaces)

        # Should still return clusters but empty jobs list
        expected_clusters = [
            c for c in sample_clusters if c['workspace'] == 'default'
        ]
        assert len(clusters) == len(expected_clusters)
        assert jobs == []  # Should be empty due to controller being down

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    def test_get_active_resources_by_names_filter_functionality(
            self, mock_queue, mock_get_clusters, sample_clusters,
            sample_managed_jobs):
        """Test the generic _get_active_resources_by_names function."""
        # Setup mocks
        mock_get_clusters.return_value = sample_clusters
        mock_queue.return_value = sample_managed_jobs, 0, {}, 0

        # Create a custom filter factory for testing
        def test_filter_factory(resource_names):
            return lambda resource: resource.get('user_hash') in resource_names

        # Test filtering by user IDs
        user_ids = ['user123', 'sa-service123']
        clusters, jobs = resource_checker._get_active_resources_by_names(
            user_ids, test_filter_factory)

        # Should return only resources belonging to specified users
        expected_clusters = [
            c for c in sample_clusters
            if 'user_hash' in c and c['user_hash'] in user_ids
        ]
        expected_jobs = [
            j for j in sample_managed_jobs
            if 'user_hash' in j and j['user_hash'] in user_ids
        ]

        assert len(clusters) == len(expected_clusters)
        assert len(jobs) == len(expected_jobs)

        # Verify filtering worked correctly
        for cluster in clusters:
            assert cluster['user_hash'] in user_ids
        for job in jobs:
            assert job['user_hash'] in user_ids

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_empty_workspaces(
            self, mock_get_all_users, mock_queue, mock_get_clusters):
        """Test with empty workspace list."""
        # Should return empty results without making API calls when workspaces is empty
        authorized_users = ['user123']
        workspaces = []

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        assert error_summary == ''
        assert missed_users == []

        # Verify no API calls were made due to empty workspace list
        mock_get_clusters.assert_not_called()
        mock_queue.assert_not_called()
        mock_get_all_users.assert_not_called()

    @mock.patch('sky.utils.resource_checker.global_user_state.get_clusters')
    @mock.patch('sky.jobs.server.core.queue')
    @mock.patch('sky.utils.resource_checker.global_user_state.get_all_users')
    def test_check_users_workspaces_active_resources_mixed_workspace_resources(
            self, mock_get_all_users, mock_queue, mock_get_clusters):
        """Test with resources in both specified and unspecified workspaces."""
        # Create test data with resources in multiple workspaces
        mixed_clusters = [{
            'name': 'cluster-in-target',
            'user_hash': 'user456',
            'workspace': 'target'
        }, {
            'name': 'cluster-in-other',
            'user_hash': 'user456',
            'workspace': 'other'
        }, {
            'name': 'cluster-in-default',
            'user_hash': 'user789',
            'workspace': 'default'
        }]
        mixed_jobs = [{
            'job_id': 'job-in-target',
            'user_hash': 'user456',
            'workspace': 'target'
        }, {
            'job_id': 'job-in-other',
            'user_hash': 'user456',
            'workspace': 'other'
        }]

        mock_get_clusters.return_value = mixed_clusters
        mock_queue.return_value = mixed_jobs, 0, {}, 0

        # Mock user data
        class MockUser:

            def __init__(self, user_id, name):
                self.id = user_id
                self.name = name

        mock_get_all_users.return_value = [
            MockUser('user456', 'bob@company.com'),
            MockUser('user789', 'charlie@company.com')
        ]

        # Only check specific workspaces
        authorized_users = ['user123']  # user456 and user789 not authorized
        workspaces = ['target', 'default']  # Excludes 'other' workspace

        error_summary, missed_users = resource_checker.check_users_workspaces_active_resources(
            authorized_users, workspaces)

        # Should only report resources from specified workspaces
        assert '2 active cluster(s): cluster-in-target, cluster-in-default' in error_summary
        assert '1 active managed job(s): job-in-target' in error_summary

        # Should not include resources from 'other' workspace
        assert 'cluster-in-other' not in error_summary
        assert 'job-in-other' not in error_summary

        # Should identify both unauthorized users
        assert 'bob@company.com' in missed_users
        assert 'charlie@company.com' in missed_users
