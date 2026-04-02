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
# These test the backend's _handle_resize_pre_provision method which
# implements the resize logic within the launch/provision pipeline.


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


def _make_mock_task(num_nodes=1):
    task = mock.MagicMock()
    task.num_nodes = num_nodes
    return task


def test_resize_scale_up_is_noop_pre_provision():
    """Scale-up should not do anything in pre-provision (bulk_provision
    handles it)."""
    from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=2)
    task = _make_mock_task(num_nodes=4)
    # Should not raise — scale-up is handled by provisioning.
    backend._handle_resize_pre_provision(handle, task, 'test-cluster')


def test_resize_same_size_is_noop_pre_provision():
    """Same-size resize should not raise."""
    from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=3)
    backend._handle_resize_pre_provision(handle, task, 'test-cluster')


@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_running_jobs_rejected(mock_load_queue):
    """Scale-down should fail if jobs are running."""
    from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
    from sky.skylet import job_lib
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    # Mock run_on_head to succeed, and load_job_queue to return a running job.
    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [{
        'job_id': 1,
        'status': job_lib.JobStatus.RUNNING,
    }]

    try:
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')
        assert False, 'Expected ValueError about running jobs'
    except ValueError as e:
        assert 'running' in str(e).lower()
        assert 'sky cancel' in str(e)
