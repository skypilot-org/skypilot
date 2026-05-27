"""Unit tests for the metrics system."""

import base64
import os
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import fastapi
from prometheus_client import CollectorRegistry
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest
import pytest

from sky.metrics import utils as metrics_utils
from sky.server import metrics
from sky.server.server import BasicAuthMiddleware


def test_get_status_code_group():
    """Test status code grouping"""
    assert metrics._get_status_code_group(200) == "2xx"
    assert metrics._get_status_code_group(201) == "2xx"
    assert metrics._get_status_code_group(299) == "2xx"

    assert metrics._get_status_code_group(400) == "4xx"
    assert metrics._get_status_code_group(404) == "4xx"
    assert metrics._get_status_code_group(499) == "4xx"

    assert metrics._get_status_code_group(500) == "5xx"
    assert metrics._get_status_code_group(503) == "5xx"
    assert metrics._get_status_code_group(599) == "5xx"


def test_is_streaming_api():
    assert metrics._is_streaming_api("/api/v1/logs") is True
    assert metrics._is_streaming_api("/api/v1/logs/") is True
    assert metrics._is_streaming_api("/logs") is True
    assert metrics._is_streaming_api("/logs/") is True

    assert metrics._is_streaming_api("/api/stream") is True
    assert metrics._is_streaming_api("/api/stream/") is True
    assert metrics._is_streaming_api("/v1/api/stream") is True

    assert metrics._is_streaming_api("/api/v1/status") is False
    assert metrics._is_streaming_api("/health") is False
    assert metrics._is_streaming_api("/api/v1/jobs") is False
    assert metrics._is_streaming_api("/metrics") is False


@pytest.mark.asyncio
async def test_metrics_endpoint_without_multiprocess():
    """Test metrics endpoint in single process mode."""
    with patch.dict(os.environ, {}, clear=False):
        # Remove PROMETHEUS_MULTIPROC_DIR if it exists
        if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
            del os.environ['PROMETHEUS_MULTIPROC_DIR']

        with patch('sky.server.metrics.generate_latest') as mock_gen:
            mock_gen.return_value = b"# HELP test_metric Test metric\n"

            response = metrics.metrics()

            assert isinstance(response, fastapi.Response)
            assert response.media_type == CONTENT_TYPE_LATEST
            assert response.headers['Cache-Control'] == 'no-cache'
            assert b"# HELP test_metric Test metric" in response.body
            mock_gen.assert_called_once()


def test_register_multiproc_cleanup_atexit_noop_without_env_var():
    """No atexit registration in single-process / unit-test mode."""
    with patch.dict(os.environ, {}, clear=False), \
         patch.object(metrics, '_multiproc_cleanup_registered', False), \
         patch('sky.server.metrics.atexit.register') as mock_register:
        if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
            del os.environ['PROMETHEUS_MULTIPROC_DIR']
        metrics.register_multiproc_cleanup_atexit()
        mock_register.assert_not_called()


def test_register_multiproc_cleanup_atexit_registers_when_enabled():
    """When PROMETHEUS_MULTIPROC_DIR is set, register mark_process_dead(pid)."""
    with patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': '/tmp/prom'}), \
         patch.object(metrics, '_multiproc_cleanup_registered', False), \
         patch('sky.server.metrics.atexit.register') as mock_register, \
         patch('sky.server.metrics.os.getpid', return_value=4242):
        metrics.register_multiproc_cleanup_atexit()
        mock_register.assert_called_once_with(
            metrics.multiprocess.mark_process_dead, 4242)


def test_register_multiproc_cleanup_atexit_is_idempotent():
    """Repeated calls in the same process only register once."""
    with patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': '/tmp/prom'}), \
         patch.object(metrics, '_multiproc_cleanup_registered', False), \
         patch('sky.server.metrics.atexit.register') as mock_register:
        metrics.register_multiproc_cleanup_atexit()
        metrics.register_multiproc_cleanup_atexit()
        metrics.register_multiproc_cleanup_atexit()
        assert mock_register.call_count == 1


# End-to-end coverage of the atexit hook. Spawns a real subprocess that
# writes a liveall gauge file, then exits — exercising the actual
# `multiprocess.mark_process_dead` path (not mocked). With the fix it
# reaps its own file; without it, the file leaks. Uses 'spawn' rather
# than 'fork' so the child does not inherit this test process's atexit
# handlers or already-imported registries.

_CHILD_SCRIPT = """
import os
from prometheus_client import Gauge
gauge = Gauge(
    '__test_atexit_liveall',
    'test',
    ['pid'],
    multiprocess_mode='liveall',
)
if os.environ.get('WITH_FIX'):
    from sky.server import metrics
    metrics.register_multiproc_cleanup_atexit()
gauge.labels(pid=str(os.getpid())).set(5.2)
# Write pid to a file rather than stdout — `import sky` logs to stdout
# on a cold subprocess (skypilot_config debug lines).
with open(os.environ['_PID_OUT'], 'w') as f:
    f.write(str(os.getpid()))
"""


def _spawn_writer(multiproc_dir: str, with_fix: bool) -> int:
    """Run the writer subprocess; return its pid."""
    import subprocess  # local — only the e2e tests need it
    import sys
    import tempfile
    env = os.environ.copy()
    env['PROMETHEUS_MULTIPROC_DIR'] = multiproc_dir
    pid_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pid')
    pid_file.close()
    env['_PID_OUT'] = pid_file.name
    if with_fix:
        env['WITH_FIX'] = '1'
    else:
        env.pop('WITH_FIX', None)
    try:
        # Generous timeout: a cold `from sky.server import metrics` in a fresh
        # subprocess pulls in the full sky import chain (~20s on CI hardware).
        subprocess.run(
            [sys.executable, '-c', _CHILD_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        with open(pid_file.name) as f:
            return int(f.read().strip())
    finally:
        os.unlink(pid_file.name)


def test_atexit_reaps_liveall_file_with_fix(tmp_path):
    pid = _spawn_writer(str(tmp_path), with_fix=True)
    leftover = sorted(os.listdir(tmp_path))
    assert f'gauge_liveall_{pid}.db' not in leftover, leftover


def test_without_fix_leaks_liveall_file(tmp_path):
    pid = _spawn_writer(str(tmp_path), with_fix=False)
    leftover = sorted(os.listdir(tmp_path))
    assert f'gauge_liveall_{pid}.db' in leftover, leftover


def _touch_live_gauge_files(directory, pid):
    """Write empty live-gauge files matching the prometheus_client schema."""
    for mode in ('liveall', 'livesum', 'livemax', 'livemin'):
        path = os.path.join(directory, f'gauge_{mode}_{pid}.db')
        with open(path, 'wb'):
            pass


def test_scan_multiproc_pids_only_returns_live_gauge_pids(tmp_path):
    """Pids derived from live-gauge files; aggregate files are ignored."""
    pid_with_live = 1234
    pid_aggregate_only = 5678
    _touch_live_gauge_files(str(tmp_path), pid_with_live)
    (tmp_path / f'counter_{pid_aggregate_only}.db').write_bytes(b'')
    (tmp_path / f'histogram_{pid_aggregate_only}.db').write_bytes(b'')
    (tmp_path / 'unrelated.txt').write_bytes(b'')

    pids = metrics._scan_multiproc_pids(str(tmp_path))
    assert pids == {pid_with_live}


def test_scan_multiproc_pids_missing_dir(tmp_path):
    """A nonexistent directory yields the empty set (no crash)."""
    pids = metrics._scan_multiproc_pids(str(tmp_path / 'does-not-exist'))
    assert pids == set()


def test_reap_stale_multiproc_files_noop_without_env(tmp_path):
    """No PROMETHEUS_MULTIPROC_DIR -> no work, no errors."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('PROMETHEUS_MULTIPROC_DIR', None)
        assert metrics._reap_stale_multiproc_files() == 0


def test_reap_stale_multiproc_files_removes_only_dead_pids(tmp_path):
    """Live pids stay; dead pids are reaped exactly once each.

    Dead pids are simulated via patching pid_exists rather than an
    out-of-range integer, to keep this test resilient on systems with a
    high pid_max.
    """
    dead_pid_a, dead_pid_b, live_pid = 991, 992, os.getpid()
    _touch_live_gauge_files(str(tmp_path), dead_pid_a)
    _touch_live_gauge_files(str(tmp_path), dead_pid_b)
    _touch_live_gauge_files(str(tmp_path), live_pid)

    def fake_pid_exists(pid):
        return pid == live_pid

    reaped_pids = []

    def fake_mark_dead(pid):
        reaped_pids.append(pid)
        for path in (
                tmp_path /
                f'gauge_liveall_{pid}.db').parent.glob(f'gauge_live*_{pid}.db'):
            path.unlink()

    with patch.dict(os.environ,
                    {'PROMETHEUS_MULTIPROC_DIR': str(tmp_path)}), \
         patch('sky.server.metrics.psutil.pid_exists',
               side_effect=fake_pid_exists), \
         patch('sky.server.metrics.multiprocess.mark_process_dead',
               side_effect=fake_mark_dead):
        reaped = metrics._reap_stale_multiproc_files()

    assert reaped == 2
    assert sorted(reaped_pids) == [dead_pid_a, dead_pid_b]
    # Live pid's files survive; dead pids' files were unlinked.
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        f'gauge_liveall_{live_pid}.db',
        f'gauge_livemax_{live_pid}.db',
        f'gauge_livemin_{live_pid}.db',
        f'gauge_livesum_{live_pid}.db',
    ]


def test_reap_stale_multiproc_files_swallows_per_pid_errors(tmp_path):
    """A failure on one pid does not stop the rest of the sweep."""
    pid_a, pid_b = 991, 992
    _touch_live_gauge_files(str(tmp_path), pid_a)
    _touch_live_gauge_files(str(tmp_path), pid_b)

    successes = []

    def flaky_mark_dead(pid):
        if pid == pid_a:
            raise OSError('boom')
        successes.append(pid)

    with patch.dict(os.environ,
                    {'PROMETHEUS_MULTIPROC_DIR': str(tmp_path)}), \
         patch('sky.server.metrics.psutil.pid_exists', return_value=False), \
         patch('sky.server.metrics.multiprocess.mark_process_dead',
               side_effect=flaky_mark_dead):
        reaped = metrics._reap_stale_multiproc_files()

    assert reaped == 1
    assert successes == [pid_b]


@pytest.mark.asyncio
async def test_multiproc_reaper_daemon_returns_when_env_unset():
    """Daemon exits immediately if PROMETHEUS_MULTIPROC_DIR is unset."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('PROMETHEUS_MULTIPROC_DIR', None)
        # Should return without sleeping or scheduling another tick.
        await metrics.multiproc_reaper_daemon(interval_seconds=3600)


@pytest.mark.asyncio
async def test_multiproc_reaper_daemon_loops_and_cancels(tmp_path):
    """Daemon ticks, calls reap, and exits cleanly on cancellation."""
    import asyncio  # local to avoid touching module-level imports
    call_count = {'n': 0}

    def fake_reap():
        call_count['n'] += 1
        return 0

    with patch.dict(os.environ,
                    {'PROMETHEUS_MULTIPROC_DIR': str(tmp_path)}), \
         patch('sky.server.metrics._reap_stale_multiproc_files',
               side_effect=fake_reap):
        task = asyncio.create_task(
            metrics.multiproc_reaper_daemon(interval_seconds=0))
        # Yield enough times for several ticks to run.
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count['n'] >= 1


@pytest.mark.asyncio
async def test_metrics_endpoint_with_multiprocess():
    """Test metrics endpoint in multiprocess mode."""
    with patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': '/tmp/prom'}):
        with patch('sky.server.metrics.prom.CollectorRegistry') as \
                mock_registry, \
             patch('sky.server.metrics.multiprocess.'
                   'MultiProcessCollector') as mock_collector, \
             patch('sky.server.metrics.generate_latest') as mock_gen:

            mock_registry_instance = MagicMock()
            mock_registry.return_value = mock_registry_instance
            mock_gen.return_value = b"# HELP multiproc_metric Test\n"

            response = metrics.metrics()

            assert isinstance(response, fastapi.Response)
            mock_registry.assert_called_once()
            mock_collector.assert_called_once_with(mock_registry_instance)
            mock_gen.assert_called_once_with(mock_registry_instance)


@pytest.fixture
def prometheus_middleware():
    """Create PrometheusMiddleware instance for testing."""
    middleware = metrics.PrometheusMiddleware(app=MagicMock())

    # Clear metric values before each test
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()

    return middleware


@pytest.mark.asyncio
async def test_middleware_successful_request(prometheus_middleware):
    """Test middleware with successful non-streaming request."""
    request = MagicMock()
    request.url.path = "/api/v1/status"
    request.method = "GET"

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    start_time = time.time()
    result = await prometheus_middleware.dispatch(request, call_next)
    end_time = time.time()

    assert result == response
    call_next.assert_called_once_with(request)

    # Check that request count was recorded
    total_requests = _get_metric_value('sky_apiserver_requests_total', {
        'path': '/api/v1/status',
        'method': 'GET',
        'status': '2xx'
    })
    assert total_requests == 1.0

    # Check that duration was recorded for non-streaming APIs
    duration_count = _get_metric_value(
        'sky_apiserver_request_duration_seconds_count', {
            'path': '/api/v1/status',
            'method': 'GET',
            'status': '2xx'
        })
    assert duration_count == 1.0

    # Check that the duration sum is reasonable
    duration_sum = _get_metric_value(
        'sky_apiserver_request_duration_seconds_sum', {
            'path': '/api/v1/status',
            'method': 'GET',
            'status': '2xx'
        })
    assert 0 <= duration_sum <= (end_time - start_time + 1)


@pytest.mark.asyncio
async def test_middleware_streaming_request(prometheus_middleware):
    """Test middleware with streaming API request."""
    request = MagicMock()
    request.url.path = "/api/v1/logs"
    request.method = "GET"

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    result = await prometheus_middleware.dispatch(request, call_next)

    assert result == response

    # Check that request count was recorded
    total_requests = _get_metric_value('sky_apiserver_requests_total', {
        'path': '/api/v1/logs',
        'method': 'GET',
        'status': '2xx'
    })
    assert total_requests == 1.0

    # Check that duration was NOT recorded for streaming APIs
    duration_count = _get_metric_value(
        'sky_apiserver_request_duration_seconds_count', {
            'path': '/api/v1/logs',
            'method': 'GET',
            'status': '2xx'
        })
    assert duration_count == 0.0


@pytest.mark.asyncio
async def test_middleware_exception_handling(prometheus_middleware):
    """Test middleware handles exceptions properly."""
    request = MagicMock()
    request.url.path = "/api/v1/failing"
    request.method = "POST"

    call_next = AsyncMock(side_effect=Exception("Test error"))

    with pytest.raises(Exception, match="Test error"):
        await prometheus_middleware.dispatch(request, call_next)

    # Check that 5xx metric was recorded even with exception
    total_requests = _get_metric_value('sky_apiserver_requests_total', {
        'path': '/api/v1/failing',
        'method': 'POST',
        'status': '5xx'
    })
    assert total_requests == 1.0


@pytest.mark.asyncio
async def test_middleware_different_status_codes(prometheus_middleware):
    """Test middleware with different HTTP status codes."""
    test_cases = [
        (404, "4xx"),
        (500, "5xx"),
        (201, "2xx"),
    ]

    for status_code, expected_group in test_cases:
        request = MagicMock()
        request.url.path = f"/test/{status_code}"
        request.method = "GET"

        response = MagicMock()
        response.status_code = status_code

        call_next = AsyncMock(return_value=response)

        await prometheus_middleware.dispatch(request, call_next)

        # Verify the correct status group was recorded
        total_requests = _get_metric_value(
            'sky_apiserver_requests_total', {
                'path': f'/test/{status_code}',
                'method': 'GET',
                'status': expected_group
            })
        assert total_requests == 1.0


def test_get_user_label_with_auth_user():
    """Test _get_user_label with authenticated user."""
    request = MagicMock()
    request.state.auth_user = MagicMock()
    request.state.auth_user.name = 'alice@example.com'

    result = metrics._get_user_label(request)
    assert result == 'alice@example.com'


def test_get_user_label_anonymous():
    """Test _get_user_label with no auth_user."""
    request = MagicMock(spec=['state'])
    request.state = MagicMock(spec=[])  # No auth_user attribute

    result = metrics._get_user_label(request)
    assert result == 'anonymous'


def test_get_user_label_no_name():
    """Test _get_user_label when auth_user has no name."""
    request = MagicMock()
    request.state.auth_user = MagicMock()
    request.state.auth_user.name = None

    result = metrics._get_user_label(request)
    assert result == 'anonymous'


def test_get_user_label_empty_name():
    """Test _get_user_label when auth_user has empty name."""
    request = MagicMock()
    request.state.auth_user = MagicMock()
    request.state.auth_user.name = ''

    result = metrics._get_user_label(request)
    assert result == 'anonymous'


def _get_metric_value(metric_name, labels=None, collectors=None):
    """Helper function to get metric value from the prometheus registry.

    Args:
        metric_name: The metric name prefix to search for.
        labels: Optional dict of label key-value pairs to match.
        collectors: List of prometheus collectors to register. If None,
            registers the default request total and duration metrics.
    """
    if collectors is None:
        collectors = [
            metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
            metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS,
        ]
    registry = CollectorRegistry()
    for collector in collectors:
        registry.register(collector)

    output = generate_latest(registry).decode('utf-8')

    lines = output.split('\n')
    for line in lines:
        if line.startswith(metric_name):
            if labels:
                if all(f'{k}="{v}"' in line for k, v in labels.items()):
                    value = line.split()[-1]
                    try:
                        return float(value)
                    except ValueError:
                        continue
            else:
                value = line.split()[-1]
                try:
                    return float(value)
                except ValueError:
                    continue
    return 0.0


@pytest.fixture
def prometheus_middleware_user():
    """Create PrometheusMiddleware instance for user metrics testing."""
    return metrics.PrometheusMiddleware(app=MagicMock())


@pytest.mark.asyncio
async def test_middleware_records_user_metrics(prometheus_middleware_user):
    """Test that middleware records per-user metrics for authenticated user."""
    request = MagicMock()
    request.url.path = '/api/v1/status'
    request.method = 'GET'
    request.state.auth_user = MagicMock()
    request.state.auth_user.name = 'alice@example.com'

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    await prometheus_middleware_user.dispatch(request, call_next)

    # Check that user metric was recorded
    user_collectors = [metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL]
    user_requests = _get_metric_value('sky_apiserver_requests_by_user_total', {
        'user': 'alice@example.com',
        'method': 'GET',
        'status': '2xx'
    },
                                      collectors=user_collectors)
    assert user_requests == 1.0


@pytest.mark.asyncio
async def test_middleware_records_anonymous_user_metrics(
        prometheus_middleware_user):
    """Test that middleware records 'anonymous' for unauthenticated requests."""
    request = MagicMock(spec=['url', 'method', 'state'])
    request.url.path = '/api/v1/status'
    request.method = 'GET'
    request.state = MagicMock(spec=[])  # No auth_user attribute

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    await prometheus_middleware_user.dispatch(request, call_next)

    # Check that anonymous user metric was recorded
    user_collectors = [metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL]
    user_requests = _get_metric_value('sky_apiserver_requests_by_user_total', {
        'user': 'anonymous',
        'method': 'GET',
        'status': '2xx'
    },
                                      collectors=user_collectors)
    assert user_requests == 1.0


@pytest.mark.asyncio
async def test_middleware_user_metrics_with_basic_auth(
        prometheus_middleware_user):
    """E2E test: BasicAuthMiddleware -> PrometheusMiddleware chain records
    correct user label for basic auth.

    Verifies that when BasicAuthMiddleware authenticates via Basic auth
    and sets request.state.auth_user, PrometheusMiddleware records the
    correct username in per-user metrics.
    """
    # Create request with Basic Auth header (bob:secret)
    request = MagicMock(spec=['url', 'method', 'headers', 'state'])
    request.url = MagicMock()
    request.url.path = '/api/v1/clusters'
    request.method = 'POST'
    request.headers = {
        'authorization': 'Basic ' + base64.b64encode(b'bob:secret').decode(),
    }
    request.state = MagicMock()
    request.state.auth_user = None  # As InitializeRequestAuthUserMiddleware

    # Final handler returning success
    async def final_handler(_req):
        return fastapi.responses.JSONResponse({'status': 'ok'})

    basic_auth_middleware = BasicAuthMiddleware(app=MagicMock())

    # Chain: BasicAuth -> Prometheus -> final_handler
    async def prometheus_call_next(req):
        return await prometheus_middleware_user.dispatch(req, final_handler)

    mock_user = MagicMock()
    mock_user.name = 'bob'
    mock_user.password = 'hashed'

    with patch('sky.global_user_state.get_user_by_name',
               return_value=[mock_user]), \
         patch('sky.server.common.crypt_ctx.verify', return_value=True), \
         patch('sky.server.auth.loopback.is_loopback_request',
               return_value=False), \
         patch('sky.jobs.utils.is_consolidation_mode', return_value=False):

        response = await basic_auth_middleware.dispatch(request,
                                                        prometheus_call_next)

    assert response.status_code == 200
    # BasicAuth should have set auth_user
    assert request.state.auth_user.name == 'bob'

    # PrometheusMiddleware should have recorded the correct user label
    user_collectors = [metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL]
    user_requests = _get_metric_value('sky_apiserver_requests_by_user_total', {
        'user': 'bob',
        'method': 'POST',
        'status': '2xx'
    },
                                      collectors=user_collectors)
    assert user_requests == 1.0


@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Clean up metrics after each test to avoid interference."""
    yield
    # Clear all metrics after each test
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()
    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL.clear()


# ─────────────────────────────────────────────────────────────────────────
# WorkspaceUsageCollector tests
# ─────────────────────────────────────────────────────────────────────────


def _make_cluster_row(*,
                      workspace,
                      user_hash,
                      status_name,
                      cloud_str,
                      cpus='4',
                      memory='16',
                      disk_size=100,
                      accelerators=None,
                      launched_nodes=1,
                      cost_per_hour=2.5):
    """Build a fake cluster dict matching the shape returned by
    global_user_state.get_clusters().

    Status is a stub object exposing .name; cloud is a stub whose str()
    returns ``cloud_str``; launched_resources is a MagicMock with the
    fields the collector reads.
    """

    class _StatusStub:

        def __init__(self, name):
            self.name = name

    class _CloudStub:

        def __init__(self, name):
            self._name = name

        def __str__(self):
            return self._name

    status_obj = _StatusStub(status_name)
    cloud_obj = _CloudStub(cloud_str)

    launched_resources = MagicMock()
    launched_resources.cloud = cloud_obj
    launched_resources.cpus = cpus
    launched_resources.memory = memory
    launched_resources.disk_size = disk_size
    launched_resources.accelerators = accelerators
    launched_resources.get_cost.return_value = cost_per_hour

    handle = MagicMock()
    handle.launched_resources = launched_resources
    handle.launched_nodes = launched_nodes

    return {
        'name': 'c',
        'workspace': workspace,
        'user_hash': user_hash,
        'user_name': 'whoever',
        'status': status_obj,
        'handle': handle,
        'is_managed': False,
        'node_names': [],
    }


def _collect_to_dict(collector):
    """Run collector.collect() and return {metric_name: {labels_tuple: value}}."""
    out = {}
    for mf in collector.collect():
        for sample in mf.samples:
            # sample is a NamedTuple (name, labels, value, timestamp, exemplar)
            key = tuple(sorted(sample.labels.items()))
            out.setdefault(sample.name, {})[key] = sample.value
    return out


def test_workspace_usage_collector_counts_by_workspace_user_status_cloud():
    """Counts emit one row per (workspace, user, status, cloud) group."""
    clusters = [
        _make_cluster_row(workspace='ws-a',
                          user_hash='u1',
                          status_name='UP',
                          cloud_str='AWS'),
        _make_cluster_row(workspace='ws-a',
                          user_hash='u1',
                          status_name='UP',
                          cloud_str='AWS'),
        _make_cluster_row(workspace='ws-a',
                          user_hash='u2',
                          status_name='UP',
                          cloud_str='GCP'),
        _make_cluster_row(workspace='ws-b',
                          user_hash='u1',
                          status_name='STOPPED',
                          cloud_str='AWS'),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_clusters_count']
    # 2 clusters in ws-a/u1/UP/AWS, 1 in ws-a/u2/UP/GCP, 1 in ws-b/u1/STOPPED/AWS
    assert counts[(('cloud', 'AWS'), ('status', 'UP'), ('user', 'u1'),
                   ('workspace', 'ws-a'))] == 2.0
    assert counts[(('cloud', 'GCP'), ('status', 'UP'), ('user', 'u2'),
                   ('workspace', 'ws-a'))] == 1.0
    assert counts[(('cloud', 'AWS'), ('status', 'STOPPED'), ('user', 'u1'),
                   ('workspace', 'ws-b'))] == 1.0


def test_workspace_usage_collector_gpus_and_burn_rate_only_for_up_clusters():
    """STOPPED clusters do not contribute to GPU or burn-rate gauges."""
    clusters = [
        # UP — counted
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2,
                          cost_per_hour=10.0),
        # STOPPED — excluded from GPU/burn totals
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='STOPPED',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2,
                          cost_per_hour=10.0),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    gpu_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('user', 'u'),
               ('workspace', 'ws'))
    # 8 H100 × 2 nodes from the UP cluster only.
    assert samples['sky_clusters_gpus_in_flight'][gpu_key] == 16.0
    # $10/hr × 2 nodes from the UP cluster only.
    assert samples['sky_clusters_burn_rate_dollars'][gpu_key] == 20.0


def test_workspace_usage_collector_gpus_and_burn_rate():
    """GPUs/burn rate aggregate by gpu_type and multiply by launched_nodes."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=4,
                          cost_per_hour=10.0),
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2,
                          cost_per_hour=10.0),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    gpu_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('user', 'u'),
               ('workspace', 'ws'))
    # 8 H100 × (4 + 2) nodes = 48
    assert samples['sky_clusters_gpus_in_flight'][gpu_key] == 48.0

    burn_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('user', 'u'),
                ('workspace', 'ws'))
    # $10/hr per node × (4 + 2) nodes = $60/hr
    assert samples['sky_clusters_burn_rate_dollars'][burn_key] == 60.0


def test_workspace_usage_collector_cpu_only_cluster_uses_cpu_burn_label():
    """Clusters without accelerators are attributed to gpu_type='cpu'."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators=None,
                          launched_nodes=1,
                          cost_per_hour=0.5),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    burn_key = (('cloud', 'AWS'), ('gpu_type', 'cpu'), ('user', 'u'),
                ('workspace', 'ws'))
    assert samples['sky_clusters_burn_rate_dollars'][burn_key] == 0.5
    # No GPU samples emitted for this cluster.
    assert (('cloud', 'AWS'), ('gpu_type', 'cpu'), ('user', 'u'),
            ('workspace',
             'ws')) not in samples.get('sky_clusters_gpus_in_flight', {})


def test_workspace_usage_collector_null_labels_default():
    """Null workspace → 'default'; null user/cloud → empty string."""
    clusters = [
        _make_cluster_row(workspace=None,
                          user_hash=None,
                          status_name='UP',
                          cloud_str=''),
    ]
    # Override cloud to be None (the helper always sets one).
    clusters[0]['handle'].launched_resources.cloud = None
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_clusters_count']
    # workspace defaulted to 'default'; user and cloud are empty.
    assert counts[(('cloud', ''), ('status', 'UP'), ('user', ''),
                   ('workspace', 'default'))] == 1.0


def test_workspace_usage_collector_cost_lookup_failure_does_not_break():
    """A get_cost() exception zeroes burn rate but other metrics still emit."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8}),
    ]
    clusters[0]['handle'].launched_resources.get_cost.side_effect = (
        RuntimeError('catalog missing'))
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    # gpus_in_flight still emitted
    assert samples['sky_clusters_gpus_in_flight'][(('cloud', 'AWS'),
                                                   ('gpu_type',
                                                    'H100'), ('user', 'u'),
                                                   ('workspace', 'ws'))] == 8.0
    # burn rate is 0 (not absent) — the catch falls through to 0.0.
    assert samples['sky_clusters_burn_rate_dollars'][(('cloud', 'AWS'),
                                                      ('gpu_type',
                                                       'H100'), ('user', 'u'),
                                                      ('workspace',
                                                       'ws'))] == 0.0


def test_workspace_usage_collector_cost_returns_none_does_not_break():
    """get_cost() returning None (no exception) must not crash collect()."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8}),
    ]
    # Some clouds / missing catalog entries return None rather than raising.
    clusters[0]['handle'].launched_resources.get_cost.return_value = None
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    burn_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('user', 'u'),
                ('workspace', 'ws'))
    # None coerces to 0.0 inside the try, not a TypeError.
    assert samples['sky_clusters_burn_rate_dollars'][burn_key] == 0.0
    # GPU gauge still emitted.
    assert samples['sky_clusters_gpus_in_flight'][burn_key] == 8.0


def test_workspace_usage_collector_cache_ttl():
    """Within the cache TTL, _compute() is not called a second time."""
    with patch('sky.global_user_state.get_clusters',
               return_value=[]) as mock_get:
        collector = metrics.WorkspaceUsageCollector()
        # First scrape triggers compute.
        list(collector.collect())
        assert mock_get.call_count == 1
        # Immediate second scrape hits the cache.
        list(collector.collect())
        assert mock_get.call_count == 1


def test_managed_jobs_collector_advances_timestamp_on_failure():
    """A failing _refresh() must still advance _last_scrape_time so the
    broken query backs off for the cache TTL instead of retrying every
    scrape (retry-storm regression guard)."""
    with patch('sky.jobs.state.get_status_counts_by_workspace_user_cloud',
               side_effect=RuntimeError('db down')) as mock_q:
        collector = metrics.ManagedJobsCollector()
        list(collector.collect())
        assert mock_q.call_count == 1
        # Second immediate scrape must NOT re-query — timestamp advanced
        # even though the first refresh raised.
        list(collector.collect())
        assert mock_q.call_count == 1


# ─────────────────────────────────────────────────────────────────────────
# ManagedJobsCollector tests
# ─────────────────────────────────────────────────────────────────────────


def test_managed_jobs_collector_emits_workspace_user_status_cloud():
    """One series per (workspace, user, status, cloud) for non-terminal jobs.

    The SQL layer filters out terminal statuses, so the test fixture
    only contains non-terminal rows — terminal-filter behavior is
    covered by the SQL helper's own tests if/when they exist.
    """
    rows = [
        # (workspace, user_hash, cloud, status, count)
        ('ws-a', 'u1', 'AWS', 'ManagedJobStatus.RUNNING', 3),
        ('ws-a', 'u1', 'GCP', 'ManagedJobStatus.RUNNING', 2),
        # Pre-cloud-assignment status — cloud is NULL in DB.
        ('ws-a', 'u1', None, 'ManagedJobStatus.PENDING', 1),
        ('ws-b', 'u2', 'AWS', 'ManagedJobStatus.LAUNCHING', 4),
    ]
    with patch('sky.jobs.state.get_status_counts_by_workspace_user_cloud',
               return_value=rows):
        collector = metrics.ManagedJobsCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_managed_jobs_count']
    # RUNNING for ws-a/u1 on AWS and GCP stay separate (cloud is a label).
    assert counts[(('cloud', 'AWS'), ('status', 'ManagedJobStatus.RUNNING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 3.0
    assert counts[(('cloud', 'GCP'), ('status', 'ManagedJobStatus.RUNNING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 2.0
    # PENDING with NULL cloud — emitted with cloud="".
    assert counts[(('cloud', ''), ('status', 'ManagedJobStatus.PENDING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 1.0
    # LAUNCHING on ws-b/u2/AWS.
    assert counts[(('cloud', 'AWS'), ('status', 'ManagedJobStatus.LAUNCHING'),
                   ('user', 'u2'), ('workspace', 'ws-b'))] == 4.0


def test_managed_jobs_collector_handles_empty_db():
    with patch('sky.jobs.state.get_status_counts_by_workspace_user_cloud',
               return_value=[]):
        collector = metrics.ManagedJobsCollector()
        samples = _collect_to_dict(collector)
    # Metric family exists, just with no rows.
    assert samples.get('sky_managed_jobs_count', {}) == {}
