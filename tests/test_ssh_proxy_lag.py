"""Modular regression tests for SSH proxy blocking - one test per endpoint.

Each endpoint has its own test function for better pytest integration:
- Can run individual tests: pytest test_ssh_proxy_modular.py::test_endpoint_api_get
- Better test reporting and debugging
- Cleaner code with shared fixtures
"""

import asyncio
import os
import pathlib
import sys
import tempfile
import time
from typing import Any, Callable, Dict
from unittest import mock

import fastapi.exceptions
import pytest

# Add parent directory to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from sky import global_user_state
from sky.data import storage_utils
from sky.server import server
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.utils import context_utils


async def _run_endpoint_func(func, *args, **kwargs):
    # Simulate fast API handling, sync function will be handled at a threadpool
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return await context_utils.to_thread(func, *args, **kwargs)


class SSHLatencyMonitor:
    """Monitor SSH responsiveness during concurrent operations."""

    def __init__(self):
        self.latencies = []
        self.baseline = None

    async def measure_baseline(self):
        """Measure baseline SSH latency without load."""
        await self._simulate_ssh_keystrokes()
        self.baseline = sum(self.latencies) / len(
            self.latencies) if self.latencies else 0
        self.latencies.clear()
        return self.baseline

    async def _simulate_ssh_keystrokes(self, num_keystrokes=20):
        """Simulate SSH keystrokes and measure latency."""
        for _ in range(num_keystrokes):
            start = time.time()
            await asyncio.sleep(0.001)  # Should take ~1ms
            latency = time.time() - start
            self.latencies.append(latency)
            if latency > 0.05:  # Report if > 50ms
                print(f"      ⚠️  SSH lag: {latency*1000:.1f}ms")
            await asyncio.sleep(0.02)

    async def monitor_during_operation(self):
        """Monitor SSH during an operation."""
        await self._simulate_ssh_keystrokes()
        if self.latencies:
            return sum(self.latencies) / len(self.latencies)
        return 0

    def get_degradation(self, current_avg):
        """Calculate degradation factor."""
        if self.baseline and self.baseline > 0:
            return current_avg / self.baseline
        return float('inf')

    def clear(self):
        """Clear current measurements."""
        self.latencies.clear()


# ========== FIXTURES ==========
@pytest.fixture(scope='session', autouse=True)
def cleanup_db_conn():
    """Ensure proper cleanup of db conn after tests."""
    yield
    if requests_lib._DB is not None:
        asyncio.run(requests_lib._DB.close())


@pytest.fixture(scope='session', autouse=True)
def enable_asyncio_debug():
    """Enable asyncio debug."""
    os.environ['PYTHONASYNCIODEBUG'] = '1'


@pytest.fixture
def monitor():
    """Create SSH latency monitor."""
    return SSHLatencyMonitor()


@pytest.fixture
async def monitor_with_baseline():
    """Create and initialize SSH latency monitor with baseline."""
    m = SSHLatencyMonitor()
    baseline = await m.measure_baseline()
    print(f"\n📊 Baseline SSH latency: {baseline*1000:.1f}ms")
    return m


@pytest.fixture
def mock_request():
    """Create mock request object."""
    mock_req = mock.MagicMock()
    mock_req.state.request_id = 'test_req_0000'
    mock_req.state.auth_user = None
    mock_req.headers = {}
    mock_req.cookies = {}
    mock_req.query_params = {}
    return mock_req


@pytest.fixture
def mock_request_obj():
    """Create mock request database object."""
    obj = mock.MagicMock()
    obj.request_id = 'test_req_0000'
    obj.status = requests_lib.RequestStatus.SUCCEEDED
    obj.should_retry = False
    obj.get_error = lambda: None
    obj.encode = lambda: mock.MagicMock(model_dump=lambda: {})
    obj.readable_encode = lambda: {}
    return obj


@pytest.fixture
def mock_schedule_request_async():
    """Mock executor.schedule_request for all tests."""
    with mock.patch.object(executor, 'schedule_request_async') as mock_sched:
        yield mock_sched


@pytest.fixture
def mock_blocking_operations(mock_request_obj):
    """Mock common blocking operations with simulated delays."""
    patches = []

    # Mock requests.get_request (blocking version - still used by some endpoints)
    get_request_patch = mock.patch('sky.server.requests.requests.get_request',
                                   side_effect=create_blocking_mock(
                                       mock_request_obj,
                                       delay=0.02,
                                       name='get_request'))
    patches.append(get_request_patch)

    # Mock requests.get_request_async (async version - should NOT block)
    async def async_get_request(*args, **kwargs):
        await asyncio.sleep(0.02)  # Async delay that doesn't block
        return mock_request_obj

    get_request_async_patch = mock.patch(
        'sky.server.requests.requests.get_request_async',
        side_effect=async_get_request)
    patches.append(get_request_async_patch)

    # Mock requests.get_request_tasks (blocking version - still used by api_status)
    get_tasks_patch = mock.patch(
        'sky.server.requests.requests.get_request_tasks',
        # Mock a significant amount of time to load all requests
        side_effect=create_blocking_mock([mock_request_obj],
                                         delay=0.2,
                                         name='get_request_tasks'))
    patches.append(get_tasks_patch)

    # Start all patches
    for patch in patches:
        patch.start()

    try:
        yield
    finally:
        # Stop all patches
        for patch in patches:
            patch.stop()


# ========== HELPER FUNCTIONS ==========


def create_blocking_mock(return_value, delay=0.02, name=None):
    """Create a mock that simulates blocking behavior.
    
    Args:
        return_value: Value to return after blocking
        delay: Time to block in seconds (default 20ms)
        name: Optional name for debugging
    """

    def blocking_func(*args, **kwargs):
        time.sleep(delay)  # Simulate blocking
        return return_value

    return blocking_func


async def run_endpoint_test(
        endpoint_func: Callable,
        monitor: SSHLatencyMonitor,
        num_concurrent: int = 100,
        expected_degradation_threshold: float = 10.0) -> Dict[str, Any]:
    """Run performance test for a single endpoint."""
    # Initialize baseline if not already done
    if monitor.baseline is None:
        baseline = await monitor.measure_baseline()
        print(f"   Baseline: {baseline*1000:.1f}ms")

    monitor.clear()

    for _ in range(3):
        # Run concurrent requests while monitoring SSH
        ssh_task = asyncio.create_task(monitor.monitor_during_operation())
        test_tasks = []
        for _ in range(num_concurrent):
            task = asyncio.create_task(endpoint_func())
            test_tasks.append(task)

        results = await asyncio.gather(*test_tasks,
                                       ssh_task,
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise result

        # Calculate results
        avg_latency = sum(monitor.latencies) / len(
            monitor.latencies) if monitor.latencies else 0
        degradation = monitor.get_degradation(avg_latency)

        # Report results
        status = "❌ BLOCKING" if degradation > expected_degradation_threshold else "✅ OK"
        print(
            f"   Latency: {avg_latency*1000:.1f}ms ({degradation:.1f}x) - {status}"
        )

        blocking = degradation > expected_degradation_threshold
        if not blocking:
            break

    return {
        'latency': avg_latency,
        'degradation': degradation,
        'blocking': blocking
    }


# ========== CATEGORY 1: API REQUEST ENDPOINTS ==========


@pytest.mark.asyncio
async def test_endpoint_api_get(monitor, mock_blocking_operations):
    """Test /api/get endpoint for blocking operations."""
    print("\n🔍 Testing: /api/get")

    async def test_func():
        try:
            await server.api_get('test_req')
        except:
            pass

    result = await run_endpoint_test(test_func, monitor)
    assert not result['blocking'], "/api/get should NOT block the event loop"


@pytest.mark.asyncio
async def test_endpoint_api_status(monitor, mock_blocking_operations):
    """Test /api/status endpoint for blocking operations."""
    print("\n🔍 Testing: /api/status")

    async def test_func():
        try:
            # This should call get_request_tasks (which should block for 1s)
            # Must pass None explicitly since we're not going through FastAPI
            await server.api_status(request_ids=None, all_status=False)
            # This should call get_request (which should block for 0.02s each)
            await server.api_status(request_ids=['test1', 'test2'],
                                    all_status=False)
        except Exception as e:
            print(f"      Error in test_func: {e}")
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=20)
    assert not result['blocking'], "/api/status should NOT block the event loop"


@pytest.mark.asyncio
async def test_endpoint_api_cancel(monitor, mock_request,
                                   mock_schedule_request_async):
    """Test /api/cancel endpoint for blocking operations."""
    print("\n🔍 Testing: /api/cancel")

    async def test_func():
        try:
            body = payloads.RequestCancelBody(request_ids=['test1'])
            await server.api_cancel(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/api/cancel should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_api_stream(monitor, mock_blocking_operations):
    """Test /api/stream endpoint for blocking operations."""
    print("\n🔍 Testing: /api/stream")

    # Create test log file
    log_file = tempfile.NamedTemporaryFile(suffix='.log', delete=False)
    log_file.write(b'Test log\n' * 10)
    log_file.close()

    async def test_func():
        try:
            count = 0
            async for _ in stream_utils.log_streamer('test',
                                                     pathlib.Path(
                                                         log_file.name),
                                                     follow=False):
                count += 1
                if count > 3:
                    break
        except:
            pass

    result = await run_endpoint_test(test_func, monitor)
    os.unlink(log_file.name)
    assert not result['blocking'], "/api/stream should NOT block the event loop"


# ========== CATEGORY 2: CLUSTER OPERATIONS ==========


@pytest.mark.asyncio
async def test_endpoint_launch(monitor, mock_request,
                               mock_schedule_request_async):
    """Test /launch endpoint for blocking operations."""
    print("\n🔍 Testing: /launch")

    async def test_func():
        try:
            body = payloads.LaunchBody(dag='test', env_vars={})
            await server.launch(body, mock_request)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/launch should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_exec(monitor, mock_request):
    """Test /exec endpoint for blocking operations."""
    print("\n🔍 Testing: /exec")

    async def test_func():
        try:
            body = payloads.ExecBody(cluster_name='test',
                                     dag='test',
                                     env_vars={})
            await server.exec(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/exec should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_stop(monitor, mock_request,
                             mock_schedule_request_async):
    """Test /stop endpoint for blocking operations."""
    print("\n🔍 Testing: /stop")

    async def test_func():
        try:
            body = payloads.StopOrDownBody(cluster_name='test', env_vars={})
            await server.stop(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/stop should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_down(monitor, mock_request,
                             mock_schedule_request_async):
    """Test /down endpoint for blocking operations."""
    print("\n🔍 Testing: /down")

    async def test_func():
        try:
            body = payloads.StopOrDownBody(cluster_name='test', env_vars={})
            await server.down(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/down should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_status(monitor, mock_request,
                               mock_schedule_request_async):
    """Test /status endpoint for blocking operations."""
    print("\n🔍 Testing: /status")

    async def test_func():
        try:
            body = payloads.StatusBody()
            await server.status(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/status should not block (uses schedule_request)"


# ========== CATEGORY 3: FILE OPERATIONS ==========


@pytest.mark.asyncio
async def test_endpoint_upload(monitor):
    """Test /upload endpoint for blocking operations."""
    print("\n🔍 Testing: /upload")

    async def test_func():
        with mock.patch('sky.server.server.unzip_file') as mock_unzip:

            async def async_unzip(*args):
                await asyncio.sleep(0.001)

            mock_unzip.side_effect = async_unzip

            mock_req = mock.MagicMock()
            mock_req.state.auth_user = None

            async def mock_stream():
                yield b'data'

            mock_req.stream = mock_stream

            try:
                await server.upload_zip_file(
                    mock_req, 'test', 'sky-2025-01-01-00-00-00-000000-12345678',
                    0, 1)
            except:
                pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=10)
    assert not result['blocking'], "/upload should not block significantly"


@pytest.mark.asyncio
async def test_endpoint_download(monitor, mock_request):
    """Test /download endpoint for blocking operations."""
    print("\n🔍 Testing: /download")

    async def test_func():
        # Mock zip operation with actual blocking behavior
        with mock.patch.object(storage_utils,
                               'zip_files_and_folders',
                               side_effect=create_blocking_mock('test.zip',
                                                                delay=0.025)):
            test_dir = tempfile.mkdtemp(prefix='test_')
            try:
                mock_request.query_params = {'relative': 'home'}
                body = payloads.DownloadBody(
                    folder_paths=[test_dir],
                    env_vars={'SKYPILOT_USER_ID': 'test'})
                with mock.patch(
                        'sky.server.common.api_server_user_logs_dir_prefix',
                        return_value=pathlib.Path(test_dir)):
                    await server.download(body, mock_request)
            except:
                pass
            finally:
                import shutil
                await context_utils.to_thread(shutil.rmtree,
                                              test_dir,
                                              ignore_errors=True)

    result = await run_endpoint_test(test_func, monitor, num_concurrent=10)
    assert not result['blocking'], "/download should not block the event loop"


# ========== CATEGORY 4: COMPLETION ENDPOINTS ==========


@pytest.mark.asyncio
async def test_endpoint_completion_cluster(monitor):
    """Test /api/completion/cluster_name endpoint for blocking operations."""
    print("\n🔍 Testing: /api/completion/cluster_name")

    async def test_func():
        # Mock the actual blocking DB call
        with mock.patch.object(global_user_state,
                               'get_cluster_names_start_with',
                               side_effect=create_blocking_mock([],
                                                                delay=0.02)):
            try:
                await server.complete_cluster_name('test')
            except:
                pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "Completion endpoints should not block the event loop"


@pytest.mark.asyncio
async def test_endpoint_completion_storage(monitor):
    """Test /api/completion/storage_name endpoint for blocking operations."""
    print("\n🔍 Testing: /api/completion/storage_name")

    async def test_func():
        # Mock the actual blocking DB call
        with mock.patch.object(global_user_state,
                               'get_storage_names_start_with',
                               side_effect=create_blocking_mock([],
                                                                delay=0.02)):
            try:
                await server.complete_storage_name('test')
            except:
                pass

    # Creating too much threads simultaneously also affects the event loop
    # (CPU contention)
    # TODO(aylei): should switch to async global_user_state operation instead
    # of using to_thread
    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "Completion endpoints should not block the event loop"


@pytest.mark.asyncio
async def test_endpoint_provision_logs(monitor):
    """Test /provision_logs endpoint for blocking operations."""
    print("\n🔍 Testing: /provision_logs")

    async def test_func():
        # Mock the actual blocking DB calls
        with mock.patch.object(global_user_state,
                               'get_cluster_provision_log_path',
                               side_effect=create_blocking_mock(None,
                                                                delay=0.02)):
            with mock.patch.object(global_user_state,
                                   'get_cluster_history_provision_log_path',
                                   side_effect=create_blocking_mock(
                                       None, delay=0.02)):
                try:
                    body = payloads.ProvisionLogsBody(cluster_name='test')
                    await _run_endpoint_func(server.provision_logs,
                                             body,
                                             follow=False)
                except fastapi.HTTPException:
                    # The cluster provision log will not be actually found
                    pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/provision_logs should not block the event loop"


# ========== CATEGORY 5: USER MANAGEMENT ==========


@pytest.mark.asyncio
async def test_endpoint_users_list(monitor):
    """Test /users endpoint for blocking operations."""
    print("\n🔍 Testing: /users")

    async def test_func():
        # Mock the database calls that fetch users
        with mock.patch('sky.global_user_state.get_all_users',
                        side_effect=create_blocking_mock([], delay=0.2)):
            from sky.users import server as users_server
            await _run_endpoint_func(users_server.users)

    result = await run_endpoint_test(test_func, monitor, num_concurrent=100)
    assert not result['blocking'], "/users should not block the event loop"


@pytest.mark.asyncio
async def test_endpoint_users_export(monitor):
    """Test /users/export endpoint for blocking operations."""
    print("\n🔍 Testing: /users/export")

    async def test_func():
        # Mock the database calls and CSV generation
        with mock.patch('sky.global_user_state.get_all_users',
                        side_effect=create_blocking_mock([], delay=0.2)):
            from sky.users import server as users_server
            await _run_endpoint_func(users_server.user_export)

    result = await run_endpoint_test(test_func, monitor, num_concurrent=100)
    assert not result[
        'blocking'], "/users/export should not block the event loop"


@pytest.mark.asyncio
async def test_endpoint_users_service_tokens(monitor):
    """Test /users/service-account-tokens endpoint for blocking operations."""
    print("\n🔍 Testing: /users/service-account-tokens")

    async def test_func():
        mock_req = mock.MagicMock()
        mock_req.state.auth_user = 'test_user'

        # Mock database operations
        with mock.patch('sky.global_user_state.get_all_service_account_tokens',
                        side_effect=create_blocking_mock([], delay=0.02)):
            from sky.users import server as users_server
            await _run_endpoint_func(users_server.get_service_account_tokens,
                                     mock_req)

    result = await run_endpoint_test(test_func, monitor)
    assert not result[
        'blocking'], "/users/service-account-tokens should not block the event loop"


# ========== CATEGORY 6: WORKSPACES ==========


@pytest.mark.asyncio
async def test_endpoint_workspaces_list(monitor, mock_request,
                                        mock_schedule_request_async):
    """Test /workspaces endpoint for blocking operations."""
    print("\n🔍 Testing: /workspaces")

    async def test_func():
        try:
            from sky.workspaces import server as workspaces_server
            await workspaces_server.get(mock_request)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/workspaces should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_workspaces_create(monitor, mock_request,
                                          mock_schedule_request_async):
    """Test /workspaces/create endpoint for blocking operations."""
    print("\n🔍 Testing: /workspaces/create")

    async def test_func():
        try:
            from sky.workspaces import server as workspaces_server
            body = payloads.CreateWorkspaceBody(name='test')
            await workspaces_server.create(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/workspaces/create should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_workspaces_config(monitor, mock_request,
                                          mock_schedule_request_async):
    """Test /workspaces/config endpoint for blocking operations."""
    print("\n🔍 Testing: /workspaces/config")

    async def test_func():
        try:
            from sky.workspaces import server as workspaces_server
            await workspaces_server.get_config(mock_request)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/workspaces/config should not block (uses schedule_request)"


# ========== CATEGORY 7: SSH NODE POOLS ==========


@pytest.mark.asyncio
async def test_endpoint_ssh_node_pools_list(monitor):
    """Test /ssh_node_pools endpoint for blocking operations."""
    print("\n🔍 Testing: /ssh_node_pools")

    async def test_func():
        from sky.ssh_node_pools import server as ssh_pools_server

        # Whatever, we think get_all_pools might be blocking.
        with mock.patch('sky.ssh_node_pools.core.get_all_pools',
                        side_effect=create_blocking_mock({}, delay=0.01)):
            await _run_endpoint_func(ssh_pools_server.get_ssh_node_pools)

    result = await run_endpoint_test(test_func, monitor, num_concurrent=20)
    assert not result[
        'blocking'], "/ssh_node_pools should not block (uses to_thread)"


@pytest.mark.asyncio
async def test_endpoint_ssh_node_pools_deploy(monitor, mock_request,
                                              mock_schedule_request_async):
    """Test /ssh_node_pools/deploy endpoint for blocking operations."""
    print("\n🔍 Testing: /ssh_node_pools/deploy")

    async def test_func():
        try:
            from sky.ssh_node_pools import server as ssh_pools_server
            body = payloads.SSHNodePoolDeployBody(pool_names=['test'])
            await ssh_pools_server.deploy_ssh_node_pool_general(
                mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/ssh_node_pools/deploy should not block (uses schedule_request)"


# ========== CATEGORY 8: VOLUMES ==========


@pytest.mark.asyncio
async def test_endpoint_volumes_list(monitor, mock_request,
                                     mock_schedule_request_async):
    """Test /volumes endpoint for blocking operations."""
    print("\n🔍 Testing: /volumes")

    async def test_func():
        try:
            from sky.volumes.server import server as volumes_server
            await volumes_server.volume_list(mock_request)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/volumes should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_volumes_delete(monitor, mock_request,
                                       mock_schedule_request_async):
    """Test /volumes/delete endpoint for blocking operations."""
    print("\n🔍 Testing: /volumes/delete")

    async def test_func():
        try:
            from sky.volumes.server import server as volumes_server
            body = payloads.VolumeDeleteBody(volume_names=['test-volume'])
            await volumes_server.volume_delete(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/volumes/delete should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_volumes_apply(monitor, mock_request,
                                      mock_schedule_request_async):
    """Test /volumes/apply endpoint for blocking operations."""
    print("\n🔍 Testing: /volumes/apply")

    async def test_func():
        try:
            from sky.utils import volume
            from sky.volumes.server import server as volumes_server
            body = payloads.VolumeApplyBody(
                volume_name='test-volume',
                cloud='aws',
                volume_type=volume.VolumeType.EBS.value,
                config={})
            await volumes_server.volume_apply(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/volumes/apply should not block (uses schedule_request)"


# ========== CATEGORY 9: JOBS ==========


@pytest.mark.asyncio
async def test_endpoint_jobs_launch(monitor, mock_request,
                                    mock_schedule_request_async):
    """Test /jobs/launch endpoint for blocking operations."""
    print("\n🔍 Testing: /jobs/launch")

    async def test_func():
        try:
            from sky.jobs.server import server as jobs_server
            body = payloads.JobsLaunchBody(managed_job_name='test-job',
                                           dag='test',
                                           env_vars={})
            await jobs_server.launch(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/jobs/launch should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_jobs_queue(monitor, mock_request,
                                   mock_schedule_request_async):
    """Test /jobs/queue endpoint for blocking operations."""
    print("\n🔍 Testing: /jobs/queue")

    async def test_func():
        try:
            from sky.jobs.server import server as jobs_server
            body = payloads.JobsQueueBody(env_vars={})
            await jobs_server.queue(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/jobs/queue should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_jobs_cancel(monitor, mock_request,
                                    mock_schedule_request_async):
    """Test /jobs/cancel endpoint for blocking operations."""
    print("\n🔍 Testing: /jobs/cancel")

    async def test_func():
        try:
            from sky.jobs.server import server as jobs_server
            body = payloads.JobsCancelBody(managed_job_name='test-job',
                                           env_vars={})
            await jobs_server.cancel(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/jobs/cancel should not block (uses schedule_request)"


# ========== CATEGORY 10: SERVE ==========


@pytest.mark.asyncio
async def test_endpoint_serve_up(monitor, mock_request,
                                 mock_schedule_request_async):
    """Test /serve/up endpoint for blocking operations."""
    print("\n🔍 Testing: /serve/up")

    async def test_func():
        try:
            from sky.serve.server import server as serve_server
            body = payloads.ServeUpBody(service_name='test-service',
                                        dag='test',
                                        env_vars={})
            await serve_server.up(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/serve/up should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_serve_down(monitor, mock_request,
                                   mock_schedule_request_async):
    """Test /serve/down endpoint for blocking operations."""
    print("\n🔍 Testing: /serve/down")

    async def test_func():
        try:
            from sky.serve.server import server as serve_server
            body = payloads.ServeDownBody(service_names=['test-service'],
                                          env_vars={})
            await serve_server.down(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/serve/down should not block (uses schedule_request)"


@pytest.mark.asyncio
async def test_endpoint_serve_status(monitor, mock_request,
                                     mock_schedule_request_async):
    """Test /serve/status endpoint for blocking operations."""
    print("\n🔍 Testing: /serve/status")

    async def test_func():
        try:
            from sky.serve.server import server as serve_server
            body = payloads.ServeStatusBody(env_vars={})
            await serve_server.status(mock_request, body)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/serve/status should not block (uses schedule_request)"


# ========== CATEGORY 11: VALIDATION & OPTIMIZATION ==========


@pytest.mark.asyncio
async def test_endpoint_validate(monitor):
    """Test /validate endpoint for blocking operations."""
    print("\n🔍 Testing: /validate")

    async def test_func():
        with mock.patch('sky.utils.context_utils.to_thread') as mock_thread:
            # to_thread should handle blocking properly
            async def async_validate(*args):
                await asyncio.sleep(0.001)

            mock_thread.side_effect = async_validate

            try:
                body = payloads.ValidateBody(dag='test', env_vars={})
                await server.validate(body)
            except:
                pass

    result = await run_endpoint_test(test_func, monitor)
    assert not result['blocking'], "/validate should not block (uses to_thread)"


@pytest.mark.asyncio
async def test_endpoint_optimize(monitor, mock_request,
                                 mock_schedule_request_async):
    """Test /optimize endpoint for blocking operations."""
    print("\n🔍 Testing: /optimize")

    async def test_func():
        try:
            body = payloads.OptimizeBody(dag='test', env_vars={})
            await server.optimize(body, mock_request)
        except:
            pass

    result = await run_endpoint_test(test_func, monitor, num_concurrent=30)
    assert not result[
        'blocking'], "/optimize should not block (uses schedule_request)"
