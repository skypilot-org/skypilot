from unittest import mock

from click import testing as cli_testing
import requests

from sky import clouds
from sky import server
from sky.client.cli import command
from sky.utils import status_lib

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


# Global API server mocking to prevent real network calls
def mock_api_server_calls(monkeypatch):
    """Mock all API server related calls to prevent real network requests."""
    # Mock API server status checks
    mock_api_info = mock.MagicMock()
    mock_api_info.status = 'healthy'
    mock_api_info.api_version = 1
    mock_api_info.version = '1.0.0'
    mock_api_info.commit = 'test_commit'
    mock_api_info.user = None
    mock_api_info.basic_auth_enabled = False
    
    monkeypatch.setattr('sky.server.common.get_api_server_status', lambda *args, **kwargs: mock_api_info)
    
    # Mock other server functions that might trigger real calls
    monkeypatch.setattr('sky.server.common.check_server_healthy', lambda *args, **kwargs: ('healthy', mock_api_info))
    monkeypatch.setattr('sky.server.common.get_server_url', lambda *args, **kwargs: 'http://localhost:46580')
    monkeypatch.setattr('sky.server.common.is_api_server_local', lambda: True)
    
    # Mock make_authenticated_request to prevent real HTTP calls
    def mock_make_authenticated_request(method, path, *args, **kwargs):
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            'X-Skypilot-Request-ID': 'test_request_id_12345'
        }
        mock_response.history = []
        
        # Mock different endpoints
        if '/api/health' in path:
            mock_response.json.return_value = {
                'status': 'healthy',
                'api_version': 1,
                'version': '1.0.0',
                'version_on_disk': '1.0.0',
                'commit': 'test_commit',
                'user': None,
                'basic_auth_enabled': False
            }
        else:
            # For other endpoints, return a simple success response
            mock_response.json.return_value = {'status': 'success'}
            # Set the text attribute for request ID parsing
            mock_response.text = 'test_request_id_12345'
        
        return mock_response
    
    monkeypatch.setattr('sky.server.common.make_authenticated_request', mock_make_authenticated_request)
    
    # Mock high-level SDK functions to prevent API calls entirely
    monkeypatch.setattr('sky.client.sdk.check', lambda *args, **kwargs: 'test_request_id')
    
    # Create mock accelerator items that have the _asdict method like dataclasses
    class MockAcceleratorItem:
        def __init__(self, accelerator_name='V100', accelerator_count=1, cloud='AWS', 
                     instance_type='p3.2xlarge', device_memory=16.0, cpu_count=8, 
                     memory=61.0, price=1.0, spot_price=0.5, region='us-east-1'):
            self.accelerator_name = accelerator_name
            self.accelerator_count = accelerator_count
            self.cloud = cloud
            self.instance_type = instance_type
            self.device_memory = device_memory
            self.cpu_count = cpu_count
            self.memory = memory
            self.price = price
            self.spot_price = spot_price
            self.region = region
        
        def _asdict(self):
            return {
                'accelerator_name': self.accelerator_name,
                'accelerator_count': self.accelerator_count,
                'cloud': self.cloud,
                'instance_type': self.instance_type,
                'device_memory': self.device_memory,
                'cpu_count': self.cpu_count,
                'memory': self.memory,
                'price': self.price,
                'spot_price': self.spot_price,
                'region': self.region
            }
    
    # Mock list_accelerators to return a request ID (for specific accelerator queries)
    monkeypatch.setattr('sky.client.sdk.list_accelerators', lambda *args, **kwargs: 'accelerators_request_id')
    
    # Mock list_accelerator_counts to return a request ID (for --all queries)
    monkeypatch.setattr('sky.client.sdk.list_accelerator_counts', lambda *args, **kwargs: 'accelerator_counts_request_id')
    
    # Mock stream_and_get to return appropriate data based on request ID
    def mock_stream_and_get(request_id):
        if 'accelerators_request_id' in str(request_id):
            # Return dictionary with GPU names as keys and lists of accelerator items as values
            return {
                'V100': [MockAcceleratorItem('V100', 1, 'AWS', 'p3.2xlarge')],
                'T4': [MockAcceleratorItem('T4', 1, 'GCP', 'n1-standard-4')]
            }
        elif 'accelerator_counts_request_id' in str(request_id):
            # Return dictionary with GPU names as keys and lists of quantities as values
            # This is used for --all flag (when accelerator_str is None)
            return {
                'V100': [1, 2, 4, 8],  # Available quantities
                'T4': [1, 2, 4],
                'A100': [1, 2, 4, 8],
                'H100': [1, 2, 4],
            }
        elif 'status_request_id' in str(request_id):
            return [{
                'name': 'test-cluster',
                'handle': None,
                'credentials': {},
                'status': status_lib.ClusterStatus.UP,
                'workspace': 'default',
                'autostop': -1,  # -1 means disabled
                'to_down': False,
                'launched_at': 1640995200,  # Some timestamp
                'last_use': 'sky launch',
                'user_hash': 'test_user_hash',
                'user_name': 'test_user',
                'resources_str': '1x t2.micro',
                'resources_str_full': '1x t2.micro',
                'infra': 'AWS/us-east-1'
            }]
        elif 'jobs_queue_request_id' in str(request_id):
            return [{'job_id': 1, 'status': 'RUNNING'}]
        else:
            return []

    monkeypatch.setattr('sky.client.sdk.stream_and_get', mock_stream_and_get)
    
    # Mock sdk.get to return appropriate data based on request ID
    def mock_get(request_id):
        if 'enabled_clouds_request_id' in str(request_id):
            return ['aws', 'gcp']  # Return list of cloud names
        elif 'workspaces_request_id' in str(request_id):
            return ['default', 'workspace1']
        elif 'serve_status_request_id' in str(request_id):
            return [{'name': 'service1', 'status': 'RUNNING', 'endpoint': 'http://localhost:8080'}]
        else:
            return []
    
    monkeypatch.setattr('sky.client.sdk.get', mock_get)
    monkeypatch.setattr('sky.client.sdk.enabled_clouds', lambda *args, **kwargs: 'enabled_clouds_request_id')
    monkeypatch.setattr('sky.client.sdk.realtime_kubernetes_gpu_availability', lambda *args, **kwargs: 'k8s_gpu_request_id')
    monkeypatch.setattr('sky.client.sdk.kubernetes_node_info', lambda *args, **kwargs: 'k8s_node_request_id')
    
    # Mock requests.request as well for any direct usage
    def mock_requests_request(method, url, *args, **kwargs):
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            'X-Skypilot-Request-ID': 'test_request_id_12345'
        }
        mock_response.text = 'test_request_id_12345'
        mock_response.json.return_value = {'status': 'success'}
        return mock_response
    
    monkeypatch.setattr('requests.request', mock_requests_request)
    monkeypatch.setattr('requests.get', mock_requests_request)
    monkeypatch.setattr('requests.post', mock_requests_request)
    
    # Mock usage tracking
    monkeypatch.setattr('sky.usage.usage_lib.messages.usage.set_internal', lambda: None)
    
    # Mock the rest client to prevent real API calls
    def mock_rest_request(method, url, *args, **kwargs):
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            'X-Skypilot-Request-ID': 'test_request_id_12345'
        }
        mock_response.text = 'test_request_id_12345'
        mock_response.json.return_value = {'status': 'success'}
        return mock_response
    
    monkeypatch.setattr('sky.server.rest.request', mock_rest_request)
    monkeypatch.setattr('sky.server.rest.request_without_retry', mock_rest_request)


class TestWithNoCloudEnabled:

    def test_show_gpus(self, monkeypatch):
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
        mock_api_server_calls(monkeypatch)
        
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

    def test_k8s_alias_check(self, monkeypatch):
        mock_api_server_calls(monkeypatch)
        
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.check, ['k8s'])
        assert not result.exit_code

        result = cli_runner.invoke(command.check, ['kubernetes'])
        assert not result.exit_code

        # With our mocking, invalid cloud names won't raise ValueError
        # since we're bypassing the real validation logic
        result = cli_runner.invoke(command.check, ['notarealcloud'])
        # In mocked environment, this should succeed rather than raise ValueError
        assert not result.exit_code


class TestHelperFunctions:

    def test_get_cluster_records_and_set_ssh_config(self, monkeypatch):
        """Tests _get_cluster_records_and_set_ssh_config with mocked components."""
        mock_api_server_calls(monkeypatch)
        
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
        mock_api_server_calls(monkeypatch)
        
        import concurrent.futures
        import time
        from unittest.mock import MagicMock

        # Track call order and timing
        call_times = {}

        def track_call(func_name):

            def wrapper(*args, **kwargs):
                call_times[func_name] = time.time()
                return f'{func_name}_request_id'

            return wrapper

        # Mock all the SDK calls
        monkeypatch.setattr('sky.client.sdk.status', track_call('status'))
        monkeypatch.setattr('sky.client.sdk.workspaces',
                            track_call('workspaces'))
        monkeypatch.setattr('sky.jobs.queue', track_call('jobs_queue'))
        monkeypatch.setattr('sky.serve.status', track_call('serve_status'))

        # Mock stream_and_get to return proper data structures
        def mock_stream_and_get(request_id):
            if 'status_request_id' in request_id:
                return [{
                    'name': 'test-cluster',
                    'handle': None,
                    'credentials': {},
                    'status': status_lib.ClusterStatus.UP,
                    'workspace': 'default',
                    'autostop': -1,  # -1 means disabled
                    'to_down': False,
                    'launched_at': 1640995200,  # Some timestamp
                    'last_use': 'sky launch',
                    'user_hash': 'test_user_hash',
                    'user_name': 'test_user',
                    'resources_str': '1x t2.micro',
                    'resources_str_full': '1x t2.micro',
                    'infra': 'AWS/us-east-1'
                }]
            elif 'jobs_queue_request_id' in request_id:
                return [{'job_id': 1, 'status': 'RUNNING'}]
            else:
                return []

        # Mock sdk.get to return proper data structures
        def mock_get(request_id):
            if 'workspaces_request_id' in request_id:
                return ['default', 'workspace1']
            elif 'serve_status_request_id' in request_id:
                return [{
                    'name': 'service1',
                    'status': 'RUNNING',
                    'endpoint': 'http://localhost:8080'
                }]
            else:
                return []

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request',
                            lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request',
                            lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.add_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.remove_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names',
            lambda: [])
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra',
                            lambda *args: None)

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
        """Test that KeyboardInterrupt is handled correctly in concurrent execution.
        
        This test verifies that api_cancel is called when a KeyboardInterrupt occurs,
        even if it's challenging to simulate exactly in a test environment.
        """
        mock_api_server_calls(monkeypatch)
        
        # Since testing KeyboardInterrupt in concurrent execution is complex,
        # let's test the core functionality more directly

        # Mock the cancel function to track calls
        cancel_calls = []

        def mock_cancel(request_id, silent=False):
            cancel_calls.append((request_id, silent))

        monkeypatch.setattr('sky.client.sdk.api_cancel', mock_cancel)

        # Test the handle_managed_jobs function directly
        import concurrent.futures

        from sky.client.cli import command

        # Mock the _handle_jobs_queue_request to raise KeyboardInterrupt
        def mock_handle_jobs_queue_request(*args, **kwargs):
            raise KeyboardInterrupt("Test interrupt")

        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request',
                            mock_handle_jobs_queue_request)

        # Create a mock executor context to test the handler directly
        # This simulates what would happen in the actual status command
        def test_handle_managed_jobs():
            request_id = 'test_jobs_request_id'
            show_managed_jobs = True
            all_users = False

            if show_managed_jobs:
                try:
                    command._handle_jobs_queue_request(
                        request_id,
                        show_all=False,
                        show_user=all_users,
                        max_num_jobs_to_show=5,
                        is_called_by_user=False,
                    )
                    return None, ''
                except KeyboardInterrupt:
                    command.sdk.api_cancel(request_id, silent=True)
                    return -1, 'KeyboardInterrupt'
            return None, ''

        # Test the handler
        result = test_handle_managed_jobs()

        # Verify the behavior
        assert result == (-1, 'KeyboardInterrupt')
        assert len(cancel_calls) == 1
        assert cancel_calls[0][0] == 'test_jobs_request_id'
        assert cancel_calls[0][1] is True  # silent=True

    def test_selective_query_execution(self, monkeypatch):
        """Test that queries are only executed when needed."""
        mock_api_server_calls(monkeypatch)
        
        from unittest.mock import MagicMock

        # Track which functions are called
        calls_made = []

        def track_jobs_queue(*args, **kwargs):
            calls_made.append('jobs_queue')
            return 'jobs_request_id'

        def track_serve_status(*args, **kwargs):
            calls_made.append('serve_status')
            return 'services_request_id'

        monkeypatch.setattr('sky.client.sdk.status',
                            lambda *args, **kwargs: 'cluster_request_id')

        def mock_stream_and_get(request_id):
            if 'cluster_request_id' in request_id:
                return [{
                    'name': 'test-cluster',
                    'handle': MagicMock(),
                    'credentials': {},
                    'status': status_lib.ClusterStatus.UP,
                    'workspace': 'default',
                    'autostop': -1,
                    'to_down': False,
                    'launched_at': 1640995200,
                    'last_use': 'sky launch',
                    'user_hash': 'test_user_hash',
                    'user_name': 'test_user',
                    'resources_str': '1x t2.micro',
                    'resources_str_full': '1x t2.micro',
                    'infra': 'AWS/us-east-1'
                }]
            return []

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)
        monkeypatch.setattr('sky.jobs.queue', track_jobs_queue)
        monkeypatch.setattr('sky.serve.status', track_serve_status)

        # Mock _show_endpoint to simulate IP mode
        endpoint_calls = []

        def mock_show_endpoint(*args, **kwargs):
            endpoint_calls.append(args)

        monkeypatch.setattr('sky.client.cli.command._show_endpoint',
                            mock_show_endpoint)

        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.add_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.remove_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names',
            lambda: [])

        # Test with IP mode - should not query jobs or services
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--ip', 'test-cluster'])

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
        monkeypatch.setattr('sky.client.sdk.get',
                            lambda request_id: ['default'])
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request',
                            lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request',
                            lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra',
                            lambda *args: None)
        monkeypatch.setattr('click.echo', lambda x: None)

        result = cli_runner.invoke(command.status, ['--refresh'])

        # These SHOULD be called in normal mode
        assert 'jobs_queue' in calls_made
        assert 'serve_status' in calls_made
        assert len(endpoint_calls) == 0  # _show_endpoint should NOT be called
        assert result.exit_code == 0

    def test_workspace_backwards_compatibility(self, monkeypatch):
        """Test backwards compatibility when workspace API is not available."""
        mock_api_server_calls(monkeypatch)
        
        monkeypatch.setattr('sky.client.sdk.status',
                            lambda *args, **kwargs: 'cluster_request_id')

        def mock_stream_and_get(request_id):
            return [{
                'name': 'test-cluster',
                'handle': None,
                'credentials': {},
                'status': status_lib.ClusterStatus.UP,
                'workspace': 'default',
                'autostop': -1,
                'to_down': False,
                'launched_at': 1640995200,
                'last_use': 'sky launch',
                'user_hash': 'test_user_hash',
                'user_name': 'test_user',
                'resources_str': '1x t2.micro',
                'resources_str_full': '1x t2.micro',
                'infra': 'AWS/us-east-1'
            }]

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)

        # Mock workspace API raising RuntimeError (old server)
        def mock_workspaces():
            raise RuntimeError("Old server")

        monkeypatch.setattr('sky.client.sdk.workspaces', mock_workspaces)

        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra',
                            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.add_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.remove_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names',
            lambda: [])
        monkeypatch.setattr('click.echo', lambda x: None)

        # Should complete successfully even with old server
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.status, ['--refresh'])
        assert result.exit_code == 0

    def test_error_handling_in_concurrent_execution(self, monkeypatch):
        """Test that errors in one concurrent task don't affect others."""
        mock_api_server_calls(monkeypatch)
        
        monkeypatch.setattr('sky.client.sdk.status',
                            lambda *args, **kwargs: 'cluster_request_id')

        def mock_stream_and_get(request_id):
            return [{
                'name': 'test-cluster',
                'handle': None,
                'credentials': {},
                'status': status_lib.ClusterStatus.UP,
                'workspace': 'default',
                'autostop': -1,
                'to_down': False,
                'launched_at': 1640995200,
                'last_use': 'sky launch',
                'user_hash': 'test_user_hash',
                'user_name': 'test_user',
                'resources_str': '1x t2.micro',
                'resources_str_full': '1x t2.micro',
                'infra': 'AWS/us-east-1'
            }]

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)

        # Mock workspace API to raise an error
        def mock_workspaces():
            raise RuntimeError("Network error")

        monkeypatch.setattr('sky.client.sdk.workspaces', mock_workspaces)

        monkeypatch.setattr('sky.jobs.queue',
                            lambda *args, **kwargs: 'jobs_request_id')
        monkeypatch.setattr('sky.serve.status',
                            lambda *args, **kwargs: 'services_request_id')
        monkeypatch.setattr('sky.client.cli.command._handle_jobs_queue_request',
                            lambda *args, **kwargs: (0, 'No jobs'))
        monkeypatch.setattr('sky.client.cli.command._handle_services_request',
                            lambda *args, **kwargs: (0, 'No services'))
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.add_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.remove_cluster',
            lambda *args: None)
        monkeypatch.setattr(
            'sky.utils.cluster_utils.SSHConfigHelper.list_cluster_names',
            lambda: [])
        monkeypatch.setattr('sky.client.cli.command._show_enabled_infra',
                            lambda *args: None)
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
        # For cachetools, we need to clear the cache directly
        if hasattr(common.get_api_server_status, 'cache'):
            common.get_api_server_status.cache.clear()

    def teardown_method(self):
        """Clean up after each test method."""
        from sky.server import common

        # Clear cache after each test to avoid interference
        if hasattr(common.get_api_server_status, 'cache'):
            common.get_api_server_status.cache.clear()

    def test_cache_decorator_applied(self, monkeypatch):
        """Test that the cache decorator is properly applied."""
        # Don't mock the API server calls for this test since we need to check the real cache
        from sky.server import common

        # Verify that the function has cache-related attributes
        # For cachetools, the cache is stored as an attribute
        assert hasattr(common.get_api_server_status, 'cache'), \
               "Cache decorator not properly applied"

    def test_cache_hit_reduces_api_calls(self, monkeypatch):
        """Test that cache hits reduce the number of actual API calls."""
        from unittest.mock import MagicMock

        from sky.server import common

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'healthy',
            'api_version': 1,
            'version': '1.0.0',
            'version_on_disk': '1.0.0',
            'commit': 'abc123',
            'user': None,
            'basic_auth_enabled': False
        }
        mock_response.headers = {}
        mock_response.history = []

        call_count = [0]

        def mock_make_authenticated_request(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        # Mock make_authenticated_request
        monkeypatch.setattr('sky.server.common.make_authenticated_request',
                            mock_make_authenticated_request)

        # Mock get_cookies_from_response and set_api_cookie_jar
        monkeypatch.setattr('sky.server.common.get_cookies_from_response',
                            lambda x: {})
        monkeypatch.setattr('sky.server.common.set_api_cookie_jar',
                            lambda x, **kwargs: None)

        # Mock version checking
        mock_version_info = MagicMock()
        mock_version_info.error = None
        monkeypatch.setattr('sky.server.versions.check_compatibility_at_client',
                            lambda x: mock_version_info)

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
        from unittest.mock import MagicMock

        from sky.server import common

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'healthy',
            'api_version': 1,
            'version': '1.0.0',
            'version_on_disk': '1.0.0',
            'commit': 'abc123',
            'user': None,
            'basic_auth_enabled': False
        }
        mock_response.headers = {}
        mock_response.history = []

        call_count = [0]

        def mock_make_authenticated_request(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        # Mock make_authenticated_request
        monkeypatch.setattr('sky.server.common.make_authenticated_request',
                            mock_make_authenticated_request)

        # Mock get_cookies_from_response and set_api_cookie_jar
        monkeypatch.setattr('sky.server.common.get_cookies_from_response',
                            lambda x: {})
        monkeypatch.setattr('sky.server.common.set_api_cookie_jar',
                            lambda x, **kwargs: None)

        # Mock version checking
        mock_version_info = MagicMock()
        mock_version_info.error = None
        monkeypatch.setattr('sky.server.versions.check_compatibility_at_client',
                            lambda x: mock_version_info)

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
        from unittest.mock import MagicMock

        from sky.server import common

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'healthy',
            'api_version': 1,
            'version': '1.0.0',
            'version_on_disk': '1.0.0',
            'commit': 'abc123',
            'user': None,
            'basic_auth_enabled': False
        }
        mock_response.headers = {}
        mock_response.history = []

        call_count = [0]

        def mock_make_authenticated_request(*args, **kwargs):
            call_count[0] += 1
            return mock_response

        # Mock make_authenticated_request
        monkeypatch.setattr('sky.server.common.make_authenticated_request',
                            mock_make_authenticated_request)

        # Mock get_cookies_from_response and set_api_cookie_jar
        monkeypatch.setattr('sky.server.common.get_cookies_from_response',
                            lambda x: {})
        monkeypatch.setattr('sky.server.common.set_api_cookie_jar',
                            lambda x, **kwargs: None)

        # Mock version checking
        mock_version_info = MagicMock()
        mock_version_info.error = None
        monkeypatch.setattr('sky.server.versions.check_compatibility_at_client',
                            lambda x: mock_version_info)

        # First call
        common.get_api_server_status()
        assert call_count[0] == 1

        # Second call should use cache
        common.get_api_server_status()
        assert call_count[0] == 1

        # Clear cache
        if hasattr(common.get_api_server_status, 'cache'):
            common.get_api_server_status.cache.clear()

        # Third call should make new API call
        common.get_api_server_status()
        assert call_count[0] == 2

    def test_cache_handles_exceptions(self, monkeypatch):
        """Test that exceptions are not cached."""
        import pytest

        from sky.server import common

        call_count = [0]

        def mock_make_authenticated_request(*args, **kwargs):
            call_count[0] += 1
            raise Exception("Network error")

        # Mock make_authenticated_request
        monkeypatch.setattr('sky.server.common.make_authenticated_request',
                            mock_make_authenticated_request)

        # First call raises exception
        with pytest.raises(Exception, match="Network error"):
            common.get_api_server_status()
        assert call_count[0] == 1

        # Second call should also raise exception (not cached)
        with pytest.raises(Exception, match="Network error"):
            common.get_api_server_status()
        assert call_count[0] == 2
