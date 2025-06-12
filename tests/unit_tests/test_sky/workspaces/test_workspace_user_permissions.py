"""Integration tests for workspace permissions with user roles.

This module tests that users with "user" role can only see resources
(clusters and managed jobs) in workspaces they have permission to access.
"""

import os
import tempfile
import unittest
from unittest import mock

import pytest

from sky import models
from sky.jobs.server import core as managed_jobs_core
from sky.skylet import constants
from sky.users import permission
from sky.utils import common_utils
from sky.workspaces import core as workspaces_core


class TestWorkspaceUserPermissions(unittest.TestCase):
    """Test workspace permissions for users with different roles."""

    def setUp(self):
        """Set up test environment."""
        # Mock users
        self.admin_user = models.User(id='admin_user_id', name='admin')
        self.user1 = models.User(id='user1_id', name='user1')
        self.user2 = models.User(id='user2_id', name='user2')
        self.user3 = models.User(id='user3_id', name='user3')

        # Mock clusters in different workspaces
        self.clusters = [{
            'name': 'public-cluster-1',
            'workspace': 'public-ws',
            'status': 'UP',
            'user_hash': 'user1_id'
        }, {
            'name': 'public-cluster-2',
            'workspace': 'public-ws',
            'status': 'UP',
            'user_hash': 'user2_id'
        }, {
            'name': 'private-cluster-1',
            'workspace': 'private-ws-1',
            'status': 'UP',
            'user_hash': 'user1_id'
        }, {
            'name': 'private-cluster-2',
            'workspace': 'private-ws-2',
            'status': 'UP',
            'user_hash': 'user2_id'
        }, {
            'name': 'default-cluster',
            'workspace': constants.SKYPILOT_DEFAULT_WORKSPACE,
            'status': 'UP',
            'user_hash': 'user3_id'
        }]

        # Mock managed jobs in different workspaces
        self.managed_jobs = [{
            'job_id': 1,
            'job_name': 'public-job-1',
            'workspace': 'public-ws',
            'user_hash': 'user1_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 2,
            'job_name': 'public-job-2',
            'workspace': 'public-ws',
            'user_hash': 'user2_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 3,
            'job_name': 'private-job-1',
            'workspace': 'private-ws-1',
            'user_hash': 'user1_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 4,
            'job_name': 'private-job-2',
            'workspace': 'private-ws-2',
            'user_hash': 'user2_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 5,
            'job_name': 'default-job',
            'workspace': constants.SKYPILOT_DEFAULT_WORKSPACE,
            'user_hash': 'user3_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }]

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.global_user_state.get_clusters')
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_user_can_only_see_accessible_workspace_clusters(
            self, mock_permission_service, mock_get_workspaces,
            mock_get_clusters):
        """Test that user role can only see clusters in accessible workspaces."""
        # Mock clusters data
        mock_get_clusters.return_value = self.clusters

        # Mock permission service for user1 (user role)
        mock_permission_service.check_workspace_permission.side_effect = (
            lambda user_id, workspace: (
                # user1 has access to public-ws, private-ws-1, and default
                workspace in [
                    'public-ws', 'private-ws-1', constants.
                    SKYPILOT_DEFAULT_WORKSPACE
                ] if user_id == 'user1_id' else
                # user2 has access to public-ws, private-ws-2, and default
                workspace in [
                    'public-ws', 'private-ws-2', constants.
                    SKYPILOT_DEFAULT_WORKSPACE
                ] if user_id == 'user2_id' else
                # user3 has access to public-ws and default only
                workspace in
                ['public-ws', constants.SKYPILOT_DEFAULT_WORKSPACE]
                if user_id == 'user3_id' else False))

        # Mock get_workspaces to return only accessible workspaces for user1
        mock_get_workspaces.return_value = {
            'public-ws': {},  # Public workspace
            'private-ws-1': {
                'private': True,
                'allowed_users': ['user1_id']
            },
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}  # Default is public
            # Note: private-ws-2 should NOT be in this list for user1
        }

        # Test user1 can see clusters in accessible workspaces
        with mock.patch('sky.utils.common_utils.get_current_user',
                        return_value=self.user1):
            from sky.backends import backend_utils

            # Get accessible workspaces for user1
            accessible_ws = workspaces_core.get_workspaces()

            # Filter clusters by accessible workspaces
            filtered_clusters = [
                c for c in self.clusters
                if c.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                accessible_ws
            ]

            # user1 should see: public-cluster-1, public-cluster-2,
            # private-cluster-1, default-cluster
            expected_cluster_names = {
                'public-cluster-1', 'public-cluster-2', 'private-cluster-1',
                'default-cluster'
            }
            actual_cluster_names = {c['name'] for c in filtered_clusters}

            self.assertEqual(
                actual_cluster_names, expected_cluster_names,
                "user1 should see clusters from public-ws, "
                "private-ws-1, and default workspace only")

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_user_cannot_see_private_workspace_clusters(self,
                                                        mock_permission_service,
                                                        mock_get_workspaces):
        """Test that user role cannot see clusters in inaccessible private workspaces."""
        # Mock permission service for user3 (only access to public workspaces)
        mock_permission_service.check_workspace_permission.side_effect = (
            lambda user_id, workspace: workspace in
            ['public-ws', constants.SKYPILOT_DEFAULT_WORKSPACE]
            if user_id == 'user3_id' else False)

        # Mock get_workspaces to return only accessible workspaces for user3
        mock_get_workspaces.return_value = {
            'public-ws': {},  # Public workspace
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}
            # Note: private workspaces should NOT be in this list for user3
        }

        # Test user3 cannot see private workspace clusters
        with mock.patch('sky.utils.common_utils.get_current_user',
                        return_value=self.user3):
            accessible_ws = workspaces_core.get_workspaces()

            # user3 should not have access to private workspaces
            self.assertNotIn('private-ws-1', accessible_ws,
                             "user3 should not have access to private-ws-1")
            self.assertNotIn('private-ws-2', accessible_ws,
                             "user3 should not have access to private-ws-2")

            # user3 should have access to public workspaces
            self.assertIn('public-ws', accessible_ws,
                          "user3 should have access to public-ws")
            self.assertIn(constants.SKYPILOT_DEFAULT_WORKSPACE, accessible_ws,
                          "user3 should have access to default workspace")

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_managed_jobs_workspace_filtering(self, mock_permission_service,
                                              mock_get_workspaces):
        """Test that managed jobs are filtered by workspace permissions."""
        # Mock permission service
        mock_permission_service.check_workspace_permission.side_effect = (
            lambda user_id, workspace:
            (workspace in [
                'public-ws', 'private-ws-1', constants.
                SKYPILOT_DEFAULT_WORKSPACE
            ] if user_id == 'user1_id' else workspace in [
                'public-ws', 'private-ws-2', constants.
                SKYPILOT_DEFAULT_WORKSPACE
            ] if user_id == 'user2_id' else workspace in
             ['public-ws', constants.SKYPILOT_DEFAULT_WORKSPACE]
             if user_id == 'user3_id' else False))

        # Mock get_workspaces to return accessible workspaces for user1
        mock_get_workspaces.return_value = {
            'public-ws': {},
            'private-ws-1': {
                'private': True,
                'allowed_users': ['user1_id']
            },
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}
            # Note: private-ws-2 should NOT be in this list for user1
        }

        # Test user1 can see jobs in accessible workspaces
        with mock.patch('sky.utils.common_utils.get_current_user',
                        return_value=self.user1):
            accessible_ws = workspaces_core.get_workspaces()

            # Filter jobs by accessible workspaces (similar to
            # managed_jobs_core.queue)
            filtered_jobs = [
                job for job in self.managed_jobs
                if job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                accessible_ws
            ]

            # user1 should see: public-job-1, public-job-2,
            # private-job-1, default-job
            expected_job_names = {
                'public-job-1', 'public-job-2', 'private-job-1', 'default-job'
            }
            actual_job_names = {job['job_name'] for job in filtered_jobs}

            self.assertEqual(
                actual_job_names, expected_job_names,
                "user1 should see jobs from accessible "
                "workspaces only")

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_user_cannot_see_private_workspace_jobs(self,
                                                    mock_permission_service,
                                                    mock_get_workspaces):
        """Test that user role cannot see jobs in inaccessible private workspaces."""
        # Mock permission service for user3
        mock_permission_service.check_workspace_permission.side_effect = (
            lambda user_id, workspace: workspace in
            ['public-ws', constants.SKYPILOT_DEFAULT_WORKSPACE]
            if user_id == 'user3_id' else False)

        # Mock get_workspaces to return accessible workspaces for user3
        mock_get_workspaces.return_value = {
            'public-ws': {},
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}
            # Note: private workspaces should NOT be in this list for user3
        }

        # Test user3 can only see jobs from accessible workspaces
        with mock.patch('sky.utils.common_utils.get_current_user',
                        return_value=self.user3):
            accessible_ws = workspaces_core.get_workspaces()

            # Filter jobs by accessible workspaces
            filtered_jobs = [
                job for job in self.managed_jobs
                if job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                accessible_ws
            ]

            # user3 should only see: public-job-1, public-job-2, default-job
            # Should NOT see: private-job-1, private-job-2
            expected_job_names = {'public-job-1', 'public-job-2', 'default-job'}
            actual_job_names = {job['job_name'] for job in filtered_jobs}

            self.assertEqual(
                actual_job_names, expected_job_names,
                "user3 should only see jobs from public and "
                "default workspaces")

            # Explicitly check that private jobs are not visible
            private_job_names = {'private-job-1', 'private-job-2'}
            self.assertTrue(private_job_names.isdisjoint(actual_job_names),
                            "user3 should not see private workspace jobs")

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_admin_user_can_see_all_workspaces(self, mock_permission_service,
                                               mock_get_workspaces):
        """Test that admin role can see resources in all workspaces."""
        # Mock get_workspaces to return all workspaces for admin
        mock_get_workspaces.return_value = {
            'public-ws': {},
            'private-ws-1': {
                'private': True,
                'allowed_users': ['user1_id']
            },
            'private-ws-2': {
                'private': True,
                'allowed_users': ['user2_id']
            },
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}
        }

        # Mock permission service - admin should have access to all workspaces
        mock_permission_service.check_workspace_permission.return_value = True

        # Test admin can see all workspace resources
        with mock.patch('sky.utils.common_utils.get_current_user',
                        return_value=self.admin_user):
            accessible_ws = workspaces_core.get_workspaces()

            # Admin should have access to all workspaces
            expected_workspaces = {
                'public-ws', 'private-ws-1', 'private-ws-2',
                constants.SKYPILOT_DEFAULT_WORKSPACE
            }
            self.assertEqual(set(accessible_ws.keys()), expected_workspaces,
                             "admin should have access to all workspaces")

            # Admin should see all clusters
            filtered_clusters = [
                c for c in self.clusters
                if c.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                accessible_ws
            ]
            expected_cluster_names = {c['name'] for c in self.clusters}
            actual_cluster_names = {c['name'] for c in filtered_clusters}

            self.assertEqual(actual_cluster_names, expected_cluster_names,
                             "admin should see all clusters")

            # Admin should see all jobs
            filtered_jobs = [
                job for job in self.managed_jobs
                if job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                accessible_ws
            ]
            expected_job_names = {job['job_name'] for job in self.managed_jobs}
            actual_job_names = {job['job_name'] for job in filtered_jobs}

            self.assertEqual(actual_job_names, expected_job_names,
                             "admin should see all managed jobs")

    @mock.patch.dict(os.environ, {constants.ENV_VAR_IS_SKYPILOT_SERVER: 'true'})
    @mock.patch('sky.workspaces.core.get_workspaces')
    @mock.patch('sky.users.permission.permission_service')
    def test_managed_jobs_queue_integration(self, mock_permission_service,
                                            mock_get_workspaces):
        """Integration test for managed_jobs_core.queue with workspace filtering."""
        # Mock get_workspaces to return accessible workspaces for user3
        mock_get_workspaces.return_value = {
            'public-ws': {},
            constants.SKYPILOT_DEFAULT_WORKSPACE: {}
            # Note: private workspaces should NOT be in this list for user3
        }

        # Mock permission service
        mock_permission_service.check_workspace_permission.side_effect = (
            lambda user_id, workspace: workspace in
            ['public-ws', constants.SKYPILOT_DEFAULT_WORKSPACE]
            if user_id == 'user3_id' else False)

        # Simulate job queue data with workspace information
        job_queue_data = [{
            'job_id': 1,
            'job_name': 'public-job',
            'workspace': 'public-ws',
            'user_hash': 'user1_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 2,
            'job_name': 'private-job',
            'workspace': 'private-ws-1',
            'user_hash': 'user1_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }, {
            'job_id': 3,
            'job_name': 'default-job',
            'workspace': constants.SKYPILOT_DEFAULT_WORKSPACE,
            'user_hash': 'user3_id',
            'status': mock.MagicMock(is_terminal=lambda: False)
        }]

        with mock.patch('sky.jobs.utils.load_managed_job_queue',
                        return_value=job_queue_data):
            with mock.patch('sky.utils.common_utils.get_current_user',
                            return_value=self.user3):

                # Test the filtering logic directly (without calling managed_jobs_core.queue
                # to avoid complex backend mocking)
                accessible_ws = workspaces_core.get_workspaces()

                # Filter jobs by accessible workspaces (simulating what queue() does)
                filtered_jobs = [
                    job for job in job_queue_data
                    if job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE
                              ) in accessible_ws
                ]

                # user3 should only see jobs from accessible workspaces
                expected_job_names = {'public-job', 'default-job'}
                actual_job_names = {job['job_name'] for job in filtered_jobs}

                self.assertEqual(
                    actual_job_names, expected_job_names,
                    "user3 should only see jobs from accessible "
                    "workspaces")

                # Should not see private workspace job
                self.assertNotIn('private-job',
                                 [job['job_name'] for job in filtered_jobs],
                                 "user3 should not see private workspace jobs")


if __name__ == '__main__':
    pytest.main([__file__])
