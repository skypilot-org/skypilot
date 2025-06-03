"""Tests for Kubernetes cloud implementation."""

import unittest
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

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
    def test_does_not_log_skipped_contexts_when_silent(self, mock_get_nested,
                                                       mock_get_workspace_cloud,
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
        from sky.utils import resources_utils
        self.resources.network_tier = resources_utils.NetworkTier.BEST

        self.cluster_name = "test-cluster"
        self.region = mock.MagicMock()
        self.region.name = "test-context"

    @patch(
        'sky.clouds.kubernetes.Kubernetes._cluster_supports_high_performance_networking'
    )
    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.provision.kubernetes.network_utils.get_networking_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    def test_ipc_lock_capability_enabled_with_user_security_context(
            self, mock_get_image, mock_get_networking_mode, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_nested, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes,
            mock_cluster_supports_high_perf):
        """Test that IPC_LOCK capability is enabled when network tier is BEST and cluster supports it."""

        # Setup mocks
        mock_cluster_supports_high_perf.return_value = True
        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_nested.side_effect = lambda path, default=None, override_configs=None: {
            ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
            ('kubernetes', 'provision_timeout'): 10,
            ('kubernetes', 'high_availability', 'storage_class_name'): None,
        }.get(path, default)

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        mock_networking_mode = mock.MagicMock()
        mock_networking_mode.value = "portforward"
        mock_get_networking_mode.return_value = mock_networking_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=self.cluster_name,
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is True
        self.assertTrue(deploy_vars['k8s_ipc_lock_capability'])

        # Verify that NCCL environment variables are set
        k8s_env_vars = deploy_vars['k8s_env_vars']
        self.assertIn('NCCL_IB_HCA', k8s_env_vars)
        self.assertEqual(k8s_env_vars['NCCL_IB_HCA'], 'mlx5')
        self.assertIn('UCX_NET_DEVICES', k8s_env_vars)

    @patch(
        'sky.clouds.kubernetes.Kubernetes._cluster_supports_high_performance_networking'
    )
    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.provision.kubernetes.network_utils.get_networking_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    def test_ipc_lock_capability_disabled_when_no_high_perf_networking(
            self, mock_get_image, mock_get_networking_mode, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_nested, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes,
            mock_cluster_supports_high_perf):
        """Test that IPC_LOCK capability is disabled when cluster doesn't support high performance networking."""

        # Setup mocks - cluster does NOT support high performance networking
        mock_cluster_supports_high_perf.return_value = False
        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_nested.side_effect = lambda path, default=None, override_configs=None: {
            ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
            ('kubernetes', 'provision_timeout'): 10,
            ('kubernetes', 'high_availability', 'storage_class_name'): None,
        }.get(path, default)

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        mock_networking_mode = mock.MagicMock()
        mock_networking_mode.value = "portforward"
        mock_get_networking_mode.return_value = mock_networking_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=self.cluster_name,
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is False
        self.assertFalse(deploy_vars['k8s_ipc_lock_capability'])

        # Verify that NCCL environment variables are not set
        k8s_env_vars = deploy_vars['k8s_env_vars']
        self.assertNotIn('NCCL_IB_HCA', k8s_env_vars)
        self.assertNotIn('UCX_NET_DEVICES', k8s_env_vars)

    @patch(
        'sky.clouds.kubernetes.Kubernetes._cluster_supports_high_performance_networking'
    )
    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.provision.kubernetes.network_utils.get_networking_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    def test_ipc_lock_capability_disabled_when_network_tier_not_best(
            self, mock_get_image, mock_get_networking_mode, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_nested, mock_is_exec_auth,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes,
            mock_cluster_supports_high_perf):
        """Test that IPC_LOCK capability is disabled when network tier is not BEST."""

        # Modify resources to not use BEST network tier
        from sky.utils import resources_utils
        self.resources.network_tier = resources_utils.NetworkTier.STANDARD

        # Setup mocks
        mock_cluster_supports_high_perf.return_value = True  # Cluster supports it but network tier is not BEST
        mock_get_current_context.return_value = "test-context"
        mock_get_namespace.return_value = "default"
        mock_get_accelerator_label_keys.return_value = []
        mock_get_workspace_cloud.return_value.get.return_value = None
        mock_is_exec_auth.return_value = (False, None)  # Not exec auth
        mock_get_nested.side_effect = lambda path, default=None, override_configs=None: {
            ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
            ('kubernetes', 'provision_timeout'): 10,
            ('kubernetes', 'high_availability', 'storage_class_name'): None,
        }.get(path, default)

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        mock_networking_mode = mock.MagicMock()
        mock_networking_mode.value = "portforward"
        mock_get_networking_mode.return_value = mock_networking_mode

        # Mock image
        mock_get_image.return_value = "test-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=self.resources,
            cluster_name=self.cluster_name,
            region=self.region,
            zones=None,
            num_nodes=1,
            dryrun=False)

        # Verify that k8s_ipc_lock_capability is False
        self.assertFalse(deploy_vars['k8s_ipc_lock_capability'])

    @patch(
        'sky.clouds.kubernetes.Kubernetes._cluster_supports_high_performance_networking'
    )
    @patch('sky.provision.kubernetes.utils.get_kubernetes_nodes')
    @patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name'
          )
    @patch('sky.provision.kubernetes.utils.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_keys')
    @patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values')
    @patch('sky.provision.kubernetes.utils.get_gpu_resource_key')
    @patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth')
    @patch('sky.skypilot_config.get_nested')
    @patch('sky.skypilot_config.get_workspace_cloud')
    @patch('sky.provision.kubernetes.network_utils.get_port_mode')
    @patch('sky.provision.kubernetes.network_utils.get_networking_mode')
    @patch('sky.catalog.get_image_id_from_tag')
    def test_nebius_network_tier_with_gpu_environment_variables(
            self, mock_get_image, mock_get_networking_mode, mock_get_port_mode,
            mock_get_workspace_cloud, mock_get_nested, mock_is_exec_auth,
            mock_get_gpu_resource_key, mock_get_accelerator_label_key_values,
            mock_get_accelerator_label_keys, mock_get_namespace,
            mock_get_current_context, mock_get_k8s_nodes,
            mock_cluster_supports_high_perf):
        """Test Nebius network tier with GPU functionality and environment variables."""

        # Create resources with GPU and BEST network tier (mimicking H100:8 --network-tier best)
        gpu_resources = mock.MagicMock()
        gpu_resources.instance_type = "8CPU--32GB--8xH100"  # Instance with 8 H100 GPUs
        gpu_resources.accelerators = {'H100': 8}
        gpu_resources.use_spot = False
        gpu_resources.region = "nebius-context"
        gpu_resources.zone = None
        gpu_resources.cluster_config_overrides = {}
        gpu_resources.image_id = None

        # Mock the assert_launchable method to return itself
        setattr(gpu_resources, 'assert_launchable', lambda: gpu_resources)

        # Set network tier to BEST
        from sky.utils import resources_utils
        gpu_resources.network_tier = resources_utils.NetworkTier.BEST

        # Setup mocks - cluster supports high performance networking (Nebius)
        mock_cluster_supports_high_perf.return_value = True
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

        mock_get_nested.side_effect = lambda path, default=None, override_configs=None: {
            ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
            ('kubernetes', 'provision_timeout'): 10,
            ('kubernetes', 'high_availability', 'storage_class_name'): None,
        }.get(path, default)

        # Mock networking
        mock_port_mode = mock.MagicMock()
        mock_port_mode.value = "portforward"
        mock_get_port_mode.return_value = mock_port_mode

        mock_networking_mode = mock.MagicMock()
        mock_networking_mode.value = "portforward"
        mock_get_networking_mode.return_value = mock_networking_mode

        # Mock image
        mock_get_image.return_value = "test-gpu-image:latest"

        # Create Kubernetes cloud instance
        k8s_cloud = kubernetes.Kubernetes()

        # Call make_deploy_resources_variables
        deploy_vars = k8s_cloud.make_deploy_resources_variables(
            resources=gpu_resources,
            cluster_name="test-nebius-gpu-cluster",
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

    @patch('yaml.safe_load')
    @patch('sky.utils.common_utils.dump_yaml')
    @patch('sky.skypilot_config.get_nested')
    def test_user_security_context_merged_with_ipc_lock_capability(
            self, mock_get_nested, mock_dump_yaml, mock_safe_load):
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
        mock_safe_load.return_value = cluster_yaml_with_ipc_lock
        mock_get_nested.return_value = user_pod_config

        # Use a temporary file to avoid file not found error
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False,
                                         suffix='.yaml') as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Call the combine_pod_config_fields function
            kubernetes_utils.combine_pod_config_fields(tmp_path, {})

            # Verify the YAML was loaded and written
            mock_safe_load.assert_called_once()
            mock_dump_yaml.assert_called_once()

            # Get the modified YAML
            modified_yaml = mock_dump_yaml.call_args[0][1]
            container = modified_yaml['available_node_types'][
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
        finally:
            import os
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('yaml.safe_load')
    @patch('sky.utils.common_utils.dump_yaml')
    @patch('sky.skypilot_config.get_nested')
    def test_user_security_context_without_ipc_lock_capability(
            self, mock_get_nested, mock_dump_yaml, mock_safe_load):
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
        mock_safe_load.return_value = cluster_yaml_without_ipc_lock
        mock_get_nested.return_value = user_pod_config

        # Use a temporary file to avoid file not found error
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False,
                                         suffix='.yaml') as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Call the combine_pod_config_fields function
            kubernetes_utils.combine_pod_config_fields(tmp_path, {})

            # Get the modified YAML
            modified_yaml = mock_dump_yaml.call_args[0][1]
            container = modified_yaml['available_node_types'][
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
        finally:
            import os
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


if __name__ == '__main__':
    unittest.main()
