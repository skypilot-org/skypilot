"""Unit tests for the metrics system."""

import base64
import os
import socket
import threading
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import urllib.request

import fastapi
from prometheus_client import CollectorRegistry
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import core as prom_core
from prometheus_client import generate_latest
import prometheus_client as prom
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
        # The daemon reaps on a worker thread (asyncio.to_thread), so
        # yielding a fixed number of times races that thread getting
        # scheduled -- under load it loses. Wait for the first tick.
        deadline = time.time() + 10
        while call_count['n'] < 1 and time.time() < deadline:
            await asyncio.sleep(0.01)
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
    metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS.clear()

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


@pytest.mark.asyncio
async def test_middleware_records_api_get_duration_by_name(
        prometheus_middleware):
    """/api/get latency is recorded under the request name the handler stamps."""
    request = MagicMock()
    request.url.path = '/api/v1/api/get'
    request.method = 'GET'
    request.state.auth_user = None
    # The api_get handler stamps request.state.request_name once it knows which
    # request is being fetched.
    request.state.request_name = 'status'

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    await prometheus_middleware.dispatch(request, call_next)

    get_collectors = [metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS]
    duration_count = _get_metric_value(
        'sky_apiserver_request_get_duration_seconds_count', {
            'name': 'status',
            'status': '2xx'
        },
        collectors=get_collectors)
    assert duration_count == 1.0


@pytest.mark.asyncio
async def test_middleware_no_api_get_duration_without_name(
        prometheus_middleware):
    """No per-name /api/get series is recorded when the name is not stamped."""
    request = MagicMock(spec=['url', 'method', 'state'])
    request.url.path = '/api/v1/status'
    request.method = 'GET'
    request.state = MagicMock(
        spec=[])  # No request_name / auth_user attributes.

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    await prometheus_middleware.dispatch(request, call_next)

    # No per-name series recorded: every _count sample stays at 0.
    registry = CollectorRegistry()
    registry.register(metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS)
    output = generate_latest(registry).decode('utf-8')
    for line in output.split('\n'):
        if line.startswith('sky_apiserver_request_get_duration_seconds_count'):
            assert float(line.split()[-1]) == 0.0


@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Clean up metrics after each test to avoid interference."""
    yield
    # Clear all metrics after each test
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()
    metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS.clear()
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
                      cost_per_hour=2.5,
                      name='c',
                      is_managed=False):
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
        'name': name,
        'workspace': workspace,
        'user_hash': user_hash,
        'user_name': 'whoever',
        'status': status_obj,
        'handle': handle,
        'is_managed': is_managed,
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
    # All are kind="cluster" (name 'c', not managed/controller).
    assert counts[(('cloud', 'AWS'), ('kind', 'cluster'), ('status', 'UP'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 2.0
    assert counts[(('cloud', 'GCP'), ('kind', 'cluster'), ('status', 'UP'),
                   ('user', 'u2'), ('workspace', 'ws-a'))] == 1.0
    assert counts[(('cloud', 'AWS'), ('kind', 'cluster'), ('status', 'STOPPED'),
                   ('user', 'u1'), ('workspace', 'ws-b'))] == 1.0


def test_workspace_usage_collector_gpus_only_for_up_clusters():
    """STOPPED clusters do not contribute to gpus_in_flight."""
    clusters = [
        # UP — counted
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2),
        # STOPPED — excluded
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='STOPPED',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    gpu_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('kind', 'cluster'),
               ('user', 'u'), ('workspace', 'ws'))
    # 8 H100 × 2 nodes from the UP cluster only.
    assert samples['sky_clusters_gpus_in_flight'][gpu_key] == 16.0


def test_workspace_usage_collector_gpus_sum_over_nodes():
    """GPUs aggregate by gpu_type and multiply by launched_nodes."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=4),
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators={'H100': 8},
                          launched_nodes=2),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    gpu_key = (('cloud', 'AWS'), ('gpu_type', 'H100'), ('kind', 'cluster'),
               ('user', 'u'), ('workspace', 'ws'))
    # 8 H100 × (4 + 2) nodes = 48
    assert samples['sky_clusters_gpus_in_flight'][gpu_key] == 48.0


def test_workspace_usage_collector_cpu_only_cluster_emits_no_gpu():
    """Clusters without accelerators emit no sky_clusters_gpus_in_flight row."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          accelerators=None,
                          launched_nodes=1),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    assert samples.get('sky_clusters_gpus_in_flight', {}) == {}


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
    assert counts[(('cloud', ''), ('kind', 'cluster'), ('status', 'UP'),
                   ('user', ''), ('workspace', 'default'))] == 1.0


def test_workspace_usage_collector_kind_label():
    """Clusters are classified cluster / managed_job / controller."""
    clusters = [
        # Plain sky launch cluster.
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          name='my-cluster'),
        # Managed-job backing cluster.
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          name='managed-x',
                          is_managed=True),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_clusters_count']
    assert counts[(('cloud', 'AWS'), ('kind', 'cluster'), ('status', 'UP'),
                   ('user', 'u'), ('workspace', 'ws'))] == 1.0
    assert counts[(('cloud', 'AWS'), ('kind', 'managed_job'), ('status', 'UP'),
                   ('user', 'u'), ('workspace', 'ws'))] == 1.0


def test_workspace_usage_collector_kind_controller():
    """A controller cluster name classifies as kind='controller'."""
    clusters = [
        _make_cluster_row(workspace='ws',
                          user_hash='u',
                          status_name='UP',
                          cloud_str='AWS',
                          name='sky-jobs-controller-abc'),
    ]
    with patch('sky.global_user_state.get_clusters', return_value=clusters), \
         patch('sky.utils.controller_utils.Controllers.from_name',
               return_value=object()):
        collector = metrics.WorkspaceUsageCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_clusters_count']
    assert counts[(('cloud', 'AWS'), ('kind', 'controller'), ('status', 'UP'),
                   ('user', 'u'), ('workspace', 'ws'))] == 1.0


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
    """One series per (workspace, user, status, cloud) — includes both
    active and terminal statuses (the SQL helper no longer filters)."""
    rows = [
        # (workspace, user_hash, cloud, status, count)
        ('ws-a', 'u1', 'AWS', 'ManagedJobStatus.RUNNING', 3),
        ('ws-a', 'u1', 'GCP', 'ManagedJobStatus.RUNNING', 2),
        # Pre-cloud-assignment status — cloud is NULL in DB.
        ('ws-a', 'u1', None, 'ManagedJobStatus.PENDING', 1),
        # Terminal statuses are also included — operators want
        # success/failure visibility (FAR Slack item 3).
        ('ws-a', 'u1', 'AWS', 'ManagedJobStatus.SUCCEEDED', 12),
        ('ws-a', 'u1', 'AWS', 'ManagedJobStatus.FAILED', 2),
    ]
    with patch('sky.jobs.state.get_status_counts_by_workspace_user_cloud',
               return_value=rows):
        collector = metrics.ManagedJobsCollector()
        samples = _collect_to_dict(collector)

    counts = samples['sky_managed_jobs_count']
    # Active states.
    assert counts[(('cloud', 'AWS'), ('status', 'ManagedJobStatus.RUNNING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 3.0
    assert counts[(('cloud', 'GCP'), ('status', 'ManagedJobStatus.RUNNING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 2.0
    assert counts[(('cloud', ''), ('status', 'ManagedJobStatus.PENDING'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 1.0
    # Terminal states surfaced too.
    assert counts[(('cloud', 'AWS'), ('status', 'ManagedJobStatus.SUCCEEDED'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 12.0
    assert counts[(('cloud', 'AWS'), ('status', 'ManagedJobStatus.FAILED'),
                   ('user', 'u1'), ('workspace', 'ws-a'))] == 2.0


def test_managed_jobs_collector_handles_empty_db():
    with patch('sky.jobs.state.get_status_counts_by_workspace_user_cloud',
               return_value=[]):
        collector = metrics.ManagedJobsCollector()
        samples = _collect_to_dict(collector)
    # Metric family exists, just with no rows.
    assert samples.get('sky_managed_jobs_count', {}) == {}


def test_sqlite_db_size_collector_no_files(tmp_path, monkeypatch):
    """No SQLite files on disk (e.g. Postgres backend) -> no series."""
    monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))
    collector = metrics.SqliteDBSizeCollector()
    samples = _collect_to_dict(collector)
    assert samples.get('sky_apiserver_sqlite_db_size_bytes', {}) == {}


def test_sqlite_db_size_collector_reports_existing_dbs(tmp_path, monkeypatch):
    """Existing DB files are reported with WAL/SHM sidecars included."""
    monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))
    sky_dir = tmp_path / '.sky'
    (sky_dir / 'api_server').mkdir(parents=True)
    (sky_dir / 'state.db').write_bytes(b'x' * 100)
    # WAL/SHM sidecars count toward the db's footprint.
    (sky_dir / 'state.db-wal').write_bytes(b'x' * 40)
    (sky_dir / 'state.db-shm').write_bytes(b'x' * 10)
    (sky_dir / 'spot_jobs.db').write_bytes(b'x' * 7)
    (sky_dir / 'api_server' / 'requests.db').write_bytes(b'x' * 55)
    # A sidecar without its main file must not create a series.
    (sky_dir / 'config.db-wal').write_bytes(b'x' * 5)

    collector = metrics.SqliteDBSizeCollector()
    sizes = _collect_to_dict(collector)['sky_apiserver_sqlite_db_size_bytes']

    assert sizes == {
        (('db', 'state'),): 150.0,
        (('db', 'spot_jobs'),): 7.0,
        (('db', 'requests'),): 55.0,
    }


# ── ResilientCollector ──────────────────────────────────────────────


class _ControlledCollector:
    """Collector whose behavior on each collect() call is scripted.

    Script entries: ``'ok:<value>'`` yields a gauge with that value,
    ``'hang'`` blocks until ``release`` is set (then yields 0.0),
    ``'raise'`` raises. The last entry repeats for further calls.
    """

    def __init__(self, script):
        self._script = script
        self.calls = 0
        self.hang_started = threading.Event()
        self.release = threading.Event()

    def collect(self):
        action = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        value = 0.0
        if action == 'hang':
            self.hang_started.set()
            self.release.wait(timeout=30)
        elif action == 'raise':
            raise RuntimeError('scripted failure')
        else:
            value = float(action.split(':', 1)[1])
        family = prom_core.GaugeMetricFamily('test_resilient_gauge', 'test')
        family.add_metric([], value)
        yield family


def _wait_until(predicate, timeout=10.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _gauge_value(families):
    for family in families:
        for sample in family.samples:
            if sample.name == 'test_resilient_gauge':
                return sample.value
    return None


def test_resilient_collector_scrape_not_blocked_by_hung_refresh():
    wrapped = _ControlledCollector(['hang'])
    collector = metrics.ResilientCollector(wrapped, ttl_seconds=0)
    try:
        start = time.time()
        assert not list(collector.collect())
        assert time.time() - start < 5.0
        assert _wait_until(wrapped.hang_started.is_set)
        # Repeated scrapes while the refresh is hung neither block nor
        # stack additional refreshes (single in-flight).
        for _ in range(5):
            assert not list(collector.collect())
        assert wrapped.calls == 1
    finally:
        wrapped.release.set()
    # Once the hung refresh finally returns, its data is served.
    assert _wait_until(lambda: _gauge_value(collector.collect()) is not None)


def test_resilient_collector_serves_stale_snapshot_while_hung():
    wrapped = _ControlledCollector(['ok:1', 'hang'])
    collector = metrics.ResilientCollector(wrapped, ttl_seconds=0)
    try:
        list(collector.collect())  # Triggers the first (successful) refresh.
        assert _wait_until(lambda: collector.last_success_time() > 0)
        # This scrape serves the snapshot and triggers the hanging refresh.
        assert _gauge_value(collector.collect()) == 1.0
        assert _wait_until(wrapped.hang_started.is_set)
        # Stale-but-served while hung; last success does not advance.
        assert _gauge_value(collector.collect()) == 1.0
        assert wrapped.calls == 2
    finally:
        wrapped.release.set()


def test_resilient_collector_refresh_error_keeps_snapshot_and_retries():
    wrapped = _ControlledCollector(['ok:1', 'raise', 'ok:2'])
    collector = metrics.ResilientCollector(wrapped, ttl_seconds=0)
    list(collector.collect())
    assert _wait_until(lambda: collector.last_success_time() > 0)
    first_success = collector.last_success_time()
    list(collector.collect())  # Triggers the failing refresh.
    assert _wait_until(lambda: wrapped.calls == 2)
    # The failure left the old snapshot in place and did not advance the
    # success time. Checked before the next collect(): that one triggers
    # the recovering refresh, which may advance the success time at any
    # point after it.
    assert collector.last_success_time() == first_success
    assert _gauge_value(collector.collect()) == 1.0
    # The previous collect() already triggered the third (recovering)
    # refresh; the in-flight flag was not left stuck by the failure.
    assert _wait_until(lambda: _gauge_value(collector.collect()) == 2.0)


def test_resilient_collector_describe_never_calls_collect():

    class _NoDescribe:

        def __init__(self):
            self.collected = False

        def collect(self):
            self.collected = True
            yield prom_core.GaugeMetricFamily('x', 'x')

    wrapped = _NoDescribe()
    collector = metrics.ResilientCollector(wrapped)
    assert not list(collector.describe())
    assert not wrapped.collected
    # With a wrapped describe(), it is delegated.
    described = metrics.ResilientCollector(_ControlledCollector(['ok:1']))
    described._wrapped.describe = lambda: iter(
        [prom_core.GaugeMetricFamily('described', 'd')])
    assert [f.name for f in described.describe()] == ['described']


def test_collector_health_active_flips_on_staleness():
    wrapped = _ControlledCollector(['ok:1', 'hang'])
    collector = metrics.ResilientCollector(wrapped,
                                           ttl_seconds=0,
                                           max_staleness_seconds=0.2)
    health = metrics.CollectorHealthCollector()

    def health_samples():
        samples = {}
        for family in health.collect():
            for sample in family.samples:
                samples[sample.name] = sample.value
        return samples

    with patch.object(metrics, '_resilient_collectors', [collector]):
        try:
            # Never refreshed yet: inactive, zero timestamp.
            samples = health_samples()
            assert samples['sky_apiserver_metrics_collector_active'] == 0.0
            assert samples[
                'sky_apiserver_metrics_collector_last_success_timestamp_'
                'seconds'] == 0.0
            list(collector.collect())
            assert _wait_until(lambda: collector.last_success_time() > 0)
            samples = health_samples()
            assert samples['sky_apiserver_metrics_collector_active'] == 1.0
            # Trigger the hanging refresh and outwait max_staleness.
            list(collector.collect())
            assert _wait_until(wrapped.hang_started.is_set)
            assert _wait_until(lambda: health_samples()[
                'sky_apiserver_metrics_collector_active'] == 0.0)
        finally:
            wrapped.release.set()


def test_wrap_collector_dedupes_health_names():
    with patch.object(metrics, '_resilient_collectors', []):
        first = metrics._wrap_collector(_ControlledCollector(['ok:1']))
        second = metrics._wrap_collector(_ControlledCollector(['ok:1']))
        assert first.name == '_ControlledCollector'
        assert second.name == '_ControlledCollector-2'


def test_metrics_endpoint_responsive_with_hung_plugin_collector():
    """A plugin collector hung on its data source (e.g. DB outage) must
    not hang the /metrics scrape: the endpoint responds promptly and the
    health gauge reports the collector as inactive."""
    if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
        del os.environ['PROMETHEUS_MULTIPROC_DIR']
    hung = _ControlledCollector(['hang'])
    metrics.register_plugin_collector(hung)
    wrapper = metrics._plugin_collectors[-1]
    try:
        start = time.time()
        response = metrics.metrics()
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 10.0
        assert _wait_until(hung.hang_started.is_set)
        body = metrics.metrics().body.decode()
        assert ('sky_apiserver_metrics_collector_active'
                '{collector="_ControlledCollector"} 0.0') in body
    finally:
        hung.release.set()
        prom.REGISTRY.unregister(wrapper)
        metrics._plugin_collectors.remove(wrapper)
        metrics._resilient_collectors.remove(wrapper)


def _live_thread_named(name):
    for thread in threading.enumerate():
        if thread.name == name:
            return thread
    return None


def test_start_metrics_server_serves_from_its_own_thread(monkeypatch):
    """The metrics app must be served from a thread, hence an event loop,
    of its own.

    A sync endpoint is dispatched through the serving loop's *default*
    anyio thread limiter, so sharing a loop with the API server's
    background daemons makes a scrape queue behind however much
    ``anyio`` thread work they have outstanding -- enough to push it past
    the Prometheus scrape timeout and flap the target to ``up == 0``.
    Keeping the server on a private thread is what decouples them, so
    assert on the thread rather than only on the response.
    """
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)
    # Port 0: let the kernel pick, then read the bound port back, so the
    # test cannot lose a race for a hardcoded port.
    server = metrics.start_metrics_server('127.0.0.1', 0)
    try:
        assert _wait_until(lambda: server.started), 'server never started'
        thread = _live_thread_named('metrics-server')
        assert thread is not None, 'no dedicated metrics-server thread'
        assert thread is not threading.main_thread()

        port = server.servers[0].sockets[0].getsockname()[1]
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics',
                                    timeout=30) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers['content-type'] == CONTENT_TYPE_LATEST
        assert body  # the collectors produced something
    finally:
        metrics.stop_metrics_server()
    assert _wait_until(lambda: not thread.is_alive()), 'thread did not exit'


def test_stop_metrics_server_without_start_is_noop():
    """Shutdown runs unconditionally, including when metrics are disabled
    and no server was ever started."""
    saved = metrics._metrics_server
    metrics._metrics_server = None
    try:
        metrics.stop_metrics_server()
    finally:
        metrics._metrics_server = saved


def test_metrics_server_reports_a_bind_failure(monkeypatch):
    """A metrics server that never came up must say so.

    uvicorn answers an unbindable port with sys.exit(1), i.e. SystemExit,
    which is not an Exception and which threading.excepthook drops
    silently -- so the thread would just vanish and the scrape target
    would look down for no stated reason.
    """
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with patch.object(metrics, 'logger') as mock_logger:
            server = metrics.start_metrics_server('127.0.0.1', port)
            assert _wait_until(lambda: _live_thread_named('metrics-server') is
                               None), ('thread outlived the failed bind')
            assert not server.started
            assert mock_logger.error.called, 'bind failure was not reported'
    finally:
        blocker.close()
