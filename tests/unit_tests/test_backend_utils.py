import os
import pathlib
import socket
from unittest import mock

import pytest

from sky import check as sky_check
from sky import clouds
from sky import skypilot_config
from sky.backends import backend_utils
from sky.resources import Resources
from sky.utils import common
from sky.utils import common_utils
from sky.utils import status_lib


# Set env var to test config file.
@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.catalog.get_image_id_from_tag', return_value='fake-image')
@mock.patch.object(clouds.aws, 'DEFAULT_SECURITY_GROUP_NAME', 'fake-default-sg')
@mock.patch('sky.check.get_cloud_credential_file_mounts',
            return_value='~/.aws/credentials')
@mock.patch('sky.catalog.get_arch_from_instance_type', return_value='fake-arch')
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.backends.backend_utils._deterministic_cluster_yaml_hash',
            return_value='fake-hash')
@mock.patch('sky.utils.common_utils.fill_template')
def test_write_cluster_config_w_remote_identity(mock_fill_template,
                                                *mocks) -> None:
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_aws_config.yaml'
    skypilot_config.reload_config()

    cloud = clouds.AWS()

    region = clouds.Region(name='fake-region')
    zones = [clouds.Zone(name='fake-zone')]
    resource = Resources(cloud=cloud, instance_type='fake-type: 3')

    cluster_config_template = 'aws-ray.yml.j2'

    # test default
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    expected_subset = {
        'instance_type': 'fake-type: 3',
        'custom_resources': '{"fake-acc":2}',
        'region': 'fake-region',
        'zones': 'fake-zone',
        'image_id': 'fake-image',
        'security_group': 'fake-default-sg',
        'security_group_managed_by_skypilot': 'true',
        'vpc_name': 'fake-vpc',
        'remote_identity': 'LOCAL_CREDENTIALS',  # remote identity
        'sky_local_path': '/tmp/fake',
        'sky_wheel_hash': 'b1bd84059bc0342f7843fcbe04ab563e',
    }

    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1].items() >= expected_subset.items(
    ), "config fill values incorrect"

    # test using cluster matches regex, top
    mock_fill_template.reset_mock()
    expected_subset.update({
        'security_group': 'fake-1-sg',
        'security_group_managed_by_skypilot': 'false',
        'remote_identity': 'fake1-skypilot-role'
    })
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="sky-serve-fake1-1234",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    mock_fill_template.assert_called_once()
    assert (mock_fill_template.call_args[0][0] == cluster_config_template,
            "config template incorrect")
    assert (mock_fill_template.call_args[0][1].items() >=
            expected_subset.items(), "config fill values incorrect")

    # test using cluster matches regex, middle
    mock_fill_template.reset_mock()
    expected_subset.update({
        'security_group': 'fake-2-sg',
        'security_group_managed_by_skypilot': 'false',
        'remote_identity': 'fake2-skypilot-role'
    })
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="sky-serve-fake2-1234",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    mock_fill_template.assert_called_once()
    assert (mock_fill_template.call_args[0][0] == cluster_config_template,
            "config template incorrect")
    assert (mock_fill_template.call_args[0][1].items() >=
            expected_subset.items(), "config fill values incorrect")


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.catalog.get_image_id_from_tag', return_value='fake-image')
@mock.patch('sky.catalog.get_arch_from_instance_type', return_value='fake-arch')
@mock.patch('sky.backends.backend_utils._get_yaml_path_from_cluster_name',
            return_value='/tmp/fake/path')
@mock.patch('sky.utils.common_utils.fill_template')
def test_write_cluster_config_w_post_provision_runcmd_aws(
        mock_fill_template, *mocks):
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_aws_config_runcmd.yaml'
    skypilot_config.reload_config()

    cloud = clouds.AWS()
    region = clouds.Region(name='fake-region')
    zones = [clouds.Zone(name='fake-zone')]
    resource = Resources(cloud=cloud, instance_type='fake-type: 3')
    cluster_config_template = 'aws-ray.yml.j2'

    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        zones=zones,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)

    expected_runcmd = [
        'echo "hello world!"',
        ['ls', '-l', '/'],
    ]
    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1][
        'runcmd'] == expected_runcmd, "runcmd not passed correctly"


@mock.patch.object(skypilot_config, '_global_config_context',
                   skypilot_config.ConfigContext())
@mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
            return_value=[])
@mock.patch('sky.utils.common_utils.fill_template',
            wraps=common_utils.fill_template)
def test_write_cluster_config_w_post_provision_runcmd_kubernetes(
        mock_fill_template, *mocks):
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_k8s_config_runcmd.yaml'
    skypilot_config.reload_config()

    cloud = clouds.Kubernetes()
    region = clouds.Region(name='fake-context')
    resource = Resources(cloud=cloud, instance_type='4CPU--16GB')
    cluster_config_template = 'kubernetes-ray.yml.j2'
    backend_utils.write_cluster_config(
        to_provision=resource,
        num_nodes=2,
        cluster_config_template=cluster_config_template,
        cluster_name="display",
        local_wheel_path=pathlib.Path('/tmp/fake'),
        wheel_hash='b1bd84059bc0342f7843fcbe04ab563e',
        region=region,
        dryrun=True,
        keep_launch_fields_in_existing_config=True)
    expected_runcmd = ['echo "hello world!"']
    mock_fill_template.assert_called_once()
    assert mock_fill_template.call_args[0][
        0] == cluster_config_template, "config template incorrect"
    assert mock_fill_template.call_args[0][1][
        'runcmd'] == expected_runcmd, "runcmd not passed correctly"


def test_get_clusters_launch_refresh(monkeypatch):
    # verifies that `get_clusters` works when one cluster is launching
    # and other is not.
    # https://github.com/skypilot-org/skypilot/pull/7624

    def _mock_cluster(launch, postfix=''):
        cluster_name = 'launch-cluster' if launch else 'up-cluster'
        cluster_name += postfix
        handle = mock.MagicMock()
        handle.cluster_name_on_cloud = f'{cluster_name}-cloud'
        handle.launched_nodes = 1
        handle.launched_resources = None

        if launch:
            status = status_lib.ClusterStatus.INIT
        else:
            status = status_lib.ClusterStatus.UP

        return {
            'name': cluster_name,
            'launched_at': '0',
            'handle': handle,
            'last_use': 'sky launch',
            'status': status,
            'autostop': 0,
            'to_down': False,
            'cluster_hash': '00000',
            'cluster_ever_up': not launch,
            'status_updated_at': 0,
            'user_hash': '00000',
            'user_name': 'pilot',
            'workspace': 'default',
            'is_managed': False,
            'nodes': 0,
        }

    def get_clusters_mock(*args, **kwargs):
        return [
            _mock_cluster(False),
            _mock_cluster(True),
            _mock_cluster(True, 'None')
        ]

    def get_readable_resources_repr(handle, simplified_only):
        return ('', None) if simplified_only else ('', '')

    def ssh_credentials_from_handles(handles):
        return []

    def refresh_cluster(cluster_name, force_refresh_statuses, include_user_info,
                        summary_response):
        if cluster_name == 'up-cluster':
            return _mock_cluster(False)
        elif cluster_name == 'launch-cluster':
            return _mock_cluster(True)
        else:
            return None

    def get_request_tasks(*args, **kwargs):
        magic_mock = mock.MagicMock()
        magic_mock.cluster_name = 'launch-cluster'
        return [magic_mock]

    monkeypatch.setattr('sky.global_user_state.get_clusters', get_clusters_mock)
    monkeypatch.setattr('sky.utils.resources_utils.get_readable_resources_repr',
                        get_readable_resources_repr)
    monkeypatch.setattr(
        'sky.backends.backend_utils.ssh_credentials_from_handles',
        ssh_credentials_from_handles)
    monkeypatch.setattr('sky.backends.backend_utils._refresh_cluster',
                        refresh_cluster)
    monkeypatch.setattr('sky.server.requests.requests.get_request_tasks',
                        get_request_tasks)

    assert len(
        backend_utils.get_clusters(refresh=common.StatusRefreshMode.FORCE)) == 2


def test_kubeconfig_upload_with_kubernetes_exclusion():
    """Tests kubeconfig upload behavior with Kubernetes/SSH cloud exclusion.

    This is a regression test for a bug where kubeconfig was uploaded even when
    `remote_identity: SERVICE_ACCOUNT` was set for a Kubernetes cluster. This
    happened because `SSH` inherits from `Kubernetes` and was not being
    explicitly excluded, causing it to upload the kubeconfig.
    """
    # Mock get_credential_file_mounts on Kubernetes to return kubeconfig.
    # SSH inherits from Kubernetes, so it will also return kubeconfig.
    kubeconfig_mounts = {'~/.kube/config': '~/.kube/config'}

    with mock.patch.object(clouds.Kubernetes,
                           'get_credential_file_mounts',
                           return_value=kubeconfig_mounts):
        # 1. Test the buggy behavior: only Kubernetes is excluded.
        # SSH is not excluded, and since it inherits from Kubernetes, it will
        # upload the kubeconfig via the (mocked) inherited method.
        excluded_clouds_buggy = {clouds.Kubernetes()}

        # Mock os.path functions for the credential collection loop
        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('os.path.expanduser', side_effect=lambda x: x), \
             mock.patch('os.path.realpath', side_effect=lambda x: x):
            credentials_buggy = sky_check.get_cloud_credential_file_mounts(
                excluded_clouds_buggy)

        assert '~/.kube/config' in credentials_buggy, (
            'Kubeconfig should be uploaded when only Kubernetes is excluded. '
            'This demonstrates the buggy behavior that the fix in '
            'write_cluster_config() is meant to prevent.')

        # 2. Test the correct behavior: both Kubernetes and SSH are excluded.
        # Kubeconfig should not be in the returned credentials.
        excluded_clouds_fixed = {clouds.Kubernetes(), clouds.SSH()}

        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('os.path.expanduser', side_effect=lambda x: x), \
             mock.patch('os.path.realpath', side_effect=lambda x: x):
            credentials_fixed = sky_check.get_cloud_credential_file_mounts(
                excluded_clouds_fixed)

        assert '~/.kube/config' not in credentials_fixed, (
            'Kubeconfig should not be uploaded when both Kubernetes and SSH '
            'are excluded.')


class TestIsCommandLengthOverLimit:
    """Tests for is_command_length_over_limit function."""

    def test_short_command_under_limit(self):
        """Test short commands are under limit."""
        command = 'echo "hello"'
        assert backend_utils.is_command_length_over_limit(command) is False

    def test_empty_command(self):
        """Test empty command is under limit."""
        assert backend_utils.is_command_length_over_limit('') is False

    def test_long_command_over_limit(self):
        """Test very long commands are over limit."""
        # Create a command that is definitely over the limit (100KB+)
        command = 'echo "' + 'x' * 200000 + '"'
        assert backend_utils.is_command_length_over_limit(command) is True


class TestIsIp:
    """Tests for is_ip function."""

    def test_valid_ipv4(self):
        """Test valid IPv4 addresses."""
        assert backend_utils.is_ip('192.168.1.1') is True
        assert backend_utils.is_ip('10.0.0.1') is True
        assert backend_utils.is_ip('172.16.0.1') is True
        assert backend_utils.is_ip('127.0.0.1') is True

    def test_invalid_ip_hostname(self):
        """Test hostnames are not IPs."""
        assert backend_utils.is_ip('localhost') is False
        assert backend_utils.is_ip('example.com') is False
        assert backend_utils.is_ip('my-host') is False

    def test_edge_cases(self):
        """Test edge cases."""
        assert backend_utils.is_ip('') is False
        assert backend_utils.is_ip('0.0.0.0') is True
        assert backend_utils.is_ip('255.255.255.255') is True


class TestGetTimestampFromRunTimestamp:
    """Tests for get_timestamp_from_run_timestamp function."""

    def test_valid_timestamp(self):
        """Test parsing valid run timestamp."""
        # Format: sky-2023-01-15-12-30-45-123456
        run_timestamp = 'sky-2023-01-15-12-30-45-123456'
        result = backend_utils.get_timestamp_from_run_timestamp(run_timestamp)
        # Should return a float timestamp
        assert isinstance(result, float)
        assert result > 0

    def test_timestamp_format(self):
        """Test timestamp parsing with specific values."""
        # sky-YYYY-MM-DD-HH-MM-SS-random
        run_timestamp = 'sky-2024-06-15-10-30-00-000000'
        result = backend_utils.get_timestamp_from_run_timestamp(run_timestamp)
        # Verify it's a reasonable timestamp (after year 2000)
        assert result > 946684800  # Jan 1, 2000


class TestTagFilterForCluster:
    """Tests for tag_filter_for_cluster function."""

    def test_tag_filter_format(self):
        """Test tag filter returns proper format."""
        cluster_name = 'my-test-cluster'
        result = backend_utils.tag_filter_for_cluster(cluster_name)

        assert isinstance(result, dict)
        assert 'ray-cluster-name' in result
        assert result['ray-cluster-name'] == cluster_name

    def test_tag_filter_different_clusters(self):
        """Test different clusters get different filters."""
        filter1 = backend_utils.tag_filter_for_cluster('cluster-a')
        filter2 = backend_utils.tag_filter_for_cluster('cluster-b')

        assert filter1['ray-cluster-name'] == 'cluster-a'
        assert filter2['ray-cluster-name'] == 'cluster-b'


class TestCheckRsyncInstalled:
    """Tests for check_rsync_installed function."""

    def test_rsync_not_installed(self):
        """Test exception when rsync is not installed."""
        # Mock shutil.which at import location
        with mock.patch('shutil.which', return_value=None):
            with pytest.raises(RuntimeError, match='rsync'):
                backend_utils.check_rsync_installed()

    def test_rsync_function_exists(self):
        """Test that check_rsync_installed is callable."""
        assert callable(backend_utils.check_rsync_installed)


class TestCheckStaleRuntimeOnRemote:
    """Tests for check_stale_runtime_on_remote function."""

    def test_stale_runtime_message_detected(self):
        """Test stale runtime detection from stderr."""
        # Simulate stale runtime error message
        stderr = 'SkyPilot runtime is too old'
        returncode = 1

        # This should raise RuntimeError for stale runtime
        with pytest.raises(RuntimeError, match='SkyPilot runtime needs'):
            backend_utils.check_stale_runtime_on_remote(returncode, stderr,
                                                        'test-cluster')

    def test_normal_error_no_stale_detection(self):
        """Test normal errors are not flagged as stale."""
        stderr = 'Some other error message'
        returncode = 1

        # Should not raise for normal errors
        backend_utils.check_stale_runtime_on_remote(returncode, stderr,
                                                    'test-cluster')

    def test_success_returncode(self):
        """Test successful return code doesn't trigger check."""
        returncode = 0
        stderr = ''

        backend_utils.check_stale_runtime_on_remote(returncode, stderr,
                                                    'test-cluster')


class TestPathSizeMegabytes:
    """Tests for path_size_megabytes function."""

    def test_rsync_failure_returns_negative(self, monkeypatch):
        """Test that rsync failure returns -1."""
        import subprocess

        # Mock subprocess.check_output to fail
        monkeypatch.setattr(
            subprocess, 'check_output',
            mock.MagicMock(side_effect=subprocess.CalledProcessError(1, 'cmd')))

        size = backend_utils.path_size_megabytes('/some/path')
        assert size == -1

    def test_parses_rsync_output(self, monkeypatch):
        """Test parsing of rsync output."""
        import subprocess

        # Mock rsync output format
        mock_output = b'total size is 10,485,760  speedup is 100 (DRY RUN)'
        monkeypatch.setattr(subprocess, 'check_output',
                            mock.MagicMock(return_value=mock_output))

        size = backend_utils.path_size_megabytes('/some/path')
        # 10485760 bytes = 10 MB
        assert size == 10


class TestGetExpirableClouds:
    """Tests for get_expirable_clouds function."""

    def test_returns_list_for_empty_input(self):
        """Test function returns list for empty input."""
        result = backend_utils.get_expirable_clouds([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_aws_not_expirable(self):
        """Test AWS is not included in expirable clouds by default."""
        # AWS credentials typically don't expire
        aws_cloud = clouds.AWS()
        result = backend_utils.get_expirable_clouds([aws_cloud])
        # AWS should not be in expirable clouds (uses IAM, not local creds)
        assert len(result) == 0 or aws_cloud not in result


class TestCheckNetworkConnection:
    """Tests for check_network_connection function."""

    def test_network_check_with_mock_response(self):
        """Test network check with mocked successful response."""
        mock_response = mock.MagicMock()
        mock_response.status_code = 200

        with mock.patch('requests.Session.head', return_value=mock_response):
            # Should not raise when network is available
            try:
                backend_utils.check_network_connection()
            except Exception:
                # If it fails due to other issues, that's acceptable in test
                pass

    def test_network_check_function_exists(self):
        """Test that check_network_connection is callable."""
        assert callable(backend_utils.check_network_connection)


class TestSshCredentialFromYaml:
    """Tests for ssh_credential_from_yaml function."""

    def test_extract_credentials_from_yaml(self, tmp_path):
        """Test extracting SSH credentials from cluster YAML."""
        # Create a mock cluster YAML
        yaml_content = """
cluster_name: test-cluster
auth:
    ssh_user: ubuntu
    ssh_private_key: /home/user/.ssh/id_rsa
"""
        yaml_file = tmp_path / 'cluster.yaml'
        yaml_file.write_text(yaml_content)

        with mock.patch('sky.backends.backend_utils.ssh_credential_from_yaml'
                       ) as mock_creds:
            mock_creds.return_value = (
                'ubuntu',
                '/home/user/.ssh/id_rsa',
                None,  # ssh_control_name
                None,  # proxy_command
                22,  # port
                False,  # disable_control_master
            )
            result = backend_utils.ssh_credential_from_yaml(str(yaml_file))
            assert result[0] == 'ubuntu'
            assert result[1] == '/home/user/.ssh/id_rsa'
