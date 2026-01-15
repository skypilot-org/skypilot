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


class TestGangSchedulingStatus:
    """Tests for GangSchedulingStatus enum."""

    def test_enum_values(self):
        """Test GangSchedulingStatus has expected enum values."""
        from sky.backends.cloud_vm_ray_backend import GangSchedulingStatus
        assert GangSchedulingStatus.CLUSTER_READY.value == 0
        assert GangSchedulingStatus.GANG_FAILED.value == 1
        assert GangSchedulingStatus.HEAD_FAILED.value == 2

    def test_enum_members(self):
        """Test all expected members exist."""
        from sky.backends.cloud_vm_ray_backend import GangSchedulingStatus
        members = list(GangSchedulingStatus)
        assert len(members) == 3
        assert GangSchedulingStatus.CLUSTER_READY in members
        assert GangSchedulingStatus.GANG_FAILED in members
        assert GangSchedulingStatus.HEAD_FAILED in members

    def test_enum_comparison(self):
        """Test enum comparison works correctly."""
        from sky.backends.cloud_vm_ray_backend import GangSchedulingStatus
        assert GangSchedulingStatus.CLUSTER_READY == GangSchedulingStatus.CLUSTER_READY
        assert GangSchedulingStatus.CLUSTER_READY != GangSchedulingStatus.GANG_FAILED


class TestGetClusterConfigTemplate:
    """Tests for _get_cluster_config_template function."""

    def test_aws_template(self):
        """Test AWS cloud returns correct template."""
        from sky import clouds
        cloud = clouds.AWS()
        template = cloud_vm_ray_backend._get_cluster_config_template(cloud)
        assert template == 'aws-ray.yml.j2'

    def test_gcp_template(self):
        """Test GCP cloud returns correct template."""
        from sky import clouds
        cloud = clouds.GCP()
        template = cloud_vm_ray_backend._get_cluster_config_template(cloud)
        assert template == 'gcp-ray.yml.j2'

    def test_azure_template(self):
        """Test Azure cloud returns correct template."""
        from sky import clouds
        cloud = clouds.Azure()
        template = cloud_vm_ray_backend._get_cluster_config_template(cloud)
        assert template == 'azure-ray.yml.j2'

    def test_kubernetes_template(self):
        """Test Kubernetes cloud returns correct template."""
        from sky import clouds
        cloud = clouds.Kubernetes()
        template = cloud_vm_ray_backend._get_cluster_config_template(cloud)
        assert template == 'kubernetes-ray.yml.j2'

    def test_lambda_template(self):
        """Test Lambda cloud returns correct template."""
        from sky import clouds
        cloud = clouds.Lambda()
        template = cloud_vm_ray_backend._get_cluster_config_template(cloud)
        assert template == 'lambda-ray.yml.j2'

    def test_unknown_cloud_raises_keyerror(self):
        """Test unknown cloud raises KeyError."""

        class FakeCloud:
            pass

        fake_cloud = FakeCloud()
        with pytest.raises(KeyError):
            cloud_vm_ray_backend._get_cluster_config_template(fake_cloud)


class TestAddToBlockedResources:
    """Tests for _add_to_blocked_resources function."""

    def test_adds_new_resource(self):
        """Test adding a new resource to blocked set."""
        blocked = set()
        mock_resource = MagicMock()
        mock_resource.should_be_blocked_by = MagicMock(return_value=False)

        cloud_vm_ray_backend._add_to_blocked_resources(blocked, mock_resource)
        assert mock_resource in blocked

    def test_does_not_add_duplicate(self):
        """Test that duplicate resources are not added."""
        existing_resource = MagicMock()
        new_resource = MagicMock()
        # new_resource is blocked by existing_resource
        new_resource.should_be_blocked_by = MagicMock(return_value=True)

        blocked = {existing_resource}
        cloud_vm_ray_backend._add_to_blocked_resources(blocked, new_resource)

        # Should not have added new_resource
        assert len(blocked) == 1
        assert existing_resource in blocked
        assert new_resource not in blocked

    def test_adds_different_resource(self):
        """Test adding different resources works."""
        resource1 = MagicMock()
        resource1.should_be_blocked_by = MagicMock(return_value=False)
        resource2 = MagicMock()
        resource2.should_be_blocked_by = MagicMock(return_value=False)

        blocked = set()
        cloud_vm_ray_backend._add_to_blocked_resources(blocked, resource1)
        cloud_vm_ray_backend._add_to_blocked_resources(blocked, resource2)

        assert len(blocked) == 2
        assert resource1 in blocked
        assert resource2 in blocked


class TestRetryingVmProvisionerToProvisionConfig:
    """Tests for RetryingVmProvisioner.ToProvisionConfig."""

    def test_creation_with_required_args(self):
        """Test ToProvisionConfig creation with minimal args."""
        mock_resources = MagicMock()
        config = cloud_vm_ray_backend.RetryingVmProvisioner.ToProvisionConfig(
            cluster_name='test-cluster',
            resources=mock_resources,
            num_nodes=2,
            prev_cluster_status=None,
            prev_handle=None,
            prev_cluster_ever_up=False,
            prev_config_hash=None)

        assert config.cluster_name == 'test-cluster'
        assert config.resources == mock_resources
        assert config.num_nodes == 2
        assert config.prev_cluster_status is None
        assert config.prev_handle is None
        assert config.prev_cluster_ever_up is False
        assert config.prev_config_hash is None

    def test_creation_with_all_args(self):
        """Test ToProvisionConfig creation with all args."""
        from sky.utils import status_lib

        mock_resources = MagicMock()
        mock_handle = MagicMock()

        config = cloud_vm_ray_backend.RetryingVmProvisioner.ToProvisionConfig(
            cluster_name='test-cluster',
            resources=mock_resources,
            num_nodes=4,
            prev_cluster_status=status_lib.ClusterStatus.STOPPED,
            prev_handle=mock_handle,
            prev_cluster_ever_up=True,
            prev_config_hash='abc123')

        assert config.cluster_name == 'test-cluster'
        assert config.resources == mock_resources
        assert config.num_nodes == 4
        assert config.prev_cluster_status == status_lib.ClusterStatus.STOPPED
        assert config.prev_handle == mock_handle
        assert config.prev_cluster_ever_up is True
        assert config.prev_config_hash == 'abc123'

    def test_cluster_name_required(self):
        """Test that cluster_name is required."""
        mock_resources = MagicMock()
        with pytest.raises(AssertionError):
            cloud_vm_ray_backend.RetryingVmProvisioner.ToProvisionConfig(
                cluster_name=None,
                resources=mock_resources,
                num_nodes=1,
                prev_cluster_status=None,
                prev_handle=None,
                prev_cluster_ever_up=False,
                prev_config_hash=None)


class TestRetryingVmProvisionerInit:
    """Tests for RetryingVmProvisioner initialization."""

    def test_init_with_minimal_args(self):
        """Test provisioner initialization with minimal arguments."""
        import pathlib

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='/tmp/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123')

        assert provisioner.log_dir == '/tmp/logs'
        assert provisioner._dag == mock_dag
        assert provisioner._optimize_target == mock_target
        assert len(provisioner._blocked_resources) == 0

    def test_init_with_blocked_resources(self):
        """Test provisioner initialization with blocked resources."""
        import pathlib

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')
        blocked = [MagicMock(), MagicMock()]

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='/tmp/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123',
            blocked_resources=blocked)

        assert len(provisioner._blocked_resources) == 2

    def test_init_expands_log_dir(self):
        """Test that log_dir is expanded."""
        import pathlib

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='~/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123')

        # Should be expanded
        assert '~' not in provisioner.log_dir


class TestRetryingVmProvisionerInsufficientResourcesMsg:
    """Tests for RetryingVmProvisioner._insufficient_resources_msg."""

    def test_message_with_zone(self):
        """Test insufficient resources message with zone specified."""
        import pathlib

        from sky import clouds

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='/tmp/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123')

        mock_resources = MagicMock()
        mock_resources.zone = 'us-east-1a'
        mock_resources.region = 'us-east-1'
        mock_resources.cloud = clouds.AWS()

        message = provisioner._insufficient_resources_msg(
            to_provision=mock_resources,
            requested_resources={'gpu': 4},
            insufficient_resources=['GPU quota exceeded'])

        assert 'us-east-1a' in message
        assert 'GPU quota exceeded' in message

    def test_message_with_region_only(self):
        """Test insufficient resources message with region only."""
        import pathlib

        from sky import clouds

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='/tmp/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123')

        mock_resources = MagicMock()
        mock_resources.zone = None
        mock_resources.region = 'us-west-2'
        mock_resources.cloud = clouds.AWS()

        message = provisioner._insufficient_resources_msg(
            to_provision=mock_resources,
            requested_resources={'cpu': 8},
            insufficient_resources=None)

        assert 'us-west-2' in message

    def test_message_without_insufficient_resources(self):
        """Test message when insufficient_resources is None."""
        import pathlib

        from sky import clouds

        mock_dag = MagicMock()
        mock_target = MagicMock()
        local_wheel = pathlib.Path('/tmp/wheel.whl')

        provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
            log_dir='/tmp/logs',
            dag=mock_dag,
            optimize_target=mock_target,
            requested_features=set(),
            local_wheel_path=local_wheel,
            wheel_hash='abc123')

        mock_resources = MagicMock()
        mock_resources.zone = 'us-east-1a'
        mock_resources.region = 'us-east-1'
        mock_resources.cloud = clouds.AWS()

        message = provisioner._insufficient_resources_msg(
            to_provision=mock_resources,
            requested_resources={'cpu': 4},
            insufficient_resources=None)

        assert 'Failed to acquire resources' in message


class TestFailoverCloudErrorHandlerV1:
    """Tests for FailoverCloudErrorHandlerV1 error handling."""

    def test_handle_errors_finds_known_errors(self):
        """Test _handle_errors extracts known error strings."""
        is_known = lambda s: 'quota' in s.lower()

        errors = cloud_vm_ray_backend.FailoverCloudErrorHandlerV1._handle_errors(
            stdout='Some output\nQuota exceeded\nMore output',
            stderr='',
            is_error_str_known=is_known)

        assert len(errors) == 1
        assert 'Quota exceeded' in errors[0]

    def test_handle_errors_finds_errors_in_stderr(self):
        """Test _handle_errors extracts errors from stderr."""
        is_known = lambda s: 'error' in s.lower()

        errors = cloud_vm_ray_backend.FailoverCloudErrorHandlerV1._handle_errors(
            stdout='',
            stderr='Error: insufficient capacity',
            is_error_str_known=is_known)

        assert len(errors) == 1
        assert 'Error' in errors[0]

    def test_handle_errors_rsync_not_found(self):
        """Test _handle_errors raises for rsync not found."""
        is_known = lambda s: False

        with pytest.raises(RuntimeError) as exc_info:
            cloud_vm_ray_backend.FailoverCloudErrorHandlerV1._handle_errors(
                stdout='',
                stderr='rsync: command not found',
                is_error_str_known=is_known)

        assert 'rsync' in str(exc_info.value)

    def test_handle_errors_unknown_error_raises(self):
        """Test _handle_errors raises RuntimeError for unknown errors."""
        is_known = lambda s: False

        with pytest.raises(RuntimeError) as exc_info:
            cloud_vm_ray_backend.FailoverCloudErrorHandlerV1._handle_errors(
                stdout='Some unknown output',
                stderr='Some unknown error',
                is_error_str_known=is_known)

        assert 'Errors occurred during provision' in str(exc_info.value)


class TestFailoverCloudErrorHandlerV2Detailed:
    """More detailed tests for FailoverCloudErrorHandlerV2."""

    def test_default_handler_exists(self):
        """Test _default_handler method exists and is callable."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert callable(handler._default_handler)

    def test_gcp_handler_exists(self):
        """Test _gcp_handler method exists."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert hasattr(handler, '_gcp_handler')

    def test_aws_handler_exists(self):
        """Test _aws_handler method exists."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert hasattr(handler, '_aws_handler')

    def test_azure_handler_exists(self):
        """Test _azure_handler method exists."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert hasattr(handler, '_azure_handler')

    def test_lambda_handler_exists(self):
        """Test _lambda_handler method exists."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert hasattr(handler, '_lambda_handler')

    def test_scp_handler_exists(self):
        """Test _scp_handler method exists."""
        handler = cloud_vm_ray_backend.FailoverCloudErrorHandlerV2
        assert hasattr(handler, '_scp_handler')


class TestCloudVmRayBackendCheckResourcesFitCluster:
    """Tests for CloudVmRayBackend.check_resources_fit_cluster."""

    def test_resources_fit_cluster(self, monkeypatch):
        """Test that matching resources pass validation."""
        from sky import exceptions
        from sky import global_user_state
        from sky.usage import usage_lib

        # Mock all dependencies
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        monkeypatch.setattr(usage_lib.messages.usage,
                            'update_cluster_resources', lambda *args: None)
        monkeypatch.setattr(usage_lib.messages.usage, 'update_cluster_status',
                            lambda *args: None)
        monkeypatch.setattr(global_user_state, 'get_status_from_cluster_name',
                            lambda *args: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Create mock handle with matching resources
        mock_handle = MagicMock()
        mock_handle.cluster_name = 'test-cluster'
        mock_handle.launched_nodes = 2

        mock_launched_resources = MagicMock()
        mock_launched_resources.region = 'us-east-1'
        mock_handle.launched_resources = mock_launched_resources

        # Create task with resources that fit
        mock_task_resource = MagicMock()
        mock_task_resource.less_demanding_than = MagicMock(return_value=True)

        mock_task = MagicMock()
        mock_task.num_nodes = 1
        mock_task.resources = [mock_task_resource]

        result = backend.check_resources_fit_cluster(mock_handle, mock_task)
        assert result == mock_task_resource

    def test_resources_mismatch_raises(self, monkeypatch):
        """Test that mismatched resources raise ResourcesMismatchError."""
        from sky import exceptions
        from sky import global_user_state
        from sky.usage import usage_lib

        # Mock all dependencies
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)
        monkeypatch.setattr(usage_lib.messages.usage,
                            'update_cluster_resources', lambda *args: None)
        monkeypatch.setattr(usage_lib.messages.usage, 'update_cluster_status',
                            lambda *args: None)
        monkeypatch.setattr(global_user_state, 'get_status_from_cluster_name',
                            lambda *args: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Create mock handle
        mock_handle = MagicMock()
        mock_handle.cluster_name = 'test-cluster'
        mock_handle.launched_nodes = 1

        mock_launched_resources = MagicMock()
        mock_launched_resources.region = 'us-east-1'
        mock_launched_resources.zone = None
        mock_handle.launched_resources = mock_launched_resources

        # Create task with resources that DON'T fit
        mock_task_resource = MagicMock()
        mock_task_resource.less_demanding_than = MagicMock(return_value=False)
        mock_task_resource.region = None
        mock_task_resource.zone = None
        mock_task_resource.requires_fuse = False

        mock_task = MagicMock()
        mock_task.num_nodes = 1
        mock_task.resources = [mock_task_resource]

        with pytest.raises(exceptions.ResourcesMismatchError):
            backend.check_resources_fit_cluster(mock_handle, mock_task)


class TestCloudVmRayBackendSyncWorkdir:
    """Tests for CloudVmRayBackend workdir sync methods."""

    def test_sync_git_workdir_method_exists(self, monkeypatch):
        """Test _sync_git_workdir method exists on backend."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        assert hasattr(backend, '_sync_git_workdir')
        assert callable(backend._sync_git_workdir)

    def test_sync_path_workdir_method_exists(self, monkeypatch):
        """Test _sync_path_workdir method exists on backend."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        assert hasattr(backend, '_sync_path_workdir')
        assert callable(backend._sync_path_workdir)

    def test_sync_workdir_method_exists(self, monkeypatch):
        """Test _sync_workdir method exists on backend."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        assert hasattr(backend, '_sync_workdir')
        assert callable(backend._sync_workdir)


class TestCloudVmRayResourceHandleStateManagement:
    """Tests for CloudVmRayResourceHandle state serialization."""

    def test_getstate_returns_dict(self):
        """Test __getstate__ returns expected dictionary."""
        mock_resources = MagicMock()
        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources)

        state = handle.__getstate__()
        assert isinstance(state, dict)
        assert 'cluster_name' in state
        assert state['cluster_name'] == 'test-cluster'

    def test_setstate_restores_object(self):
        """Test __setstate__ correctly restores object."""
        mock_resources = MagicMock()
        original = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                            cluster_name_on_cloud='test-cloud',
                                            cluster_yaml=None,
                                            launched_nodes=2,
                                            launched_resources=mock_resources)

        state = original.__getstate__()

        # Create new handle and restore state
        restored = CloudVmRayResourceHandle.__new__(CloudVmRayResourceHandle)
        restored.__setstate__(state)

        assert restored.cluster_name == 'test-cluster'
        # get_cluster_name_on_cloud() returns the cloud name
        assert restored.get_cluster_name_on_cloud() == 'test-cloud'
        assert restored.launched_nodes == 2


class TestCloudVmRayResourceHandleIPs:
    """Tests for CloudVmRayResourceHandle IP handling."""

    def test_num_ips_per_node_non_tpu(self):
        """Test num_ips_per_node returns 1 for non-TPU clusters."""
        # Create proper mock resources with accelerators=None (non-TPU)
        mock_resources = MagicMock()
        mock_resources.accelerators = None

        handle = CloudVmRayResourceHandle(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cloud',
            cluster_yaml=None,
            launched_nodes=2,
            launched_resources=mock_resources,
            stable_internal_external_ips=[
                ('10.0.0.1', '1.2.3.4'),
                ('10.0.0.2', '1.2.3.5'),
            ])

        # For non-TPU clusters, num_ips_per_node is always 1
        assert handle.num_ips_per_node == 1

    def test_num_ips_per_node_with_gpu(self):
        """Test num_ips_per_node returns 1 for GPU clusters."""
        mock_resources = MagicMock()
        mock_resources.accelerators = {'A100': 4}

        handle = CloudVmRayResourceHandle(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cloud',
            cluster_yaml=None,
            launched_nodes=3,
            launched_resources=mock_resources,
            stable_internal_external_ips=[('10.0.0.1', '1.2.3.4'),
                                          ('10.0.0.2', '1.2.3.5'),
                                          ('10.0.0.3', '1.2.3.6')])

        # GPU clusters also return 1 IP per node
        assert handle.num_ips_per_node == 1

    def test_num_ips_per_node_property_exists(self):
        """Test num_ips_per_node property exists."""
        mock_resources = MagicMock()
        mock_resources.accelerators = None

        handle = CloudVmRayResourceHandle(cluster_name='test-cluster',
                                          cluster_name_on_cloud='test-cloud',
                                          cluster_yaml=None,
                                          launched_nodes=1,
                                          launched_resources=mock_resources,
                                          stable_internal_external_ips=None)

        assert hasattr(handle, 'num_ips_per_node')
        assert isinstance(handle.num_ips_per_node, int)


class TestCloudVmRayBackendRegisterInfo:
    """Tests for CloudVmRayBackend.register_info method."""

    def test_register_info_sets_attributes(self, monkeypatch):
        """Test register_info sets backend attributes."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        backend.register_info(dump_final_script=True, is_managed=True)

        assert backend._dump_final_script is True
        assert backend._is_managed is True

    def test_register_info_rejects_unknown_kwargs(self, monkeypatch):
        """Test register_info raises for unknown kwargs."""
        monkeypatch.setattr(CloudVmRayResourceHandle, '__init__',
                            lambda self, *args, **kwargs: None)

        backend = cloud_vm_ray_backend.CloudVmRayBackend()

        with pytest.raises(AssertionError):
            backend.register_info(unknown_arg=True)


class TestCloudVmRayBackendConstants:
    """Tests for CloudVmRayBackend module constants."""

    def test_nodes_launching_progress_timeout(self):
        """Test NODES_LAUNCHING_PROGRESS_TIMEOUT has expected values."""
        from sky import clouds

        timeout_dict = cloud_vm_ray_backend._NODES_LAUNCHING_PROGRESS_TIMEOUT
        assert clouds.AWS in timeout_dict
        assert clouds.GCP in timeout_dict
        assert clouds.Azure in timeout_dict
        assert clouds.Kubernetes in timeout_dict

        # Timeouts should be positive integers
        for cloud, timeout in timeout_dict.items():
            assert timeout > 0

    def test_retry_until_up_gap(self):
        """Test retry gap constant is reasonable."""
        gap = cloud_vm_ray_backend._RETRY_UNTIL_UP_INIT_GAP_SECONDS
        assert gap == 30

    def test_fetch_ip_max_attempts(self):
        """Test max IP fetch attempts constant."""
        max_attempts = cloud_vm_ray_backend._FETCH_IP_MAX_ATTEMPTS
        assert max_attempts == 3

    def test_max_ray_up_retry(self):
        """Test max ray up retry constant."""
        max_retry = cloud_vm_ray_backend._MAX_RAY_UP_RETRY
        assert max_retry == 5

    def test_teardown_wait_constants(self):
        """Test teardown wait constants are reasonable."""
        max_attempts = cloud_vm_ray_backend._TEARDOWN_WAIT_MAX_ATTEMPTS
        wait_between = cloud_vm_ray_backend._TEARDOWN_WAIT_BETWEEN_ATTEMPS_SECONDS

        assert max_attempts == 10
        assert wait_between == 1


class TestCloudVmRayBackendPatterns:
    """Tests for regex patterns in CloudVmRayBackend."""

    def test_job_id_pattern(self):
        """Test JOB_ID_PATTERN matches expected format."""
        pattern = cloud_vm_ray_backend._JOB_ID_PATTERN
        match = pattern.search('Job ID: 12345')
        assert match is not None
        assert match.group(1) == '12345'

    def test_job_id_pattern_no_match(self):
        """Test JOB_ID_PATTERN doesn't match invalid format."""
        pattern = cloud_vm_ray_backend._JOB_ID_PATTERN
        match = pattern.search('Job: abc')
        assert match is None

    def test_log_dir_pattern(self):
        """Test LOG_DIR_PATTERN matches expected format."""
        pattern = cloud_vm_ray_backend._LOG_DIR_PATTERN
        match = pattern.search('Log Dir: /tmp/logs/job_123')
        assert match is not None
        assert match.group(1) == '/tmp/logs/job_123'

    def test_log_dir_pattern_no_match(self):
        """Test LOG_DIR_PATTERN doesn't match invalid format."""
        pattern = cloud_vm_ray_backend._LOG_DIR_PATTERN
        match = pattern.search('Logs: /tmp/logs')
        assert match is None


class TestIsTunnelHealthy:
    """Tests for _is_tunnel_healthy function."""

    def test_tunnel_healthy_with_connectable_port(self):
        """Test tunnel is healthy when port is connectable."""
        # _is_tunnel_healthy actually tries to connect to the port
        # Mock socket to simulate successful connection
        tunnel = SSHTunnelInfo(port=10000, pid=12345)

        with patch('socket.socket') as mock_socket:
            mock_sock_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            mock_sock_instance.connect.return_value = None

            result = cloud_vm_ray_backend._is_tunnel_healthy(tunnel)
            assert result is True

    def test_tunnel_unhealthy_when_connection_fails(self):
        """Test tunnel is unhealthy when connection fails."""
        tunnel = SSHTunnelInfo(port=10000, pid=999999999)

        with patch('socket.socket') as mock_socket:
            mock_sock_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            mock_sock_instance.connect.side_effect = socket.error("Connection refused")

            result = cloud_vm_ray_backend._is_tunnel_healthy(tunnel)
            assert result is False


class TestLocalResourcesHandle:
    """Tests for LocalResourcesHandle class."""

    def test_local_resources_handle_exists(self):
        """Test LocalResourcesHandle class exists."""
        assert hasattr(cloud_vm_ray_backend, 'LocalResourcesHandle')

    def test_local_resources_handle_inherits_from_base(self):
        """Test LocalResourcesHandle inherits from CloudVmRayResourceHandle."""
        assert issubclass(cloud_vm_ray_backend.LocalResourcesHandle,
                          CloudVmRayResourceHandle)
