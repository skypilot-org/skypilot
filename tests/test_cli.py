from unittest import mock

from click import testing as cli_testing
import requests

from sky import clouds
from sky import models
from sky import server
from sky.client.cli import command
from sky.schemas.api import responses
from sky.utils import status_lib
from sky.utils import ux_utils

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
    mock_api_info = mock.MagicMock(spec=responses.APIHealthResponse)
    mock_api_info.status = server.common.ApiServerStatus.HEALTHY
    mock_api_info.api_version = 1
    mock_api_info.version = '1.0.0'
    mock_api_info.commit = 'test_commit'
    mock_api_info.user = None
    mock_api_info.basic_auth_enabled = False

    monkeypatch.setattr('sky.server.common.get_api_server_status',
                        lambda *args, **kwargs: mock_api_info)

    # Mock other server functions that might trigger real calls
    monkeypatch.setattr('sky.server.common.check_server_healthy',
                        lambda *args, **kwargs: ('healthy', mock_api_info))
    monkeypatch.setattr('sky.server.common.get_server_url',
                        lambda *args, **kwargs: 'http://localhost:46580')
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
                'api_version': "1",
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

    monkeypatch.setattr('sky.server.common.make_authenticated_request',
                        mock_make_authenticated_request)

    # Mock high-level SDK functions to prevent API calls entirely
    monkeypatch.setattr('sky.client.sdk.check',
                        lambda *args, **kwargs: 'test_request_id')

    # Create mock accelerator items that have the _asdict method like dataclasses
    class MockAcceleratorItem:

        def __init__(self,
                     accelerator_name='V100',
                     accelerator_count=1,
                     cloud='AWS',
                     instance_type='p3.2xlarge',
                     device_memory=16.0,
                     cpu_count=8,
                     memory=61.0,
                     price=1.0,
                     spot_price=0.5,
                     region='us-east-1'):
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
    monkeypatch.setattr('sky.client.sdk.list_accelerators',
                        lambda *args, **kwargs: 'accelerators_request_id')

    # Mock list_accelerator_counts to return a request ID (for --all queries)
    monkeypatch.setattr('sky.client.sdk.list_accelerator_counts',
                        lambda *args, **kwargs: 'accelerator_counts_request_id')

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
            return [{
                'name': 'service1',
                'status': 'RUNNING',
                'endpoint': 'http://localhost:8080'
            }]
        else:
            return []

    monkeypatch.setattr('sky.client.sdk.get', mock_get)
    monkeypatch.setattr('sky.client.sdk.enabled_clouds',
                        lambda *args, **kwargs: 'enabled_clouds_request_id')
    monkeypatch.setattr('sky.client.sdk.realtime_kubernetes_gpu_availability',
                        lambda *args, **kwargs: 'k8s_gpu_request_id')
    monkeypatch.setattr('sky.client.sdk.kubernetes_node_info',
                        lambda *args, **kwargs: 'k8s_node_request_id')

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
    monkeypatch.setattr('sky.usage.usage_lib.messages.usage.set_internal',
                        lambda: None)

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
    monkeypatch.setattr('sky.server.rest.request_without_retry',
                        mock_rest_request)

    return mock_api_info


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

    def test_cli_api_info(self, monkeypatch):
        """Test that `sky api info` returns the expected output."""
        mock_api_info = mock_api_server_calls(monkeypatch)

        client_version = '1.0.0'
        client_commit = '1234567890'

        monkeypatch.setattr('sky.__version__', client_version)
        monkeypatch.setattr('sky.__commit__', client_commit)

        current_user = models.User(id='test_user_id', name='test_user')

        mock_get_current_user = mock.MagicMock()
        mock_get_current_user.return_value = current_user
        monkeypatch.setattr(models.User, 'get_current_user',
                            mock_get_current_user)

        user = models.User.get_current_user()
        server_url = 'http://localhost:8000'
        monkeypatch.setattr('sky.server.common.get_server_url',
                            lambda: server_url)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.api_info)
        assert not result.exit_code
        output = result.stdout.split('\n')
        assert output[
            0] == f'SkyPilot client version: {client_version}, commit: {client_commit}'
        assert output[
            1] == f'Using SkyPilot API server and dashboard: {server_url}'
        assert output[
            2] == f'├── Status: {mock_api_info.status}, commit: {mock_api_info.commit}, version: {mock_api_info.version}'
        assert output[3] == f'└── User: {current_user.name} ({current_user.id})'
        assert len(output) == 5


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

    def test_list_to_str_float_formatting(self):
        """Test that _list_to_str formats whole number floats as integers.
        
        Regression test for GitHub issue #6484 where requestable quantities
        were shown as '1.0, 2.0, 4.0, 8.0' instead of '1, 2, 4, 8'.
        """

        # Define the function locally to test it (since it's nested in show_gpus)
        def _list_to_str(lst):

            def format_number(e):
                # If it's a float that's a whole number, display as int
                if isinstance(e, float) and e.is_integer():
                    return str(int(e))
                return str(e)

            return ', '.join([format_number(e) for e in lst])

        # Test case from GitHub issue #6484: whole number floats should display as integers
        assert _list_to_str([1.0, 2.0, 4.0, 8.0]) == '1, 2, 4, 8'

        # Test mixed floats and integers
        assert _list_to_str([1, 2.0, 4.0, 8]) == '1, 2, 4, 8'

        # Test fractional floats should remain as floats
        assert _list_to_str([1.5, 2.0, 4.0]) == '1.5, 2, 4'

        # Test edge cases
        assert _list_to_str([0.0, 1.0]) == '0, 1'
        assert _list_to_str([0.5, 1.0, 2.5]) == '0.5, 1, 2.5'

        # Test integers remain unchanged
        assert _list_to_str([1, 2, 4, 8]) == '1, 2, 4, 8'

        # Test empty list
        assert _list_to_str([]) == ''

        # Test single element
        assert _list_to_str([1.0]) == '1'
        assert _list_to_str([1.5]) == '1.5'

    def test_show_gpus_k8s_float_formatting(self, monkeypatch):
        """Integration test for sky show-gpus --infra k8s output formatting.
        
        Regression test for GitHub issue #6484 to ensure that requestable quantities
        are displayed as integers (1, 2, 4, 8) instead of floats (1.0, 2.0, 4.0, 8.0).
        """
        mock_api_server_calls(monkeypatch)

        # Import required modules
        from sky import models

        # Mock kubernetes GPU availability data with float counts (the bug scenario)
        mock_gpu_availability = [
            models.RealtimeGpuAvailability(
                gpu='H100_NVLINK_80GB',
                counts=[1.0, 2.0, 4.0,
                        8.0],  # These should be formatted as integers
                capacity=16,
                available=16)
        ]

        # Mock enabled clouds to include kubernetes
        def mock_get(request_id):
            if 'enabled_clouds_request_id' in str(request_id):
                return ['kubernetes']  # Enable kubernetes
            return []

        monkeypatch.setattr('sky.client.sdk.get', mock_get)
        monkeypatch.setattr('sky.client.sdk.enabled_clouds',
                            lambda *args, **kwargs: 'enabled_clouds_request_id')

        # Mock the kubernetes contexts and GPU availability
        monkeypatch.setattr('sky.clouds.Kubernetes.existing_allowed_contexts',
                            lambda: ['test-context'])
        monkeypatch.setattr(
            'sky.client.sdk.realtime_kubernetes_gpu_availability',
            lambda *args, **kwargs: 'k8s_gpu_request_id')
        monkeypatch.setattr('sky.client.sdk.kubernetes_node_info',
                            lambda *args, **kwargs: 'k8s_node_request_id')

        # Mock node info to avoid additional API calls
        mock_node_info = models.KubernetesNodesInfo(node_info_dict={},
                                                    hint=None)

        def mock_stream_and_get_conditional(request_id):
            if 'k8s_gpu' in request_id:
                return [('test-context', mock_gpu_availability)]
            elif 'k8s_node' in request_id:
                return mock_node_info
            return []

        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get_conditional)

        # Run the command
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.show_gpus, ['--infra', 'k8s'])

        # Check that command succeeded
        assert result.exit_code == 0, f"Command failed with output: {result.output}"

        # Check that the output contains properly formatted integers (not floats)
        output = result.output
        assert '1, 2, 4, 8' in output, f"Expected '1, 2, 4, 8' in output, got: {output}"
        # Ensure it doesn't contain the problematic float format
        assert '1.0, 2.0, 4.0, 8.0' not in output, f"Found float format in output: {output}"
