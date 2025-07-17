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
