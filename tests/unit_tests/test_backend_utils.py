import os
import pathlib
from unittest import mock

from sky import check as sky_check
from sky import clouds
from sky import skypilot_config
from sky.backends import backend_utils
from sky.exceptions import ClusterNotUpError
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


@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_check_cluster_available_accepts_autostopping(mock_refresh,
                                                      mock_get_backend):
    """Verify check_cluster_available accepts AUTOSTOPPING status."""
    # Mock AUTOSTOPPING cluster
    mock_handle = mock.MagicMock()
    mock_refresh.return_value = (status_lib.ClusterStatus.AUTOSTOPPING,
                                 mock_handle)
    mock_get_backend.return_value = mock.MagicMock()

    # Should not raise ClusterNotUpError for AUTOSTOPPING
    result = backend_utils.check_cluster_available(
        'test-cluster',
        operation='test_operation',
        check_cloud_vm_ray_backend=False)
    assert result == mock_handle


@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_check_cluster_available_rejects_init(mock_refresh, mock_get_backend):
    """Verify check_cluster_available rejects INIT status."""
    mock_handle = mock.MagicMock()
    mock_refresh.return_value = (status_lib.ClusterStatus.INIT, mock_handle)
    mock_get_backend.return_value = mock.MagicMock()

    # Should raise ClusterNotUpError for INIT
    try:
        backend_utils.check_cluster_available('test-cluster',
                                              operation='test_operation',
                                              check_cloud_vm_ray_backend=False)
        assert False, 'Expected ClusterNotUpError to be raised'
    except ClusterNotUpError:
        pass


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_is_controller_accessible_accepts_autostopping(mock_refresh):
    """Verify is_controller_accessible accepts AUTOSTOPPING status."""
    from sky.utils import controller_utils

    mock_handle = mock.MagicMock()
    mock_handle.head_ip = '1.2.3.4'
    mock_refresh.return_value = (status_lib.ClusterStatus.AUTOSTOPPING,
                                 mock_handle)

    # Should not raise for AUTOSTOPPING controller
    result = backend_utils.is_controller_accessible(
        controller_utils.Controllers.JOBS_CONTROLLER,
        stopped_message='Test stopped',
        exit_if_not_accessible=False)
    assert result == mock_handle
