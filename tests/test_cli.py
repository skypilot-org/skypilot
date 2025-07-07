from unittest import mock

from click import testing as cli_testing
import requests

from sky import clouds
from sky import server
from sky.client.cli import command

CLOUDS_TO_TEST = [
    'aws', 'gcp', 'ibm', 'azure', 'lambda', 'scp', 'oci', 'vsphere', 'nebius'
]


def mock_server_api_version(monkeypatch, version):
    original_request = requests.request

    def mock_request(method, url, *args, **kwargs):
        if '/api/health' in url and method == 'GET':
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                'api_version': version,
                'version': '1.0.0',
                'commit': '1234567890'
            }
            return mock_response
        return original_request(method, url, *args, **kwargs)

    monkeypatch.setattr(requests, 'request', mock_request)


class TestWithNoCloudEnabled:

    def test_show_gpus(self):
        """Tests `sky show-gpus` can be invoked (but not correctness).

        Tests below correspond to the following terminal commands, in order:

        -> sky show-gpus
        -> sky show-gpus --all
        -> sky show-gpus V100:4
        -> sky show-gpus :4
        -> sky show-gpus V100:0
        -> sky show-gpus V100:-2
        -> sky show-gpus --cloud aws --region us-west-1
        -> sky show-gpus --cloud lambda
        -> sky show-gpus --cloud lambda --all
        -> sky show-gpus V100:4 --cloud lambda
        -> sky show-gpus V100:4 --cloud lambda --all
        """
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.show_gpus, [])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus, ['--all'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus, ['V100:4'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus, [':4'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus, ['V100:0'])
        assert isinstance(result.exception, SystemExit)

        result = cli_runner.invoke(command.show_gpus, ['V100:-2'])
        assert isinstance(result.exception, SystemExit)

        result = cli_runner.invoke(command.show_gpus,
                                   ['--cloud', 'aws', '--region', 'us-west-1'])
        assert not result.exit_code

        result = cli_runner.invoke(command.show_gpus,
                                   ['--infra', 'aws/us-west-1'])
        assert not result.exit_code

        for cloud in CLOUDS_TO_TEST:
            result = cli_runner.invoke(command.show_gpus, ['--infra', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(command.show_gpus,
                                       ['--cloud', cloud, '--all'])
            assert not result.exit_code

            result = cli_runner.invoke(command.show_gpus,
                                       ['V100', '--cloud', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(command.show_gpus,
                                       ['V100:4', '--cloud', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(command.show_gpus,
                                       ['V100:4', '--cloud', cloud, '--all'])
            assert isinstance(result.exception, SystemExit)

    def test_k8s_alias_check(self):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.check, ['k8s'])
        assert not result.exit_code

        result = cli_runner.invoke(command.check, ['kubernetes'])
        assert not result.exit_code

        result = cli_runner.invoke(command.check, ['notarealcloud'])
        assert isinstance(result.exception, ValueError)


<<<<<<< HEAD
class TestServerVersion:

    def test_cli_low_version_server_high_version(self, monkeypatch,
                                                 mock_client_requests):
        # Clear cache to ensure mock is used
        from sky.server import common
        common.get_api_server_status.cache_clear()  # type: ignore

        mock_server_api_version(monkeypatch, '2')
        monkeypatch.setattr(server.constants, 'API_VERSION', 3)
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.status, [])
        assert "Client and local API server version mismatch" in str(
            result.exception)
        assert result.exit_code == 1

    def test_cli_high_version_server_low_version(self, monkeypatch,
                                                 mock_client_requests):
        # Clear cache to ensure mock is used
        from sky.server import common
        common.get_api_server_status.cache_clear()  # type: ignore

        mock_server_api_version(monkeypatch, '3')
        monkeypatch.setattr(server.constants, 'API_VERSION', 2)
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.status, [])

        # Verify the error message contains correct versions
        assert "Client and local API server version mismatch" in str(
            result.exception)
        assert result.exit_code == 1


=======
>>>>>>> master
class TestHelperFunctions:

    def test_get_cluster_records_and_set_ssh_config(self, monkeypatch):
        """Tests _get_cluster_records_and_set_ssh_config with mocked components."""
        # Mock cluster records that would be returned by stream_and_get
        mock_handle = mock.MagicMock()
        mock_handle.cluster_name = 'test-cluster'
        mock_handle.cached_external_ips = ['1.2.3.4']
        mock_handle.cached_external_ssh_ports = [22]
        mock_handle.docker_user = None
        mock_handle.ssh_user = 'ubuntu'
        mock_handle.launched_resources.cloud = mock.MagicMock()

        mock_records = [{
            'name': 'test-cluster',
            'handle': mock_handle,
            'credentials': {
                'ssh_user': 'ubuntu',
                'ssh_private_key': '/path/to/key.pem'
            }
        }]

        # Mock the SDK calls
        def mock_status(*args, **kwargs):
            return 'request-id'

        def mock_stream_and_get(*args, **kwargs):
            return mock_records

        monkeypatch.setattr('sky.client.sdk.status', mock_status)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)

        # Mock SSHConfigHelper methods
        mock_add_cluster = mock.MagicMock()
        mock_remove_cluster = mock.MagicMock()
        mock_list_cluster_names = mock.MagicMock(
            return_value=['test-cluster', 'old-cluster'])

        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.add_cluster',
            mock_add_cluster)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.remove_cluster',
            mock_remove_cluster)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names',
            mock_list_cluster_names)

        # Test case 1: Get records for specific clusters
        records = command._get_cluster_records_and_set_ssh_config(
            ['test-cluster'])
        assert records == mock_records
        mock_add_cluster.assert_called_once_with('test-cluster', ['1.2.3.4'], {
            'ssh_user': 'ubuntu',
            'ssh_private_key': '/path/to/key.pem'
        }, [22], None, 'ubuntu')
        # Shouldn't remove anything because all clusters provided are in the returned records
        mock_remove_cluster.assert_not_called()

        # Reset mocks for next test
        mock_add_cluster.reset_mock()
        mock_remove_cluster.reset_mock()

        # Test case 2: Get records for all users
        records = command._get_cluster_records_and_set_ssh_config(
            None, all_users=True)
        assert records == mock_records
        mock_add_cluster.assert_called_once()
        # Should remove old-cluster since it's not in the returned records
        mock_remove_cluster.assert_called_once_with('old-cluster')

        # Test case 3: Test with a cluster that has no handle/credentials
        mock_records_no_handle = [{'name': 'test-cluster', 'handle': None}]
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *args, **kwargs: mock_records_no_handle)

        records = command._get_cluster_records_and_set_ssh_config(
            ['test-cluster'])
        assert records == mock_records_no_handle
        # Should remove the cluster from SSH config since it has no handle
        mock_remove_cluster.assert_called_with('test-cluster')

        # Reset mocks for next test
        mock_add_cluster.reset_mock()
        mock_remove_cluster.reset_mock()

        # Test case 4: Test with a cluster that is using kubernetes
        mock_k8s_handle = mock.MagicMock()
        mock_k8s_handle.cluster_name = 'test-cluster'
        mock_k8s_handle.cached_external_ips = ['1.2.3.4']
        mock_k8s_handle.cached_external_ssh_ports = [22]
        mock_k8s_handle.docker_user = None
        mock_k8s_handle.ssh_user = 'ubuntu'
        mock_k8s_handle.launched_resources.cloud = clouds.Kubernetes()
        mock_records_kubernetes = [{
            'name': 'test-cluster',
            'handle': mock_k8s_handle,
            'credentials': {
                'ssh_user': 'ubuntu',
                'ssh_private_key': '/path/to/key.pem'
            },
        }]
        server_url = 'http://localhost:8000'
        monkeypatch.setattr('sky.server.common.get_server_url',
                            lambda: server_url)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *args, **kwargs: mock_records_kubernetes)
        records = command._get_cluster_records_and_set_ssh_config(
            ['test-cluster'])
        assert records == mock_records_kubernetes
        mock_add_cluster.assert_called_once()
        added_cluster_args = mock_add_cluster.call_args
        assert added_cluster_args[0][0] == 'test-cluster'
        assert added_cluster_args[0][1] == ['1.2.3.4']
        # proxy command should be set, but is dependent on the server url, so we don't check the exact value
        assert added_cluster_args[0][2].get('ssh_proxy_command') is not None
        assert server_url in added_cluster_args[0][2].get('ssh_proxy_command')
        assert added_cluster_args[0][2].get(
            'ssh_private_key') == '/path/to/key.pem'
        assert added_cluster_args[0][2].get('ssh_user') == 'ubuntu'
        assert added_cluster_args[0][3] == [22]
        assert added_cluster_args[0][4] is None
        assert added_cluster_args[0][5] == 'ubuntu'
        mock_remove_cluster.assert_not_called()


class TestConcurrentStatus:
    """Test the concurrent implementation of the status command."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Clear any cached results
        from sky.server import common
        if hasattr(common.get_api_server_status, 'cache_clear'):
            common.get_api_server_status.cache_clear()

    def test_concurrent_api_calls_same_results(self, monkeypatch):
        """Test that concurrent API calls produce same results as sequential."""
        import time
        import concurrent.futures
        from unittest.mock import MagicMock, patch

        # Track call order and timing
        call_times = {}

        def track_call(func_name):
            def wrapper(*args, **kwargs):
                call_times[func_name] = time.time()
                return f'{func_name}_request_id'
            return wrapper

        # Mock all the SDK calls
        monkeypatch.setattr('sky.client.sdk.status', track_call('status'))
        monkeypatch.setattr('sky.client.sdk.workspaces', track_call('workspaces'))
        monkeypatch.setattr('sky.managed_jobs.queue', track_call('jobs_queue'))
        monkeypatch.setattr('sky.serve_lib.status', track_call('serve_status'))
        
        monkeypatch.setattr('sky.client.sdk.stream_and_get', 
                           lambda x: [{'name': 'test-cluster', 'handle': None, 'credentials': {}}])
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request', 
                           lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request', 
                           lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.add_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.remove_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names', lambda: [])
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra', lambda *args: None)
        
        # Mock click.echo to capture output
        output_lines = []
        monkeypatch.setattr('click.echo', lambda x: output_lines.append(x))

        # Run the status command
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--refresh'])

        # Verify all functions were called
        assert 'status' in call_times
        assert 'workspaces' in call_times
        assert 'jobs_queue' in call_times
        assert 'serve_status' in call_times

        # Verify calls happened in parallel (within a short time window)
        times = list(call_times.values())
        max_time_diff = max(times) - min(times)
        assert max_time_diff < 0.1, f"Calls should be concurrent, but time diff was {max_time_diff}"
        assert result.exit_code == 0

    def test_keyboard_interrupt_handling(self, monkeypatch):
        """Test that KeyboardInterrupt is handled correctly in concurrent execution."""
        from unittest.mock import MagicMock

        # Mock basic setup
        monkeypatch.setattr('sky.client.sdk.status', lambda *args, **kwargs: 'cluster_request_id')
        monkeypatch.setattr('sky.client.sdk.stream_and_get', 
                           lambda x: [{'name': 'test-cluster', 'handle': None, 'credentials': {}}])
        monkeypatch.setattr('sky.managed_jobs.queue', lambda *args, **kwargs: 'jobs_request_id')
        monkeypatch.setattr('sky.client.sdk.workspaces', lambda: ['default'])
        monkeypatch.setattr('sky.serve_lib.status', lambda *args, **kwargs: 'services_request_id')

        # Mock KeyboardInterrupt in jobs handling
        def mock_handle_jobs(*args, **kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request', mock_handle_jobs)
        
        monkeypatch.setattr('sky.client.cli.command._handle_services_request', 
                           lambda *args, **kwargs: (0, 'No services'))

        # Mock api_cancel to track if it gets called
        cancel_calls = []
        def mock_cancel(request_id, silent=False):
            cancel_calls.append((request_id, silent))
        monkeypatch.setattr('sky.client.sdk.api_cancel', mock_cancel)

        # Mock other dependencies
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.add_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.remove_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names', lambda: [])
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra', lambda *args: None)
        monkeypatch.setattr('click.echo', lambda x: None)

        # Run the status command
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--refresh'])

        # Verify that api_cancel was called when KeyboardInterrupt occurred
        assert len(cancel_calls) == 1
        assert cancel_calls[0][0] == 'jobs_request_id'
        assert cancel_calls[0][1] is True  # silent=True

    def test_selective_query_execution(self, monkeypatch):
        """Test that queries are only executed when needed."""
        from unittest.mock import MagicMock

        # Track which functions are called
        calls_made = []

        def track_jobs_queue(*args, **kwargs):
            calls_made.append('jobs_queue')
            return 'jobs_request_id'

        def track_serve_status(*args, **kwargs):
            calls_made.append('serve_status')
            return 'services_request_id'

        monkeypatch.setattr('sky.client.sdk.status', lambda *args, **kwargs: 'cluster_request_id')
        monkeypatch.setattr('sky.client.sdk.stream_and_get', 
                           lambda x: [{'name': 'test-cluster', 'handle': MagicMock(), 'credentials': {}}])
        monkeypatch.setattr('sky.managed_jobs.queue', track_jobs_queue)
        monkeypatch.setattr('sky.serve_lib.status', track_serve_status)

        # Mock _show_endpoint to simulate IP mode
        endpoint_calls = []
        def mock_show_endpoint(*args, **kwargs):
            endpoint_calls.append(args)
        monkeypatch.setattr('sky.client.cli.command._show_endpoint', mock_show_endpoint)
        
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.add_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.remove_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names', lambda: [])

        # Test with IP mode - should not query jobs or services
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--ip'])

        # These should NOT be called in IP mode
        assert 'jobs_queue' not in calls_made
        assert 'serve_status' not in calls_made
        assert len(endpoint_calls) == 1  # _show_endpoint should be called
        assert result.exit_code == 0

        # Reset tracking
        calls_made.clear()
        endpoint_calls.clear()

        # Test with normal mode - should query jobs and services
        monkeypatch.setattr('sky.client.sdk.workspaces', lambda: ['default'])
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request', 
                           lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request', 
                           lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra', lambda *args: None)
        monkeypatch.setattr('click.echo', lambda x: None)

        result = cli_runner.invoke(command.status, ['--refresh'])

        # These SHOULD be called in normal mode
        assert 'jobs_queue' in calls_made
        assert 'serve_status' in calls_made
        assert len(endpoint_calls) == 0  # _show_endpoint should NOT be called
        assert result.exit_code == 0

    def test_workspace_backwards_compatibility(self, monkeypatch):
        """Test backwards compatibility when workspace API is not available."""
        monkeypatch.setattr('sky.client.sdk.status', lambda *args, **kwargs: 'cluster_request_id')
        monkeypatch.setattr('sky.client.sdk.stream_and_get', 
                           lambda x: [{'name': 'test-cluster', 'handle': None, 'credentials': {}}])

        # Mock workspace API raising RuntimeError (old server)
        def mock_workspaces():
            raise RuntimeError("Old server")
        monkeypatch.setattr('sky.client.sdk.workspaces', mock_workspaces)

        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.add_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.remove_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names', lambda: [])
        monkeypatch.setattr('click.echo', lambda x: None)

        # Should complete successfully even with old server
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--refresh'])
        assert result.exit_code == 0

    def test_error_handling_in_concurrent_execution(self, monkeypatch):
        """Test that errors in one concurrent task don't affect others."""
        monkeypatch.setattr('sky.client.sdk.status', lambda *args, **kwargs: 'cluster_request_id')
        monkeypatch.setattr('sky.client.sdk.stream_and_get', 
                           lambda x: [{'name': 'test-cluster', 'handle': None, 'credentials': {}}])

        # Mock workspace API to raise an error
        def mock_workspaces():
            raise RuntimeError("Network error")
        monkeypatch.setattr('sky.client.sdk.workspaces', mock_workspaces)

        monkeypatch.setattr('sky.managed_jobs.queue', lambda *args, **kwargs: 'jobs_request_id')
        monkeypatch.setattr('sky.serve_lib.status', lambda *args, **kwargs: 'services_request_id')
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request', 
                           lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request', 
                           lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.add_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.remove_cluster', lambda *args: None)
        monkeypatch.setattr('sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names', lambda: [])
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra', lambda *args: None)
        monkeypatch.setattr('click.echo', lambda x: None)

        # Should complete successfully even with workspace error
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--refresh'])
        assert result.exit_code == 0


class TestApiServerStatusCaching:
    """Test the caching functionality of get_api_server_status."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        from sky.server import common
        # Clear any cached results before each test
        if hasattr(common.get_api_server_status, 'cache_clear'):
            common.get_api_server_status.cache_clear()

    def teardown_method(self):
        """Clean up after each test method."""
        from sky.server import common
        # Clear cache after each test to avoid interference
        if hasattr(common.get_api_server_status, 'cache_clear'):
            common.get_api_server_status.cache_clear()

    def test_cache_decorator_applied(self):
        """Test that the cache decorator is properly applied."""
        from sky.server import common
        # Verify that the function has cache-related attributes
        assert hasattr(common.get_api_server_status, 'cache_info') or \
               hasattr(common.get_api_server_status, 'cache_clear'), \
               "Cache decorator not properly applied"

    def test_cache_hit_reduces_api_calls(self, monkeypatch):
        """Test that cache hits reduce the number of actual API calls."""
        from sky.server import common
        from unittest.mock import MagicMock

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'running',
            'api_version': 1
        }

        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        monkeypatch.setattr('sky.server.common.requests.get', mock_get)

        # First call should hit the API
        result1 = common.get_api_server_status()
        assert call_count[0] == 1

        # Second call within TTL should use cache (no additional API call)
        result2 = common.get_api_server_status()
        assert call_count[0] == 1  # Still 1, not 2

        # Results should be identical
        assert result1.status == result2.status
        assert result1.api_version == result2.api_version

    def test_cache_with_different_endpoints(self, monkeypatch):
        """Test that different endpoints are cached separately."""
        from sky.server import common
        from unittest.mock import MagicMock

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'running',
            'api_version': 1
        }

        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        monkeypatch.setattr('sky.server.common.requests.get', mock_get)

        # Call with first endpoint
        result1 = common.get_api_server_status('http://localhost:8080')
        assert call_count[0] == 1

        # Call with second endpoint should make new API call
        result2 = common.get_api_server_status('http://different:8080')
        assert call_count[0] == 2

        # Call first endpoint again should use cache
        result3 = common.get_api_server_status('http://localhost:8080')
        assert call_count[0] == 2  # Still 2, not 3

    def test_cache_clearing_functionality(self, monkeypatch):
        """Test that cache can be manually cleared."""
        from sky.server import common
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'running',
            'api_version': 1
        }

        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        monkeypatch.setattr('sky.server.common.requests.get', mock_get)

        # First call
        common.get_api_server_status()
        assert call_count[0] == 1

        # Second call should use cache
        common.get_api_server_status()
        assert call_count[0] == 1

        # Clear cache
        if hasattr(common.get_api_server_status, 'cache_clear'):
            common.get_api_server_status.cache_clear()

        # Third call should make new API call
        common.get_api_server_status()
        assert call_count[0] == 2

    def test_cache_handles_exceptions(self, monkeypatch):
        """Test that exceptions are not cached."""
        from sky.server import common
        import pytest

        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            raise Exception("Network error")

        monkeypatch.setattr('sky.server.common.requests.get', mock_get)

        # First call raises exception
        with pytest.raises(Exception, match="Network error"):
            common.get_api_server_status()
        assert call_count[0] == 1

        # Second call should also raise exception (not cached)
        with pytest.raises(Exception, match="Network error"):
            common.get_api_server_status()
        assert call_count[0] == 2
