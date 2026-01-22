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
from sky.utils import resources_utils


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


class TestSSHMakeDeployResourcesVariables(unittest.TestCase):
    """Test cases for SSH.make_deploy_resources_variables method."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock resources object for testing
        self.resources = mock.MagicMock()
        self.resources.instance_type = "2CPU--4GB"
        self.resources.accelerators = None
        self.resources.use_spot = False
        self.resources.region = "ssh-my-cluster"
        self.resources.zone = None
        self.resources.cluster_config_overrides = {}
        self.resources.image_id = None

        # Mock the assert_launchable method
        setattr(self.resources, 'assert_launchable', lambda: self.resources)

        # Import NetworkTier for setting network_tier
        from sky.utils import resources_utils
        self.resources.network_tier = resources_utils.NetworkTier.BEST

        self.cluster_name = "test-ssh-cluster"
        self.region = mock.MagicMock()
        self.region.name = "ssh-my-cluster"

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_ssh_cloud_uses_ssh_config_for_provision_timeout(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that SSH cloud uses 'ssh' config (not 'kubernetes') for provision_timeout."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, '')

        mock_get_current_context.return_value = "ssh-my-cluster"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)

        # Track calls to get_effective_region_config
        config_calls = []

        def config_side_effect(cloud,
                               keys,
                               region,
                               default_value=None,
                               override_configs=None):
            config_calls.append({
                'cloud': cloud,
                'keys': keys,
                'region': region
            })
            # Return different values based on cloud and keys
            if cloud == 'ssh' and keys == ('provision_timeout',):
                return 7200  # SSH-specific timeout
            elif cloud == 'kubernetes' and keys == ('provision_timeout',):
                return 3600  # K8s-specific timeout (should not be used)
            elif keys == ('remote_identity',):
                return 'SERVICE_ACCOUNT'
            elif keys == ('high_availability', 'storage_class_name'):
                return None
            return default_value

        mock_get_cloud_config_value.side_effect = config_side_effect

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create SSH cloud instance
        ssh_cloud = ssh.SSH()

        # Verify SSH cloud has correct _REPR
        self.assertEqual(ssh_cloud._REPR, 'SSH')

        # Call make_deploy_resources_variables
        deploy_vars = ssh_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that provision_timeout was retrieved from 'ssh' config
        provision_timeout_calls = [
            call for call in config_calls
            if call['keys'] == ('provision_timeout',)
        ]
        self.assertEqual(len(provision_timeout_calls), 1)
        self.assertEqual(provision_timeout_calls[0]['cloud'], 'ssh')
        # The region should be the context name (with 'ssh-' prefix)
        self.assertEqual(provision_timeout_calls[0]['region'], 'ssh-my-cluster')

        # Verify the timeout value is set in deploy vars
        self.assertIn('timeout', deploy_vars)
        self.assertEqual(deploy_vars['timeout'], '7200')

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_ssh_cloud_uses_context_specific_config(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that SSH cloud uses context-specific config when available."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, '')

        mock_get_current_context.return_value = "ssh-prod-cluster"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)

        # Simulate context-specific config
        def config_side_effect(cloud,
                               keys,
                               region,
                               default_value=None,
                               override_configs=None):
            if cloud == 'ssh' and keys == ('provision_timeout',):
                if region == 'ssh-prod-cluster':
                    return 9000  # Context-specific timeout
                else:
                    return 7200  # Default SSH timeout
            elif keys == ('remote_identity',):
                return 'SERVICE_ACCOUNT'
            elif keys == ('high_availability', 'storage_class_name'):
                return None
            return default_value

        mock_get_cloud_config_value.side_effect = config_side_effect

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create SSH cloud instance and resources for prod cluster
        ssh_cloud = ssh.SSH()
        prod_resources = mock.MagicMock()
        prod_resources.instance_type = "2CPU--4GB"
        prod_resources.accelerators = None
        prod_resources.use_spot = False
        prod_resources.region = "ssh-prod-cluster"
        prod_resources.zone = None
        prod_resources.cluster_config_overrides = {}
        prod_resources.image_id = None
        setattr(prod_resources, 'assert_launchable', lambda: prod_resources)

        from sky.utils import resources_utils
        prod_resources.network_tier = resources_utils.NetworkTier.BEST

        prod_region = mock.MagicMock()
        prod_region.name = "ssh-prod-cluster"

        # Call make_deploy_resources_variables
        deploy_vars = ssh_cloud.make_deploy_resources_variables(
            resources=prod_resources,
            cluster_name=resources_utils.ClusterName(
                display_name="test-prod-cluster",
                name_on_cloud="test-prod-cluster"),
            region=prod_region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify the context-specific timeout is used
        self.assertIn('timeout', deploy_vars)
        self.assertEqual(deploy_vars['timeout'], '9000')


if __name__ == '__main__':
    unittest.main()
