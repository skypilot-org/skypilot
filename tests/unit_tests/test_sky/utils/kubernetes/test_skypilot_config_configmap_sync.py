"""Tests for SkyPilot config ConfigMap synchronization functionality."""
import os
import tempfile
import unittest
from unittest import mock

from sky.utils import config_utils
from sky.utils.kubernetes import config_map_utils


class TestConfigMapSync(unittest.TestCase):

    def setUp(self):
        self.mock_kubernetes = mock.MagicMock()

    def test_is_running_in_kubernetes_true(self):
        """Test detection of running in Kubernetes environment."""
        with mock.patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            self.assertTrue(config_map_utils.is_running_in_kubernetes())

    def test_is_running_in_kubernetes_false(self):
        """Test detection of not running in Kubernetes environment."""
        with mock.patch('os.path.exists') as mock_exists:
            mock_exists.return_value = False
            self.assertFalse(config_map_utils.is_running_in_kubernetes())

    def test_get_kubernetes_namespace_from_file(self):
        """Test reading namespace from service account file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test-namespace')
            f.flush()

            with mock.patch('os.path.exists') as mock_exists, \
                 mock.patch('builtins.open',
                            mock.mock_open(read_data='test-namespace')):
                mock_exists.return_value = True
                namespace = config_map_utils._get_kubernetes_namespace()
                self.assertEqual(namespace, 'test-namespace')

            os.unlink(f.name)

    def test_get_kubernetes_namespace_default(self):
        """Test fallback to default namespace when file doesn't exist."""
        with mock.patch('os.path.exists') as mock_exists:
            mock_exists.return_value = False
            namespace = config_map_utils._get_kubernetes_namespace()
            self.assertEqual(namespace, 'default')

    def test_get_configmap_name_from_env(self):
        """Test ConfigMap name detection from environment variables."""
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_RELEASE_NAME': 'my-release'}):
            configmap_name = config_map_utils._get_configmap_name()
            self.assertEqual(configmap_name, 'my-release-config')

    def test_get_configmap_name_default(self):
        """Test ConfigMap name fallback to default."""
        with mock.patch.dict(os.environ, {}, clear=True):
            configmap_name = config_map_utils._get_configmap_name()
            self.assertEqual(configmap_name, 'skypilot-config')

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_patch_configmap_not_in_kubernetes(self, mock_is_k8s):
        """Test that ConfigMap patching returns early when not in Kubernetes."""
        mock_is_k8s.return_value = False

        config = config_utils.Config({'test': 'value'})
        # Should return early without raising exceptions when not in Kubernetes
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            config_path = f.name

        try:
            # Should not raise any exceptions, just return early
            config_map_utils.patch_configmap_with_config(config, config_path)
        finally:
            os.unlink(config_path)

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                '_get_kubernetes_namespace')
    @mock.patch('sky.utils.kubernetes.config_map_utils._get_configmap_name')
    @mock.patch('sky.utils.yaml_utils.dump_yaml_str')
    @mock.patch('sky.utils.kubernetes.config_map_utils.kubernetes')
    def test_patch_configmap_success(self, mock_k8s, mock_dump_yaml,
                                     mock_get_name, mock_get_ns, mock_is_k8s):
        """Test successful ConfigMap patching."""
        mock_is_k8s.return_value = True
        mock_get_ns.return_value = 'test-namespace'
        mock_get_name.return_value = 'test-configmap'
        mock_dump_yaml.return_value = 'workspaces:\n  test:\n    aws: {}'

        mock_core_api = mock.MagicMock()
        mock_k8s.core_api.return_value = mock_core_api

        # Ensure the exception types are properly mocked
        class MockApiException(Exception):

            def __init__(self, status=404):
                super().__init__()
                self.status = status

        mock_k8s.kubernetes.client.rest.ApiException = MockApiException

        # Mock successful patch (no exception raised)
        mock_core_api.patch_namespaced_config_map.return_value = None

        config = config_utils.Config({'workspaces': {'test': {'aws': {}}}})
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            config_path = f.name

        try:
            config_map_utils.patch_configmap_with_config(config, config_path)
        finally:
            os.unlink(config_path)

        # Verify the ConfigMap patch was called
        mock_core_api.patch_namespaced_config_map.assert_called_once()
        call_args = mock_core_api.patch_namespaced_config_map.call_args

        self.assertEqual(call_args[1]['name'], 'test-configmap')
        self.assertEqual(call_args[1]['namespace'], 'test-namespace')
        self.assertIn('data', call_args[1]['body'])
        self.assertIn('config.yaml', call_args[1]['body']['data'])

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_patch_configmap_import_error(self, mock_is_k8s):
        """Test handling of import error for Kubernetes client."""
        mock_is_k8s.return_value = True

        with mock.patch('sky.adaptors.kubernetes', side_effect=ImportError()):
            config = config_utils.Config({'test': 'value'})
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                config_path = f.name

            try:
                # Should not raise exceptions, just log and continue
                config_map_utils.patch_configmap_with_config(
                    config, config_path)
            finally:
                os.unlink(config_path)

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'patch_configmap_with_config')
    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_update_api_server_config_no_lock_calls_patch_in_kubernetes(
            self, mock_is_k8s, mock_patch):
        """Test that update_api_server_config_no_lock calls ConfigMap patching in K8s."""
        mock_is_k8s.return_value = True

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            config_path = f.name

        from sky import skypilot_config
        with mock.patch.object(skypilot_config, 'get_user_config_path',
                               return_value=config_path), \
             mock.patch('sky.utils.yaml_utils.dump_yaml') as mock_dump_yaml, \
             mock.patch('sky.skypilot_config.reload_config'):

            config = config_utils.Config({'test': 'value'})
            skypilot_config.update_api_server_config_no_lock(config)

            # In Kubernetes, should call both dump_yaml and ConfigMap patching
            mock_patch.assert_called_once_with(config, config_path)
            mock_dump_yaml.assert_called_once_with(config_path, dict(config))

        os.unlink(config_path)

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'patch_configmap_with_config')
    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_update_api_server_config_no_lock_calls_dump_yaml_non_kubernetes(
            self, mock_is_k8s, mock_patch):
        """Test that update_api_server_config_no_lock calls dump_yaml in non-K8s envs."""
        mock_is_k8s.return_value = False

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            config_path = f.name

        from sky import skypilot_config
        with mock.patch.object(skypilot_config, 'get_user_config_path',
                               return_value=config_path), \
             mock.patch('sky.utils.yaml_utils.dump_yaml') as mock_dump_yaml, \
             mock.patch('sky.skypilot_config.reload_config'):

            config = config_utils.Config({'test': 'value'})
            skypilot_config.update_api_server_config_no_lock(config)

            # In non-Kubernetes, should call dump_yaml but not ConfigMap patch
            mock_dump_yaml.assert_called_once_with(config_path, dict(config))
            mock_patch.assert_not_called()

        os.unlink(config_path)

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                '_get_kubernetes_namespace')
    @mock.patch('sky.utils.kubernetes.config_map_utils._get_configmap_name')
    @mock.patch('sky.skypilot_config.parse_and_validate_config_file')
    @mock.patch('sky.utils.yaml_utils.dump_yaml_str')
    @mock.patch('sky.utils.kubernetes.config_map_utils.kubernetes')
    def test_initialize_configmap_sync_on_startup_creates_configmap(
            self, mock_k8s, mock_dump_yaml, mock_parse_config, mock_get_name,
            mock_get_ns, mock_is_k8s):
        """Test that initialize_configmap_sync_on_startup creates ConfigMap."""
        mock_is_k8s.return_value = True
        mock_get_ns.return_value = 'test-namespace'
        mock_get_name.return_value = 'test-configmap'
        mock_dump_yaml.return_value = 'test:\n  value: 123'
        mock_parse_config.return_value = config_utils.Config(
            {'test': {
                'value': 123
            }})

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test:\n  value: 123\n')
            f.flush()
            config_path = f.name

        mock_core_api = mock.MagicMock()
        mock_k8s.core_api.return_value = mock_core_api

        # Create a simple exception that has status = 404
        class SimpleApiException(Exception):
            status = 404

        mock_k8s.kubernetes.client.rest.ApiException = SimpleApiException
        mock_core_api.read_namespaced_config_map.side_effect = (
            SimpleApiException())

        # Mock successful creation
        mock_core_api.create_namespaced_config_map.return_value = None

        config_map_utils.initialize_configmap_sync_on_startup(config_path)

        # Verify ConfigMap creation was called
        mock_core_api.create_namespaced_config_map.assert_called_once()

        os.unlink(config_path)

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_initialize_configmap_sync_on_startup_no_config_file(
            self, mock_is_k8s):
        """Test initialize_configmap_sync_on_startup when no config exists."""
        mock_is_k8s.return_value = True

        # Use a non-existent file path
        non_existent_path = '/tmp/non_existent_config.yaml'

        with mock.patch('sky.utils.kubernetes.config_map_utils.kubernetes') \
                as mock_k8s_module:
            config_map_utils.initialize_configmap_sync_on_startup(
                non_existent_path)
            # Should not call any kubernetes operations when no config exists
            mock_k8s_module.core_api.assert_not_called()

    @mock.patch('sky.utils.kubernetes.config_map_utils.'
                'is_running_in_kubernetes')
    def test_initialize_configmap_sync_not_in_kubernetes(self, mock_is_k8s):
        """Test initialize_configmap_sync_on_startup when not in K8s."""
        mock_is_k8s.return_value = False

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test:\n  value: 123\n')
            f.flush()
            config_path = f.name

        with mock.patch('sky.utils.kubernetes.config_map_utils.kubernetes') \
                as mock_k8s_module:
            config_map_utils.initialize_configmap_sync_on_startup(config_path)
            # Should not call any kubernetes operations when not in K8s
            mock_k8s_module.core_api.assert_not_called()

        os.unlink(config_path)


if __name__ == '__main__':
    unittest.main()
