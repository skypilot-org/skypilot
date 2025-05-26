"""Tests for Kubernetes cloud implementation."""

import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from sky.clouds import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils


class TestKubernetesExistingAllowedContexts(unittest.TestCase):
    """Test cases for Kubernetes.existing_allowed_contexts method."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear any cached results
        kubernetes.Kubernetes._log_skipped_contexts_once.cache_clear()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_no_contexts_available(self, mock_get_nested, 
                                   mock_get_workspace_cloud, 
                                   mock_get_all_contexts):
        """Test when no Kubernetes contexts are available."""
        mock_get_all_contexts.return_value = []
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, [])
        mock_get_all_contexts.assert_called_once()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_workspace_allowed_contexts_takes_precedence(
            self, mock_get_nested, mock_get_workspace_cloud, 
            mock_get_all_contexts):
        """Test that workspace allowed_contexts takes precedence over global."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = ['ctx1', 'ctx2']
        mock_get_nested.return_value = ['ctx3']  # This should be ignored
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx1', 'ctx2'])
        mock_get_workspace_cloud.assert_called_once_with('kubernetes')
        # get_nested should not be called when workspace config exists
        mock_get_nested.assert_not_called()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_global_allowed_contexts_when_no_workspace_config(
            self, mock_get_nested, mock_get_workspace_cloud, 
            mock_get_all_contexts):
        """Test using global allowed_contexts when workspace config is None."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = ['ctx2', 'ctx3']
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx2', 'ctx3'])
        mock_get_nested.assert_called_once_with(
            ('kubernetes', 'allowed_contexts'), None)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_excludes_ssh_contexts(self, mock_get_nested, 
                                   mock_get_workspace_cloud, 
                                   mock_get_all_contexts):
        """Test that contexts starting with 'ssh-' are excluded."""
        mock_get_all_contexts.return_value = [
            'ctx1', 'ssh-cluster1', 'ctx2', 'ssh-cluster2'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None
        
        # Mock current context to be ctx1
        with patch('sky.provision.kubernetes.utils.'
                   'get_current_kube_config_context_name', 
                   return_value='ctx1'):
            result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx1'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.provision.kubernetes.utils.'
           'get_current_kube_config_context_name')
    @patch('sky.provision.kubernetes.utils.is_incluster_config_available')
    @patch('sky.adaptors.kubernetes.in_cluster_context_name')
    def test_fallback_to_current_context(self, mock_in_cluster_name,
                                         mock_is_incluster_available,
                                         mock_get_current_context,
                                         mock_get_nested,
                                         mock_get_workspace_cloud,
                                         mock_get_all_contexts):
        """Test fallback to current context when no allowed_contexts set."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None
        mock_get_current_context.return_value = 'ctx1'
        mock_is_incluster_available.return_value = False
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx1'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.provision.kubernetes.utils.'
           'get_current_kube_config_context_name')
    @patch('sky.provision.kubernetes.utils.is_incluster_config_available')
    @patch('sky.adaptors.kubernetes.in_cluster_context_name')
    def test_fallback_to_incluster_when_no_current_context(
            self, mock_in_cluster_name, mock_is_incluster_available,
            mock_get_current_context, mock_get_nested,
            mock_get_workspace_cloud, mock_get_all_contexts):
        """Test fallback to in-cluster context when no current context."""
        # Include the in-cluster context in all_contexts
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'in-cluster']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None
        mock_get_current_context.return_value = None
        mock_is_incluster_available.return_value = True
        mock_in_cluster_name.return_value = 'in-cluster'
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['in-cluster'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_filters_existing_contexts_only(self, mock_get_nested,
                                            mock_get_workspace_cloud,
                                            mock_get_all_contexts):
        """Test that only existing contexts are returned."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = ['ctx1', 'ctx2', 'nonexistent-ctx']
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx1', 'ctx2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_skips_ssh_contexts_in_allowed_list(self, mock_get_nested,
                                                mock_get_workspace_cloud,
                                                mock_get_all_contexts):
        """Test that SSH contexts in allowed list are skipped."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = ['ctx1', 'ssh-cluster', 'ctx2']
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, ['ctx1', 'ctx2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_logs_skipped_contexts_when_not_silent(self, mock_get_nested,
                                                   mock_get_workspace_cloud,
                                                   mock_get_all_contexts):
        """Test that skipped contexts are logged when not silent."""
        mock_get_all_contexts.return_value = ['ctx1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = ['ctx1', 'nonexistent-ctx']
        
        with patch.object(kubernetes.Kubernetes, 
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts(
                silent=False)
            
            self.assertEqual(result, ['ctx1'])
            mock_log.assert_called_once_with(('nonexistent-ctx',))

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_does_not_log_skipped_contexts_when_silent(
            self, mock_get_nested, mock_get_workspace_cloud, 
            mock_get_all_contexts):
        """Test that skipped contexts are not logged when silent=True."""
        mock_get_all_contexts.return_value = ['ctx1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = ['ctx1', 'nonexistent-ctx']
        
        with patch.object(kubernetes.Kubernetes, 
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts(
                silent=True)
            
            self.assertEqual(result, ['ctx1'])
            mock_log.assert_not_called()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.provision.kubernetes.utils.'
           'get_current_kube_config_context_name')
    @patch('sky.provision.kubernetes.utils.is_incluster_config_available')
    def test_empty_result_when_no_contexts_found(
            self, mock_is_incluster_available, mock_get_current_context,
            mock_get_nested, mock_get_workspace_cloud, mock_get_all_contexts):
        """Test empty result when no valid contexts are found."""
        mock_get_all_contexts.return_value = ['ctx1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_nested.return_value = None
        mock_get_current_context.return_value = None
        mock_is_incluster_available.return_value = False
        
        result = kubernetes.Kubernetes.existing_allowed_contexts()
        
        self.assertEqual(result, [])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    def test_complex_scenario_with_mixed_contexts(self, mock_get_nested,
                                                  mock_get_workspace_cloud,
                                                  mock_get_all_contexts):
        """Test complex scenario with various context types."""
        # Available contexts include regular, SSH, and others
        mock_get_all_contexts.return_value = [
            'prod-cluster', 'ssh-dev-cluster', 'staging-cluster', 
            'ssh-test-cluster', 'local-cluster'
        ]
        mock_get_workspace_cloud.return_value.get.return_value = None
        # Allowed contexts include existing, non-existing, and SSH contexts
        mock_get_nested.return_value = [
            'prod-cluster', 'nonexistent-cluster', 'ssh-dev-cluster',
            'staging-cluster', 'ssh-nonexistent'
        ]
        
        with patch.object(kubernetes.Kubernetes, 
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts()
            
            # Should only return non-SSH existing contexts
            self.assertEqual(sorted(result), ['prod-cluster', 'staging-cluster'])
            # Should log the nonexistent context (SSH contexts are skipped)
            mock_log.assert_called_once_with(('nonexistent-cluster',))


if __name__ == '__main__':
    unittest.main() 
