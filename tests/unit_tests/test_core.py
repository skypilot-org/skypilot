from unittest import mock

import click
import pytest

from sky import clouds
from sky import core
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.skylet import job_lib
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


# ---------------------------------------------------------------------------
# Resize tests
# ---------------------------------------------------------------------------
# These test the backend's _handle_resize_pre_provision method which
# implements the resize logic, and the _check_existing_cluster integration.


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


# --- Scale-up / same-size (pre-provision is a no-op) ---


def test_resize_scale_up_is_noop_pre_provision():
    """Scale-up should not do anything in pre-provision (bulk_provision
    handles it)."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=2)
    task = _make_mock_task(num_nodes=4)
    # Should not raise — scale-up is handled by provisioning.
    backend._handle_resize_pre_provision(handle, task, 'test-cluster')


def test_resize_same_size_is_noop_pre_provision():
    """Same-size resize should not raise."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=3)
    backend._handle_resize_pre_provision(handle, task, 'test-cluster')


# --- Scale-down: rejection cases ---


@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_running_jobs_rejected(mock_load_queue):
    """Scale-down should fail if jobs are running."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [{
        'job_id': 1,
        'status': job_lib.JobStatus.RUNNING,
    }]

    with pytest.raises(ValueError, match=r'Cannot scale down.*running'):
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')


@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_setting_up_jobs_rejected(mock_load_queue):
    """Scale-down should fail if jobs are setting up."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [{
        'job_id': 5,
        'status': job_lib.JobStatus.SETTING_UP,
    }]

    with pytest.raises(ValueError, match=r'Cannot scale down'):
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')


@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_pending_jobs_rejected(mock_load_queue):
    """Scale-down should fail if jobs are pending."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [{
        'job_id': 10,
        'status': job_lib.JobStatus.PENDING,
    }]

    with pytest.raises(ValueError, match=r'Cannot scale down'):
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')


@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_multiple_in_progress_shows_all_ids(mock_load_queue):
    """Error message should list all in-progress job IDs."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=4)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [
        {
            'job_id': 1,
            'status': job_lib.JobStatus.RUNNING
        },
        {
            'job_id': 2,
            'status': job_lib.JobStatus.PENDING
        },
        {
            'job_id': 3,
            'status': job_lib.JobStatus.SETTING_UP
        },
    ]

    with pytest.raises(ValueError, match=r'3 job\(s\)') as exc_info:
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')
    err = str(exc_info.value)
    assert '1' in err and '2' in err and '3' in err


def test_resize_scale_down_ssh_failure_aborts():
    """Scale-down should abort if job queue check fails (SSH error)."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(255, '',
                                                       'Connection refused'))

    with pytest.raises(RuntimeError, match=r'Failed to check job queue'):
        backend._handle_resize_pre_provision(handle, task, 'test-cluster')


# --- Scale-down: success cases (the happy path) ---


@mock.patch('sky.provision.terminate_instances')
@mock.patch('sky.global_user_state.get_cluster_yaml_dict')
@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_idle_cluster_terminates_workers(
        mock_load_queue, mock_get_yaml, mock_terminate):
    """Scale-down on an idle cluster should terminate excess workers."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=4)
    task = _make_mock_task(num_nodes=2)

    # No running jobs.
    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = []
    mock_get_yaml.return_value = {'provider': {'type': 'kubernetes'}}

    backend._handle_resize_pre_provision(handle, task, 'test-cluster')

    mock_terminate.assert_called_once_with(
        mock.ANY,  # cloud_name
        handle.cluster_name_on_cloud,
        {'type': 'kubernetes'},
        worker_only=True,
    )


@mock.patch('sky.provision.terminate_instances')
@mock.patch('sky.global_user_state.get_cluster_yaml_dict')
@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_completed_jobs_allowed(mock_load_queue,
                                                  mock_get_yaml,
                                                  mock_terminate):
    """Completed/failed jobs should not block scale-down."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = [
        {
            'job_id': 1,
            'status': job_lib.JobStatus.SUCCEEDED
        },
        {
            'job_id': 2,
            'status': job_lib.JobStatus.FAILED
        },
        {
            'job_id': 3,
            'status': job_lib.JobStatus.CANCELLED
        },
    ]
    mock_get_yaml.return_value = {'provider': {'type': 'kubernetes'}}

    # Should not raise — all jobs are terminal.
    backend._handle_resize_pre_provision(handle, task, 'test-cluster')
    mock_terminate.assert_called_once()


@mock.patch('sky.provision.terminate_instances')
@mock.patch('sky.global_user_state.get_cluster_yaml_dict')
@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_to_one_node(mock_load_queue, mock_get_yaml,
                                       mock_terminate):
    """Scale-down to 1 node (head-only) should work."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = []
    mock_get_yaml.return_value = {'provider': {'type': 'kubernetes'}}

    backend._handle_resize_pre_provision(handle, task, 'test-cluster')
    mock_terminate.assert_called_once()


# --- _check_existing_cluster integration ---


@mock.patch('sky.backends.backend_utils.refresh_cluster_record')
@mock.patch('sky.global_user_state.get_cluster_from_name')
def test_check_existing_cluster_resize_uses_task_num_nodes(
        mock_get_cluster, mock_refresh):
    """When resize=True and cluster exists, ToProvisionConfig.num_nodes
    should be task.num_nodes (not handle.launched_nodes)."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=2)
    # Make launched_resources.assert_launchable() work (mock treats
    # assert_* as assertions by default).
    # unsafe=True allows mock attributes starting with 'assert_'.
    launched_res = mock.MagicMock(unsafe=True)
    launched_res.cloud = mock.MagicMock()
    launched_res.region = 'us-central1'
    launched_res.zone = 'us-central1-a'
    launched_res.ports = None
    launched_res.assert_launchable.return_value = launched_res
    launched_res.cloud.OPEN_PORTS_VERSION = clouds.OpenPortsVersion.UPDATABLE
    handle.launched_resources = launched_res

    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_ever_up': True,
        'config_hash': 'abc123',
    }
    mock_get_cluster.return_value = record
    mock_refresh.return_value = record

    task = _make_mock_task(num_nodes=5)
    mock_resource = mock.MagicMock()
    mock_resource.ports = None
    mock_resource.docker_login_config = None
    mock_resource.cluster_config_overrides = None
    task.resources = {mock_resource}

    with mock.patch(
            'sky.global_user_state.get_cluster_yaml_str') as mock_yaml_str:
        mock_yaml_str.return_value = None
        config = backend._check_existing_cluster(task,
                                                 launched_res,
                                                 'test-cluster',
                                                 dryrun=False,
                                                 resize=True)

    assert config.num_nodes == 5, (f'Expected task.num_nodes=5 but got '
                                   f'{config.num_nodes}')


@mock.patch('sky.backends.backend_utils.refresh_cluster_record')
@mock.patch('sky.global_user_state.get_cluster_from_name')
def test_check_existing_cluster_no_resize_uses_handle_nodes(
        mock_get_cluster, mock_refresh):
    """Without resize, ToProvisionConfig.num_nodes should be
    handle.launched_nodes (not task.num_nodes)."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=2)
    launched_res = mock.MagicMock(unsafe=True)
    launched_res.cloud = mock.MagicMock()
    launched_res.region = 'us-central1'
    launched_res.zone = 'us-central1-a'
    launched_res.ports = None
    launched_res.assert_launchable.return_value = launched_res
    launched_res.cloud.OPEN_PORTS_VERSION = clouds.OpenPortsVersion.UPDATABLE
    handle.launched_resources = launched_res

    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_ever_up': True,
        'config_hash': 'abc123',
    }
    mock_get_cluster.return_value = record
    mock_refresh.return_value = record

    task = _make_mock_task(num_nodes=2)
    mock_resource = mock.MagicMock()
    mock_resource.ports = None
    mock_resource.docker_login_config = None
    mock_resource.cluster_config_overrides = None
    mock_resource.less_demanding_than.return_value = True
    task.resources = {mock_resource}

    with mock.patch('sky.global_user_state.get_cluster_yaml_str'
                   ) as mock_yaml_str, \
         mock.patch('sky.usage.usage_lib.messages'):
        mock_yaml_str.return_value = None
        config = backend._check_existing_cluster(task,
                                                 launched_res,
                                                 'test-cluster',
                                                 dryrun=False,
                                                 resize=False)

    assert config.num_nodes == 2, (f'Expected handle.launched_nodes=2 but got '
                                   f'{config.num_nodes}')


@mock.patch('sky.backends.backend_utils.refresh_cluster_record')
@mock.patch('sky.global_user_state.get_cluster_from_name')
def test_check_existing_cluster_resize_nonexistent_warns(
        mock_get_cluster, mock_refresh):
    """resize=True on a non-existent cluster should warn and fall through
    to normal launch (not raise)."""
    backend = CloudVmRayBackend()

    mock_get_cluster.return_value = None
    mock_refresh.return_value = None

    task = _make_mock_task(num_nodes=3)
    mock_resource = mock.MagicMock()
    mock_resource.less_demanding_than.return_value = True
    task.resources = {mock_resource}

    to_provision = mock.MagicMock(unsafe=True)
    to_provision.assert_launchable.return_value = to_provision

    # Should not raise — just warn and proceed to new cluster creation.
    with mock.patch('sky.utils.common_utils.check_cluster_name_is_valid'):
        config = backend._check_existing_cluster(task,
                                                 to_provision,
                                                 'new-cluster',
                                                 dryrun=False,
                                                 resize=True)
    # Should return a config for a new cluster.
    assert config.prev_cluster_status is None


# --- CLI validation ---


def test_cli_resize_without_cluster_name_errors():
    """--resize without -c should raise UsageError."""
    from click.testing import CliRunner

    from sky.client.cli import command as cli_command

    runner = CliRunner()
    result = runner.invoke(cli_command.launch, ['--resize', '--num-nodes', '4'])

    assert result.exit_code != 0
    assert '--resize requires -c' in result.output or \
           '--resize requires -c' in str(result.exception)
