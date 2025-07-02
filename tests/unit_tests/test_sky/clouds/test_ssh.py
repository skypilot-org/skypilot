"""Tests for SSH cloud implementation."""

import os
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import yaml

from sky.clouds import ssh
from sky.provision.kubernetes import utils as kubernetes_utils


class TestSSHExistingAllowedContexts(unittest.TestCase):
    """Test cases for SSH.existing_allowed_contexts method."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear any cached results
        ssh.SSH._ssh_log_skipped_contexts_once.cache_clear()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    def test_no_contexts_available(self, mock_get_all_contexts):
        """Test when no Kubernetes contexts are available."""
        mock_get_all_contexts.return_value = []

        result = ssh.SSH.existing_allowed_contexts()

        self.assertEqual(result, [])
        mock_get_all_contexts.assert_called_once()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_filters_only_ssh_contexts(self, mock_get_nested,
                                       mock_get_workspace_cloud,
                                       mock_get_all_contexts):
        """Test that only SSH contexts (starting with 'ssh-') are returned."""
        mock_get_all_contexts.return_value = [
            'regular-ctx1', 'ssh-cluster1', 'regular-ctx2', 'ssh-cluster2'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None

        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=[]):
            result = ssh.SSH.existing_allowed_contexts()

            # Should return all SSH contexts when no node pools file
            self.assertEqual(sorted(result), ['ssh-cluster1', 'ssh-cluster2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_workspace_allowed_node_pools_takes_precedence(
            self, mock_get_nested, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test that workspace allowed_node_pools takes precedence."""
        mock_get_all_contexts.return_value = [
            'ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = [
            'cluster1', 'cluster2'
        ]
        mock_get_nested.return_value = ['cluster3']  # Should be ignored

        with patch.object(
                ssh.SSH,
                'get_ssh_node_pool_contexts',
                return_value=['ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3']):
            result = ssh.SSH.existing_allowed_contexts()

            # Should only return contexts matching allowed node pools
            self.assertEqual(sorted(result), ['ssh-cluster1', 'ssh-cluster2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_global_allowed_node_pools_when_no_workspace_config(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test using global allowed_node_pools when workspace config is None."""
        mock_get_all_contexts.return_value = [
            'ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['cluster2', 'cluster3']

        with patch.object(
                ssh.SSH,
                'get_ssh_node_pool_contexts',
                return_value=['ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3']):
            result = ssh.SSH.existing_allowed_contexts()

            self.assertEqual(sorted(result), ['ssh-cluster2', 'ssh-cluster3'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_node_pool_contexts_filter_existing_contexts(
            self, mock_get_nested, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test that node pool contexts are filtered by existing contexts."""
        mock_get_all_contexts.return_value = ['ssh-cluster1', 'ssh-cluster3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None

        # Node pools file has more contexts than actually exist
        with patch.object(
                ssh.SSH,
                'get_ssh_node_pool_contexts',
                return_value=['ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3']):
            result = ssh.SSH.existing_allowed_contexts()

            # Should only return contexts that actually exist
            self.assertEqual(sorted(result), ['ssh-cluster1', 'ssh-cluster3'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_logs_skipped_contexts_when_not_silent(self, mock_get_nested,
                                                   mock_get_workspace_cloud,
                                                   mock_get_all_contexts):
        """Test that skipped contexts are logged when not silent."""
        mock_get_all_contexts.return_value = ['ssh-cluster1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None

        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=['ssh-cluster1', 'ssh-nonexistent']):
            with patch.object(ssh.SSH,
                              '_ssh_log_skipped_contexts_once') as mock_log:
                result = ssh.SSH.existing_allowed_contexts(silent=False)

                self.assertEqual(result, ['ssh-cluster1'])
                mock_log.assert_called_once_with(('ssh-nonexistent',))

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_does_not_log_skipped_contexts_when_silent(self, mock_get_nested,
                                                       mock_get_workspace_cloud,
                                                       mock_get_all_contexts):
        """Test that skipped contexts are not logged when silent=True."""
        mock_get_all_contexts.return_value = ['ssh-cluster1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None

        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=['ssh-cluster1', 'ssh-nonexistent']):
            with patch.object(ssh.SSH,
                              '_ssh_log_skipped_contexts_once') as mock_log:
                result = ssh.SSH.existing_allowed_contexts(silent=True)

                self.assertEqual(result, ['ssh-cluster1'])
                mock_log.assert_not_called()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_fallback_to_all_ssh_contexts_when_no_node_pools(
            self, mock_get_nested, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test fallback to all SSH contexts when no node pools file."""
        mock_get_all_contexts.return_value = [
            'regular-ctx', 'ssh-cluster1', 'ssh-cluster2'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None

        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=[]):
            result = ssh.SSH.existing_allowed_contexts()

            # Should return all SSH contexts when no node pools
            self.assertEqual(sorted(result), ['ssh-cluster1', 'ssh-cluster2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_allowed_node_pools_filtering_with_fallback(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test allowed_node_pools filtering with fallback scenario."""
        mock_get_all_contexts.return_value = [
            'ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['cluster1', 'cluster3']

        # No node pools file, so fallback to all SSH contexts
        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=[]):
            result = ssh.SSH.existing_allowed_contexts()

            # Should filter by allowed_node_pools even in fallback
            self.assertEqual(sorted(result), ['ssh-cluster1', 'ssh-cluster3'])

    def test_get_ssh_node_pool_contexts_with_valid_file(self):
        """Test get_ssh_node_pool_contexts with a valid YAML file."""
        test_config = {
            'cluster1': {
                'some': 'config'
            },
            'cluster2': {
                'other': 'config'
            },
            'cluster3': {
                'more': 'config'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(test_config, f)
            temp_path = f.name

        try:
            with patch('sky.clouds.ssh.SSH_NODE_POOLS_PATH', temp_path):
                result = ssh.SSH.get_ssh_node_pool_contexts()

                expected = ['ssh-cluster1', 'ssh-cluster2', 'ssh-cluster3']
                self.assertEqual(sorted(result), sorted(expected))
        finally:
            os.unlink(temp_path)

    def test_get_ssh_node_pool_contexts_with_nonexistent_file(self):
        """Test get_ssh_node_pool_contexts with nonexistent file."""
        with patch('sky.clouds.ssh.SSH_NODE_POOLS_PATH',
                   '/nonexistent/path/file.yaml'):
            result = ssh.SSH.get_ssh_node_pool_contexts()

            self.assertEqual(result, [])

    def test_get_ssh_node_pool_contexts_with_empty_file(self):
        """Test get_ssh_node_pool_contexts with empty YAML file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write('')  # Empty file
            temp_path = f.name

        try:
            with patch('sky.clouds.ssh.SSH_NODE_POOLS_PATH', temp_path):
                result = ssh.SSH.get_ssh_node_pool_contexts()

                self.assertEqual(result, [])
        finally:
            os.unlink(temp_path)

    def test_get_ssh_node_pool_contexts_with_invalid_yaml(self):
        """Test get_ssh_node_pool_contexts with invalid YAML file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write('invalid: yaml: content: [')  # Invalid YAML
            temp_path = f.name

        try:
            with patch('sky.clouds.ssh.SSH_NODE_POOLS_PATH', temp_path):
                result = ssh.SSH.get_ssh_node_pool_contexts()

                # Should return empty list on error
                self.assertEqual(result, [])
        finally:
            os.unlink(temp_path)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_complex_scenario_with_mixed_filtering(self,
                                                   mock_get_cloud_config_value,
                                                   mock_get_workspace_cloud,
                                                   mock_get_all_contexts):
        """Test complex scenario with node pools and allowed filtering."""
        # Available contexts include regular and SSH contexts
        mock_get_all_contexts.return_value = [
            'regular-ctx', 'ssh-prod', 'ssh-dev', 'ssh-staging', 'ssh-test'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = [
            'prod', 'staging', 'nonexistent'
        ]

        # Node pools file has some contexts
        with patch.object(ssh.SSH,
                          'get_ssh_node_pool_contexts',
                          return_value=['ssh-prod', 'ssh-dev', 'ssh-staging']):
            with patch.object(ssh.SSH,
                              '_ssh_log_skipped_contexts_once') as mock_log:
                result = ssh.SSH.existing_allowed_contexts()

                # Should return intersection of node pools, existing, and allowed
                # Only ssh-prod and ssh-staging should match (prod and taging)
                self.assertEqual(sorted(result), ['ssh-prod', 'ssh-staging'])
                # Should log empty tuple since all node pool contexts exist
                mock_log.assert_called_once_with(())


if __name__ == '__main__':
    unittest.main()
