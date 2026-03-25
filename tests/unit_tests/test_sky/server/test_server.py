"""Unit tests for the SkyPilot API server."""

import argparse
import asyncio
import json
import os
import pathlib
import threading
import time
from unittest import mock

import fastapi
import pytest
import uvicorn

from sky import models
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import server
from sky.server.requests import executor
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils


@mock.patch('uvicorn.run')
@mock.patch('sky.server.requests.executor.start')
@mock.patch('sky.utils.common_utils.get_cpu_count')
def test_deploy_flag_sets_workers_to_cpu_count(mock_get_cpu_count,
                                               mock_executor_start,
                                               mock_uvicorn_run):
    """Test that --deploy flag sets workers to CPU count."""
    # Setup
    mock_get_cpu_count.return_value = 8
    mock_executor_start.return_value = []

    # Create mock args with deploy=True
    test_args = argparse.Namespace(host='127.0.0.1', port=46580, deploy=True)

    # Call the main block with mocked args
    with mock.patch('argparse.ArgumentParser.parse_args',
                    return_value=test_args):
        with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
            with mock.patch('sky.usage.usage_lib.maybe_show_privacy_policy'):
                # Execute the main block code directly
                num_workers = None
                if test_args.deploy:
                    num_workers = mock_get_cpu_count()

                workers = []
                try:
                    workers = mock_executor_start(test_args.deploy)
                    uvicorn.run('sky.server.server:app',
                                host=test_args.host,
                                port=test_args.port,
                                workers=num_workers)
                except Exception:
                    pass
                finally:
                    for worker in workers:
                        worker.terminate()

    # Verify that uvicorn.run was called with the correct number of workers
    mock_uvicorn_run.assert_called_once()
    call_args = mock_uvicorn_run.call_args[1]
    assert call_args['workers'] == 8
    assert call_args['host'] == '127.0.0.1'
    assert call_args['port'] == 46580


@mock.patch('uvicorn.run')
@mock.patch('sky.server.requests.executor.start')
def test_no_deploy_flag_uses_default_workers(mock_executor_start,
                                             mock_uvicorn_run):
    """Test that without --deploy flag, workers is None (default)."""
    # Setup
    mock_executor_start.return_value = []

    # Create mock args with deploy=False
    test_args = argparse.Namespace(host='127.0.0.1', port=46580, deploy=False)

    # Call the main block with mocked args
    with mock.patch('argparse.ArgumentParser.parse_args',
                    return_value=test_args):
        with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
            with mock.patch('sky.usage.usage_lib.maybe_show_privacy_policy'):
                # Execute the main block code directly
                num_workers = None
                if test_args.deploy:
                    num_workers = common_utils.get_cpu_count()

                workers = []
                try:
                    workers = mock_executor_start(test_args.deploy)
                    uvicorn.run('sky.server.server:app',
                                host=test_args.host,
                                port=test_args.port,
                                workers=num_workers)
                except Exception:
                    pass
                finally:
                    for worker in workers:
                        worker.terminate()

    # Verify that uvicorn.run was called with workers=None
    mock_uvicorn_run.assert_called_once()
    call_args = mock_uvicorn_run.call_args[1]
    assert call_args['workers'] is None
    assert call_args['host'] == '127.0.0.1'
    assert call_args['port'] == 46580


@pytest.mark.asyncio
async def test_validate():
    """Test the validate endpoint."""
    mock_dag = mock.MagicMock()
    mock_validate_body = mock.MagicMock()
    mock_validate_body.dag = 'test_dag_yaml'
    mock_validate_body.request_options = {}

    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate') as mock_validate:
        # Call validate endpoint
        await server.validate(mock_validate_body)
        # Verify validate was called with correct args
        mock_validate.assert_called_once_with(skip_file_mounts=True,
                                              skip_workdir=True)

    error_msg = 'Invalid DAG'
    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate',
                          side_effect=ValueError(error_msg)):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.validate(mock_validate_body)
        assert exc_info.value.status_code == 400
        assert error_msg in str(exc_info.value.detail)

    # Create an event to track when validation completes
    validation_complete = asyncio.Event()

    def slow_validate(*args, **kwargs):
        # Simulate slow validation
        time.sleep(0.1)
        validation_complete.set()

    with mock.patch('sky.server.server.dag_utils.load_chain_dag_from_yaml_str',
                   return_value=mock_dag), \
         mock.patch('sky.server.server.admin_policy_utils.apply',
                   return_value=(mock_dag, config_utils.Config())), \
         mock.patch.object(mock_dag, 'validate',
                          side_effect=slow_validate):
        # Start validation in background
        validation_task = asyncio.create_task(
            server.validate(mock_validate_body))

        # Check that validation hasn't completed immediately
        assert not validation_complete.is_set()

        # Wait for validation to complete
        await validation_task
        assert validation_complete.is_set()


@pytest.mark.asyncio
async def test_logs():
    """Test the logs endpoint."""
    mock_cluster_job_body = mock.MagicMock()
    mock_cluster_job_body.cluster_name = 'test-cluster'
    background_tasks = fastapi.BackgroundTasks()

    # Create an event to track when logs streaming starts
    streaming_started = threading.Event()

    # Mock the stream_response function
    def mock_stream_response(*args, **kwargs):
        streaming_started.set()
        return fastapi.responses.StreamingResponse(
            content=iter([]),  # Empty iterator for testing
            media_type='text/plain')

    def slow_execute(*args, **kwargs):
        # Simulate slow execution
        task = asyncio.create_task(asyncio.sleep(0.1))
        return executor.CoroutineTask(task)

    with mock.patch('sky.server.requests.executor.prepare_request_async') as mock_prepare_async, \
         mock.patch('sky.server.requests.executor.execute_request_in_coroutine',
                   side_effect=slow_execute) as mock_execute, \
         mock.patch('sky.server.stream_utils.stream_response',
                   side_effect=mock_stream_response) as mock_stream:

        # Mock prepare_request to return a request task
        mock_request_task = mock.MagicMock()
        mock_request_task.log_path = '/tmp/test.log'
        mock_prepare_async.return_value = mock_request_task

        # Start logs endpoint in background
        logs_task = asyncio.create_task(
            server.logs(mock.MagicMock(), mock_cluster_job_body,
                        background_tasks))

        # Execute should be run in background and does not block streaming start
        streaming_started.wait(timeout=0.1)

        # Verify the response was created
        response = await logs_task
        assert isinstance(response, fastapi.responses.StreamingResponse)
        assert response.media_type == 'text/plain'
        await background_tasks()

        # Verify the executor calls
        mock_prepare_async.assert_called_once()
        mock_execute.assert_called_once_with(mock_request_task)
        mock_stream.assert_called_once_with(mock.ANY,
                                            mock_request_task.log_path,
                                            mock.ANY,
                                            polling_interval=1,
                                            kill_request_on_disconnect=False)


@mock.patch('sky.utils.context_utils.hijack_sys_attrs')
@mock.patch('asyncio.run')
def test_server_run_uses_uvloop(mock_asyncio_run, mock_hijack_sys_attrs):
    """Test that Server.run uses uvloop event loop policy."""
    from sky.server.uvicorn import Server

    threads_before = len(threading.enumerate())

    config = uvicorn.Config(app='sky.server.server:app',
                            host='127.0.0.1',
                            port=8000)
    server_instance = Server(config)
    original_setup = config.setup_event_loop

    uvloop_policy_set = False
    uvloop_available = True

    def setup_and_check():
        # Save previous event loop policy
        previous_policy = asyncio.get_event_loop_policy()
        try:
            # Call original setup to configure event loop
            original_setup()
            # Check if uvloop policy is now set
            nonlocal uvloop_policy_set, uvloop_available
            try:
                import uvloop
                policy = asyncio.get_event_loop_policy()
                uvloop_policy_set = isinstance(policy, uvloop.EventLoopPolicy)
            except ImportError:
                # uvloop not available
                uvloop_available = False
        finally:
            # Restore previous event loop policy
            # This is needed because other tests/fixtures running on the same
            # pytest worker may not work with the uvicorn event loop policy,
            # such as _seed_test_jobs in test_managed_jobs_service.py
            asyncio.set_event_loop_policy(previous_policy)

    with mock.patch.object(config,
                           'setup_event_loop',
                           side_effect=setup_and_check):
        # Call server.run
        server_instance.run()

    mock_asyncio_run.assert_called_once()
    threads_after = len(threading.enumerate())
    assert threads_after == threads_before

    # Check uvloop policy was set (if uvloop is available)
    if uvloop_available:
        assert uvloop_policy_set, (
            "Expected uvloop event loop policy to be set when uvloop "
            "is available")
    else:
        pytest.skip("uvloop not available, skipping uvloop policy check")


@pytest.mark.asyncio
async def test_enabled_clouds_respect_auth_user():
    """Test that enabled_clouds endpoint passes auth_user to schedule_request_async.

    After the refactor, auth_user is passed directly to the executor function
    which handles setting env_vars internally, rather than modifying env_vars
    before the call.
    """
    auth_user = models.User(id='auth-user-id', name='Auth User')
    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'request-id'
    request.state.auth_user = auth_user

    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.enabled_clouds(request, workspace='ws', expand=True)

    mock_schedule.assert_awaited_once()
    _, kwargs = mock_schedule.call_args
    # Verify auth_user is passed to schedule_request_async
    assert kwargs['auth_user'] == auth_user


@pytest.mark.asyncio
async def test_schedule_request_passes_auth_user():
    """Test that schedule_request_async receives auth_user from various endpoints."""
    from sky.server.requests import payloads

    auth_user = models.User(id='test-user-id', name='Test User')
    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'test-request-id'
    request.state.auth_user = auth_user

    # Test check endpoint
    check_body = payloads.CheckBody()
    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.check(request, check_body)
        mock_schedule.assert_awaited_once()
        _, kwargs = mock_schedule.call_args
        assert kwargs['auth_user'] == auth_user

    # Test status endpoint
    status_body = payloads.StatusBody()
    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.status(request, status_body)
        mock_schedule.assert_awaited_once()
        _, kwargs = mock_schedule.call_args
        assert kwargs['auth_user'] == auth_user

    # Test stop endpoint
    stop_body = payloads.StopOrDownBody(cluster_name='test-cluster')
    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.stop(request, stop_body)
        mock_schedule.assert_awaited_once()
        _, kwargs = mock_schedule.call_args
        assert kwargs['auth_user'] == auth_user


@pytest.mark.asyncio
async def test_schedule_request_with_none_auth_user():
    """Test that endpoints work correctly when auth_user is None."""
    from sky.server.requests import payloads

    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'test-request-id'
    request.state.auth_user = None

    check_body = payloads.CheckBody()
    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.check(request, check_body)
        mock_schedule.assert_awaited_once()
        _, kwargs = mock_schedule.call_args
        assert kwargs['auth_user'] is None


@pytest.mark.asyncio
async def test_prepare_request_passes_auth_user():
    """Test that prepare_request_async receives auth_user for streaming endpoints."""
    from sky.server.requests import payloads

    auth_user = models.User(id='test-user-id', name='Test User')
    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'test-request-id'
    request.state.auth_user = auth_user

    cluster_job_body = payloads.ClusterJobBody(cluster_name='test-cluster',
                                               job_id=1)
    background_tasks = fastapi.BackgroundTasks()

    mock_request_task = mock.MagicMock()
    mock_request_task.request_id = 'test-request-id'
    mock_request_task.log_path = '/tmp/test.log'

    with mock.patch('sky.server.requests.executor.prepare_request_async',
                    new_callable=mock.AsyncMock,
                    return_value=mock_request_task) as mock_prepare, \
         mock.patch('sky.server.requests.executor.execute_request_in_coroutine') as mock_execute, \
         mock.patch('sky.server.stream_utils.stream_response_for_long_request',
                    return_value=fastapi.responses.StreamingResponse(
                        content=iter([]),
                        media_type='text/plain')):
        mock_execute.return_value = executor.CoroutineTask(
            asyncio.create_task(asyncio.sleep(0)))

        await server.logs(request, cluster_job_body, background_tasks)

        mock_prepare.assert_awaited_once()
        _, kwargs = mock_prepare.call_args
        assert kwargs['auth_user'] == auth_user


@pytest.mark.asyncio
async def test_launch_endpoint_passes_auth_user():
    """Test that launch endpoint passes auth_user to schedule_request_async."""
    from sky.server.requests import payloads

    auth_user = models.User(id='launch-user-id', name='Launch User')
    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'launch-request-id'
    request.state.auth_user = auth_user

    launch_body = payloads.LaunchBody(
        task='test_task_yaml',
        cluster_name='test-cluster',
    )

    with mock.patch('sky.server.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await server.launch(launch_body, request)
        mock_schedule.assert_awaited_once()
        args, kwargs = mock_schedule.call_args
        assert kwargs['auth_user'] == auth_user
        # request_id is passed as first positional argument
        assert args[0] == 'launch-request-id'


# --- Tests for cleanup_unreferenced_file_mounts ---

# A deterministic 64-char hex string used as a blob ID in tests.
_BLOB_HEX = 'a' * 64


def _make_blobs_dir(tmp_path: pathlib.Path,
                    user: str = 'userA') -> pathlib.Path:
    """Create the blobs directory structure under *tmp_path* and return it."""
    blobs_dir = tmp_path / user / 'file_mounts' / 'blobs'
    blobs_dir.mkdir(parents=True)
    return blobs_dir


def _create_blob(blobs_dir: pathlib.Path, blob_id: str,
                 mtime: float) -> pathlib.Path:
    """Create a blob extraction directory with the given *mtime*."""
    extraction_dir = blobs_dir / blob_id
    extraction_dir.mkdir(parents=True, exist_ok=True)
    (extraction_dir / 'placeholder.txt').write_text('test')
    os.utime(extraction_dir, (mtime, mtime))
    return extraction_dir


def _mock_sleep_cancel():
    """Return a side_effect for asyncio.sleep that cancels after one call.

    The first call lets the ``while True`` body execute once, then raises
    ``asyncio.CancelledError`` to break out of the loop.
    """
    call_count = 0

    async def _side_effect(_delay):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: the sleep *before* the first cleanup iteration.
            # Return immediately so we proceed to _do_cleanup.
            return
        # Second call (next iteration) – break out.
        raise asyncio.CancelledError

    return _side_effect


@pytest.mark.asyncio
async def test_cleanup_blobs_no_blobs_dir(tmp_path):
    """clients_dir exists but has no blobs subdirectory – should be a no-op."""
    # Create a user directory without the blobs sub-tree.
    user_dir = tmp_path / 'userA'
    user_dir.mkdir()

    with mock.patch.object(server_common, 'API_SERVER_CLIENT_DIR',
                           pathlib.Path(tmp_path)), \
         mock.patch('sky.server.requests.requests'
                    '.get_active_file_mounts_blob_ids') as mock_get, \
         mock.patch('asyncio.sleep',
                    side_effect=_mock_sleep_cancel()):
        with pytest.raises(asyncio.CancelledError):
            await server.cleanup_unreferenced_file_mounts()

    # get_active_file_mounts_blob_ids is called unconditionally, but
    # no blobs are deleted because there is no blobs directory.
    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_blobs_active_blob_not_deleted(tmp_path):
    """A blob referenced by an active request must NOT be deleted,
    even if its mtime is older than the grace period."""
    blobs_dir = _make_blobs_dir(tmp_path, user='userA')
    old_mtime = time.time() - 7200  # 2 hours ago
    blob_path = _create_blob(blobs_dir, _BLOB_HEX, old_mtime)

    with mock.patch.object(server_common, 'API_SERVER_CLIENT_DIR',
                           pathlib.Path(tmp_path)), \
         mock.patch('sky.server.requests.requests'
                    '.get_active_file_mounts_blob_ids',
                    return_value={_BLOB_HEX}), \
         mock.patch('asyncio.sleep',
                    side_effect=_mock_sleep_cancel()):
        with pytest.raises(asyncio.CancelledError):
            await server.cleanup_unreferenced_file_mounts()

    assert blob_path.exists(), 'Active blob should not be deleted'


@pytest.mark.asyncio
async def test_cleanup_blobs_unreferenced_old_blob_deleted(tmp_path):
    """An unreferenced blob older than the 1-hour grace period IS deleted."""
    blobs_dir = _make_blobs_dir(tmp_path, user='userA')
    old_mtime = time.time() - 7200  # 2 hours ago
    extraction_dir = _create_blob(blobs_dir, _BLOB_HEX, old_mtime)

    with mock.patch.object(server_common, 'API_SERVER_CLIENT_DIR',
                           pathlib.Path(tmp_path)), \
         mock.patch('sky.server.requests.requests'
                    '.get_active_file_mounts_blob_ids',
                    return_value=set()), \
         mock.patch('asyncio.sleep',
                    side_effect=_mock_sleep_cancel()):
        with pytest.raises(asyncio.CancelledError):
            await server.cleanup_unreferenced_file_mounts()

    assert not extraction_dir.exists(
    ), 'Unreferenced old blob should be deleted'


@pytest.mark.asyncio
async def test_cleanup_blobs_unreferenced_recent_blob_not_deleted(tmp_path):
    """An unreferenced blob within the grace period (recent) is NOT deleted."""
    blobs_dir = _make_blobs_dir(tmp_path, user='userA')
    recent_mtime = time.time() - 60  # 1 minute ago (well within grace)
    blob_path = _create_blob(blobs_dir, _BLOB_HEX, recent_mtime)

    with mock.patch.object(server_common, 'API_SERVER_CLIENT_DIR',
                           pathlib.Path(tmp_path)), \
         mock.patch('sky.server.requests.requests'
                    '.get_active_file_mounts_blob_ids',
                    return_value=set()), \
         mock.patch('asyncio.sleep',
                    side_effect=_mock_sleep_cancel()):
        with pytest.raises(asyncio.CancelledError):
            await server.cleanup_unreferenced_file_mounts()

    assert blob_path.exists(), (
        'Unreferenced but recent blob should not be deleted')


# --- Tests for _resolve_dynamic_route ---

# A minimal routes manifest matching the real Next.js dashboard build output.
_TEST_ROUTES_MANIFEST = {
    'dynamicRoutes': [
        {
            'page': '/clusters/[cluster]',
            'regex': '^/clusters/([^/]+?)(?:/)?$'
        },
        {
            'page': '/clusters/[cluster]/[job]',
            'regex': '^/clusters/([^/]+?)/([^/]+?)(?:/)?$'
        },
        {
            'page': '/infra/[context]',
            'regex': '^/infra/([^/]+?)(?:/)?$'
        },
        {
            'page': '/jobs/pools/[pool]',
            'regex': '^/jobs/pools/([^/]+?)(?:/)?$'
        },
        {
            'page': '/jobs/[job]',
            'regex': '^/jobs/([^/]+?)(?:/)?$'
        },
        {
            'page': '/jobs/[job]/[task]',
            'regex': '^/jobs/([^/]+?)/([^/]+?)(?:/)?$'
        },
        {
            'page': '/plugins/[...slug]',
            'regex': '^/plugins/(.+?)(?:/)?$'
        },
        {
            'page': '/recipes/[recipe]',
            'regex': '^/recipes/([^/]+?)(?:/)?$'
        },
        {
            'page': '/volumes/[volume]',
            'regex': '^/volumes/([^/]+?)(?:/)?$'
        },
        {
            'page': '/workspaces/[name]',
            'regex': '^/workspaces/([^/]+?)(?:/)?$'
        },
        {
            'page': '/[...path]',
            'regex': '^/(.+?)(?:/)?$'
        },
    ]
}


def _build_dashboard_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a directory tree mimicking the Next.js dashboard build output."""
    d = tmp_path / 'dashboard'
    d.mkdir()
    # Write the routes manifest
    (d / 'routes-manifest.json').write_text(json.dumps(_TEST_ROUTES_MANIFEST))
    # Top-level files
    (d / 'index.html').write_text('')
    (d / '[...path].html').write_text('')
    # clusters
    (d / 'clusters').mkdir()
    (d / 'clusters' / '[cluster].html').write_text('')
    cluster_dynamic = d / 'clusters' / '[cluster]'
    cluster_dynamic.mkdir()
    (cluster_dynamic / '[job].html').write_text('')
    # infra
    (d / 'infra').mkdir()
    (d / 'infra' / '[context].html').write_text('')
    # jobs
    (d / 'jobs').mkdir()
    (d / 'jobs' / '[job].html').write_text('')
    job_dynamic = d / 'jobs' / '[job]'
    job_dynamic.mkdir()
    (job_dynamic / '[task].html').write_text('')
    pools = d / 'jobs' / 'pools'
    pools.mkdir()
    (pools / '[pool].html').write_text('')
    # plugins (catch-all)
    (d / 'plugins').mkdir()
    (d / 'plugins' / '[...slug].html').write_text('')
    # recipes
    (d / 'recipes').mkdir()
    (d / 'recipes' / '[recipe].html').write_text('')
    # volumes
    (d / 'volumes').mkdir()
    (d / 'volumes' / '[volume].html').write_text('')
    # workspaces
    (d / 'workspaces').mkdir()
    (d / 'workspaces' / '[name].html').write_text('')
    return d


class TestResolveDynamicRoute:
    """Tests for _resolve_dynamic_route using the routes manifest."""

    @pytest.fixture(autouse=True)
    def _clear_routes_cache(self):
        """Reset the cached dynamic routes between tests."""
        server._DYNAMIC_ROUTES = None
        yield
        server._DYNAMIC_ROUTES = None

    def test_single_dynamic_segment(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d),
                                                   'clusters/my-cluster')
        assert result is not None
        assert result.endswith('[cluster].html')

    def test_nested_dynamic_segments(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'jobs/123/456')
        assert result is not None
        assert result.endswith('[task].html')

    def test_literal_dir_over_dynamic(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'jobs/pools/my-pool')
        assert result is not None
        assert result.endswith('[pool].html')
        assert 'pools' in result

    def test_catchall_route(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'plugins/foo/bar')
        assert result is not None
        assert result.endswith('[...slug].html')

    def test_catchall_single_segment(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'plugins/foo')
        assert result is not None
        assert result.endswith('[...slug].html')

    def test_root_catchall_for_unknown_path(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'unknown/deep/path')
        assert result is not None
        assert result.endswith('[...path].html')

    def test_infra_dynamic(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'infra/k8s')
        assert result is not None
        assert result.endswith('[context].html')

    def test_volumes_dynamic(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'volumes/my-vol')
        assert result is not None
        assert result.endswith('[volume].html')

    def test_workspaces_dynamic(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'workspaces/my-ws')
        assert result is not None
        assert result.endswith('[name].html')

    def test_recipes_dynamic(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'recipes/my-recipe')
        assert result is not None
        assert result.endswith('[recipe].html')

    def test_nested_cluster_job(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(
                str(d), 'clusters/my-cluster/job-42')
        assert result is not None
        assert result.endswith('[job].html')

    def test_no_match_returns_none(self, tmp_path):
        d = tmp_path / 'empty'
        d.mkdir()
        # No manifest, no routes
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'anything')
        assert result is None

    def test_single_unknown_segment_uses_root_catchall(self, tmp_path):
        d = _build_dashboard_tree(tmp_path)
        with mock.patch.object(server_constants, 'DASHBOARD_DIR', str(d)):
            result = server._resolve_dynamic_route(str(d), 'cron')
        assert result is not None
        assert result.endswith('[...path].html')
