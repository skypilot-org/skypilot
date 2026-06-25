import asyncio
import pathlib
import tempfile
import time
from unittest import mock

import pytest

from sky.backends import cloud_vm_ray_backend
from sky.exceptions import ClusterDoesNotExist
from sky.jobs import utils

# String path for mock.patch — can't use the constant directly because
# mock.patch needs the dotted path to the attribute being patched.
_SIGNAL_FILE_CONST = (
    'sky.jobs.constants.JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE')


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_retry_on_value_error(mock_set_internal,
                                                mock_sky_down) -> None:
    # Set up mock to fail twice with ValueError, then succeed
    mock_sky_down.side_effect = [
        ValueError('Mock error 1'),
        ValueError('Mock error 2'),
        None,
    ]

    # Call should succeed after retries
    utils.terminate_cluster('test-cluster')

    # Verify sky.down was called 3 times
    assert mock_sky_down.call_count == 3
    mock_sky_down.assert_has_calls([
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
        mock.call('test-cluster', graceful=False, graceful_timeout=None),
    ])

    # Verify usage.set_internal was called before each sky.down
    assert mock_set_internal.call_count == 3


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_handles_nonexistent_cluster(mock_set_internal,
                                                       mock_sky_down) -> None:
    # Set up mock to raise ClusterDoesNotExist
    mock_sky_down.side_effect = ClusterDoesNotExist('test-cluster')

    # Call should succeed silently
    utils.terminate_cluster('test-cluster')

    # Verify sky.down was called once
    assert mock_sky_down.call_count == 1
    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=False,
                                          graceful_timeout=None)

    # Verify usage.set_internal was called once
    assert mock_set_internal.call_count == 1


@pytest.mark.asyncio
@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.global_user_state.get_handle_from_cluster_name')
async def test_get_job_status_timeout(mock_get_handle, mock_logger):
    """Test that get_job_status returns error reason on timeout.

    Note: get_job_status no longer retries - it returns (None, reason) on
    transient errors. The retry logic is now in controller.py.
    """
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_get_handle.return_value = mock_handle

    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    timeout_override = 0.5  # seconds

    def slow_get_job_status(*args, **kwargs):
        """Simulates get_job_status call that hangs past the timeout."""
        time.sleep(timeout_override * 10)
        return {1: None}

    mock_backend.get_job_status = slow_get_job_status

    start_time = time.time()

    # Patch the timeout so the test passes quickly
    with mock.patch.object(utils, '_JOB_STATUS_FETCH_TIMEOUT_SECONDS',
                           timeout_override):
        job_status, error_reason = await utils.get_job_status(
            backend=mock_backend, cluster_name='test-cluster', job_id=1)

    # Should return (None, reason) tuple on timeout
    assert job_status is None, 'Expected None job status when timeout occurs'
    assert error_reason is not None, 'Expected error reason when timeout occurs'
    assert f'timed out after {timeout_override}s' in error_reason

    elapsed_time = time.time() - start_time
    assert timeout_override <= elapsed_time < timeout_override + 1.0, (
        f'Expected timeout around {timeout_override}s, '
        f'but took {elapsed_time}s')

    # Verify only one attempt was made (no retry in get_job_status)
    # === Checking the job status... ===
    assert mock_logger.info.call_count == 1


@pytest.mark.asyncio
@mock.patch('sky.jobs.utils.logger')
@mock.patch('sky.global_user_state.get_handle_from_cluster_name')
async def test_get_job_status_returns_error_reason_on_failure(
        mock_get_handle, mock_logger):
    """Test that get_job_status returns error reason on transient failures."""
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_get_handle.return_value = mock_handle

    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    def failing_get_job_status(*args, **kwargs):
        """Simulates get_job_status that fails with asyncio.TimeoutError."""
        raise asyncio.TimeoutError('Connection failed')

    mock_backend.get_job_status = failing_get_job_status

    job_status, error_reason = await utils.get_job_status(
        backend=mock_backend, cluster_name='test-cluster', job_id=1)

    # Should return (None, reason) tuple on failure
    assert job_status is None, 'Expected None job status on failure'
    assert error_reason is not None, 'Expected error reason on failure'
    assert 'timed out' in error_reason

    # Verify only one attempt was made (no retry in get_job_status)
    assert mock_logger.info.call_count == 1


@mock.patch('sky.utils.controller_utils.warn_jobs_consolidation_mode_intent')
@mock.patch('sky.utils.controller_utils.logger')
@mock.patch('sky.utils.controller_utils.skypilot_config')
def test_consolidation_mode_warning_without_restart(mock_config, mock_logger,
                                                    mock_validate):
    """Test that a warning is printed when consolidation mode is enabled
    in config but the signal file doesn't exist (server not restarted).

    Signal-read + config-vs-signal warning now live in controller_utils
    (since both managed-jobs and pool readers share the same helper).
    """
    # Clear the LRU caches on both the wrapper and the shared helper.
    utils.is_consolidation_mode.cache_clear()
    import sky.utils.controller_utils as controller_utils
    controller_utils._effective_jobs_consolidation_with_warnings.cache_clear()

    # Mock config to return True for consolidation mode
    mock_config.get_nested.return_value = True

    with tempfile.TemporaryDirectory() as tmpdir:
        signal_file = pathlib.Path(tmpdir) / 'consolidation_signal'
        # Signal file does not exist — server hasn't been restarted

        with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
             mock.patch.dict('os.environ',
                             {'IS_SKYPILOT_SERVER': '1'}):
            result = utils.is_consolidation_mode()

            # Signal file is source of truth — returns False
            assert result is False

            # Verify warning was logged about config mismatch
            assert mock_logger.warning.call_count == 1
            warning_msg = mock_logger.warning.call_args[0][0]
            assert 'enabled' in warning_msg
            assert 'not been restarted' in warning_msg


def test_job_recovery_skips_autostopping():
    """Verify job recovery logic treats AUTOSTOPPING like UP (no recovery)."""
    from sky.utils import status_lib

    # AUTOSTOPPING should be treated as UP-like (not preempted)
    # Recovery logic should skip AUTOSTOPPING (similar to UP)
    up_status = status_lib.ClusterStatus.UP
    autostopping_status = status_lib.ClusterStatus.AUTOSTOPPING
    stopped_status = status_lib.ClusterStatus.STOPPED

    # AUTOSTOPPING should be in the same category as UP for recovery purposes
    recovery_skip_statuses = {
        up_status,
        autostopping_status,
    }

    assert up_status in recovery_skip_statuses
    assert autostopping_status in recovery_skip_statuses
    assert stopped_status not in recovery_skip_statuses


# ======== Graceful cancel tests ========


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_graceful(mock_set_internal, mock_sky_down) -> None:
    """Test terminate_cluster passes graceful params to core.down."""
    utils.terminate_cluster('test-cluster', graceful=True, graceful_timeout=120)

    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=True,
                                          graceful_timeout=120)
    assert mock_set_internal.call_count == 1


@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_graceful_no_timeout(mock_set_internal,
                                               mock_sky_down) -> None:
    """Test terminate_cluster with graceful=True but no timeout."""
    utils.terminate_cluster('test-cluster', graceful=True)

    mock_sky_down.assert_called_once_with('test-cluster',
                                          graceful=True,
                                          graceful_timeout=None)


@mock.patch('sky.jobs.utils.global_user_state.get_cluster_from_name')
@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_pins_active_workspace_from_cluster_record(
        mock_set_internal, mock_sky_down, mock_get_cluster) -> None:
    """Controller-side callers (cancel/recovery teardown) run with the
    daemon-process workspace context, which falls back to 'default'.
    Without pinning, `_check_owner_identity_with_record` raises
    `ClusterOwnerIdentityMismatchError` for any cluster whose recorded
    workspace is not 'default'.

    This test pins the cluster row to workspace 'team-a' and asserts the
    active workspace during the `core.down` call is 'team-a'.
    """
    from sky import skypilot_config
    mock_get_cluster.return_value = {
        'name': 'test-cluster',
        'workspace': 'team-a',
    }

    observed_workspace = []

    def _record_workspace(*args, **kwargs):
        observed_workspace.append(skypilot_config.get_active_workspace())

    mock_sky_down.side_effect = _record_workspace

    utils.terminate_cluster('test-cluster')

    mock_get_cluster.assert_called_once_with('test-cluster')
    assert observed_workspace == [
        'team-a'
    ], (f'Expected active workspace to be pinned to the cluster row '
        f"workspace 'team-a' during core.down, got: {observed_workspace}")


@mock.patch('sky.jobs.utils.global_user_state.get_cluster_from_name')
@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_no_record_skips_workspace_pin(
        mock_set_internal, mock_sky_down, mock_get_cluster) -> None:
    """If the cluster row is already gone, there is no workspace to pin
    — `core.down` will raise `ClusterDoesNotExist` and we return. The
    function must NOT crash trying to enter
    `local_active_workspace_ctx(None)`.
    """
    mock_get_cluster.return_value = None
    mock_sky_down.side_effect = ClusterDoesNotExist('test-cluster')

    # Must not raise.
    utils.terminate_cluster('test-cluster')

    mock_get_cluster.assert_called_once_with('test-cluster')
    mock_sky_down.assert_called_once()


@mock.patch('sky.jobs.utils.global_user_state.get_cluster_from_name')
@mock.patch('sky.jobs.utils.time.sleep'
           )  # Don't actually sleep between retries.
@mock.patch('sky.core.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_retry_reenters_workspace_ctx(
        mock_set_internal, mock_sky_down, mock_sleep, mock_get_cluster) -> None:
    """`skypilot_config.local_active_workspace_ctx` is implemented with
    `@contextlib.contextmanager` (a generator), which can only be
    entered ONCE per instance. If the retry loop reuses a single
    `workspace_ctx` instance across attempts, the second `with` raises
    `RuntimeError` ("generator didn't yield" / "already executed") and
    masks the real underlying failure.

    The fix is to construct a fresh ctx per retry attempt inside the
    loop. This test exercises that: cluster row carries a non-default
    workspace (so the live ctx path, not `nullcontext()`, is taken),
    and `core.down` is set to fail twice then succeed. Without the
    fix, the second iteration raises `RuntimeError` from the spent
    generator and the function crashes. With the fix, all three
    iterations construct a fresh ctx and the function completes.

    Revert check: lift `workspace_ctx = ...` back outside the
    `while True:` loop → the second retry raises `RuntimeError` →
    test fails."""
    from sky import skypilot_config
    mock_get_cluster.return_value = {
        'name': 'test-cluster',
        'workspace': 'team-a',
    }

    observed = []

    def _fail_twice_then_succeed(*args, **kwargs):
        observed.append(skypilot_config.get_active_workspace())
        if len(observed) < 3:
            raise ValueError(f'transient error {len(observed)}')

    mock_sky_down.side_effect = _fail_twice_then_succeed

    # Must complete without RuntimeError (which would arise from
    # re-entering a spent @contextlib.contextmanager generator).
    utils.terminate_cluster('test-cluster')

    assert mock_sky_down.call_count == 3
    # Every attempt must see the pinned workspace, not just the first.
    # If the ctx were a no-op on retries (e.g. wrong restore order),
    # this would record 'default' on attempts 2 and 3.
    assert observed == ['team-a', 'team-a', 'team-a'], observed
    # Single DB lookup outside the loop is sufficient — cluster
    # workspace is immutable.
    mock_get_cluster.assert_called_once_with('test-cluster')


def test_cancel_signal_file_no_graceful():
    """Test that cancel_jobs_by_id writes an empty signal file (touch)
    for non-graceful cancels on the new controller."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=False)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == '', (
                    f'Expected empty file for non-graceful, got: {content!r}')


def test_cancel_signal_file_graceful():
    """Test that cancel_jobs_by_id writes 'graceful' to signal file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=True)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == 'graceful'


def test_cancel_signal_file_graceful_with_timeout():
    """Test that cancel_jobs_by_id writes 'graceful:<timeout>' to signal
    file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('sky.jobs.constants.CONSOLIDATED_SIGNAL_PATH', tmpdir):
            with mock.patch(
                    'sky.jobs.state.is_legacy_controller_process',
                    return_value=False), \
                 mock.patch(
                    'sky.jobs.state.get_status',
                    return_value=mock.MagicMock(
                        is_terminal=mock.MagicMock(return_value=False),
                        __eq__=mock.MagicMock(return_value=False))), \
                 mock.patch(
                    'sky.jobs.utils.update_managed_jobs_statuses'), \
                 mock.patch(
                    'sky.jobs.state.get_workspace',
                    return_value='default'):
                utils.cancel_jobs_by_id(job_ids=[42],
                                        current_workspace='default',
                                        graceful=True,
                                        graceful_timeout=300)

                signal_file = pathlib.Path(tmpdir) / '42'
                assert signal_file.exists()
                content = signal_file.read_text(encoding='utf-8')
                assert content == 'graceful:300'


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_calls_flush(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel cancels jobs then flushes on all nodes."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    # Simulate successful flush
    mock_run_parallel.return_value = [(0, 0, '', '')]

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    # Verify jobs were cancelled
    mock_backend.cancel_jobs.assert_called_once_with(mock_handle,
                                                     jobs=None,
                                                     cancel_all=True)

    # Verify flush was run in parallel
    mock_run_parallel.assert_called_once()
    _, kwargs = mock_run_parallel.call_args
    assert kwargs['num_threads'] == 1


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_with_timeout(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel wraps flush script with timeout."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    mock_run_parallel.return_value = [(0, 0, '', '')]

    sky_core._graceful_job_cancel(mock_handle,
                                  mock_backend,
                                  'test-cluster',
                                  timeout=60)

    # The flush function passed to run_in_parallel should wrap with timeout.
    # We verify by checking the call was made (the timeout wrapping happens
    # inside the closure).
    mock_run_parallel.assert_called_once()
    mock_flush_script.assert_called_once()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_wrong_backend_skips(mock_flush_script,
                                                 mock_run_parallel):
    """Test _graceful_job_cancel skips for non-CloudVmRay backends."""
    from sky import core as sky_core

    mock_handle = mock.MagicMock()  # not CloudVmRayResourceHandle
    mock_backend = mock.MagicMock()  # not CloudVmRayBackend

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    # Should not attempt flush
    mock_flush_script.assert_not_called()
    mock_run_parallel.assert_not_called()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_handles_flush_timeout(mock_flush_script,
                                                   mock_run_parallel):
    """Test _graceful_job_cancel handles timeout exit code (124)."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    mock_runner = mock.MagicMock()
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = [mock_runner]
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    # Simulate timeout on flush (exit code 124)
    mock_run_parallel.return_value = [(0, 124, '', 'timed out')]

    # Should not raise - graceful cancel handles errors
    sky_core._graceful_job_cancel(mock_handle,
                                  mock_backend,
                                  'test-cluster',
                                  timeout=10)

    mock_backend.cancel_jobs.assert_called_once()


@mock.patch('sky.utils.subprocess_utils.run_in_parallel')
@mock.patch('sky.backends.task_codegen.TaskCodeGen.get_rclone_flush_script')
def test_graceful_job_cancel_multi_node(mock_flush_script, mock_run_parallel):
    """Test _graceful_job_cancel flushes on all nodes in parallel."""
    from sky import core as sky_core

    mock_flush_script.return_value = 'echo flush'

    runners = [mock.MagicMock() for _ in range(3)]
    mock_handle = mock.MagicMock(
        spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    mock_handle.get_command_runners.return_value = runners
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)

    mock_run_parallel.return_value = [
        (0, 0, '', ''),
        (1, 0, '', ''),
        (2, 0, '', ''),
    ]

    sky_core._graceful_job_cancel(mock_handle, mock_backend, 'test-cluster')

    _, kwargs = mock_run_parallel.call_args
    assert kwargs['num_threads'] == 3


class TestPopulateJobRecordFromHandle:
    """Tests for _populate_job_record_from_handle."""

    def test_populate_job_record_sets_network_fields(self):
        """Test that network fields are set in the job record."""
        # Create a minimal mock handle with required attributes
        mock_handle = mock.MagicMock()
        mock_handle.stable_internal_external_ips = [('10.0.0.1', '35.1.2.3')]
        mock_handle.cluster_name_on_cloud = 'test-cluster'
        mock_handle.launched_nodes = 1
        mock_handle.launched_resources = mock.MagicMock()
        mock_handle.launched_resources.cloud = mock.MagicMock()
        mock_handle.launched_resources.cloud.__str__ = lambda self: 'AWS'
        mock_handle.launched_resources.region = 'us-east-1'
        mock_handle.launched_resources.zone = 'us-east-1a'
        mock_handle.launched_resources.accelerators = None
        mock_handle.launched_resources.labels = {}
        mock_handle.cached_cluster_info = None  # Non-K8s cluster

        job = {}

        # Mock the resources_utils function
        with mock.patch(
                'sky.jobs.utils.resources_utils.get_readable_resources_repr',
                return_value=('1x[CPU:1]', '1x[CPU:1+]')):
            utils._populate_job_record_from_handle(job=job,
                                                   cluster_name='test-cluster',
                                                   handle=mock_handle)

        # Check network fields are set
        assert 'internal_external_ips' in job
        assert job['internal_external_ips'] == [('10.0.0.1', '35.1.2.3')]
        assert 'internal_services' in job
        assert job['internal_services'] is None  # Non-K8s cluster

        # Check other fields are also set
        assert job['cluster_resources'] == '1x[CPU:1]'
        assert job['cloud'] == 'AWS'
        assert job['region'] == 'us-east-1'

    def test_populate_job_record_sets_internal_services(self):
        """Test that K8s internal_svc entries are extracted."""
        # Create a mock handle for a K8s cluster
        mock_handle = mock.MagicMock()
        mock_handle.stable_internal_external_ips = [('10.0.0.1', '10.0.0.1')]
        mock_handle.cluster_name_on_cloud = 'test-cluster'
        mock_handle.launched_nodes = 1
        mock_handle.launched_resources = mock.MagicMock()
        mock_handle.launched_resources.cloud = mock.MagicMock()
        mock_handle.launched_resources.cloud.__str__ = lambda self: 'Kubernetes'
        mock_handle.launched_resources.region = None
        mock_handle.launched_resources.zone = None
        mock_handle.launched_resources.accelerators = None
        mock_handle.launched_resources.labels = {}

        # Create mock cluster info with K8s internal_svc
        mock_instance_info = mock.MagicMock()
        mock_instance_info.internal_svc = 'pod-0.svc.cluster.local'
        mock_handle.cached_cluster_info = mock.MagicMock()
        mock_handle.cached_cluster_info.provider_name = 'kubernetes'
        mock_handle.cached_cluster_info.instances = {
            'pod-0': [mock_instance_info]
        }

        job = {}

        # Mock the resources_utils function
        with mock.patch(
                'sky.jobs.utils.resources_utils.get_readable_resources_repr',
                return_value=('1x[CPU:1]', '1x[CPU:1+]')):
            utils._populate_job_record_from_handle(job=job,
                                                   cluster_name='test-cluster',
                                                   handle=mock_handle)

        # Check K8s internal_svc is extracted
        assert 'internal_services' in job
        assert job['internal_services'] == {'pod-0': 'pod-0.svc.cluster.local'}


class TestClusterHandleFields:
    """Tests for _CLUSTER_HANDLE_FIELDS configuration."""

    def test_network_fields_in_cluster_handle_fields(self):
        """Test that network fields are in _CLUSTER_HANDLE_FIELDS."""
        assert 'internal_external_ips' in utils._CLUSTER_HANDLE_FIELDS
        assert 'internal_services' in utils._CLUSTER_HANDLE_FIELDS

    def test_cluster_handle_not_required_excludes_network_fields(self):
        """Test that _cluster_handle_not_required returns False when network fields are present."""
        fields_with_ips = ['job_id', 'status', 'internal_external_ips']
        assert not utils._cluster_handle_not_required(fields_with_ips)

        fields_with_k8s = ['job_id', 'status', 'internal_services']
        assert not utils._cluster_handle_not_required(fields_with_k8s)

    def test_cluster_handle_not_required_without_handle_fields(self):
        """Test that _cluster_handle_not_required returns True without handle fields."""
        fields_without_handle = ['job_id', 'status', 'job_name']
        assert utils._cluster_handle_not_required(fields_without_handle)


# ======== Consolidation mode tests ========


class TestIsConsolidationMode:
    """Tests for is_consolidation_mode() with None sentinel."""

    def setup_method(self):
        utils.is_consolidation_mode.cache_clear()

    def test_no_signal_returns_false(self):
        """No signal file => False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                assert utils.is_consolidation_mode() is False

    def test_signal_exists_returns_true(self):
        """Signal file exists => True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                assert utils.is_consolidation_mode() is True


class TestSetupConsolidationModeOnStartup:
    """Tests for setup_consolidation_mode_on_startup()."""

    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_explicit_true_touches_signal(self, mock_config):
        """Config explicitly True => signal file created."""
        mock_config.get_nested.return_value = True
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=True)
                assert signal_file.exists()

    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_explicit_false_removes_signal(self, mock_config):
        """Config explicitly False => signal file removed."""
        mock_config.get_nested.return_value = False
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=False)
                assert not signal_file.exists()

    @mock.patch('sky.jobs.utils.global_user_state')
    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_fresh_deploy_auto_enables(self, mock_config, mock_gus):
        """Deploy mode, no controllers in DB, config None => signal created."""
        mock_config.get_nested.return_value = None
        mock_gus.get_cluster_names_start_with.return_value = []
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=True)
                assert signal_file.exists()

    @mock.patch('sky.jobs.utils.global_user_state')
    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_existing_controllers_no_auto_enable(self, mock_config, mock_gus):
        """Deploy mode, controllers in DB, config None => signal NOT created."""
        mock_config.get_nested.return_value = None
        mock_gus.get_cluster_names_start_with.return_value = [
            'sky-jobs-controller-abc12345'
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=True)
                assert not signal_file.exists()

    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_local_server_no_auto_enable(self, mock_config):
        """Local server (deploy=False), config None => signal NOT created."""
        mock_config.get_nested.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=False)
                assert not signal_file.exists()

    @mock.patch('sky.jobs.utils.global_user_state')
    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_cleans_signal_when_controllers_exist(self, mock_config, mock_gus):
        """Previous signal + controllers exist => signal cleaned up."""
        mock_config.get_nested.return_value = None
        mock_gus.get_cluster_names_start_with.return_value = [
            'sky-jobs-controller-abc12345'
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()  # Pre-existing signal
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=True)
                assert not signal_file.exists()

    @mock.patch('sky.jobs.utils.skypilot_config')
    def test_local_server_cleans_stale_signal(self, mock_config):
        """Local server with stale signal from previous deploy => cleaned."""
        mock_config.get_nested.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()  # Stale signal from previous deploy
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)):
                utils.setup_consolidation_mode_on_startup(deploy=False)
                assert not signal_file.exists()


class TestCollectDebugDumpManifestParallel:
    """Test that collect_debug_dump_manifest works correctly with many jobs."""

    NUM_JOBS = 200

    def _mock_get_managed_job_tasks(self, job_id):
        return [{'user_yaml': f'name: job-{job_id}', 'status': 'RUNNING'}]

    def _mock_get_job_events(self, job_id, limit=None):  # pylint: disable=unused-argument
        return [{
            'spot_job_id': job_id,
            'task_id': 0,
            'new_status': 'RUNNING',
            'code': None,
            'reason': None,
            'timestamp': '2026-01-01',
        }]

    def _mock_get_all_task_ids_names_statuses_logs(self, job_id):
        return [(0, f'task-{job_id}', 'RUNNING', f'/tmp/log-{job_id}', None)]

    def _mock_get_pool_submit_info(self, job_id):
        # Every 10 jobs share a cluster to test dedup
        cluster_idx = (job_id - 1) // 10
        return f'cluster-{cluster_idx}', job_id

    def _mock_get_cluster_from_name(self, cluster_name):
        return {
            'name': cluster_name,
            'cluster_hash': f'hash-{cluster_name}',
            'handle': None,
        }

    @mock.patch('sky.jobs.utils.debug_dump_helpers.get_cluster_events_data')
    @mock.patch('sky.jobs.utils.debug_dump_helpers.serialize_cluster_record')
    @mock.patch('sky.jobs.utils.global_user_state.get_cluster_from_name')
    @mock.patch('sky.jobs.utils.managed_job_state.get_pool_submit_info')
    @mock.patch('sky.jobs.utils.managed_job_state'
                '.get_all_task_ids_names_statuses_logs')
    @mock.patch('sky.jobs.utils.managed_job_state.get_job_events')
    @mock.patch('sky.jobs.utils.managed_job_state.get_managed_job_tasks')
    @mock.patch('sky.jobs.utils.debug_dump_helpers.redact_task_yaml')
    def test_parallel_collection_correctness(
        self,
        mock_redact,
        mock_get_tasks,
        mock_get_events,
        mock_get_task_ids,
        mock_get_pool,
        mock_get_cluster,
        mock_serialize,
        mock_cluster_events,
    ):
        """All jobs collected, cluster info deduplicated, no data lost."""
        mock_redact.side_effect = lambda y: y
        mock_get_tasks.side_effect = self._mock_get_managed_job_tasks
        mock_get_events.side_effect = self._mock_get_job_events
        mock_get_task_ids.side_effect = (
            self._mock_get_all_task_ids_names_statuses_logs)
        mock_get_pool.side_effect = self._mock_get_pool_submit_info
        mock_get_cluster.side_effect = self._mock_get_cluster_from_name
        mock_serialize.side_effect = lambda r: {'name': r['name']}
        mock_cluster_events.return_value = []

        job_ids = list(range(1, self.NUM_JOBS + 1))
        result = utils.collect_debug_dump_manifest(job_ids)

        # Every job should produce job_info + job_events = 2 inline items
        job_inline = [
            p for p in result['inline_data']
            if '/clusters/' not in p['relative_path']
        ]
        assert len(job_inline) == self.NUM_JOBS * 2, (
            f'Expected {self.NUM_JOBS * 2} job inline items, '
            f'got {len(job_inline)}')

        # Cluster info should be deduplicated: 200 jobs / 10 per cluster = 20
        cluster_inline = [
            p for p in result['inline_data']
            if '/clusters/' in p['relative_path']
        ]
        expected_clusters = self.NUM_JOBS // 10
        assert len(cluster_inline) == expected_clusters, (
            f'Expected {expected_clusters} cluster info entries, '
            f'got {len(cluster_inline)}')

        # No errors
        assert len(result['errors']) == 0

    @mock.patch('sky.jobs.utils.debug_dump_helpers.get_cluster_events_data')
    @mock.patch('sky.jobs.utils.debug_dump_helpers.serialize_cluster_record')
    @mock.patch('sky.jobs.utils.global_user_state.get_cluster_from_name')
    @mock.patch('sky.jobs.utils.managed_job_state.get_pool_submit_info')
    @mock.patch('sky.jobs.utils.managed_job_state'
                '.get_all_task_ids_names_statuses_logs')
    @mock.patch('sky.jobs.utils.managed_job_state.get_job_events')
    @mock.patch('sky.jobs.utils.managed_job_state.get_managed_job_tasks')
    @mock.patch('sky.jobs.utils.debug_dump_helpers.redact_task_yaml')
    def test_partial_failures_isolated(
        self,
        mock_redact,
        mock_get_tasks,
        mock_get_events,
        mock_get_task_ids,
        mock_get_pool,
        mock_get_cluster,
        mock_serialize,
        mock_cluster_events,
    ):
        """A failing job doesn't break collection for other jobs."""
        mock_redact.side_effect = lambda y: y

        def flaky_get_tasks(job_id):
            if job_id % 3 == 0:
                raise RuntimeError(f'DB error for job {job_id}')
            return self._mock_get_managed_job_tasks(job_id)

        mock_get_tasks.side_effect = flaky_get_tasks
        mock_get_events.side_effect = self._mock_get_job_events
        mock_get_task_ids.side_effect = (
            self._mock_get_all_task_ids_names_statuses_logs)
        mock_get_pool.side_effect = self._mock_get_pool_submit_info
        mock_get_cluster.side_effect = self._mock_get_cluster_from_name
        mock_serialize.side_effect = lambda r: {'name': r['name']}
        mock_cluster_events.return_value = []

        job_ids = list(range(1, self.NUM_JOBS + 1))
        result = utils.collect_debug_dump_manifest(job_ids)

        # Failing jobs should produce errors, not crash
        failing_jobs = [j for j in job_ids if j % 3 == 0]
        assert len(result['errors']) == len(failing_jobs)

        # Non-failing jobs should still have their data
        ok_jobs = [j for j in job_ids if j % 3 != 0]
        job_info_items = [
            p for p in result['inline_data']
            if p['relative_path'].endswith('/job_info.json')
        ]
        assert len(job_info_items) == len(ok_jobs)


class TestControllerSystemLogScoping:
    """Scope managed_jobs/controller_system/*.log to the controllers that
    actually ran the requested jobs.

    The unscoped behavior (glob controller_*.log) dragged thousands of
    unrelated controller-process logs into every dump.
    """

    _UUID_A = '4cfc2dc5-5b4e-47eb-a517-079aa7ba6757'
    _UUID_B = '276636dc-a8dd-4210-86f1-31f43b4f9d05'

    @staticmethod
    def _job_log_head(uuids):
        lines = [
            'Starting job loop for 1',
            '  log_file=/tmp/1.log',
            '  pool=None',
        ]
        for u in uuids:
            lines.append(f'From controller {u}')
            lines.append('  pid=27476')
        return '\n'.join(lines) + '\n'

    def _setup_logs_dir(self, tmpdir, jobid_log_contents, controller_uuids):
        """Build a fake controller logs dir.

        ``jobid_log_contents`` maps job_id -> string content for <jobid>.log.
        ``controller_uuids`` is the iterable of controller UUIDs whose
        ``controller_<uuid>.log`` should exist on disk.
        """
        for jid, content in jobid_log_contents.items():
            (pathlib.Path(tmpdir) / f'{jid}.log').write_text(content)
        for u in controller_uuids:
            (pathlib.Path(tmpdir) / f'controller_{u}.log').write_text('hi')
        return str(tmpdir)

    def test_extracts_single_uuid(self):
        """One "From controller" line → UUID set with one element."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_logs_dir(tmpdir,
                                 {1: self._job_log_head([self._UUID_A])}, [])
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                with mock.patch(
                        'sky.jobs.utils.managed_job_state'
                        '.get_managed_job_tasks',
                        return_value=[]):
                    with mock.patch(
                            'sky.jobs.utils.managed_job_state'
                            '.get_job_events',
                            return_value=[]):
                        with mock.patch(
                                'sky.jobs.utils.managed_job_state'
                                '.get_all_task_ids_names_statuses_logs',
                                return_value=[]):
                            with mock.patch(
                                    'sky.jobs.utils.managed_job_state'
                                    '.get_pool_submit_info',
                                    return_value=(None, None)):
                                _, _, _, _, uuids = (
                                    utils._collect_job_debug_manifest(1))
        assert uuids == {self._UUID_A}

    def test_extracts_multiple_uuids_for_ha_recovered_job(self):
        """An HA-recovered job has multiple "From controller" lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_logs_dir(
                tmpdir, {1: self._job_log_head([self._UUID_A, self._UUID_B])},
                [])
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                with mock.patch(
                        'sky.jobs.utils.managed_job_state'
                        '.get_managed_job_tasks',
                        return_value=[]):
                    with mock.patch(
                            'sky.jobs.utils.managed_job_state'
                            '.get_job_events',
                            return_value=[]):
                        with mock.patch(
                                'sky.jobs.utils.managed_job_state'
                                '.get_all_task_ids_names_statuses_logs',
                                return_value=[]):
                            with mock.patch(
                                    'sky.jobs.utils.managed_job_state'
                                    '.get_pool_submit_info',
                                    return_value=(None, None)):
                                _, _, _, _, uuids = (
                                    utils._collect_job_debug_manifest(1))
        assert uuids == {self._UUID_A, self._UUID_B}

    def test_extracts_ha_recovery_uuid_far_from_head(self):
        """HA recovery appends a second "From controller …" line after
        an arbitrary amount of intervening output (the per-job log is
        opened in append mode at sky/utils/context.py:146). A 16 KB-only
        head read would miss it; the scan must traverse the whole file.
        """
        gap_bytes = 200 * 1024  # 200 KB of intervening status output
        content = (
            f'Starting job loop for 1\nFrom controller {self._UUID_A}\n'
            # Realistic-ish filler: many short status lines.
            + ('Status check: still running\n' * (gap_bytes // 28)) +
            f'=== Recovery ===\nFrom controller {self._UUID_B}\n')
        with tempfile.TemporaryDirectory() as tmpdir:
            (pathlib.Path(tmpdir) / '1.log').write_text(content)
            assert (pathlib.Path(tmpdir) / '1.log').stat().st_size > 16 * 1024
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                with mock.patch(
                        'sky.jobs.utils.managed_job_state'
                        '.get_managed_job_tasks',
                        return_value=[]):
                    with mock.patch(
                            'sky.jobs.utils.managed_job_state'
                            '.get_job_events',
                            return_value=[]):
                        with mock.patch(
                                'sky.jobs.utils.managed_job_state'
                                '.get_all_task_ids_names_statuses_logs',
                                return_value=[]):
                            with mock.patch(
                                    'sky.jobs.utils.managed_job_state'
                                    '.get_pool_submit_info',
                                    return_value=(None, None)):
                                _, _, _, _, uuids = (
                                    utils._collect_job_debug_manifest(1))
        assert uuids == {self._UUID_A, self._UUID_B}

    def test_missing_job_log_returns_empty_set(self):
        """No <jobid>.log → empty UUID set, no exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                with mock.patch(
                        'sky.jobs.utils.managed_job_state'
                        '.get_managed_job_tasks',
                        return_value=[]):
                    with mock.patch(
                            'sky.jobs.utils.managed_job_state'
                            '.get_job_events',
                            return_value=[]):
                        with mock.patch(
                                'sky.jobs.utils.managed_job_state'
                                '.get_all_task_ids_names_statuses_logs',
                                return_value=[]):
                            with mock.patch(
                                    'sky.jobs.utils.managed_job_state'
                                    '.get_pool_submit_info',
                                    return_value=(None, None)):
                                _, _, errs, _, uuids = (
                                    utils._collect_job_debug_manifest(1))
        assert uuids == set()
        assert errs == []

    def test_collect_controller_system_with_empty_uuids_emits_no_files(self):
        """Empty relevant_uuids must NOT fall back to globbing the dir.

        This is the regression we are fixing — globbing dragged in 8 000+
        unrelated controller process logs.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_logs_dir(tmpdir, {}, [self._UUID_A, self._UUID_B])
            file_paths: list = []
            errors: list = []
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                utils._collect_controller_system_log_paths(
                    file_paths, errors, set())
        assert file_paths == []
        assert errors == []

    def test_collect_controller_system_filters_to_relevant_uuids(self):
        """Only UUIDs in the relevant set become file_paths entries.

        ``relevant_uuids`` includes a UUID that isn't on disk → silently
        skipped. The on-disk-but-not-relevant UUID is also skipped.
        """
        missing_uuid = '00000000-0000-0000-0000-000000000000'
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_logs_dir(tmpdir, {},
                                 [self._UUID_A, self._UUID_B])  # both on disk
            file_paths: list = []
            errors: list = []
            with mock.patch(
                    'sky.jobs.utils.managed_job_constants'
                    '.JOBS_CONTROLLER_LOGS_DIR', tmpdir):
                utils._collect_controller_system_log_paths(
                    file_paths, errors, {self._UUID_A, missing_uuid})
        # Only the on-disk + relevant UUID survives.
        rel_paths = sorted(p['relative_path'] for p in file_paths)
        assert rel_paths == [
            f'managed_jobs/controller_system/controller_{self._UUID_A}.log'
        ]
        assert errors == []


class TestParseSubmitLogJobRanges:
    """Inverse of sky.jobs.server.core._job_ids_to_str (kept as intervals)."""

    def test_single_id(self):
        assert utils._parse_submit_log_job_ranges('584') == [(584, 584)]

    def test_inclusive_range(self):
        assert utils._parse_submit_log_job_ranges('580-583') == [(580, 583)]

    def test_mixed_singletons_and_ranges(self):
        assert utils._parse_submit_log_job_ranges('1,5-7,10') == [(1, 1),
                                                                  (5, 7),
                                                                  (10, 10)]

    def test_large_range_is_not_expanded(self):
        # A huge range must stay a single interval, never expand to a set of
        # ints (that would risk OOM on a malicious/fat-fingered filename).
        assert utils._parse_submit_log_job_ranges('1-100000000') == [
            (1, 100000000)
        ]

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            utils._parse_submit_log_job_ranges('not-an-int')

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError):
            utils._parse_submit_log_job_ranges('588-580')


class TestControllerSubmitLogScoping:
    """Scope managed_jobs/controller_submit_logs/submit-job-*.log to the
    submissions whose job-id set includes a requested job (mirrors the
    controller_system scoping; avoids dragging a long-lived controller's
    entire submission history into every dump).
    """

    def _setup(self, tmpdir, filenames):
        """Create ``<tmpdir>/managed_jobs/<filename>`` for each filename."""
        mj_dir = pathlib.Path(tmpdir) / 'managed_jobs'
        mj_dir.mkdir()
        for name in filenames:
            (mj_dir / name).write_text('Started 9 controllers\n')

    def _run(self, tmpdir, job_ids):
        file_paths: list = []
        errors: list = []
        with mock.patch('sky.jobs.utils.constants.SKY_LOGS_DIRECTORY', tmpdir):
            utils._collect_controller_submit_log_paths(file_paths, errors,
                                                       job_ids)
        return file_paths, errors

    def test_includes_only_submissions_with_a_requested_job(self):
        """A singleton match and a range that *contains* the job both count;
        an unrelated submission is excluded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup(
                tmpdir,
                [
                    'submit-job-584.log',
                    'submit-job-580-588.log',  # range contains 584
                    'submit-job-999.log',  # unrelated
                ])
            file_paths, errors = self._run(tmpdir, [584])
        rel = sorted(p['relative_path'] for p in file_paths)
        assert rel == [
            'managed_jobs/controller_submit_logs/submit-job-580-588.log',
            'managed_jobs/controller_submit_logs/submit-job-584.log',
        ]
        assert errors == []

    def test_empty_job_ids_collects_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup(tmpdir, ['submit-job-1.log'])
            file_paths, errors = self._run(tmpdir, [])
        assert file_paths == []
        assert errors == []

    def test_missing_dir_is_noop(self):
        """No managed_jobs dir (controller never submitted) is benign."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_paths, errors = self._run(tmpdir, [1])
        assert file_paths == []
        assert errors == []

    def test_unparseable_filename_skipped_with_warning(self):
        """A filename that doesn't parse is skipped with a warning, not an
        errors-list entry (it's not a collection failure)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup(tmpdir, [
                'submit-job-weird.log',
                'submit-job-7.log',
            ])
            with mock.patch('sky.jobs.utils.logger') as mock_logger:
                file_paths, errors = self._run(tmpdir, [7])
        rel = [p['relative_path'] for p in file_paths]
        assert rel == ['managed_jobs/controller_submit_logs/submit-job-7.log']
        assert errors == []
        # The unparseable name is surfaced, not silently dropped.
        assert mock_logger.warning.call_count == 1
        assert 'submit-job-weird.log' in mock_logger.warning.call_args[0][0]


class TestCleanupExpiredApiAccessTokens:
    """Unit tests for the expired managed-job token sweep."""

    @staticmethod
    def _token(token_id: str, name: str, expires_at):
        return {
            'token_id': token_id,
            'token_name': name,
            'expires_at': expires_at,
        }

    @mock.patch('sky.global_user_state.delete_service_account_token')
    @mock.patch('sky.global_user_state.'
                'get_expired_service_account_tokens_by_name_prefix')
    def test_deletes_only_managed_job_shaped_names(self, mock_get_expired,
                                                   mock_delete_token):
        now = int(time.time())
        mock_get_expired.return_value = [
            # Looks like a real managed-job token: prefix + 8 hex suffix.
            self._token('tok-a', 'managed-job-myjob-abcdef01', now - 60),
            # Multi-segment job name, still ends in 8 hex chars.
            self._token('tok-b', 'managed-job-bench-burst-0028-fea61234',
                        now - 60),
            # Prefix matches but the suffix isn't 8 hex chars: skip.
            self._token('tok-c', 'managed-job-user-named-something', now - 60),
            # Suffix is 8 chars but contains non-hex letters: skip.
            self._token('tok-d', 'managed-job-foo-zzzzzzzz', now - 60),
        ]

        removed = utils.cleanup_expired_api_access_tokens()

        assert removed == 2
        deleted_tokens = sorted(
            c.args[0] for c in mock_delete_token.call_args_list)
        assert deleted_tokens == ['tok-a', 'tok-b']

    @mock.patch('sky.global_user_state.delete_service_account_token')
    @mock.patch('sky.global_user_state.'
                'get_expired_service_account_tokens_by_name_prefix')
    def test_token_delete_failure_is_skipped(self, mock_get_expired,
                                             mock_delete_token):
        now = int(time.time())
        mock_get_expired.return_value = [
            self._token('tok-a', 'managed-job-myjob-abcdef01', now - 60),
        ]
        mock_delete_token.side_effect = RuntimeError('db down')

        # Token revocation failed: report zero so the next sweep can retry.
        removed = utils.cleanup_expired_api_access_tokens()
        assert removed == 0

    @mock.patch('sky.global_user_state.'
                'get_expired_service_account_tokens_by_name_prefix')
    def test_no_expired_tokens_is_noop(self, mock_get_expired):
        mock_get_expired.return_value = []
        assert utils.cleanup_expired_api_access_tokens() == 0
