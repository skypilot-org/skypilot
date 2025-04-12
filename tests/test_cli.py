import tempfile
import textwrap
from unittest import mock

from click import testing as cli_testing
import requests

from sky import cli
from sky import clouds
from sky import exceptions
from sky import server

CLOUDS_TO_TEST = [
    'aws', 'gcp', 'ibm', 'azure', 'lambda', 'scp', 'oci', 'vsphere', 'nebius'
]


def mock_server_api_version(monkeypatch, version):
    original_get = requests.get

    def mock_get(url, *args, **kwargs):
        if '/api/health' in url:
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'api_version': version}
            return mock_response
        return original_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, 'get', mock_get)


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
        result = cli_runner.invoke(cli.show_gpus, [])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['--all'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['V100:4'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, [':4'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['V100:0'])
        assert isinstance(result.exception, SystemExit)

        result = cli_runner.invoke(cli.show_gpus, ['V100:-2'])
        assert isinstance(result.exception, SystemExit)

        result = cli_runner.invoke(cli.show_gpus,
                                   ['--cloud', 'aws', '--region', 'us-west-1'])
        assert not result.exit_code

        for cloud in CLOUDS_TO_TEST:
            result = cli_runner.invoke(cli.show_gpus, ['--cloud', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(cli.show_gpus,
                                       ['--cloud', cloud, '--all'])
            assert not result.exit_code

            result = cli_runner.invoke(cli.show_gpus,
                                       ['V100', '--cloud', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(cli.show_gpus,
                                       ['V100:4', '--cloud', cloud])
            assert not result.exit_code

            result = cli_runner.invoke(cli.show_gpus,
                                       ['V100:4', '--cloud', cloud, '--all'])
            assert isinstance(result.exception, SystemExit)

    def test_k8s_alias_check(self):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.check, ['k8s'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.check, ['kubernetes'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.check, ['notarealcloud'])
        assert isinstance(result.exception, ValueError)


class TestAllCloudsEnabled:

    def test_accelerator_mismatch(self, enable_all_clouds):
        """Test the specified accelerator does not match the instance_type."""

        spec = textwrap.dedent("""\
            resources:
                cloud: aws
                instance_type: p3.2xlarge""")
        cli_runner = cli_testing.CliRunner()

        def _capture_mismatch_gpus_spec(file_path, gpus: str):
            result = cli_runner.invoke(cli.launch,
                                       [file_path, '--gpus', gpus, '--dryrun'])
            assert isinstance(result.exception,
                              exceptions.ResourcesMismatchError)
            assert 'Infeasible resource demands found:' in str(result.exception)

        def _capture_match_gpus_spec(file_path, gpus: str):
            result = cli_runner.invoke(cli.launch,
                                       [file_path, '--gpus', gpus, '--dryrun'])
            assert not result.exit_code

        with tempfile.NamedTemporaryFile('w', suffix='.yml') as f:
            f.write(spec)
            f.flush()

            _capture_mismatch_gpus_spec(f.name, 'T4:1')
            _capture_mismatch_gpus_spec(f.name, 'T4:0.5')
            _capture_mismatch_gpus_spec(f.name, 'V100:2')
            _capture_mismatch_gpus_spec(f.name, 'v100:2')
            _capture_mismatch_gpus_spec(f.name, 'V100:0.5')

            _capture_match_gpus_spec(f.name, 'V100:1')
            _capture_match_gpus_spec(f.name, 'V100:1')
            _capture_match_gpus_spec(f.name, 'V100')

    def test_k8s_alias(self, enable_all_clouds):
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.launch, ['--cloud', 'k8s', '--dryrun'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.launch,
                                   ['--cloud', 'kubernetes', '--dryrun'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.launch,
                                   ['--cloud', 'notarealcloud', '--dryrun'])
        assert isinstance(result.exception, ValueError)

        result = cli_runner.invoke(cli.show_gpus, ['--cloud', 'k8s'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['--cloud', 'kubernetes'])
        assert not result.exit_code

        result = cli_runner.invoke(cli.show_gpus, ['--cloud', 'notarealcloud'])
        assert isinstance(result.exception, ValueError)


class TestServerVersion:

    def test_cli_low_version_server_high_version(self, monkeypatch,
                                                 mock_client_requests):
        mock_server_api_version(monkeypatch, '2')
        monkeypatch.setattr(server.constants, 'API_VERSION', 3)
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.status, [])
        assert "SkyPilot API server is too old: v2 (client version is v3)." in str(
            result.exception)
        assert result.exit_code == 1

    def test_cli_high_version_server_low_version(self, monkeypatch,
                                                 mock_client_requests):
        mock_server_api_version(monkeypatch, '3')
        monkeypatch.setattr(server.constants, 'API_VERSION', 2)
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(cli.status, [])

        # Verify the error message contains correct versions
        assert "SkyPilot API server is too old: v3 (client version is v2)." in str(
            result.exception)
        assert result.exit_code == 1


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
        records = cli._get_cluster_records_and_set_ssh_config(['test-cluster'])
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
        records = cli._get_cluster_records_and_set_ssh_config(None,
                                                              all_users=True)
        assert records == mock_records
        mock_add_cluster.assert_called_once()
        # Should remove old-cluster since it's not in the returned records
        mock_remove_cluster.assert_called_once_with('old-cluster')

        # Test case 3: Test with a cluster that has no handle/credentials
        mock_records_no_handle = [{'name': 'test-cluster', 'handle': None}]
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            lambda *args, **kwargs: mock_records_no_handle)

        records = cli._get_cluster_records_and_set_ssh_config(['test-cluster'])
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
        records = cli._get_cluster_records_and_set_ssh_config(['test-cluster'])
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
