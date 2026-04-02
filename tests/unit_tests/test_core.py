from unittest import mock

from sky import core
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.utils import common
from sky.utils import common_utils
from sky.utils import status_lib


@mock.patch('sky.backends.backend_utils.check_cluster_available')
@mock.patch('sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.cancel_jobs')
def test_cancel_jobs_for_current_user(mock_cancel_jobs,
                                      mock_check_cluster_available) -> None:
    mock_handle = mock.create_autospec(CloudVmRayResourceHandle, instance=True)
    mock_check_cluster_available.return_value = mock_handle
    core.cancel('test-cluster', all=True)
    mock_cancel_jobs.assert_called_once_with(
        mock_handle,
        None,
        cancel_all=True,
        user_hash=common_utils.get_current_user().id,
    )


@mock.patch('sky.backends.backend_utils.check_cluster_available')
@mock.patch('sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.cancel_jobs')
def test_cancel_jobs_for_all_users(mock_cancel_jobs,
                                   mock_check_cluster_available) -> None:
    mock_handle = mock.create_autospec(CloudVmRayResourceHandle, instance=True)
    mock_check_cluster_available.return_value = mock_handle
    core.cancel('test-cluster', all_users=True)
    mock_cancel_jobs.assert_called_once_with(
        mock_handle,
        None,
        cancel_all=True,
        user_hash=None,
    )


@mock.patch(
    'sky.backends.backend_utils.get_clusters',
    return_value=[
        {
            # properly formatted cluster record
            'name': 'test-cluster',
            'launched_at': '0',
            'handle': None,
            'last_use': 'sky launch',
            'status': status_lib.ClusterStatus.UP,
            'autostop': 0,
            'to_down': False,
            'cluster_hash': '00000',
            'cluster_ever_up': True,
            'status_updated_at': 0,
            'user_hash': '00000',
            'user_name': 'pilot',
            'workspace': 'default',
            'is_managed': False,
            'nodes': 0,
        },
        {
            # cluster record with missing fields
            'name': 'malformed-cluster',
        }
    ])
def test_status_best_effort(mock_get_clusters) -> None:
    with mock.patch('sky.core.logger') as mock_logger:
        core.status()
    mock_get_clusters.assert_called_once_with(
        refresh=common.StatusRefreshMode.NONE,
        cluster_names=None,
        all_users=False,
        include_credentials=False,
        summary_response=False,
        include_handle=True,
    )
    assert len(core.status()) == 1
    # Verify logging shows 0 cleaned requests
    mock_logger.warning.assert_called_once()
    log_message = mock_logger.warning.call_args[0][0]
    assert ('Failed to validate status responses for cluster malformed-cluster'
            in log_message)


# --- Resize tests ---


def _make_mock_handle(cluster_name='test-cluster',
                      launched_nodes=2,
                      region='us-central1',
                      zone='us-central1-a'):
    """Helper to create a mock CloudVmRayResourceHandle."""
    handle = mock.create_autospec(CloudVmRayResourceHandle, instance=True)
    handle.cluster_name = cluster_name
    handle.cluster_name_on_cloud = f'{cluster_name}-abcd1234'
    handle.launched_nodes = launched_nodes
    handle.docker_user = None
    handle.cluster_yaml = '/tmp/fake-cluster.yaml'

    mock_resources = mock.MagicMock()
    mock_resources.cloud = mock.MagicMock()
    mock_resources.region = region
    mock_resources.zone = zone
    handle.launched_resources = mock_resources
    handle.stable_internal_external_ips = [('10.0.0.1', '1.2.3.4'),
                                           ('10.0.0.2', '1.2.3.5')]
    handle.stable_ssh_ports = [22, 22]
    handle.cached_cluster_info = None
    return handle


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_resize_cluster_does_not_exist(mock_refresh):
    """Resize should raise ClusterDoesNotExist if cluster not found."""
    from sky import exceptions
    mock_refresh.return_value = (None, None)
    try:
        core.resize('no-such-cluster', num_nodes=4)
        assert False, 'Expected ClusterDoesNotExist'
    except exceptions.ClusterDoesNotExist:
        pass


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_resize_cluster_not_up(mock_refresh):
    """Resize should raise ClusterNotUpError if not UP."""
    from sky import exceptions
    handle = _make_mock_handle()
    mock_refresh.return_value = (status_lib.ClusterStatus.STOPPED, handle)
    try:
        core.resize('test-cluster', num_nodes=4)
        assert False, 'Expected ClusterNotUpError'
    except exceptions.ClusterNotUpError:
        pass


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
def test_resize_invalid_num_nodes(mock_refresh):
    """Resize should raise ValueError for num_nodes < 1."""
    handle = _make_mock_handle()
    mock_refresh.return_value = (status_lib.ClusterStatus.UP, handle)
    try:
        core.resize('test-cluster', num_nodes=0)
        assert False, 'Expected ValueError'
    except ValueError:
        pass


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
@mock.patch('sky.core._check_no_running_jobs')
@mock.patch('sky.global_user_state.get_cluster_yaml_dict')
@mock.patch('sky.provision.get_cluster_info')
def test_resize_scale_down_running_jobs_rejected(mock_get_info, mock_get_yaml,
                                                 mock_check_jobs,
                                                 mock_get_backend,
                                                 mock_refresh):
    """Scale-down should fail if jobs are running on the cluster."""
    from sky.backends import cloud_vm_ray_backend
    from sky.provision import common as provision_common
    handle = _make_mock_handle(launched_nodes=3)
    mock_refresh.return_value = (status_lib.ClusterStatus.UP, handle)
    mock_backend = mock.create_autospec(cloud_vm_ray_backend.CloudVmRayBackend,
                                        instance=True)
    mock_get_backend.return_value = mock_backend
    mock_get_yaml.return_value = {'provider': {}}
    worker1 = provision_common.InstanceInfo(instance_id='w1',
                                            internal_ip='10.0.0.2',
                                            external_ip=None,
                                            tags={})
    worker2 = provision_common.InstanceInfo(instance_id='w2',
                                            internal_ip='10.0.0.3',
                                            external_ip=None,
                                            tags={})
    mock_cluster_info = mock.MagicMock()
    mock_cluster_info.get_worker_instances.return_value = [worker1, worker2]
    mock_get_info.return_value = mock_cluster_info
    # Simulate running jobs blocking scale-down.
    mock_check_jobs.side_effect = ValueError(
        'Cannot scale down: 1 job(s) still running (IDs: 1). '
        'Cancel them first with: sky cancel test-cluster -a')
    try:
        core.resize('test-cluster', num_nodes=1)
        assert False, 'Expected ValueError about running jobs'
    except ValueError as e:
        assert 'running' in str(e)
        assert 'sky cancel' in str(e)


@mock.patch('sky.backends.backend_utils.refresh_cluster_status_handle')
@mock.patch('sky.backends.backend_utils.get_backend_from_handle')
def test_resize_same_num_nodes_is_noop(mock_get_backend, mock_refresh):
    """Resize to same num_nodes should be a no-op and return handle."""
    from sky.backends import cloud_vm_ray_backend
    handle = _make_mock_handle(launched_nodes=3)
    mock_refresh.return_value = (status_lib.ClusterStatus.UP, handle)
    mock_backend = mock.create_autospec(cloud_vm_ray_backend.CloudVmRayBackend,
                                        instance=True)
    mock_get_backend.return_value = mock_backend
    result = core.resize('test-cluster', num_nodes=3)
    assert result is handle
