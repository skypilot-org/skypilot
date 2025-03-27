import tempfile
import textwrap
from unittest import mock

from click import testing as cli_testing
import requests

from sky import cli
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
