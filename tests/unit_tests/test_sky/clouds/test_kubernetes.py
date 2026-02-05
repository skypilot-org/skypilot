"""Tests for Kubernetes cloud implementation."""

import copy
import os
import unittest
from unittest import mock
from unittest.mock import patch

import pytest

from sky.clouds import kubernetes
from sky.clouds.utils import gcp_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import resources_utils


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
        mock_get_workspace_cloud.return_value.get.return_value = [
            'ctx1', 'ctx2'
        ]
        mock_get_nested.return_value = ['ctx3']  # This should be ignored

        result = kubernetes.Kubernetes.existing_allowed_contexts()

        self.assertEqual(result, ['ctx1', 'ctx2'])
        mock_get_workspace_cloud.assert_called_once_with('kubernetes')
        # get_nested should not be called when workspace config exists
        mock_get_nested.assert_not_called()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_global_allowed_contexts_when_no_workspace_config(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test using global allowed_contexts when workspace config is None."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['ctx2', 'ctx3']

        result = kubernetes.Kubernetes.existing_allowed_contexts()

        self.assertEqual(result, ['ctx2', 'ctx3'])
        mock_get_cloud_config_value.assert_called_once_with(
            cloud='kubernetes',
            keys=('allowed_contexts',),
            region=None,
            default_value=None)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_global_allowed_all_contexts_in_config_when_no_workspace_config(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test using global allowed_contexts=all in config when workspace config is None."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = 'all'

        result = kubernetes.Kubernetes.existing_allowed_contexts()

        self.assertEqual(set(result), {'ctx1', 'ctx2', 'ctx3'})
        mock_get_cloud_config_value.assert_called_once_with(
            cloud='kubernetes',
            keys=('allowed_contexts',),
            region=None,
            default_value=None)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_env_ignored_when_global_allowed_contexts_is_set(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Env var should NOT override when global allowed_contexts is set (even empty)."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        # Global config present but empty list means no contexts allowed; env should be ignored.
        mock_get_cloud_config_value.return_value = []

        with patch.dict(os.environ,
                        {'SKYPILOT_ALLOW_ALL_KUBERNETES_CONTEXTS': 'true'},
                        clear=False):
            result = kubernetes.Kubernetes.existing_allowed_contexts()

        # Since global allowed_contexts is explicitly set (empty), env is ignored -> no contexts.
        self.assertEqual(result, [])
        mock_get_cloud_config_value.assert_called_once_with(
            cloud='kubernetes',
            keys=('allowed_contexts',),
            region=None,
            default_value=None)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_env_does_not_override_global_when_present(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Env var should NOT override global allowed_contexts when present."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['ctx1']

        with patch.dict(os.environ,
                        {'SKYPILOT_ALLOW_ALL_KUBERNETES_CONTEXTS': 'true'},
                        clear=False):
            result = kubernetes.Kubernetes.existing_allowed_contexts()

        # Global allowed_contexts is set; env should be ignored -> only ctx1 allowed.
        self.assertEqual(result, ['ctx1'])
        mock_get_cloud_config_value.assert_called_once_with(
            cloud='kubernetes',
            keys=('allowed_contexts',),
            region=None,
            default_value=None)

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_env_does_not_override_workspace_when_present(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Env var should NOT override workspace/global allowed_contexts when present."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']
        # Workspace config present
        mock_get_workspace_cloud.return_value.get.return_value = ['ctx1']
        # Global config also present but should be ignored due to env override
        mock_get_cloud_config_value.return_value = ['ctx1', 'ctx2']

        with patch.dict(os.environ,
                        {'SKYPILOT_ALLOW_ALL_KUBERNETES_CONTEXTS': 'true'},
                        clear=False):
            result = kubernetes.Kubernetes.existing_allowed_contexts()
        # Workspace allowed_contexts takes precedence; env ignored -> only ctx1 allowed.
        self.assertEqual(result, ['ctx1'])

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
        with patch(
                'sky.provision.kubernetes.utils.'
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
            mock_get_current_context, mock_get_nested, mock_get_workspace_cloud,
            mock_get_all_contexts):
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
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_filters_existing_contexts_only(self, mock_get_cloud_config_value,
                                            mock_get_workspace_cloud,
                                            mock_get_all_contexts):
        """Test that only existing contexts are returned."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = [
            'ctx1', 'ctx2', 'nonexistent-ctx'
        ]

        result = kubernetes.Kubernetes.existing_allowed_contexts()

        self.assertEqual(result, ['ctx1', 'ctx2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_skips_ssh_contexts_in_allowed_list(self,
                                                mock_get_cloud_config_value,
                                                mock_get_workspace_cloud,
                                                mock_get_all_contexts):
        """Test that SSH contexts in allowed list are skipped."""
        mock_get_all_contexts.return_value = ['ctx1', 'ctx2']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = [
            'ctx1', 'ssh-cluster', 'ctx2'
        ]

        result = kubernetes.Kubernetes.existing_allowed_contexts()

        self.assertEqual(result, ['ctx1', 'ctx2'])

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_logs_skipped_contexts_when_not_silent(self,
                                                   mock_get_cloud_config_value,
                                                   mock_get_workspace_cloud,
                                                   mock_get_all_contexts):
        """Test that skipped contexts are logged when not silent."""
        mock_get_all_contexts.return_value = ['ctx1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['ctx1', 'nonexistent-ctx']

        with patch.object(kubernetes.Kubernetes,
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts(
                silent=False)

            self.assertEqual(result, ['ctx1'])
            mock_log.assert_called_once_with(('nonexistent-ctx',))

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_does_not_log_skipped_contexts_when_silent(
            self, mock_get_cloud_config_value, mock_get_workspace_cloud,
            mock_get_all_contexts):
        """Test that skipped contexts are not logged when silent=True."""
        mock_get_all_contexts.return_value = ['ctx1']
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_get_cloud_config_value.return_value = ['ctx1', 'nonexistent-ctx']

        with patch.object(kubernetes.Kubernetes,
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts(
                silent=True)

            self.assertEqual(result, ['ctx1'])
            mock_log.assert_not_called()

    @patch('sky.provision.kubernetes.utils.get_all_kube_context_names')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
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
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_complex_scenario_with_mixed_contexts(self,
                                                  mock_get_cloud_config_value,
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
        mock_get_cloud_config_value.return_value = [
            'prod-cluster', 'nonexistent-cluster', 'ssh-dev-cluster',
            'staging-cluster', 'ssh-nonexistent'
        ]

        with patch.object(kubernetes.Kubernetes,
                          '_log_skipped_contexts_once') as mock_log:
            result = kubernetes.Kubernetes.existing_allowed_contexts()

            # Should only return non-SSH existing contexts
            self.assertEqual(sorted(result),
                             ['prod-cluster', 'staging-cluster'])
            # Should log the nonexistent context (SSH contexts are skipped)
            mock_log.assert_called_once_with(('nonexistent-cluster',))


class TestKubernetesSecurityContextMerging(unittest.TestCase):
    """Test cases for merging user-specified securityContext with IPC_LOCK capability."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock resources object for testing
        self.resources = mock.MagicMock()
        self.resources.instance_type = "2CPU--4GB"  # Use valid Kubernetes instance type format
        self.resources.accelerators = None
        self.resources.use_spot = False
        self.resources.region = "test-context"
        self.resources.zone = None
        self.resources.cluster_config_overrides = {}
        self.resources.image_id = None  # Set image_id to None to use default

        # Mock the assert_launchable method to return itself
        # Use setattr to avoid the assertion detection issue
        setattr(self.resources, 'assert_launchable', lambda: self.resources)

        # Import NetworkTier for setting network_tier
        self.resources.network_tier = resources_utils.NetworkTier.BEST

        self.cluster_name = "test-cluster"
        self.region = mock.MagicMock()
        self.region.name = "test-context"

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
    def test_ipc_lock_capability_enabled_with_user_security_context(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that IPC_LOCK capability is enabled when network tier is BEST and cluster supports it."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_cluster_type = mock.MagicMock()
        mock_cluster_type.supports_high_performance_networking.return_value = True
        mock_cluster_type.requires_ipc_lock_capability.return_value = True
        mock_detect_network_type.return_value = (mock_cluster_type, None)

        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_cloud_config_value.side_effect = (
            lambda cloud, keys, region, default_value=None, override_configs=
            None: {
                ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
                ('kubernetes', 'provision_timeout'): 10,
                ('kubernetes', 'high_availability', 'storage_class_name'): None,
            }.get((cloud,) + keys, default_value))

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is True
        self.assertTrue(deploy_vars['k8s_ipc_lock_capability'])

        # For GCP clusters, NCCL environment variables are set in the template, not Python code
        # We should not expect them in k8s_env_vars for GCP cluster types
        k8s_env_vars = deploy_vars['k8s_env_vars']
        self.assertIn('SKYPILOT_IN_CLUSTER_CONTEXT_NAME', k8s_env_vars)
        self.assertEqual(k8s_env_vars['SKYPILOT_IN_CLUSTER_CONTEXT_NAME'],
                         'test-context')

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
    def test_ipc_lock_capability_disabled_when_no_high_perf_networking(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that IPC_LOCK capability is disabled when cluster doesn't support high performance networking."""

        # Setup mocks - cluster does NOT support high performance networking
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_cloud_config_value.side_effect = (
            lambda cloud, keys, region, default_value=None, override_configs=
            None: {
                ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
                ('kubernetes', 'provision_timeout'): 10,
                ('kubernetes', 'high_availability', 'storage_class_name'): None,
            }.get((cloud,) + keys, default_value))

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is False
        self.assertFalse(deploy_vars['k8s_ipc_lock_capability'])

        # Verify that high performance networking environment variables are not set
        k8s_env_vars = deploy_vars['k8s_env_vars']
        self.assertNotIn('NCCL_IB_HCA', k8s_env_vars)
        self.assertNotIn('UCX_NET_DEVICES', k8s_env_vars)

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
    def test_ipc_lock_capability_disabled_when_network_tier_not_best(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that IPC_LOCK capability is disabled when network tier is not BEST."""

        # Modify resources to not use BEST network tier
        self.resources.network_tier = resources_utils.NetworkTier.STANDARD

        # Setup mocks - when network tier is not BEST, _detect_network_type returns NONE
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_cloud_config_value.side_effect = (
            lambda cloud, keys, region, default_value=None, override_configs=
            None: {
                ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
                ('kubernetes', 'provision_timeout'): 10,
                ('kubernetes', 'high_availability', 'storage_class_name'): None,
            }.get((cloud,) + keys, default_value))

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is False
        self.assertFalse(deploy_vars['k8s_ipc_lock_capability'])

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values')
    @patch('sky.provision.kubernetes.utils.get_gpu_resource_key')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_nebius_network_tier_with_gpu_environment_variables(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_gpu_resource_key,
            mock_get_accelerator_label_key_values,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes):
        """Test Nebius network tier with GPU functionality and environment variables."""

        # Create resources with GPU and BEST network tier (mimicking H100:8 --network-tier best)
        gpu_resources = mock.MagicMock()
        gpu_resources.instance_type = "8CPU--32GB--H100:8"  # Instance with 8 H100 GPUs
        gpu_resources.accelerators = {'H100': 8}
        gpu_resources.use_spot = False
        gpu_resources.region = "nebius-context"
        gpu_resources.zone = None
        gpu_resources.cluster_config_overrides = {}
        gpu_resources.image_id = None

        # Mock the assert_launchable method to return itself
        setattr(gpu_resources, 'assert_launchable', lambda: gpu_resources)

        # Set network tier to BEST
        gpu_resources.network_tier = resources_utils.NetworkTier.BEST

        # Setup mocks - cluster supports high performance networking (Nebius)
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NEBIUS, None)

        mock_get_current_context.return_value = "nebius-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)

        # Mock GPU-related functions
        mock_get_accelerator_label_key_values.return_value = ('accelerator',
                                                              ['H100'
                                                              ], None, None)
        mock_get_gpu_resource_key.return_value = 'nvidia.com/gpu'

        mock_get_cloud_config_value.side_effect = (
            lambda cloud, keys, region, default_value=None, override_configs=
            None: {
                ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
                ('kubernetes', 'provision_timeout'): 10,
                ('kubernetes', 'high_availability', 'storage_class_name'): None,
            }.get((cloud,) + keys, default_value))

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        # Mock image
        mock_get_image.return_value = "test-gpu-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=gpu_resources,
            cluster_name=resources_utils.ClusterName(
                display_name="test-nebius-gpu-cluster",
                name_on_cloud="test-nebius-gpu-cluster"),
            region=mock.MagicMock(name="nebius-context"),
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is True for GPU + BEST network tier
        self.assertTrue(deploy_vars['k8s_ipc_lock_capability'])

        # Verify that NCCL and UCX environment variables are set correctly
        k8s_env_vars = deploy_vars['k8s_env_vars']

        # Check NCCL_IB_HCA environment variable
        self.assertIn('NCCL_IB_HCA', k8s_env_vars)
        self.assertEqual(k8s_env_vars['NCCL_IB_HCA'], 'mlx5')

        # Check UCX_NET_DEVICES environment variable
        self.assertIn('UCX_NET_DEVICES', k8s_env_vars)
        expected_ucx_devices = 'mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1'
        self.assertEqual(k8s_env_vars['UCX_NET_DEVICES'], expected_ucx_devices)

        # Verify GPU-related variables are set
        self.assertEqual(deploy_vars['accelerator_count'], '8')
        self.assertEqual(deploy_vars['k8s_acc_label_key'], 'accelerator')
        self.assertEqual(deploy_vars['k8s_acc_label_values'], ['H100'])
        self.assertEqual(deploy_vars['k8s_resource_key'], 'nvidia.com/gpu')
        self.assertFalse(deploy_vars['tpu_requested'])  # H100 is GPU, not TPU


class TestKubernetesMakeDeployResourcesVariables(unittest.TestCase):
    """Test cases for Kubernetes.make_deploy_resources_variables method."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock resources object for testing
        self.resources = mock.MagicMock()
        self.resources.instance_type = "2CPU--4GB"
        self.resources.accelerators = None
        self.resources.use_spot = False
        self.resources.region = "my-k8s-cluster"
        self.resources.zone = None
        self.resources.cluster_config_overrides = {}
        self.resources.image_id = None

        # Mock the assert_launchable method
        setattr(self.resources, 'assert_launchable', lambda: self.resources)

        # Import NetworkTier for setting network_tier
        self.resources.network_tier = resources_utils.NetworkTier.BEST

        self.cluster_name = "test-k8s-cluster"
        self.region = mock.MagicMock()
        self.region.name = "my-k8s-cluster"

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
    def test_kubernetes_cloud_uses_kubernetes_config_for_provision_timeout(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that Kubernetes cloud uses 'kubernetes' config (not 'ssh') for provision_timeout."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "my-k8s-cluster"
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
            if cloud == 'kubernetes' and keys == ('provision_timeout',):
                return 3600  # Kubernetes-specific timeout
            elif cloud == 'ssh' and keys == ('provision_timeout',):
                return 7200  # SSH-specific timeout (should not be used)
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

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Verify Kubernetes cloud has correct _REPR
        self.assertEqual(k8s_cloud._REPR, 'Kubernetes')

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that provision_timeout was retrieved from 'kubernetes' config
        provision_timeout_calls = [
            call for call in config_calls
            if call['keys'] == ('provision_timeout',)
        ]
        self.assertEqual(len(provision_timeout_calls), 1)
        self.assertEqual(provision_timeout_calls[0]['cloud'], 'kubernetes')
        # The region should be the context name
        self.assertEqual(provision_timeout_calls[0]['region'], 'my-k8s-cluster')

        # Verify the timeout value is set in deploy vars
        self.assertIn('timeout', deploy_vars)
        self.assertEqual(deploy_vars['timeout'], '3600')

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
    def test_kubernetes_cloud_uses_context_specific_config(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that Kubernetes cloud uses context-specific config when available."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "prod-k8s-cluster"
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
            if cloud == 'kubernetes' and keys == ('provision_timeout',):
                if region == 'prod-k8s-cluster':
                    return 5400  # Context-specific timeout
                else:
                    return 3600  # Default Kubernetes timeout
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

        # Create Kubernetes cloud instance and resources for prod cluster
        k8s_cloud = kubernetes.Kubernetes()
        prod_resources = mock.MagicMock()
        prod_resources.instance_type = "2CPU--4GB"
        prod_resources.accelerators = None
        prod_resources.use_spot = False
        prod_resources.region = "prod-k8s-cluster"
        prod_resources.zone = None
        prod_resources.cluster_config_overrides = {}
        prod_resources.image_id = None
        setattr(prod_resources, 'assert_launchable', lambda: prod_resources)

        prod_resources.network_tier = resources_utils.NetworkTier.BEST

        prod_region = mock.MagicMock()
        prod_region.name = "prod-k8s-cluster"

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
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
        self.assertEqual(deploy_vars['timeout'], '5400')

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
    def test_remote_identity_with_cluster_overrides(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_cloud_config_value,
            mock_is_exec_auth, mock_get_accelerator_label_keys,
            mock_get_namespace, mock_get_current_context, mock_get_k8s_nodes):
        """Test that remote_identity override from task config is passed correctly."""

        # Setup mocks
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "my-k8s-cluster"
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
                'region': region,
                'override_configs': override_configs
            })
            if keys == ('remote_identity',):
                # Return NO_UPLOAD when override is provided
                if override_configs and override_configs.get(
                        'kubernetes', {}).get('remote_identity') == 'NO_UPLOAD':
                    return 'NO_UPLOAD'
                return 'SERVICE_ACCOUNT'
            elif keys == ('provision_timeout',):
                return 3600
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

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Set up resources with cluster_config_overrides
        override_resources = mock.MagicMock()
        override_resources.instance_type = "2CPU--4GB"
        override_resources.accelerators = None
        override_resources.use_spot = False
        override_resources.region = "my-k8s-cluster"
        override_resources.zone = None
        override_resources.cluster_config_overrides = {
            'kubernetes': {
                'remote_identity': 'NO_UPLOAD'
            }
        }
        override_resources.image_id = None
        setattr(override_resources, 'assert_launchable',
                lambda: override_resources)
        override_resources.network_tier = resources_utils.NetworkTier.BEST

        # Call make_deploy_resources_variables
        k8s_cloud.make_deploy_resources_variables(
            resources=override_resources,
            cluster_name=resources_utils.ClusterName(
                display_name="test-cluster", name_on_cloud="test-cluster"),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Find the call for remote_identity
        remote_identity_calls = [
            c for c in config_calls if c['keys'] == ('remote_identity',)
        ]
        self.assertTrue(
            len(remote_identity_calls) > 0,
            "remote_identity config should be fetched")

        # Verify override_configs was passed
        remote_identity_call = remote_identity_calls[0]
        self.assertEqual(
            remote_identity_call['override_configs'],
            {'kubernetes': {
                'remote_identity': 'NO_UPLOAD'
            }},
            "override_configs should be passed to get_effective_region_config")

    def _setup_mocks_for_pod_resource_limits_test(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_workspace_region_config,
            mock_get_cloud_config_value, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, set_pod_resource_limits_value):
        """Helper to set up common mocks for set_pod_resource_limits tests."""
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        mock_get_current_context.return_value = "my-k8s-cluster"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)

        def workspace_config_side_effect(cloud,
                                         region,
                                         keys,
                                         default_value=None,
                                         override_configs=None):
            if keys == ('set_pod_resource_limits',):
                return set_pod_resource_limits_value
            elif keys == ('kueue', 'local_queue_name'):
                return None
            return default_value

        mock_get_workspace_region_config.side_effect = workspace_config_side_effect

        def config_side_effect(cloud,
                               keys,
                               region,
                               default_value=None,
                               override_configs=None):
            if keys == ('remote_identity',):
                return 'SERVICE_ACCOUNT'
            elif keys == ('high_availability', 'storage_class_name'):
                return None
            elif keys == ('provision_timeout',):
                return 600
            return default_value

        mock_get_cloud_config_value.side_effect = config_side_effect

        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode
        mock_get_image.return_value = "test-image:latest"

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_effective_workspace_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_set_pod_resource_limits_config_option(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_workspace_region_config,
            mock_get_cloud_config_value, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes):
        """Test that set_pod_resource_limits=True sets limits equal to requests."""
        self._setup_mocks_for_pod_resource_limits_test(
            mock_detect_network_type,
            mock_get_image,
            mock_get_port_mode,
            mock_get_workspace_cloud,
            mock_get_workspace_region_config,
            mock_get_cloud_config_value,
            mock_is_exec_auth,
            mock_get_accelerator_label_keys,
            mock_get_namespace,
            mock_get_current_context,
            set_pod_resource_limits_value=True)

        k8s_cloud = kubernetes.Kubernetes()
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Instance type "2CPU--4GB" means cpus=2, memory=4
        # With True (multiplier 1.0): limits = requests
        self.assertIn('k8s_cpu_limit', deploy_vars)
        self.assertIn('k8s_memory_limit', deploy_vars)
        self.assertEqual(deploy_vars['k8s_cpu_limit'], 2.0)
        self.assertEqual(deploy_vars['k8s_memory_limit'], 4.0)

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_effective_workspace_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_set_pod_resource_limits_with_multiplier(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_workspace_region_config,
            mock_get_cloud_config_value, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes):
        """Test set_pod_resource_limits with a numeric multiplier value."""
        self._setup_mocks_for_pod_resource_limits_test(
            mock_detect_network_type,
            mock_get_image,
            mock_get_port_mode,
            mock_get_workspace_cloud,
            mock_get_workspace_region_config,
            mock_get_cloud_config_value,
            mock_is_exec_auth,
            mock_get_accelerator_label_keys,
            mock_get_namespace,
            mock_get_current_context,
            set_pod_resource_limits_value=1.5)

        k8s_cloud = kubernetes.Kubernetes()
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Instance type "2CPU--4GB" means cpus=2, memory=4
        # With multiplier 1.5: limits = requests * 1.5
        self.assertIn('k8s_cpu_limit', deploy_vars)
        self.assertIn('k8s_memory_limit', deploy_vars)
        self.assertEqual(deploy_vars['k8s_cpu_limit'], 3.0)
        self.assertEqual(deploy_vars['k8s_memory_limit'], 6.0)

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.skypilot_config.get_effective_workspace_region_config')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_set_pod_resource_limits_disabled(
            self, mock_detect_network_type, mock_get_image, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_workspace_region_config,
            mock_get_cloud_config_value, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes):
        """Test set_pod_resource_limits when disabled (False)."""
        self._setup_mocks_for_pod_resource_limits_test(
            mock_detect_network_type,
            mock_get_image,
            mock_get_port_mode,
            mock_get_workspace_cloud,
            mock_get_workspace_region_config,
            mock_get_cloud_config_value,
            mock_is_exec_auth,
            mock_get_accelerator_label_keys,
            mock_get_namespace,
            mock_get_current_context,
            set_pod_resource_limits_value=False)

        k8s_cloud = kubernetes.Kubernetes()
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=resources_utils.ClusterName(
                display_name=self.cluster_name,
                name_on_cloud=self.cluster_name),
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # With False: no limits should be set
        self.assertNotIn('k8s_cpu_limit', deploy_vars)
        self.assertNotIn('k8s_memory_limit', deploy_vars)


class TestKubernetesSecurityContext(unittest.TestCase):
    """Test cases for Kubernetes security context handling."""

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_user_security_context_merged_with_ipc_lock_capability(
            self, mock_get_cloud_config_value):
        """Test that user-specified securityContext is correctly merged with IPC_LOCK capability."""

        # Create a YAML structure with IPC_LOCK capability set
        cluster_yaml_with_ipc_lock = {
            'available_node_types': {
                'ray_head_default': {
                    'node_config': {
                        'apiVersion': 'v1',
                        'kind': 'Pod',
                        'spec': {
                            'containers': [{
                                'name': 'ray-node',
                                'image': 'test-image',
                                'securityContext': {
                                    'capabilities': {
                                        'add': ['IPC_LOCK']
                                    }
                                }
                            }]
                        }
                    }
                }
            }
        }

        # User config with additional securityContext
        user_pod_config = {
            'spec': {
                'containers': [{
                    'securityContext': {
                        'runAsUser': 1000,
                        'runAsGroup': 1000,
                        'capabilities': {
                            'add': ['SYS_ADMIN'],
                            'drop': ['NET_RAW']
                        }
                    }
                }]
            }
        }

        # Set up mocks
        mock_get_cloud_config_value.return_value = user_pod_config

        # Call the combine_pod_config_fields function
        combined_yaml_obj = kubernetes_utils.combine_pod_config_fields(
            cluster_yaml_with_ipc_lock, {}, None)

        # Get the modified YAML
        container = combined_yaml_obj['available_node_types'][
            'ray_head_default']['node_config']['spec']['containers'][0]

        # Verify that both IPC_LOCK and user-specified capabilities are present
        security_context = container['securityContext']

        # Check that runAsUser and runAsGroup from user config are preserved
        self.assertEqual(security_context['runAsUser'], 1000)
        self.assertEqual(security_context['runAsGroup'], 1000)

        # Check that capabilities are merged correctly
        add_capabilities = security_context['capabilities']['add']
        self.assertIn('IPC_LOCK', add_capabilities)  # From template
        self.assertIn('SYS_ADMIN', add_capabilities)  # From user config

        # Check that drop capabilities from user config are preserved
        drop_capabilities = security_context['capabilities']['drop']
        self.assertIn('NET_RAW', drop_capabilities)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_user_security_context_without_ipc_lock_capability(
            self, mock_get_cloud_config_value):
        """Test that user-specified securityContext works when IPC_LOCK capability is not needed."""

        # Create a YAML structure without IPC_LOCK capability
        cluster_yaml_without_ipc_lock = {
            'available_node_types': {
                'ray_head_default': {
                    'node_config': {
                        'apiVersion': 'v1',
                        'kind': 'Pod',
                        'spec': {
                            'containers': [{
                                'name': 'ray-node',
                                'image': 'test-image'
                                # No securityContext initially
                            }]
                        }
                    }
                }
            }
        }

        # User config with securityContext
        user_pod_config = {
            'spec': {
                'containers': [{
                    'securityContext': {
                        'runAsUser': 1000,
                        'capabilities': {
                            'add': ['SYS_ADMIN'],
                            'drop': ['NET_RAW']
                        }
                    }
                }]
            }
        }

        # Set up mocks
        mock_get_cloud_config_value.return_value = user_pod_config

        # Call the combine_pod_config_fields function
        combined_yaml_obj = kubernetes_utils.combine_pod_config_fields(
            cluster_yaml_without_ipc_lock, {}, None)

        # Get the modified YAML
        container = combined_yaml_obj['available_node_types'][
            'ray_head_default']['node_config']['spec']['containers'][0]

        # Verify that only user-specified securityContext is present
        security_context = container['securityContext']

        # Check that runAsUser from user config is preserved
        self.assertEqual(security_context['runAsUser'], 1000)

        # Check that only user capabilities are present (no IPC_LOCK)
        add_capabilities = security_context['capabilities']['add']
        self.assertIn('SYS_ADMIN', add_capabilities)
        self.assertNotIn('IPC_LOCK', add_capabilities)

        # Check that drop capabilities from user config are preserved
        drop_capabilities = security_context['capabilities']['drop']
        self.assertIn('NET_RAW', drop_capabilities)


class TestKubernetesVolumeMerging(unittest.TestCase):
    """Test cases for merging user-specified volume mounts and volumes with pod_config."""

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_user_volume_mounts_merged_correctly(self,
                                                 mock_get_cloud_config_value):
        """Test that user-specified volume mounts and volumes are correctly merged."""

        # Based on kubernetes-ray.yml.j2
        cluster_yaml_with_system_volumes = {
            'available_node_types': {
                'ray_head_default': {
                    'node_config': {
                        'apiVersion': 'v1',
                        'kind': 'Pod',
                        'spec': {
                            'volumes': [{
                                'name': 'dshm',
                                'emptyDir': {
                                    'medium': 'Memory'
                                }
                            }],
                            'containers': [{
                                'name': 'ray-node',
                                'image': 'test-image',
                                'volumeMounts': [{
                                    'mountPath': '/dev/shm',
                                    'name': 'dshm'
                                }]
                            }]
                        }
                    }
                }
            }
        }

        # User config with additional volume mounts and volumes
        user_pod_config = {
            'spec': {
                'containers': [{
                    'volumeMounts': [{
                        'name': 'data-volume',
                        'mountPath': '/data',
                        'readOnly': False
                    }, {
                        'name': 'logs-volume',
                        'mountPath': '/logs',
                        'readOnly': False
                    }]
                }],
                'volumes': [{
                    'name': 'data-volume',
                    'persistentVolumeClaim': {
                        'claimName': 'data-pvc',
                        'readOnly': False
                    }
                }, {
                    'name': 'logs-volume',
                    'persistentVolumeClaim': {
                        'claimName': 'logs-pvc',
                        'readOnly': False
                    }
                }]
            }
        }

        mock_get_cloud_config_value.return_value = user_pod_config

        combined_yaml_obj = kubernetes_utils.combine_pod_config_fields(
            cluster_yaml_with_system_volumes, {}, None)

        # Get the modified YAML
        container = combined_yaml_obj['available_node_types'][
            'ray_head_default']['node_config']['spec']['containers'][0]
        pod_spec = combined_yaml_obj['available_node_types'][
            'ray_head_default']['node_config']['spec']

        # Verify that both system and user volume mounts are present
        volume_mounts = container['volumeMounts']
        self.assertEqual(len(volume_mounts), 3)  # 1 system + 2 user

        # Check system volume mounts are preserved
        dshm_mount = next((vm for vm in volume_mounts if vm['name'] == 'dshm'),
                          None)
        self.assertIsNotNone(dshm_mount)
        self.assertEqual(dshm_mount['mountPath'], '/dev/shm')

        # Check user volume mounts are added
        data_mount = next(
            (vm for vm in volume_mounts if vm['name'] == 'data-volume'), None)
        self.assertIsNotNone(data_mount)
        self.assertEqual(data_mount['mountPath'], '/data')
        self.assertFalse(data_mount['readOnly'])

        logs_mount = next(
            (vm for vm in volume_mounts if vm['name'] == 'logs-volume'), None)
        self.assertIsNotNone(logs_mount)
        self.assertEqual(logs_mount['mountPath'], '/logs')
        self.assertFalse(logs_mount['readOnly'])

        # Verify that both system and user volumes are present
        volumes = pod_spec['volumes']
        self.assertEqual(len(volumes), 3)  # 1 system + 2 user

        # Check system volumes are preserved
        dshm_volume = next((v for v in volumes if v['name'] == 'dshm'), None)
        self.assertIsNotNone(dshm_volume)
        self.assertIn('emptyDir', dshm_volume)
        self.assertEqual(dshm_volume['emptyDir']['medium'], 'Memory')

        # Check user volumes are added
        data_volume = next((v for v in volumes if v['name'] == 'data-volume'),
                           None)
        self.assertIsNotNone(data_volume)
        self.assertIn('persistentVolumeClaim', data_volume)
        self.assertEqual(data_volume['persistentVolumeClaim']['claimName'],
                         'data-pvc')

        logs_volume = next((v for v in volumes if v['name'] == 'logs-volume'),
                           None)
        self.assertIsNotNone(logs_volume)
        self.assertIn('persistentVolumeClaim', logs_volume)
        self.assertEqual(logs_volume['persistentVolumeClaim']['claimName'],
                         'logs-pvc')


class TestGKEDWSConfig(unittest.TestCase):
    """Test cases for gcp_utils.get_dws_config method."""

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_no_dws_config(self, mock_get_config):
        """Test when no DWS config is provided."""
        mock_get_config.return_value = {}
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', None))

        self.assertFalse(enable_flex_start)
        self.assertFalse(enable_flex_start_queued_provisioning)
        self.assertIsNone(max_run_duration)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_dws_not_enabled(self, mock_get_config):
        """Test when DWS is configured but not enabled."""
        mock_get_config.return_value = {'enabled': False}
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', None))

        self.assertFalse(enable_flex_start)
        self.assertFalse(enable_flex_start_queued_provisioning)
        self.assertIsNone(max_run_duration)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_dws_enabled_without_kueue(self, mock_get_config):
        """Test when DWS is enabled without Kueue queue name."""
        mock_get_config.return_value = {'enabled': True}
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', None))

        self.assertTrue(enable_flex_start)
        self.assertFalse(enable_flex_start_queued_provisioning)
        self.assertIsNone(max_run_duration)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_dws_enabled_with_kueue(self, mock_get_config):
        """Test when DWS is enabled with Kueue queue name."""
        mock_get_config.return_value = {'enabled': True}
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', 'test-queue'))

        self.assertFalse(enable_flex_start)
        self.assertTrue(enable_flex_start_queued_provisioning)
        self.assertIsNone(max_run_duration)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_dws_enabled_with_max_duration(self, mock_get_config):
        """Test when DWS is enabled with max run duration."""
        mock_get_config.return_value = {
            'enabled': True,
            'max_run_duration': '2h'
        }
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', 'test-queue'))

        self.assertFalse(enable_flex_start)
        self.assertTrue(enable_flex_start_queued_provisioning)
        # 2h = 120 minutes = 7200 seconds
        self.assertEqual(max_run_duration, 7200)

    @patch('sky.skypilot_config.get_effective_region_config')
    def test_dws_with_cluster_overrides(self, mock_get_config):
        """Test DWS config with cluster overrides."""
        mock_get_config.return_value = {'enabled': False}
        cluster_overrides = {'dws': {'enabled': False}}
        enable_flex_start, enable_flex_start_queued_provisioning, max_run_duration = (
            gcp_utils.get_dws_config('test-context', None, cluster_overrides))

        self.assertFalse(enable_flex_start)
        self.assertFalse(enable_flex_start_queued_provisioning)
        self.assertIsNone(max_run_duration)
        # Verify the override_configs parameter was passed
        mock_get_config.assert_called_once_with(
            cloud='kubernetes',
            region='test-context',
            keys=('dws',),
            default_value={},
            override_configs=cluster_overrides)


class TestKubernetesVolumeNameValidation(unittest.TestCase):

    def test_valid_volume_names(self):
        valid_names = [
            'data',
            'data1',
            'data-1',
            'data-1.volume',
            'a-b.c-d',
            'a' * 10 + '.' + 'b' * 10,
        ]
        for name in valid_names:
            ok, reason = kubernetes.Kubernetes.is_volume_name_valid(name)
            self.assertTrue(ok, msg=f'{name} should be valid, got: {reason}')

    def test_invalid_due_to_length(self):
        too_long = 'a' * 254  # > 253
        ok, reason = kubernetes.Kubernetes.is_volume_name_valid(too_long)
        self.assertFalse(ok)
        self.assertIn('maximum length', reason or '')

    def test_invalid_characters(self):
        # Uppercase and underscore are invalid
        for name in ['Data', 'data_volume', 'data@vol', 'data+vol']:
            ok, _ = kubernetes.Kubernetes.is_volume_name_valid(name)
            self.assertFalse(ok, msg=f'{name} should be invalid')

    def test_invalid_start_or_end(self):
        for name in ['-data', 'data-', '.data', 'data.']:
            ok, _ = kubernetes.Kubernetes.is_volume_name_valid(name)
            self.assertFalse(ok, msg=f'{name} should be invalid')

    def test_invalid_double_dot(self):
        ok, _ = kubernetes.Kubernetes.is_volume_name_valid('a..b')
        self.assertFalse(ok)

    def test_empty_string_invalid(self):
        ok, _ = kubernetes.Kubernetes.is_volume_name_valid('')
        self.assertFalse(ok)


class TestCloudFlare403ErrorDetection(unittest.TestCase):
    """Test cases for CloudFlare 403 error detection and retry."""

    def test_cloudflare_403_with_cf_ray_header(self):
        """Test that 403 with CF-RAY header is detected as CloudFlare error."""
        mock_exception = mock.Mock()
        mock_exception.status = 403
        mock_exception.headers = {
            'Date': 'Wed, 08 Oct 2025 19:26:17 GMT',
            'CF-RAY': '98b8076cfae4058d-IAD',
            'Server': 'cloudflare'
        }

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_exception)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_exception)

        self.assertTrue(result)

    def test_cloudflare_403_with_server_cloudflare(self):
        """Test that 403 with Server: cloudflare header is detected."""
        mock_exception = mock.Mock()
        mock_exception.status = 403
        mock_exception.headers = {
            'Date': 'Wed, 08 Oct 2025 19:26:17 GMT',
            'Server': 'cloudflare',
            'Content-Length': '0'
        }

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_exception)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_exception)

        self.assertTrue(result)

    def test_real_rbac_403_not_cloudflare(self):
        """Test that real RBAC 403 without CloudFlare headers is NOT detected."""
        mock_exception = mock.Mock()
        mock_exception.status = 403
        mock_exception.headers = {
            'Date': 'Wed, 08 Oct 2025 19:26:17 GMT',
            'Content-Type': 'application/json'
        }

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_exception)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_exception)

        self.assertFalse(result)

    def test_other_status_codes_not_checked(self):
        """Test that non-403 status codes return False (only 403 is checked)."""
        # Test 401
        mock_401 = mock.Mock()
        mock_401.status = 401
        mock_401.headers = {'CF-RAY': '12345-IAD', 'Server': 'cloudflare'}

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_401)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_401)
            self.assertFalse(result)

        # Test 404
        mock_404 = mock.Mock()
        mock_404.status = 404
        mock_404.headers = {'CF-RAY': '12345-IAD', 'Server': 'cloudflare'}

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_404)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_404)
            self.assertFalse(result)

        # Test 429
        mock_429 = mock.Mock()
        mock_429.status = 429
        mock_429.headers = {'CF-RAY': '12345-IAD', 'Server': 'cloudflare'}

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_429)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_429)
            self.assertFalse(result)

    def test_no_headers_not_detected(self):
        """Test that 403 without headers is not detected as CloudFlare."""
        mock_exception = mock.Mock()
        mock_exception.status = 403
        # Simulate hasattr returning False
        del mock_exception.headers

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_exception)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_exception)

        self.assertFalse(result)

    def test_case_insensitive_header_matching(self):
        """Test that header matching is case-insensitive."""
        mock_exception = mock.Mock()
        mock_exception.status = 403
        mock_exception.headers = {
            'cf-ray': '12345-IAD',  # lowercase
            'server': 'CloudFlare'  # mixed case
        }

        with patch('sky.adaptors.kubernetes.api_exception',
                   return_value=type(mock_exception)):
            result = kubernetes_utils._is_cloudflare_403_error(mock_exception)

        self.assertTrue(result)

    def test_check_nvidia_runtime_class_retries_on_cloudflare_403(self):
        """Test that check_nvidia_runtime_class retries on CloudFlare 403.

        Simulates CloudFlare proxy returning transient 403 on first
        call, then succeeding on retry.
        """

        # Create a CloudFlare 403 exception class
        class CloudFlare403Exception(Exception):

            def __init__(self):
                self.status = 403
                self.headers = {
                    'Date': 'Wed, 08 Oct 2025 19:26:17 GMT',
                    'CF-RAY': '98b8076cfae4058d-IAD',
                    'Server': 'cloudflare',
                    'Content-Length': '0'
                }
                super().__init__('Forbidden')

        # Successful response
        mock_runtime_class = mock.Mock()
        mock_runtime_class.metadata.name = 'nvidia'
        successful_response = mock.Mock()
        successful_response.items = [mock_runtime_class]

        call_count = {'count': 0}

        def mock_list_runtime_class():
            call_count['count'] += 1
            if call_count['count'] == 1:
                raise CloudFlare403Exception()
            else:
                return successful_response

        mock_node_api = mock.Mock()
        mock_node_api.list_runtime_class = mock_list_runtime_class

        with patch('sky.adaptors.kubernetes.node_api',
                   return_value=mock_node_api), \
             patch('sky.adaptors.kubernetes.api_exception', return_value=CloudFlare403Exception), \
             patch('time.sleep'):

            result = kubernetes_utils.check_nvidia_runtime_class(
                context='test-context')

            self.assertTrue(result)
            self.assertEqual(call_count['count'], 2)

    def test_check_nvidia_runtime_class_fails_on_real_rbac_403(self):
        """Test that real RBAC 403 errors fail immediately without retry."""

        # Real RBAC 403 (no CloudFlare headers)
        class RBACApiException(Exception):

            def __init__(self):
                self.status = 403
                self.headers = {
                    'Date': 'Wed, 08 Oct 2025 19:26:17 GMT',
                    'Content-Type': 'application/json'
                }
                super().__init__('Forbidden: User does not have permission')

        call_count = {'count': 0}

        def mock_list_runtime_class():
            call_count['count'] += 1
            raise RBACApiException()

        mock_node_api = mock.Mock()
        mock_node_api.list_runtime_class = mock_list_runtime_class

        from sky import exceptions as sky_exceptions

        with patch('sky.adaptors.kubernetes.node_api',
                   return_value=mock_node_api), \
             patch('sky.adaptors.kubernetes.api_exception', return_value=RBACApiException):

            with self.assertRaises(sky_exceptions.KubeAPIUnreachableError):
                kubernetes_utils.check_nvidia_runtime_class(
                    context='test-context')

            self.assertEqual(call_count['count'], 1)


class TestKubernetesUnsupportedFeaturesForResources(unittest.TestCase):
    """Test cases for Kubernetes._unsupported_features_for_resources method."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear any cached results
        kubernetes.Kubernetes.logged_unreachable_contexts.clear()

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_basic_unsupported_features(self, mock_detect_network_type,
                                        mock_get_spot_label,
                                        mock_existing_allowed_contexts):
        """Test basic unsupported features without spot or network tier support."""
        mock_existing_allowed_contexts.return_value = ['test-context']
        mock_get_spot_label.return_value = (None, None)  # No spot support
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        resources = mock.MagicMock()
        resources.region = None
        resources.network_tier = None

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources)

        # Should have basic unsupported features
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE, result)
        self.assertIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                      result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_spot_instance_supported(self, mock_detect_network_type,
                                     mock_get_spot_label,
                                     mock_existing_allowed_contexts):
        """Test when spot instances are supported."""
        mock_existing_allowed_contexts.return_value = ['test-context']
        mock_get_spot_label.return_value = ('spot-label-key',
                                            'spot-label-value')
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        resources = mock.MagicMock()
        resources.region = 'test-context'
        resources.network_tier = None

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources, region='test-context')

        # Spot instance should not be in unsupported features
        self.assertNotIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE,
                         result)
        # Other features should still be unsupported
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                      result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_custom_network_tier_supported(self, mock_detect_network_type,
                                           mock_get_spot_label,
                                           mock_existing_allowed_contexts):
        """Test when custom network tier is supported."""
        mock_existing_allowed_contexts.return_value = ['test-context']
        mock_get_spot_label.return_value = (None, None)
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NEBIUS, None)

        resources = mock.MagicMock()
        resources.region = 'test-context'
        resources.network_tier = resources_utils.NetworkTier.BEST

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources, region='test-context')

        # Custom network tier should not be in unsupported features
        self.assertNotIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                         result)
        # Other features should still be unsupported
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_both_spot_and_network_tier_supported(
            self, mock_detect_network_type, mock_get_spot_label,
            mock_existing_allowed_contexts):
        """Test when both spot and custom network tier are supported."""
        mock_existing_allowed_contexts.return_value = ['test-context']
        mock_get_spot_label.return_value = ('spot-label-key',
                                            'spot-label-value')
        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            })

        resources = mock.MagicMock()
        resources.region = 'test-context'
        resources.network_tier = resources_utils.NetworkTier.BEST

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources, region='test-context')

        # Both should not be in unsupported features
        self.assertNotIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE,
                         result)
        self.assertNotIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                         result)
        # Other features should still be unsupported
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    def test_api_unreachable(self, mock_get_spot_label,
                             mock_existing_allowed_contexts):
        """Test when Kubernetes API is unreachable."""
        mock_existing_allowed_contexts.return_value = ['test-context']
        from sky import exceptions as sky_exceptions
        mock_get_spot_label.side_effect = (
            sky_exceptions.KubeAPIUnreachableError('API unreachable'))

        resources = mock.MagicMock()
        resources.region = 'test-context'
        resources.network_tier = None

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources, region='test-context')

        # Should return base unsupported features
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE, result)
        self.assertIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                      result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.get_spot_label')
    @patch('sky.clouds.kubernetes.Kubernetes._detect_network_type')
    def test_multiple_contexts(self, mock_detect_network_type,
                               mock_get_spot_label,
                               mock_existing_allowed_contexts):
        """Test with multiple contexts."""
        mock_existing_allowed_contexts.return_value = ['context1', 'context2']

        # First context supports spot, second doesn't
        def spot_label_side_effect(context):
            if context == 'context1':
                return ('spot-key', 'spot-value')
            return (None, None)

        mock_get_spot_label.side_effect = spot_label_side_effect

        from sky.provision.kubernetes.utils import (
            KubernetesHighPerformanceNetworkType)
        mock_detect_network_type.return_value = (
            KubernetesHighPerformanceNetworkType.NONE, None)

        resources = mock.MagicMock()
        resources.region = None
        resources.network_tier = None

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources)

        # If any context supports spot, it should not be in unsupported
        self.assertNotIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE,
                         result)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    def test_no_contexts_available(self, mock_existing_allowed_contexts):
        """Test when no contexts are available."""
        mock_existing_allowed_contexts.return_value = []

        resources = mock.MagicMock()
        resources.region = None
        resources.network_tier = None

        from sky import clouds
        result = kubernetes.Kubernetes._unsupported_features_for_resources(
            resources)

        # Should return base unsupported features
        self.assertIn(clouds.CloudImplementationFeatures.STOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.AUTOSTOP, result)
        self.assertIn(clouds.CloudImplementationFeatures.SPOT_INSTANCE, result)
        self.assertIn(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                      result)


class TestKubernetesRegionsWithOffering(unittest.TestCase):
    """Test cases for Kubernetes.regions_with_offering method."""

    def setUp(self):
        """Set up test fixtures."""
        kubernetes.Kubernetes.logged_unreachable_contexts.clear()

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    def test_no_instance_type_returns_all_regions(
            self, mock_existing_allowed_contexts):
        """Test that when instance_type is None, all regions are returned."""
        mock_existing_allowed_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type=None,
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        self.assertEqual(len(regions), 3)
        region_names = [r.name for r in regions]
        self.assertIn('ctx1', region_names)
        self.assertIn('ctx2', region_names)
        self.assertIn('ctx3', region_names)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    def test_filter_by_region(self, mock_existing_allowed_contexts):
        """Test filtering by specific region."""
        mock_existing_allowed_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type=None,
            accelerators=None,
            use_spot=False,
            region='ctx2',
            zone=None,
            resources=None)

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, 'ctx2')

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.clouds.kubernetes.Kubernetes.check_features_are_supported')
    def test_filter_by_required_features(self, mock_check_features,
                                         mock_existing_allowed_contexts):
        """Test filtering by resources' required features."""
        mock_existing_allowed_contexts.return_value = ['ctx1', 'ctx2', 'ctx3']

        # ctx1 and ctx3 support features, ctx2 doesn't
        def check_features_side_effect(resources, features, region):
            if region == 'ctx2':
                from sky import exceptions as sky_exceptions
                raise sky_exceptions.NotSupportedError('Not supported')

        mock_check_features.side_effect = check_features_side_effect

        resources = mock.MagicMock()
        resources.get_required_cloud_features.return_value = {}

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type=None,
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=resources)

        self.assertEqual(len(regions), 2)
        region_names = [r.name for r in regions]
        self.assertIn('ctx1', region_names)
        self.assertIn('ctx3', region_names)
        self.assertNotIn('ctx2', region_names)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    def test_instance_fits_in_cluster(self, mock_check_instance_fits,
                                      mock_existing_allowed_contexts):
        """Test when instance type fits in the cluster."""
        mock_existing_allowed_contexts.return_value = ['ctx1', 'ctx2']
        mock_check_instance_fits.return_value = (True, None)

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='4CPU--8GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        self.assertEqual(len(regions), 2)
        self.assertEqual(mock_check_instance_fits.call_count, 2)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    def test_instance_does_not_fit_no_autoscaler(
            self, mock_check_instance_fits, mock_existing_allowed_contexts):
        """Test when instance doesn't fit and no autoscaler configured."""
        mock_existing_allowed_contexts.return_value = ['ctx1']
        mock_check_instance_fits.return_value = (False, 'Not enough resources')

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='8CPU--16GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        # Should return empty list since instance doesn't fit
        self.assertEqual(len(regions), 0)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_instance_does_not_fit_with_autoscaler_can_create(
            self, mock_get_autoscaler, mock_get_config,
            mock_check_instance_fits, mock_existing_allowed_contexts):
        """Test instance doesn't fit but autoscaler can create it."""
        mock_existing_allowed_contexts.return_value = ['ctx1']
        mock_check_instance_fits.return_value = (False, 'Not enough resources')
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.Mock()
        mock_autoscaler.can_query_backend = True
        mock_autoscaler.can_create_new_instance_of_type.return_value = True
        mock_get_autoscaler.return_value = mock_autoscaler

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='8CPU--16GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        # Should return the region since autoscaler can create it
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, 'ctx1')

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_unsupported_autoscaler_type(self, mock_get_autoscaler,
                                         mock_get_config,
                                         mock_check_instance_fits,
                                         mock_existing_allowed_contexts):
        """Test with autoscaler type that can't query backend."""
        mock_existing_allowed_contexts.return_value = ['ctx1']
        mock_check_instance_fits.return_value = (False, 'Not enough resources')
        # Use a valid autoscaler type (generic kubernetes)
        mock_get_config.return_value = 'generic'

        mock_autoscaler = mock.Mock()
        mock_autoscaler.can_query_backend = False
        mock_get_autoscaler.return_value = mock_autoscaler

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='8CPU--16GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        # Should return the region (rely on autoscaler without checks)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, 'ctx1')

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    def test_api_unreachable_context_excluded(self, mock_check_instance_fits,
                                              mock_existing_allowed_contexts):
        """Test that unreachable contexts are excluded."""
        mock_existing_allowed_contexts.return_value = ['ctx1', 'ctx2']

        def check_instance_side_effect(context, instance_type):
            if context == 'ctx1':
                from sky import exceptions as sky_exceptions
                raise sky_exceptions.KubeAPIUnreachableError('API unreachable')
            return (True, None)

        mock_check_instance_fits.side_effect = check_instance_side_effect

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='4CPU--8GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        # Only ctx2 should be returned
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, 'ctx2')

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_autoscaler_cannot_create_instance(self, mock_get_autoscaler,
                                               mock_get_config,
                                               mock_check_instance_fits,
                                               mock_existing_allowed_contexts):
        """Test when autoscaler exists but cannot create instance type."""
        mock_existing_allowed_contexts.return_value = ['ctx1']
        mock_check_instance_fits.return_value = (False, 'Not enough resources')
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.Mock()
        mock_autoscaler.can_query_backend = True
        mock_autoscaler.can_create_new_instance_of_type.return_value = False
        mock_get_autoscaler.return_value = mock_autoscaler

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='8CPU--16GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        # Should return empty list
        self.assertEqual(len(regions), 0)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    @patch('sky.clouds.kubernetes.Kubernetes.check_features_are_supported')
    @patch('sky.provision.kubernetes.utils.check_instance_fits')
    def test_complex_scenario_with_multiple_filters(
            self, mock_check_instance_fits, mock_check_features,
            mock_existing_allowed_contexts):
        """Test complex scenario with multiple filtering conditions."""
        mock_existing_allowed_contexts.return_value = [
            'ctx1', 'ctx2', 'ctx3', 'ctx4'
        ]

        # ctx2 doesn't support required features
        def check_features_side_effect(resources, features, region):
            if region == 'ctx2':
                from sky import exceptions as sky_exceptions
                raise sky_exceptions.NotSupportedError('Not supported')

        mock_check_features.side_effect = check_features_side_effect

        # ctx3 doesn't have enough resources
        def check_instance_side_effect(context, instance_type):
            if context == 'ctx3':
                return (False, 'Not enough resources')
            return (True, None)

        mock_check_instance_fits.side_effect = check_instance_side_effect

        resources = mock.MagicMock()
        resources.get_required_cloud_features.return_value = {}

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='4CPU--8GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=resources)

        # Only ctx1 and ctx4 should be returned
        self.assertEqual(len(regions), 2)
        region_names = [r.name for r in regions]
        self.assertIn('ctx1', region_names)
        self.assertIn('ctx4', region_names)

    @patch('sky.clouds.kubernetes.Kubernetes.existing_allowed_contexts')
    def test_no_contexts_available(self, mock_existing_allowed_contexts):
        """Test when no contexts are available."""
        mock_existing_allowed_contexts.return_value = []

        regions = kubernetes.Kubernetes.regions_with_offering(
            instance_type='4CPU--8GB',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None,
            resources=None)

        self.assertEqual(len(regions), 0)


class TestKubernetesDetectNetworkType(unittest.TestCase):
    """Test cases for Kubernetes._detect_network_type method."""

    def _create_mock_node(self, labels=None):
        """Helper to create a mock Kubernetes node with labels."""
        mock_node = mock.MagicMock()
        mock_node.metadata.labels = labels or {}
        return mock_node

    def test_network_tier_none_returns_none(self):
        """Test that when network_tier is None, returns NONE type."""
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context', network_tier=None)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    def test_network_tier_not_best_returns_none(self):
        """Test that when network_tier is not BEST, returns NONE type."""
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.STANDARD)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_nebius_cluster_detection(self, mock_get_nodes):
        """Test detection of Nebius clusters via node labels."""
        mock_node = self._create_mock_node({
            'nebius.com/gpu-model': 'h100',
            'kubernetes.io/hostname': 'node-1'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NEBIUS,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_coreweave_cluster_detection(self, mock_get_nodes):
        """Test detection of CoreWeave clusters via node labels."""
        mock_node = self._create_mock_node({
            'ib.coreweave.cloud/enabled': 'true',
            'kubernetes.io/hostname': 'node-1'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.COREWEAVE,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_together_cluster_detection(self, mock_get_nodes):
        """Test detection of Together AI clusters via node labels."""
        mock_node = self._create_mock_node({
            'node-role.together.ai/gpu': 'true',
            'kubernetes.io/hostname': 'node-1'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.TOGETHER,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a3_highgpu_detection(self, mock_get_nodes):
        """Test detection of GKE A3 highgpu instance type."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a3',
            'node.kubernetes.io/instance-type': 'a3-highgpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a3_edgegpu_detection(self, mock_get_nodes):
        """Test detection of GKE A3 edgegpu instance type."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a3',
            'node.kubernetes.io/instance-type': 'a3-edgegpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-edgegpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a3_megagpu_detection(self, mock_get_nodes):
        """Test detection of GKE A3 megagpu instance type (TCPXO)."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a3',
            'node.kubernetes.io/instance-type': 'a3-megagpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPXO, {
                'instance_type': 'a3-megagpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a4_highgpu_detection(self, mock_get_nodes):
        """Test detection of GKE A4 highgpu instance type (GPUDirect RDMA)."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a4',
            'node.kubernetes.io/instance-type': 'a4-highgpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-b200'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(result,
                         (kubernetes_utils.KubernetesHighPerformanceNetworkType.
                          GCP_GPUDIRECT_RDMA, {
                              'instance_type': 'a4-highgpu-8g'
                          }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a3_ultragpu_detection(self, mock_get_nodes):
        """Test detection of GKE A3 ultragpu instance type (GPUDirect RDMA)."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a3',
            'node.kubernetes.io/instance-type': 'a3-ultragpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h200'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(result,
                         (kubernetes_utils.KubernetesHighPerformanceNetworkType.
                          GCP_GPUDIRECT_RDMA, {
                              'instance_type': 'a3-ultragpu-8g'
                          }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_a4_generic_fallback(self, mock_get_nodes):
        """Test generic A4 machine family detection as fallback."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a4',
            'node.kubernetes.io/instance-type': 'a4-unknown-type',
            'cloud.google.com/gke-accelerator': 'nvidia-b200'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(result,
                         (kubernetes_utils.KubernetesHighPerformanceNetworkType.
                          GCP_GPUDIRECT_RDMA, {
                              'instance_type': 'a4'
                          }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_tcpx_fallback_with_h100(self, mock_get_nodes):
        """Test TCPX fallback detection via GPU and instance type."""
        mock_node = self._create_mock_node({
            'node.kubernetes.io/instance-type': 'a3-highgpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_tcpx_fallback_with_h200(self, mock_get_nodes):
        """Test TCPX fallback detection via H200 GPU."""
        mock_node = self._create_mock_node({
            'node.kubernetes.io/instance-type': 'a3-edgegpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-h200-141gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-edgegpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_gke_tcpx_fallback_with_b200(self, mock_get_nodes):
        """Test TCPX fallback detection via B200 GPU."""
        mock_node = self._create_mock_node({
            'node.kubernetes.io/instance-type': 'a3-highgpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-b200-180gb'
        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_no_high_perf_gpu_returns_none_from_node_loop(self, mock_get_nodes):
        """Test node with TCPX instance but no high-perf GPU doesn't match."""
        mock_node = self._create_mock_node({
            'node.kubernetes.io/instance-type': 'a3-highgpu-8g',
            'cloud.google.com/gke-accelerator': 'nvidia-t4'
        })
        mock_get_nodes.return_value = [mock_node]

        # This should not match the TCPX fallback, so continue to autoscaler check
        with patch('sky.skypilot_config.get_effective_region_config',
                   return_value=None):
            result = kubernetes.Kubernetes._detect_network_type(
                context='test-context',
                network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_node_without_labels_continues_iteration(self, mock_get_nodes):
        """Test that nodes without labels are skipped."""
        mock_node_no_labels = mock.MagicMock()
        mock_node_no_labels.metadata.labels = None

        mock_node_with_labels = self._create_mock_node(
            {'nebius.com/gpu-model': 'h100'})

        mock_get_nodes.return_value = [
            mock_node_no_labels, mock_node_with_labels
        ]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NEBIUS,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_kube_api_unreachable_falls_through(self, mock_get_nodes):
        """Test that KubeAPIUnreachableError is caught and continues."""
        from sky import exceptions
        mock_get_nodes.side_effect = exceptions.KubeAPIUnreachableError(
            'Cannot reach cluster')

        with patch('sky.skypilot_config.get_effective_region_config',
                   return_value=None):
            result = kubernetes.Kubernetes._detect_network_type(
                context='test-context',
                network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_autoscaler_non_gke_returns_none(self, mock_get_config,
                                             mock_get_nodes):
        """Test that non-GKE autoscaler returns NONE."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'karpenter'

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_a3_highgpu(self, mock_get_autoscaler,
                                       mock_get_config, mock_get_nodes):
        """Test GKE autoscaler with a3-highgpu-8g machine type."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'a3-highgpu-8g', 'n2-standard-8'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_a3_edgegpu(self, mock_get_autoscaler,
                                       mock_get_config, mock_get_nodes):
        """Test GKE autoscaler with a3-edgegpu-8g machine type."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'a3-edgegpu-8g'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-edgegpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_a3_megagpu(self, mock_get_autoscaler,
                                       mock_get_config, mock_get_nodes):
        """Test GKE autoscaler with a3-megagpu-8g machine type."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'a3-megagpu-8g'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPXO, {
                'instance_type': 'a3-megagpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_a4_highgpu(self, mock_get_autoscaler,
                                       mock_get_config, mock_get_nodes):
        """Test GKE autoscaler with a4-highgpu-8g machine type."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'a4-highgpu-8g'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(result,
                         (kubernetes_utils.KubernetesHighPerformanceNetworkType.
                          GCP_GPUDIRECT_RDMA, {
                              'instance_type': 'a4-highgpu-8g'
                          }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_a3_ultragpu(self, mock_get_autoscaler,
                                        mock_get_config, mock_get_nodes):
        """Test GKE autoscaler with a3-ultragpu-8g machine type."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'a3-ultragpu-8g'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(result,
                         (kubernetes_utils.KubernetesHighPerformanceNetworkType.
                          GCP_GPUDIRECT_RDMA, {
                              'instance_type': 'a3-ultragpu-8g'
                          }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_no_high_perf_types(self, mock_get_autoscaler,
                                               mock_get_config, mock_get_nodes):
        """Test GKE autoscaler without high-performance machine types."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        mock_autoscaler.get_available_machine_types.return_value = [
            'n1-standard-4', 'n2-standard-8'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    def test_autoscaler_none_returns_none(self, mock_get_config,
                                          mock_get_nodes):
        """Test that None autoscaler config returns NONE."""
        mock_get_nodes.return_value = []
        mock_get_config.return_value = None

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_empty_node_list_falls_through_to_autoscaler(self, mock_get_nodes):
        """Test that empty node list falls through to autoscaler check."""
        mock_get_nodes.return_value = []

        with patch('sky.skypilot_config.get_effective_region_config',
                   return_value=None):
            result = kubernetes.Kubernetes._detect_network_type(
                context='test-context',
                network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_a3_without_specific_instance_no_fallback(self, mock_get_nodes):
        """Test A3 machine family without specific instance type doesn't match A4 fallback."""
        mock_node = self._create_mock_node({
            'cloud.google.com/machine-family': 'a3',
            'node.kubernetes.io/instance-type': 'a3-some-other-type',
            'cloud.google.com/gke-accelerator': 'nvidia-v100'
        })
        mock_get_nodes.return_value = [mock_node]

        with patch('sky.skypilot_config.get_effective_region_config',
                   return_value=None):
            result = kubernetes.Kubernetes._detect_network_type(
                context='test-context',
                network_tier=resources_utils.NetworkTier.BEST)

        # A3 family without specific instance type and without high-perf GPU
        # should fall through to NONE
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.skypilot_config.get_effective_region_config')
    @patch('sky.provision.kubernetes.utils.get_autoscaler')
    def test_gke_autoscaler_priority_order(self, mock_get_autoscaler,
                                           mock_get_config, mock_get_nodes):
        """Test that autoscaler checks machine types in priority order.

        When multiple high-perf types available, a3-highgpu-8g should be
        returned first.
        """
        mock_get_nodes.return_value = []
        mock_get_config.return_value = 'gke'

        mock_autoscaler = mock.MagicMock()
        # Include multiple high-perf types, but a3-highgpu-8g should match first
        mock_autoscaler.get_available_machine_types.return_value = [
            'a3-ultragpu-8g', 'a4-highgpu-8g', 'a3-megagpu-8g', 'a3-edgegpu-8g',
            'a3-highgpu-8g'
        ]
        mock_get_autoscaler.return_value = mock_autoscaler

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        # a3-highgpu-8g is checked first in the elif chain
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.GCP_TCPX, {
                'instance_type': 'a3-highgpu-8g'
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_multiple_nodes_first_match_wins(self, mock_get_nodes):
        """Test that first matching node determines the result."""
        nebius_node = self._create_mock_node({'nebius.com/gpu-model': 'h100'})
        coreweave_node = self._create_mock_node(
            {'ib.coreweave.cloud/enabled': 'true'})

        mock_get_nodes.return_value = [nebius_node, coreweave_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        # Nebius is detected first
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NEBIUS,
             None))

    def _create_mock_node_with_allocatable(self, labels=None, allocatable=None):
        """Helper to create a mock Kubernetes node with labels and allocatable resources."""
        mock_node = mock.MagicMock()
        mock_node.metadata.labels = labels or {}
        mock_node.status.allocatable = allocatable or {}
        return mock_node

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_basic(self, mock_get_nodes):
        """Test detection of AWS EKS clusters via node labels (without GPU params)."""
        mock_node = self._create_mock_node(
            {'k8s.io/cloud-provider-aws': 'true'})
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_via_topology_k8s_aws_label(self, mock_get_nodes):
        """Test detection of AWS EKS clusters via topology.k8s.aws label prefix."""
        mock_node = self._create_mock_node(
            {'topology.k8s.aws/zone-id': 'use1-az1'})
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_via_topology_ebs_csi_label(self, mock_get_nodes):
        """Test detection of AWS EKS clusters via topology.ebs.csi.aws.com label prefix."""
        mock_node = self._create_mock_node(
            {'topology.ebs.csi.aws.com/zone': 'us-east-1a'})
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_without_acc_params(self, mock_get_nodes):
        """Test AWS EFA detection returns early when GPU params are not specified."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={'k8s.io/cloud-provider-aws': 'true'},
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node]

        # Without k8s_acc_label_key, should return early without EFA count
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_with_efa_resources(self, mock_get_nodes):
        """Test AWS EFA detection with EFA count calculation."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=8)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA, {
                'efa_count': 4
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_proportional_allocation(self, mock_get_nodes):
        """Test AWS EFA count is calculated proportionally to GPU request."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node]

        # Requesting 4 GPUs out of 8 should give 2 EFAs (4/8 * 4 = 2)
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=4)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA, {
                'efa_count': 2
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_minimum_one_efa(self, mock_get_nodes):
        """Test AWS EFA count is at least 1 when EFA is available."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node]

        # Requesting 1 GPU out of 8 should give at least 1 EFA (floor(1/8 * 4) = 0, but min is 1)
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=1)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA, {
                'efa_count': 1
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_node_without_enough_gpus(self, mock_get_nodes):
        """Test AWS EFA detection skips nodes without enough GPUs."""
        # First node doesn't have enough GPUs
        mock_node1 = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '4',
                'vpc.amazonaws.com/efa': '2'
            })
        # Second node has enough GPUs
        mock_node2 = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node1, mock_node2]

        # Requesting 8 GPUs - first node only has 4, should use second node
        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=8)

        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA, {
                'efa_count': 4
            }))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_node_without_efa_resource(self, mock_get_nodes):
        """Test AWS EFA detection when node doesn't have EFA resources."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={'nvidia.com/gpu': '8'
                         # No EFA resource
                        })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=8)

        # Should return AWS_EFA type but without efa_count metadata
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_node_without_gpu_label(self, mock_get_nodes):
        """Test AWS EFA detection skips nodes without the required GPU label."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                # Missing 'nvidia.com/gpu.product' label
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '4'
            })
        mock_get_nodes.return_value = [mock_node]

        with patch('sky.skypilot_config.get_effective_region_config',
                   return_value=None):
            result = kubernetes.Kubernetes._detect_network_type(
                context='test-context',
                network_tier=resources_utils.NetworkTier.BEST,
                k8s_acc_label_key='nvidia.com/gpu.product',
                k8s_resource_key='nvidia.com/gpu',
                acc_count=8)

        # Node doesn't have the required GPU label, continues to next node
        # Since there's no matching node and no GKE autoscaler, returns NONE
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE, None))

    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    def test_aws_efa_detection_zero_efa_available(self, mock_get_nodes):
        """Test AWS EFA detection when EFA count is zero."""
        mock_node = self._create_mock_node_with_allocatable(
            labels={
                'k8s.io/cloud-provider-aws': 'true',
                'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'
            },
            allocatable={
                'nvidia.com/gpu': '8',
                'vpc.amazonaws.com/efa': '0'
            })
        mock_get_nodes.return_value = [mock_node]

        result = kubernetes.Kubernetes._detect_network_type(
            context='test-context',
            network_tier=resources_utils.NetworkTier.BEST,
            k8s_acc_label_key='nvidia.com/gpu.product',
            k8s_resource_key='nvidia.com/gpu',
            acc_count=8)

        # EFA count is 0, so AWS_EFA is still returned but without efa_count metadata
        self.assertEqual(
            result,
            (kubernetes_utils.KubernetesHighPerformanceNetworkType.AWS_EFA,
             None))


if __name__ == '__main__':
    unittest.main()
