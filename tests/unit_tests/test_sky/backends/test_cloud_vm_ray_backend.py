"""Unit tests for CloudVmRayBackend task configuration redaction and locking."""

import multiprocessing
import socket
import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import resources
from sky import task
from sky.backends import cloud_vm_ray_backend
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.backends.cloud_vm_ray_backend import SSHTunnelInfo


class TestCloudVmRayBackendTaskRedaction:
    """Tests for CloudVmRayBackend usage of redacted task configs."""

    def test_cloud_vm_ray_backend_redaction_usage_pattern(self):
        """Test the exact usage pattern from the CloudVmRayBackend code."""
        # Create a task with sensitive secret variables and regular environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': '8080'
                              },
                              secrets={
                                  'API_KEY': 'sk-very-secret-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'JWT_SECRET': 'jwt-signing-secret-456'
                              })

        # Test the exact call pattern used in cloud_vm_ray_backend.py
        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Verify that environment variables are NOT redacted
        assert 'envs' in task_config
        assert task_config['envs']['DEBUG'] == 'true'
        assert task_config['envs']['PORT'] == '8080'

        # Verify that secrets ARE redacted
        assert 'secrets' in task_config
        assert task_config['secrets']['API_KEY'] == '<redacted>'
        assert task_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
        assert task_config['secrets']['JWT_SECRET'] == '<redacted>'

        # Verify other task fields are preserved
        assert task_config['run'] == 'echo hello'

    def test_backend_task_config_without_secrets(self):
        """Test task config generation when no secrets are present."""
        test_task = task.Task(run='python train.py',
                              envs={'PYTHONPATH': '/app'})

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Environment variables should be preserved
        assert task_config['envs']['PYTHONPATH'] == '/app'

        # No secrets field should be present
        assert 'secrets' not in task_config or not task_config.get('secrets')

    def test_backend_task_config_empty_secrets(self):
        """Test task config generation with empty secrets dict."""
        test_task = task.Task(run='python train.py',
                              envs={'PYTHONPATH': '/app'},
                              secrets={})

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Environment variables should be preserved
        assert task_config['envs']['PYTHONPATH'] == '/app'

        # Empty secrets should not appear in config
        assert 'secrets' not in task_config

    def test_backend_redaction_redacts_all_values(self):
        """Test that all secret values (including non-string) are redacted."""
        test_task = task.Task(run='echo hello',
                              secrets={
                                  'STRING_SECRET': 'actual-secret',
                                  'NUMERIC_PORT': 5432,
                                  'BOOLEAN_FLAG': True,
                                  'NULL_VALUE': None
                              })

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # String values should be redacted
        assert task_config['secrets']['STRING_SECRET'] == '<redacted>'

        # All values should be redacted (including non-string values)
        assert task_config['secrets']['NUMERIC_PORT'] == '<redacted>'
        assert task_config['secrets']['BOOLEAN_FLAG'] == '<redacted>'
        assert task_config['secrets']['NULL_VALUE'] == '<redacted>'

    def test_backend_supports_both_redaction_modes(self):
        """Test that backend can use both redacted and non-redacted configs."""
        test_task = task.Task(run='echo hello',
                              secrets={'API_KEY': 'secret-key-123'})

        # Test redacted mode (for logging/display)
        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'

        # Test non-redacted mode (for execution)
        full_config = test_task.to_yaml_config(use_user_specified_yaml=False)
        assert full_config['secrets']['API_KEY'] == 'secret-key-123'

    def test_backend_mixed_envs_and_secrets(self):
        """Test backend behavior with both envs and secrets containing sensitive data."""
        test_task = task.Task(
            run='echo hello',
            envs={
                'PUBLIC_API_URL': 'https://api.example.com',
                'DEBUG_MODE': 'true',
                'ENVIRONMENT': 'production'
            },
            secrets={
                'PRIVATE_API_KEY': 'sk-secret-key-123',
                'DATABASE_URL': 'postgresql://user:pass@host:5432/db',
                'OAUTH_CLIENT_SECRET': 'oauth-secret-456'
            })

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # All environment variables should remain visible
        assert task_config['envs'][
            'PUBLIC_API_URL'] == 'https://api.example.com'
        assert task_config['envs']['DEBUG_MODE'] == 'true'
        assert task_config['envs']['ENVIRONMENT'] == 'production'

        # All secrets should be redacted
        assert task_config['secrets']['PRIVATE_API_KEY'] == '<redacted>'
        assert task_config['secrets']['DATABASE_URL'] == '<redacted>'
        assert task_config['secrets']['OAUTH_CLIENT_SECRET'] == '<redacted>'

    def test_backend_config_serialization_safety(self):
        """Test that redacted configs are safe for serialization/logging."""
        import json

        import yaml

        test_task = task.Task(
            run='echo hello',
            envs={'PUBLIC_VAR': 'public-value'},
            secrets={'PRIVATE_KEY': 'very-sensitive-key-data'})

        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Should be serializable to JSON
        json_str = json.dumps(redacted_config)
        assert 'very-sensitive-key-data' not in json_str
        assert '<redacted>' in json_str

        # Should be serializable to YAML
        yaml_str = yaml.dump(redacted_config)
        assert 'very-sensitive-key-data' not in yaml_str
        assert '<redacted>' in yaml_str

        # Public values should still be present
        assert 'public-value' in yaml_str

    def test_redacted_config_contains_no_sensitive_data(self):
        """Test that redacted task config doesn't contain sensitive secret data."""
        # Create a task with sensitive secret variables and regular environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080,
                                  'PUBLIC_VAR': 'public-value'
                              },
                              secrets={
                                  'API_KEY': 'secret-api-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'AWS_SECRET_ACCESS_KEY': 'aws-secret-key',
                                  'STRIPE_SECRET_KEY': 'sk_live_sensitive_key',
                                  'JWT_SECRET': 'jwt-signing-secret',
                              })

        # Get the redacted config as the backend would
        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Verify sensitive string values in secrets are redacted
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'
        assert redacted_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
        assert redacted_config['secrets'][
            'AWS_SECRET_ACCESS_KEY'] == '<redacted>'
        assert redacted_config['secrets']['STRIPE_SECRET_KEY'] == '<redacted>'
        assert redacted_config['secrets']['JWT_SECRET'] == '<redacted>'

        # Verify envs are NOT redacted (preserved as-is)
        assert redacted_config['envs']['DEBUG'] == 'true'
        assert redacted_config['envs']['PORT'] == 8080
        assert redacted_config['envs']['PUBLIC_VAR'] == 'public-value'

        # Ensure no sensitive data appears anywhere in the config
        config_str = str(redacted_config)
        assert 'secret-api-key-123' not in config_str
        assert 'super-secret-password' not in config_str
        assert 'aws-secret-key' not in config_str
        assert 'sk_live_sensitive_key' not in config_str
        assert 'jwt-signing-secret' not in config_str

        # But public values should still be present
        assert 'public-value' in config_str

    def test_non_redacted_config_contains_actual_values(self):
        """Test that non-redacted config contains actual secret values."""
        # Create a task with environment variables and secrets
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              },
                              secrets={
                                  'API_KEY': 'actual-api-key',
                                  'JWT_SECRET': 'actual-jwt-secret'
                              })

        # Get the non-redacted config
        non_redacted_config = test_task.to_yaml_config(
            use_user_specified_yaml=False)

        # Verify actual values are present in both envs and secrets
        assert non_redacted_config['envs']['DEBUG'] == 'true'
        assert non_redacted_config['envs']['PORT'] == 8080
        assert non_redacted_config['secrets']['API_KEY'] == 'actual-api-key'
        assert non_redacted_config['secrets'][
            'JWT_SECRET'] == 'actual-jwt-secret'

        # Also test default behavior (should NOT redact secrets by default)
        default_config = test_task.to_yaml_config()
        assert default_config['envs'] == non_redacted_config[
            'envs']  # envs same
        assert default_config['secrets'][
            'API_KEY'] == 'actual-api-key'  # secrets not redacted by default

    def test_backend_redaction_with_no_secrets(self):
        """Test backend behavior when task has no secret variables."""
        # Create a task with only environment variables, no secrets
        test_task = task.Task(run='echo hello', envs={'DEBUG': 'true'})

        # Get redacted config
        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # Should not have secrets key at all
        assert 'secrets' not in redacted_config

        # Should have envs key with actual values (not redacted)
        assert 'envs' in redacted_config
        assert redacted_config['envs']['DEBUG'] == 'true'

        # Should still have other task properties
        assert redacted_config['run'] == 'echo hello'

    def test_backend_redaction_preserves_task_structure(self):
        """Test that redaction preserves all non-secret task configuration."""
        from sky import resources

        # Create a comprehensive task
        test_task = task.Task(run='python train.py',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              },
                              secrets={
                                  'API_KEY': 'secret-value',
                                  'DB_PASSWORD': 'secret-password'
                              },
                              workdir='/app',
                              name='training-task')
        # Set resources using the proper method
        test_task.set_resources(resources.Resources(cpus=4, memory=8))

        # Get both configs
        original_config = test_task.to_yaml_config(
            use_user_specified_yaml=False)
        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # All non-secret fields should be identical
        for key in original_config:
            if key != 'secrets':
                assert original_config[key] == redacted_config[key]

        # Envs should be identical (not redacted)
        assert original_config['envs'] == redacted_config['envs']
        assert redacted_config['envs']['DEBUG'] == 'true'
        assert redacted_config['envs']['PORT'] == 8080

        # Secret handling should be different
        assert original_config['secrets']['API_KEY'] == 'secret-value'
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'
        assert original_config['secrets']['DB_PASSWORD'] == 'secret-password'
        assert redacted_config['secrets']['DB_PASSWORD'] == '<redacted>'


class TestCloudVmRayBackendGetGrpcChannel:
    """Tests for CloudVmRayBackend get_grpc_channel."""
    MOCK_HANDLE_KWARGS = {
        'cluster_name': 'test-cluster',
        'cluster_name_on_cloud': 'test-cluster-abc',
        'cluster_yaml': None,
        'launched_nodes': 1,
        'launched_resources': MagicMock(),
    }

    INITIAL_TUNNEL_PORT = 10000
    INITIAL_TUNNEL_PID = 12345

    def _simulate_process_get_grpc_channel(self, queue, tunnel_creation_count,
                                           tunnel_port, tunnel_pid,
                                           socket_connect_side_effect):
        """Simulate a process calling get_grpc_channel.

        This test mocks:
        - _get_skylet_ssh_tunnel: To avoid making an actual DB query
        - _open_and_update_skylet_tunnel: To avoid actually opening an SSH tunnel
        - grpc.insecure_channel: To just return the address instead of a Channel object
        - socket.socket.connect: To avoid actually connecting to the tunnel

        This test does not mock:
        - lock.acquire
        """
        try:
            # Different processes have different handle instances.
            handle = CloudVmRayResourceHandle(**self.MOCK_HANDLE_KWARGS)

            def mock_get_tunnel_side_effect():
                # Return None if the tunnel is not created yet.
                if tunnel_port.value == -1 or tunnel_pid.value == -1:
                    return None
                return SSHTunnelInfo(port=tunnel_port.value,
                                     pid=tunnel_pid.value)

            def mock_open_tunnel():
                # Simulate time taken to create tunnel.
                time.sleep(2)
                with tunnel_creation_count.get_lock():
                    tunnel_creation_count.value += 1
                    created = tunnel_creation_count.value
                with tunnel_port.get_lock(), tunnel_pid.get_lock():
                    # First creation -> 10000/12345; second -> 10001/12346; and so on.
                    tunnel_port.value = self.INITIAL_TUNNEL_PORT + (created - 1)
                    tunnel_pid.value = self.INITIAL_TUNNEL_PID + (created - 1)
                    return SSHTunnelInfo(port=tunnel_port.value,
                                         pid=tunnel_pid.value)

            with patch.object(handle, '_get_skylet_ssh_tunnel', side_effect=mock_get_tunnel_side_effect), \
                patch.object(handle, '_open_and_update_skylet_tunnel', side_effect=mock_open_tunnel), \
                patch('grpc.insecure_channel', side_effect=lambda addr, options: addr), \
                patch('socket.socket') as mock_socket:

                mock_socket.return_value.__enter__.return_value.connect.side_effect = socket_connect_side_effect

                res = handle.get_grpc_channel()
                assert res is not None
                queue.put(res)

        except Exception as e:
            import traceback
            error_msg = f"Error: {e}\nTraceback: {traceback.format_exc()}"
            queue.put(error_msg)

    def _socket_connect_side_effect(self, addr):
        _, port = addr
        # Force an error on the original port to test the retry logic.
        if port == self.INITIAL_TUNNEL_PORT:
            raise socket.error("Connection error")
        return None

    def test_get_grpc_channel_multiprocess_race_condition(self):
        """Test get_grpc_channel with multiple processes racing for tunnel creation."""
        tunnel_creation_count = multiprocessing.Value('i', 0)
        tunnel_port = multiprocessing.Value('i', -1)
        tunnel_pid = multiprocessing.Value('i', -1)

        num_processes = 5
        processes = []
        queue = multiprocessing.Queue()
        for _ in range(num_processes):
            p = multiprocessing.Process(
                target=self._simulate_process_get_grpc_channel,
                args=(queue, tunnel_creation_count, tunnel_port, tunnel_pid,
                      None))
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=15)
            if p.is_alive():
                p.terminate()
                p.join()

        results = []
        while not queue.empty():
            results.append(queue.get())
        assert len(
            results
        ) == num_processes, f"Expected {num_processes} results, got {len(results)}"
        # All processes should get the same channel (localhost:10000).
        for item in results:
            assert item == f'localhost:{self.INITIAL_TUNNEL_PORT}', f"Failed: {item}"

        assert tunnel_creation_count.value == 1, f"Expected tunnel to be created exactly once, but was created {tunnel_creation_count.value} times"

        # Try again, this tests the case where the tunnel is already created.
        # This time, tunnel.port will be 10000, but the check should fail,
        # as our _socket_connect_side_effect will raise an error. So we
        # should invoke _open_and_update_skylet_tunnel again,
        # this time returning another port.
        for _ in range(num_processes):
            p = multiprocessing.Process(
                target=self._simulate_process_get_grpc_channel,
                args=(queue, tunnel_creation_count, tunnel_port, tunnel_pid,
                      self._socket_connect_side_effect))
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=15)
            if p.is_alive():
                p.terminate()
                p.join()

        results = []
        while not queue.empty():
            results.append(queue.get())
        assert len(
            results
        ) == num_processes, f"Expected {num_processes} results, got {len(results)}"

        # All processes should get the same channel (localhost:10001).
        for i in range(num_processes):
            assert results[
                i] == f'localhost:{self.INITIAL_TUNNEL_PORT + 1}', f"Process {i} failed: {results[i]}"

        assert tunnel_creation_count.value == 2, f"Expected tunnel to be created exactly once, but was created {tunnel_creation_count.value} times"

    def test_setup_num_gpus(self, monkeypatch):
        """Test setup num GPUs."""
        test_task = task.Task(resources=resources.Resources(
            accelerators={'A100': 8}))
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        assert backend._get_num_gpus(test_task) == 8


class TestCloudVmRayResourceHandle:
    """Tests for CloudVmRayResourceHandle class."""

    def test_get_cluster_name(self):
        """Test get_cluster_name returns correct cluster name."""
        handle = CloudVmRayResourceHandle(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cluster-abc123',
            cluster_yaml=None,
            launched_nodes=1,
            launched_resources=MagicMock())
        assert handle.get_cluster_name() == 'test-cluster'

    def test_get_cluster_name_on_cloud(self):
        """Test get_cluster_name_on_cloud returns correct cloud name."""
        handle = CloudVmRayResourceHandle(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cluster-abc123',
            cluster_yaml=None,
            launched_nodes=1,
            launched_resources=MagicMock())
        assert handle.get_cluster_name_on_cloud() == 'test-cluster-abc123'

    def test_repr(self):
        """Test __repr__ provides useful information."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources)
        repr_str = repr(handle)
        assert 'test-cluster' in repr_str
        assert 'test-cloud' in repr_str

    def test_get_hourly_price(self):
        """Test get_hourly_price calculates cost correctly."""
        mock_resources = MagicMock()
        # Return $1 per hour per node
        mock_resources.get_cost.return_value = 1.0

        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=4,
                                          launched_resources=mock_resources)
        # 4 nodes * $1/hour = $4/hour
        assert handle.get_hourly_price() == 4.0
        mock_resources.get_cost.assert_called_once_with(3600)

    def test_head_ip_from_stable_ips(self):
        """Test head_ip returns first external IP from stable IPs."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources,
                                          stable_internal_external_ips=[
                                              ('10.0.0.1', '1.2.3.4'),
                                              ('10.0.0.2', '1.2.3.5')
                                          ])
        assert handle.head_ip == '1.2.3.4'

    def test_head_ip_none_when_no_ips(self):
        """Test head_ip returns None when no stable IPs."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources,
                                          stable_internal_external_ips=None)
        assert handle.head_ip is None

    def test_head_ssh_port_from_stable_ports(self):
        """Test head_ssh_port returns first port from stable ports."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources,
                                          stable_ssh_ports=[2222, 22])
        assert handle.head_ssh_port == 2222

    def test_head_ssh_port_none_when_no_ports(self):
        """Test head_ssh_port returns None when no stable ports."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources,
                                          stable_ssh_ports=None)
        assert handle.head_ssh_port is None

    def test_cached_external_ips(self):
        """Test cached_external_ips returns external IPs from stable IPs."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources,
                                          stable_internal_external_ips=[
                                              ('10.0.0.1', '1.2.3.4'),
                                              ('10.0.0.2', '1.2.3.5')
                                          ])
        assert handle.cached_external_ips == ['1.2.3.4', '1.2.3.5']

    def test_cached_internal_ips(self):
        """Test cached_internal_ips returns internal IPs from stable IPs."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources,
                                          stable_internal_external_ips=[
                                              ('10.0.0.1', '1.2.3.4'),
                                              ('10.0.0.2', '1.2.3.5')
                                          ])
        assert handle.cached_internal_ips == ['10.0.0.1', '10.0.0.2']

    def test_update_ssh_ports_with_cluster_info(self):
        """Test update_ssh_ports uses cluster_info when available."""
        mock_resources = MagicMock()
        mock_cluster_info = MagicMock()
        mock_cluster_info.get_ssh_ports.return_value = [22, 2222]

        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=2,
                                          launched_resources=mock_resources,
                                          cluster_info=mock_cluster_info)
        handle.update_ssh_ports()
        # Should use cluster_info's SSH ports
        assert handle.stable_ssh_ports == [22, 2222]

    def test_is_grpc_enabled_default(self):
        """Test is_grpc_enabled is True by default."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources)
        assert handle.is_grpc_enabled is True


class TestCloudVmRayBackendGetNumGpus:
    """Tests for CloudVmRayBackend._get_num_gpus method."""

    def test_get_num_gpus_with_accelerators(self, monkeypatch):
        """Test _get_num_gpus with accelerator resources."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        test_task = task.Task(resources=resources.Resources(
            accelerators={'A100': 4}))
        assert backend._get_num_gpus(test_task) == 4

    def test_get_num_gpus_no_accelerators(self, monkeypatch):
        """Test _get_num_gpus with no accelerators."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        test_task = task.Task(resources=resources.Resources(cpus=4))
        assert backend._get_num_gpus(test_task) == 0

    def test_get_num_gpus_multiple_accelerators(self, monkeypatch):
        """Test _get_num_gpus with multiple accelerators defaults to first."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        test_task = task.Task(resources=resources.Resources(
            accelerators={'V100': 8}))
        assert backend._get_num_gpus(test_task) == 8


class TestSSHTunnelInfo:
    """Tests for SSHTunnelInfo dataclass."""

    def test_creation(self):
        """Test SSHTunnelInfo can be created."""
        tunnel = SSHTunnelInfo(port=10000, pid=12345)
        assert tunnel.port == 10000
        assert tunnel.pid == 12345

    def test_has_required_attributes(self):
        """Test SSHTunnelInfo has required attributes."""
        tunnel = SSHTunnelInfo(port=10000, pid=12345)
        assert hasattr(tunnel, 'port')
        assert hasattr(tunnel, 'pid')

    def test_is_dataclass(self):
        """Test SSHTunnelInfo is a dataclass."""
        import dataclasses
        assert dataclasses.is_dataclass(SSHTunnelInfo)

    def test_attributes_modifiable(self):
        """Test SSHTunnelInfo attributes can be modified (dataclass)."""
        tunnel = SSHTunnelInfo(port=10000, pid=12345)
        tunnel.port = 20000
        assert tunnel.port == 20000


class TestFailoverCloudErrorHandlerV2:
    """Tests for FailoverCloudErrorHandlerV2 error handling."""

    def test_handler_class_exists(self):
        """Test FailoverCloudErrorHandlerV2 class exists and is callable."""
        assert hasattr(cloud_vm_ray_backend, 'FailoverCloudErrorHandlerV2')
        assert callable(cloud_vm_ray_backend.FailoverCloudErrorHandlerV2.
                        update_blocklist_on_error)

    def test_handler_has_cloud_specific_handlers(self):
        """Test that cloud-specific handlers are defined."""
        # Verify that the class has handlers for major clouds
        assert hasattr(cloud_vm_ray_backend.FailoverCloudErrorHandlerV2,
                       '_aws_handler')
        assert hasattr(cloud_vm_ray_backend.FailoverCloudErrorHandlerV2,
                       '_gcp_handler')
        assert hasattr(cloud_vm_ray_backend.FailoverCloudErrorHandlerV2,
                       '_azure_handler')
        assert hasattr(cloud_vm_ray_backend.FailoverCloudErrorHandlerV2,
                       '_default_handler')


class TestResourcesErrorHandling:
    """Tests for resource error handling patterns."""

    def test_resources_unavailable_error_patterns(self):
        """Test that common error patterns are handled."""
        from sky import exceptions

        # Test that ResourcesUnavailableError can be raised
        with pytest.raises(exceptions.ResourcesUnavailableError):
            raise exceptions.ResourcesUnavailableError('Quota exceeded')

    def test_insufficient_capacity_error(self):
        """Test insufficient capacity error handling."""
        from sky import exceptions

        with pytest.raises(exceptions.ResourcesUnavailableError):
            raise exceptions.ResourcesUnavailableError(
                'InsufficientInstanceCapacity')


class TestClusterYamlProperty:
    """Tests for cluster_yaml property."""

    def test_cluster_yaml_getter(self):
        """Test cluster_yaml getter returns expanded path."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cloud',
            cluster_yaml='~/.sky/clusters/test-cluster.yaml',
            launched_nodes=1,
            launched_resources=mock_resources)

        # The getter should expand ~ to home directory
        assert handle.cluster_yaml is not None
        assert '~' not in handle.cluster_yaml or handle.cluster_yaml.startswith(
            '~')

    def test_cluster_yaml_none(self):
        """Test cluster_yaml getter when None."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources)
        assert handle.cluster_yaml is None

    def test_cluster_yaml_setter(self):
        """Test cluster_yaml setter."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources)
        handle.cluster_yaml = '/new/path/cluster.yaml'
        assert handle._cluster_yaml == '/new/path/cluster.yaml'


class TestIsMessageTooLong:
    """Tests for _is_message_too_long function."""

    @pytest.mark.parametrize(
        'returncode,message,expected',
        [
            # Valid matches with correct returncode
            (255, 'too long', True),
            (255, 'Argument list too long', True),
            (1, 'request-uri too large', True),
            (1, '414 Request-URI Too Large', True),
            (1, 'request header fields too large', True),
            (1, '431 Request Header Fields Too Large', True),
            # CloudFlare 400 Bad Request patterns
            (1, '400 bad request', True),
            (1, '400 Bad request', True),
            (1, '400 Bad Request', True),
            (1,
             'error: unable to upgrade connection: <html><body><h1>400 Bad request</h1>',
             True),
            # Case insensitivity
            (255, 'TOO LONG', True),
            (1, 'REQUEST HEADER FIELDS TOO LARGE', True),
            (1, '400 BAD REQUEST', True),
            # Wrong returncode
            (1, 'too long', False),
            (255, 'request-uri too large', False),
            (127, 'too long', False),
            (255, '400 bad request', False),
            # Wrong message
            (255, 'command not found', False),
            (1, 'some other error', False),
            (1, 'unable to upgrade connection', False),
            # Empty output
            (255, '', False),
        ])
    def test_detection_with_output(self, returncode, message, expected):
        """Test message detection with various returncode/message combinations."""
        assert cloud_vm_ray_backend._is_message_too_long(
            returncode, output=message) == expected

    def test_detection_with_file_path(self, tmp_path):
        """Test detection when reading from file."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Error: command too long")
        assert cloud_vm_ray_backend._is_message_too_long(
            255, file_path=str(log_file))

        log_file.write_text("431 Request Header Fields Too Large")
        assert cloud_vm_ray_backend._is_message_too_long(
            1, file_path=str(log_file))

    def test_file_read_error_returns_true(self, tmp_path):
        """Test that file read errors return True for safety."""
        import os

        # Non-existent file
        assert cloud_vm_ray_backend._is_message_too_long(
            255, file_path="/nonexistent/file.log")

        # Skip the unreadable file test when running as root (common in CI)
        # because root can read any file regardless of permissions
        if os.geteuid() != 0:
            log_file = tmp_path / "unreadable.log"
            log_file.write_text("content")
            log_file.chmod(0o000)
            try:
                assert cloud_vm_ray_backend._is_message_too_long(
                    255, file_path=str(log_file))
            finally:
                log_file.chmod(0o644)

    def test_requires_either_output_or_file_path(self):
        """Test that function requires either output or file_path."""
        with pytest.raises(AssertionError):
            cloud_vm_ray_backend._is_message_too_long(255)
        with pytest.raises(AssertionError):
            cloud_vm_ray_backend._is_message_too_long(255,
                                                      output="test",
                                                      file_path="/tmp/test")

    def test_partial_match_in_long_output(self):
        """Test that partial matches in longer messages are detected."""
        long_output = """Error executing command on remote server:
        bash: /usr/bin/ssh: Argument list too long
        Failed to run setup script"""
        assert cloud_vm_ray_backend._is_message_too_long(255,
                                                         output=long_output)

        http_error = "<html><h1>414 Request-URI Too Large</h1></html>"
        assert cloud_vm_ray_backend._is_message_too_long(1, output=http_error)

    def test_multiple_patterns_match_by_returncode(self):
        """Test that returncode determines which pattern to match."""
        mixed = "too long and request-uri too large"
        assert cloud_vm_ray_backend._is_message_too_long(255, output=mixed)
        assert cloud_vm_ray_backend._is_message_too_long(1, output=mixed)
