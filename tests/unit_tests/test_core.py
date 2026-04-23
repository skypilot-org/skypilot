from unittest import mock

import pytest

from sky import admin_policy as sky_admin_policy
from sky import clouds
from sky import core
from sky import exceptions
from sky import models
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


class TestEnabledCloudsWorkspacePermission:
    """Tests for workspace permission check in core.enabled_clouds."""

    @mock.patch('sky.core.global_user_state.get_cached_enabled_clouds',
                return_value=[])
    @mock.patch('sky.core.workspaces_core.check_workspace_permission')
    def test_rejects_unauthorized_workspace(self, mock_check, _):
        mock_check.side_effect = exceptions.PermissionDeniedError('no access')
        mock_user = models.User(id='user-1', name='User1')
        with mock.patch('sky.core.common_utils.get_current_user',
                        return_value=mock_user):
            with pytest.raises(exceptions.PermissionDeniedError,
                               match='no access'):
                core.enabled_clouds(workspace='restricted')
        mock_check.assert_called_once_with(mock_user, 'restricted')

    @mock.patch('sky.core.global_user_state.get_cached_enabled_clouds',
                return_value=[])
    @mock.patch('sky.core.workspaces_core.check_workspace_permission')
    def test_skips_check_when_workspace_is_none(self, mock_check, _):
        """When workspace is None, falls back to active workspace
        and does not call check_workspace_permission."""
        with mock.patch('sky.core.skypilot_config.get_active_workspace',
                        return_value='default'), \
             mock.patch('sky.core.skypilot_config.local_active_workspace_ctx'):
            core.enabled_clouds(workspace=None)
        mock_check.assert_not_called()


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


def test_resize_scale_down_on_stopped_cluster_rejected():
    """Scale-down on a non-UP cluster should fail fast rather than hang on
    SSH. Addresses PR review comment on run_on_head + STOPPED."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=3)
    task = _make_mock_task(num_nodes=1)

    # Should not even attempt SSH when cluster is STOPPED.
    backend.run_on_head = mock.MagicMock(side_effect=AssertionError(
        'run_on_head must not be called on a non-UP cluster'))

    with pytest.raises(ValueError, match=r"Cannot scale down.*'STOPPED'"):
        backend._handle_resize_pre_provision(
            handle,
            task,
            'test-cluster',
            cluster_status=status_lib.ClusterStatus.STOPPED)


@mock.patch('sky.provision.terminate_instances')
@mock.patch('sky.global_user_state.get_cluster_yaml_dict')
@mock.patch('sky.skylet.job_lib.load_job_queue')
def test_resize_scale_down_handles_missing_cluster_yaml(mock_load_queue,
                                                        mock_get_yaml,
                                                        mock_terminate):
    """If get_cluster_yaml_dict returns None (e.g. yaml file missing), the
    resize path must not crash with AttributeError."""
    backend = CloudVmRayBackend()
    handle = _make_mock_handle(launched_nodes=4)
    task = _make_mock_task(num_nodes=2)

    backend.run_on_head = mock.MagicMock(return_value=(0, 'payload', ''))
    mock_load_queue.return_value = []
    mock_get_yaml.return_value = None  # <- yaml missing

    backend._handle_resize_pre_provision(handle, task, 'test-cluster')

    # terminate_instances still called, with provider_config=None.
    mock_terminate.assert_called_once_with(
        mock.ANY,
        handle.cluster_name_on_cloud,
        None,
        worker_only=True,
    )


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


@mock.patch('sky.backends.cloud_vm_ray_backend.CloudVmRayBackend.'
            'check_resources_fit_cluster')
@mock.patch('sky.backends.backend_utils.refresh_cluster_record')
@mock.patch('sky.global_user_state.get_cluster_from_name')
def test_check_existing_cluster_resize_uses_task_num_nodes(
        mock_get_cluster, mock_refresh, mock_check_fit):
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

    # Capture task.num_nodes at the moment check_resources_fit_cluster is
    # called. During resize, it must see handle.launched_nodes (2), not the
    # new target 5, so only hardware (not node count) is validated.
    observed_num_nodes = []

    def _capture(_handle, _task, **_kwargs):
        observed_num_nodes.append(_task.num_nodes)

    mock_check_fit.side_effect = _capture

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
    assert observed_num_nodes == [
        2
    ], (f'During resize, check_resources_fit_cluster should see '
        f'num_nodes=handle.launched_nodes (2), got {observed_num_nodes}')
    # Original task.num_nodes must be restored after the check.
    assert task.num_nodes == 5


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


# ---------------------------------------------------------------------------
# Backward compatibility tests for the resize field on /launch
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_launch_preamble(monkeypatch):
    """Stub the sdk.launch decorator preamble so tests can reach the guard
    without hitting a real API server, and ensure an event loop exists for
    Python 3.9 (sky.utils.context.SkyPilotContext.__init__ creates an
    asyncio.Event, which requires a running/set loop on py3.9)."""
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    # Bypass the @check_server_healthy_or_start decorator's network call.
    monkeypatch.setattr('sky.server.common.check_server_healthy_or_start_fn',
                        lambda *a, **kw: None)


@pytest.mark.parametrize('api_version', [None, 1, 24, 49])
def test_sdk_launch_resize_errors_on_old_server(api_version,
                                                _stub_launch_preamble,
                                                monkeypatch):
    """sdk.launch(resize=True) should error if remote API version < 50."""
    import sky
    from sky import exceptions
    from sky.client import sdk

    monkeypatch.setattr('sky.client.sdk.versions.get_remote_api_version',
                        lambda: api_version)

    task = sky.Task(run='echo hi', num_nodes=2)

    with pytest.raises(exceptions.APINotSupportedError, match='Cluster resize'):
        sdk.launch(task, cluster_name='my-cluster', resize=True)


def test_sdk_launch_resize_allowed_on_new_server(_stub_launch_preamble,
                                                 monkeypatch):
    """sdk.launch(resize=True) should pass the guard when remote API version
    >= 50. We short-circuit the real launch path with a sentinel so reaching
    it proves the guard didn't raise."""
    import sky
    from sky.client import sdk

    monkeypatch.setattr('sky.client.sdk.versions.get_remote_api_version',
                        lambda: 50)

    sentinel = RuntimeError('reached _launch')
    monkeypatch.setattr('sky.client.sdk._launch',
                        mock.Mock(side_effect=sentinel))

    task = sky.Task(run='echo hi', num_nodes=2)
    with pytest.raises(RuntimeError, match='reached _launch'):
        sdk.launch(task, cluster_name='my-cluster', resize=True)


def test_sdk_launch_no_resize_skips_version_guard(_stub_launch_preamble,
                                                  monkeypatch):
    """resize=False must never trigger the version guard, even against an
    unknown (None) server version."""
    import sky
    from sky.client import sdk

    monkeypatch.setattr('sky.client.sdk.versions.get_remote_api_version',
                        lambda: None)

    sentinel = RuntimeError('reached _launch')
    monkeypatch.setattr('sky.client.sdk._launch',
                        mock.Mock(side_effect=sentinel))

    task = sky.Task(run='echo hi', num_nodes=2)
    with pytest.raises(RuntimeError, match='reached _launch'):
        sdk.launch(task, cluster_name='my-cluster', resize=False)


def test_launch_body_accepts_resize_field():
    """New server should accept resize=True in the request body."""
    from sky.server.requests import payloads

    body = payloads.LaunchBody(task='task-yaml',
                               cluster_name='my-cluster',
                               resize=True)
    assert body.resize is True


def test_launch_body_defaults_resize_to_false():
    """Old client payloads omitting resize should default to False."""
    from sky.server.requests import payloads

    body = payloads.LaunchBody(task='task-yaml', cluster_name='my-cluster')
    assert body.resize is False


def test_launch_body_old_server_ignores_unknown_resize():
    """Simulate old server: a LaunchBody without the resize field must silently
    drop the unknown field (extra='ignore'), not raise."""
    import pydantic

    # Old LaunchBody schema (pre-resize): same shape minus the resize field,
    # inheriting extra='ignore' from BasePayload.
    from sky.server.requests.payloads import RequestBody

    class OldLaunchBody(RequestBody):
        task: str
        cluster_name: str

    # New client sends resize=True; old server should ignore it.
    body = OldLaunchBody(task='task-yaml',
                         cluster_name='my-cluster',
                         resize=True)
    dumped = body.model_dump()
    assert 'resize' not in dumped
    assert isinstance(body, pydantic.BaseModel)


# ---------------------------------------------------------------------------
# Resize confirmation prompts in sdk._launch
# ---------------------------------------------------------------------------


def _make_resize_confirm_env(monkeypatch, cluster_record, user_hash='u-me'):
    """Stub out dependencies of sdk._launch so the confirmation block runs.

    Returns the captured click.confirm call list and the sdk/sky modules.
    """
    # Imported locally to avoid pulling these heavy modules in at test
    # collection time.
    import sky as _sky  # pylint: disable=import-outside-toplevel
    from sky.client import sdk as _sdk

    # Skip the validate() inside _launch.
    monkeypatch.setattr('sky.client.sdk.validate', lambda *a, **kw: None)
    # Stub the status RPC used inside the confirm block.
    monkeypatch.setattr('sky.client.sdk.status', lambda *a, **kw: 'request-id')
    monkeypatch.setattr('sky.client.sdk.get', lambda _rid: [cluster_record]
                        if cluster_record else [])
    monkeypatch.setattr('sky.client.sdk.common_utils.get_user_hash',
                        lambda: user_hash)
    monkeypatch.setattr('sky.client.sdk.common_utils.get_local_user_name',
                        lambda: 'me')
    monkeypatch.setattr('sky.client.sdk.optimize', lambda *a, **kw: 'req-opt')
    monkeypatch.setattr('sky.client.sdk.stream_and_get',
                        lambda *_a, **_kw: None)
    # Hard-stop before the actual launch HTTP call so we only exercise the
    # confirmation logic.
    monkeypatch.setattr(
        'sky.client.sdk.client_common.upload_mounts_to_api_server',
        lambda *a, **kw:
        (_ for _ in ()).throw(RuntimeError('stop-after-confirm')))

    calls = []

    def fake_confirm(msg, *args, **kwargs):
        calls.append(msg)

    monkeypatch.setattr('click.confirm', fake_confirm)
    monkeypatch.setattr('sky.client.sdk.click.confirm', fake_confirm)

    return calls, _sdk, _sky


def _run_launch_confirm(sdk_mod,
                        sky_mod,
                        cluster_name='my-cluster',
                        num_nodes=3,
                        resize=True):
    dag = sky_mod.Dag()
    with dag:
        task = sky_mod.Task(run='echo hi')
        task.num_nodes = num_nodes
    request_options = sky_admin_policy.RequestOptions(
        cluster_name=cluster_name,
        idle_minutes_to_autostop=None,
        down=False,
        dryrun=False)
    with pytest.raises(RuntimeError, match='stop-after-confirm'):
        sdk_mod._launch(
            dag,
            cluster_name,
            request_options,
            resize=resize,
            _need_confirmation=True,
        )


def test_launch_confirm_resize_scale_up(monkeypatch):
    record = {
        'status': status_lib.ClusterStatus.UP,
        'user_hash': 'u-me',
        'user_name': 'me',
        'nodes': 2,
    }
    calls, sdk_mod, sky_mod = _make_resize_confirm_env(monkeypatch, record)
    _run_launch_confirm(sdk_mod, sky_mod, num_nodes=4)
    assert len(calls) == 1
    msg = calls[0]
    assert 'from 2 to 4 node' in msg
    assert '+2 worker' in msg
    assert 'scale up' in msg


def test_launch_confirm_resize_scale_down(monkeypatch):
    record = {
        'status': status_lib.ClusterStatus.UP,
        'user_hash': 'u-me',
        'user_name': 'me',
        'nodes': 5,
    }
    calls, sdk_mod, sky_mod = _make_resize_confirm_env(monkeypatch, record)
    _run_launch_confirm(sdk_mod, sky_mod, num_nodes=2)
    assert len(calls) == 1
    msg = calls[0]
    assert 'from 5 to 2 node' in msg
    assert '-3 worker' in msg
    assert 'scale down' in msg
    assert 'terminated' in msg


def test_launch_confirm_resize_noop(monkeypatch):
    record = {
        'status': status_lib.ClusterStatus.UP,
        'user_hash': 'u-me',
        'user_name': 'me',
        'nodes': 3,
    }
    calls, sdk_mod, sky_mod = _make_resize_confirm_env(monkeypatch, record)
    _run_launch_confirm(sdk_mod, sky_mod, num_nodes=3)
    assert len(calls) == 1
    msg = calls[0]
    assert 'already has 3 node' in msg
    assert 'no-op' in msg


def test_launch_confirm_resize_cluster_missing_uses_new_cluster_prompt(
        monkeypatch):
    """resize=True against a non-existent cluster should fall through to the
    new-cluster prompt with a note that --resize is ignored."""
    calls, sdk_mod, sky_mod = _make_resize_confirm_env(monkeypatch,
                                                       cluster_record=None)
    _run_launch_confirm(sdk_mod, sky_mod, num_nodes=4)
    assert len(calls) == 1
    msg = calls[0]
    assert 'Launching a new cluster' in msg
    assert '--resize will be ignored' in msg


def test_launch_confirm_resize_nodes_field_missing(monkeypatch):
    """If the cluster record is missing the 'nodes' field (e.g. older server
    or partial status response), the resize prompt must fall back to a
    generic message instead of crashing with TypeError from comparing int to
    None."""
    # Intentionally omit 'nodes' from the record.
    record = {
        'status': status_lib.ClusterStatus.UP,
        'user_hash': 'u-me',
        'user_name': 'me',
    }
    calls, sdk_mod, sky_mod = _make_resize_confirm_env(monkeypatch, record)
    _run_launch_confirm(sdk_mod, sky_mod, num_nodes=4)
    assert len(calls) == 1
    msg = calls[0]
    # Generic fallback: no "from N to M" comparison, just target count.
    assert 'to 4 node' in msg
    assert 'from' not in msg
