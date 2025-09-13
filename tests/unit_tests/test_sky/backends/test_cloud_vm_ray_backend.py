"""Unit tests for CloudVmRayBackend task configuration redaction and locking."""

import multiprocessing
import socket
import time
from unittest.mock import MagicMock
from unittest.mock import patch

from sky import task
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
                patch('grpc.insecure_channel', side_effect=lambda addr: addr), \
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
            assert item == f'localhost:{self.INITIAL_TUNNEL_PORT}', f"Process {i} failed: {item}"

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
