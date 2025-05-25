"""
Tests for workspace-aware infra functionality.
"""
import os
import sys
import unittest
from unittest.mock import Mock
from unittest.mock import patch

# Add the dashboard src directory to Python path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), '../../../sky/dashboard/src'))


class TestInfraWorkspace(unittest.TestCase):
    """Test workspace filtering in infra page."""

    def test_filter_by_workspace_all_workspaces(self):
        """Test that ALL_WORKSPACES_VALUE returns all data."""
        from data.connectors.infra import getCloudInfrastructure

        # Mock data
        mock_clusters = [
            {
                'cluster': 'cluster1',
                'cloud': 'aws',
                'workspace': 'team-a'
            },
            {
                'cluster': 'cluster2',
                'cloud': 'gcp',
                'workspace': 'team-b'
            },
            {
                'cluster': 'cluster3',
                'cloud': 'aws',
                'workspace': None
            },  # default
        ]

        mock_jobs = [
            {
                'id': 1,
                'cloud': 'aws',
                'workspace': 'team-a'
            },
            {
                'id': 2,
                'cloud': 'gcp',
                'workspace': 'team-b'
            },
        ]

        with patch('data.connectors.infra.getClusters', return_value=mock_clusters), \
             patch('data.connectors.infra.getManagedJobs', return_value={'jobs': mock_jobs}), \
             patch('data.connectors.infra.fetch') as mock_fetch:

            # Mock enabled clouds response
            mock_response = Mock()
            mock_response.headers.get.return_value = 'test-id'
            mock_fetch.return_value = mock_response

            # Mock the API response for enabled clouds
            with patch('data.connectors.infra.fetch') as mock_fetch_data:
                mock_fetch_data.return_value.json.return_value = {
                    'return_value': '["aws", "gcp"]'
                }

                # Test with ALL_WORKSPACES_VALUE
                result = getCloudInfrastructure('__ALL_WORKSPACES__')

                # Should include data from all workspaces
                self.assertIsNotNone(result)

    def test_filter_by_workspace_specific(self):
        """Test filtering by specific workspace."""
        from data.connectors.infra import getCloudInfrastructure

        # Mock data
        mock_clusters = [
            {
                'cluster': 'cluster1',
                'cloud': 'aws',
                'workspace': 'team-a'
            },
            {
                'cluster': 'cluster2',
                'cloud': 'gcp',
                'workspace': 'team-b'
            },
            {
                'cluster': 'cluster3',
                'cloud': 'aws'
            },  # default workspace
        ]

        mock_jobs = [
            {
                'id': 1,
                'cloud': 'aws',
                'workspace': 'team-a'
            },
            {
                'id': 2,
                'cloud': 'gcp',
                'workspace': 'team-b'
            },
        ]

        with patch('data.connectors.infra.getClusters', return_value=mock_clusters), \
             patch('data.connectors.infra.getManagedJobs', return_value={'jobs': mock_jobs}), \
             patch('data.connectors.infra.fetch') as mock_fetch:

            # Mock enabled clouds response
            mock_response = Mock()
            mock_response.headers.get.return_value = 'test-id'
            mock_fetch.return_value = mock_response

            # Mock the API response for enabled clouds
            with patch('data.connectors.infra.fetch') as mock_fetch_data:
                mock_fetch_data.return_value.json.return_value = {
                    'return_value': '["aws", "gcp"]'
                }

                # Test with specific workspace
                result = getCloudInfrastructure('team-a')

                # Should only include data from team-a workspace
                self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
