"""Unit tests for the metrics system."""

import base64
import os
import socket
import threading
import time
from unittest.mock import MagicMock
from unittest.mock import patch
import urllib.request

import fastapi
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import core as prom_core
from prometheus_client import generate_latest
from prometheus_client import multiprocess
import prometheus_client as prom
import pytest
import starlette.responses
import starlette.routing
from starlette.staticfiles import StaticFiles
import starlette.websockets

from sky.metrics import utils as metrics_utils
from sky.server import metrics
from sky.server import middleware_utils
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
             patch('sky.server.metrics._get_multiproc_collector') as \
                mock_multiproc, \
             patch('sky.server.metrics.generate_latest') as mock_gen:

            mock_registry_instance = MagicMock()
            mock_registry.return_value = mock_registry_instance
            mock_gen.return_value = b"# HELP multiproc_metric Test\n"

            response = metrics.metrics()

            assert isinstance(response, fastapi.Response)
            mock_registry.assert_called_once()
            mock_registry_instance.register.assert_any_call(
                mock_multiproc.return_value)
            mock_gen.assert_called_once_with(mock_registry_instance)


@pytest.fixture
def prometheus_middleware():
    """A PrometheusMiddleware wrapping a configurable ASGI app.

    The middleware is pure ASGI (it observes `http.response.start` /
    `websocket.*` messages), so the tests drive it with a scope, a receive
    and a send instead of a `dispatch(request, call_next)` call.
    """
    _clear_request_metrics()
    return _Harness()


def _clear_request_metrics():
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()
    metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS.clear()
    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL.clear()


def _responding_app(status_code=200, state_updates=None, exc=None):
    """An inner ASGI app: optionally mutates request state, then answers."""

    async def app(scope, receive, send):
        del receive
        if state_updates:
            scope['state'].update(state_updates)
        if exc is not None:
            raise exc
        await send({
            'type': 'http.response.start',
            'status': status_code,
            'headers': [],
        })
        await send({'type': 'http.response.body', 'body': b''})

    return app


def _ws_app(messages, state_updates=None, exc=None):
    """An inner ASGI app for websocket scopes that sends `messages`."""

    async def app(scope, receive, send):
        del receive
        if state_updates:
            scope['state'].update(state_updates)
        if exc is not None:
            raise exc
        for message in messages:
            await send(message)

    return app


class _Harness:
    """Runs a PrometheusMiddleware over an inner app with a synthetic scope."""

    def __init__(self):
        self.sent = []

    @staticmethod
    def _scope(scope_type, path, method, state, app):
        scope = {
            'type': scope_type,
            'path': path,
            'root_path': '',
            'query_string': b'',
            'headers': [],
            'state': dict(state or {}),
        }
        if scope_type == 'http':
            scope['method'] = method
        if app is not None:
            scope['app'] = app
        return scope

    async def run(self,
                  inner_app,
                  path,
                  method='GET',
                  state=None,
                  scope_type='http',
                  app=None):
        middleware = metrics.PrometheusMiddleware(inner_app)
        scope = self._scope(scope_type, path, method, state, app)

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(message):
            self.sent.append(message)

        await middleware(scope, receive, send)
        return scope


@pytest.mark.asyncio
async def test_middleware_successful_request(prometheus_middleware):
    """Test middleware with successful non-streaming request."""
    start_time = time.time()
    await prometheus_middleware.run(_responding_app(200), '/api/v1/status')
    end_time = time.time()

    # The response reached the client unchanged.
    assert [m['type'] for m in prometheus_middleware.sent
           ] == ['http.response.start', 'http.response.body']
    assert prometheus_middleware.sent[0]['status'] == 200

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
    await prometheus_middleware.run(_responding_app(200), '/api/v1/logs')

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
    """An exception escaping every inner layer is counted as a 5xx.

    Starlette's ServerErrorMiddleware (outside the metrics layer) turns it
    into a bare 500, so that is what the client sees.
    """
    with pytest.raises(Exception, match="Test error"):
        await prometheus_middleware.run(
            _responding_app(exc=Exception("Test error")),
            '/api/v1/failing',
            method='POST')

    # Check that 5xx metric was recorded even with exception
    total_requests = _get_metric_value('sky_apiserver_requests_total', {
        'path': '/api/v1/failing',
        'method': 'POST',
        'status': '5xx'
    })
    assert total_requests == 1.0
    # ...and attributed to an unhandled exception in the rejection counter.
    assert _rejections(
        reason=middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION,
        status='500',
        kind='http') == 1.0


@pytest.mark.asyncio
async def test_middleware_different_status_codes(prometheus_middleware):
    """Test middleware with different HTTP status codes."""
    test_cases = [
        (404, "4xx"),
        (500, "5xx"),
        (201, "2xx"),
    ]

    for status_code, expected_group in test_cases:
        await prometheus_middleware.run(_responding_app(status_code),
                                        f'/test/{status_code}')

        # Verify the correct status group was recorded
        total_requests = _get_metric_value(
            'sky_apiserver_requests_total', {
                'path': f'/test/{status_code}',
                'method': 'GET',
                'status': expected_group
            })
        assert total_requests == 1.0


@pytest.mark.asyncio
async def test_middleware_counts_a_response_answered_by_a_middleware(
        prometheus_middleware):
    """The whole point of the layer being outermost.

    An inner middleware that answers a request itself (here: the auth
    executor is saturated) never calls the next layer. The metrics layer
    sees the response anyway, counts it as a 5xx on the path, and records
    the reason the middleware stamped on the request.
    """
    inner = _responding_app(
        503,
        state_updates={
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED
        })
    await prometheus_middleware.run(inner, '/status')

    assert _get_metric_value('sky_apiserver_requests_total', {
        'path': '/status',
        'method': 'GET',
        'status': '5xx'
    }) == 1.0
    assert _rejections(
        reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
        status='503',
        kind='http') == 1.0


@pytest.mark.asyncio
async def test_middleware_does_not_count_a_rejection_without_a_reason(
        prometheus_middleware):
    """A plain 404 from the router is a request, not a rejection."""
    await prometheus_middleware.run(_responding_app(404), '/nope')
    assert _get_metric_value('sky_apiserver_requests_total', {
        'path': '/nope',
        'status': '4xx'
    }) == 1.0
    assert _rejections() == 0.0


def test_get_user_label_with_auth_user():
    """Test _get_user_label with authenticated user."""
    auth_user = MagicMock()
    auth_user.name = 'alice@example.com'

    result = metrics._get_user_label({'auth_user': auth_user})
    assert result == 'alice@example.com'


def test_get_user_label_anonymous():
    """Test _get_user_label with no auth_user."""
    assert metrics._get_user_label({}) == 'anonymous'
    assert metrics._get_user_label({'auth_user': None}) == 'anonymous'


def test_get_user_label_no_name():
    """Test _get_user_label when auth_user has no name."""
    auth_user = MagicMock()
    auth_user.name = None

    result = metrics._get_user_label({'auth_user': auth_user})
    assert result == 'anonymous'


def test_get_user_label_empty_name():
    """Test _get_user_label when auth_user has empty name."""
    auth_user = MagicMock()
    auth_user.name = ''

    result = metrics._get_user_label({'auth_user': auth_user})
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


def _handshakes(**labels):
    return _get_metric_value(
        'sky_apiserver_websocket_handshakes_total',
        labels,
        collectors=[metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL])


def _rejections(**labels):
    return _get_metric_value(
        'sky_apiserver_request_rejections_total',
        labels,
        collectors=[metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL])


@pytest.mark.asyncio
async def test_middleware_records_user_metrics(prometheus_middleware):
    """Per-user metrics use the auth user an inner middleware stored."""
    auth_user = MagicMock()
    auth_user.name = 'alice@example.com'
    # The inner (auth) middleware sets request.state.auth_user, which is
    # backed by the scope's state dict the outer metrics layer reads.
    inner = _responding_app(200, state_updates={'auth_user': auth_user})
    await prometheus_middleware.run(inner, '/api/v1/status')

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
async def test_middleware_records_anonymous_user_metrics(prometheus_middleware):
    """Test that middleware records 'anonymous' for unauthenticated requests."""
    await prometheus_middleware.run(_responding_app(200), '/api/v1/status')

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
async def test_middleware_user_metrics_with_basic_auth(prometheus_middleware):
    """E2E test: PrometheusMiddleware -> BasicAuthMiddleware chain records
    the correct user label for basic auth.

    The metrics layer is OUTSIDE the auth middleware (as in server.py).
    BasicAuthMiddleware authenticates and sets request.state.auth_user on
    the shared scope state; the outer metrics layer must still see it when
    the response passes through.
    """

    async def final_app(scope, receive, send):
        del scope, receive
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-type', b'application/json')]
        })
        await send({'type': 'http.response.body', 'body': b'{"status":"ok"}'})

    basic_auth_middleware = BasicAuthMiddleware(app=final_app)
    middleware = metrics.PrometheusMiddleware(basic_auth_middleware)

    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/api/v1/clusters',
        'root_path': '',
        'query_string': b'',
        'headers': [(b'authorization',
                     b'Basic ' + base64.b64encode(b'bob:secret'))],
        # As InitializeRequestAuthUserMiddleware would have set it.
        'state': {
            'auth_user': None
        },
    }
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        sent.append(message)

    mock_user = MagicMock()
    mock_user.name = 'bob'
    mock_user.password = 'hashed'

    with patch('sky.global_user_state.get_user_by_name',
               return_value=[mock_user]), \
         patch('sky.server.common.crypt_ctx.verify', return_value=True), \
         patch('sky.server.auth.loopback.is_loopback_request',
               return_value=False), \
         patch('sky.jobs.utils.is_consolidation_mode', return_value=False):
        await middleware(scope, receive, send)

    assert sent[0]['type'] == 'http.response.start'
    assert sent[0]['status'] == 200
    # BasicAuth should have set auth_user
    assert scope['state']['auth_user'].name == 'bob'

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
    # The api_get handler stamps request.state.request_name once it knows which
    # request is being fetched.
    inner = _responding_app(200, state_updates={'request_name': 'status'})
    await prometheus_middleware.run(inner, '/api/v1/api/get')

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
    await prometheus_middleware.run(_responding_app(200), '/api/v1/status')

    # No per-name series recorded: every _count sample stays at 0.
    registry = CollectorRegistry()
    registry.register(metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS)
    output = generate_latest(registry).decode('utf-8')
    for line in output.split('\n'):
        if line.startswith('sky_apiserver_request_get_duration_seconds_count'):
            assert float(line.split()[-1]) == 0.0


# ─────────────────────────────────────────────────────────────────────────
# WebSocket handshakes
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_websocket_accepted_handshake_is_counted(prometheus_middleware):
    inner = _ws_app([{'type': 'websocket.accept'}])
    await prometheus_middleware.run(inner, '/ws', scope_type='websocket')

    assert _handshakes(path='/ws', outcome='accepted',
                       client_status='101') == 1.0
    # A handshake is not an HTTP request.
    assert _get_metric_value('sky_apiserver_requests_total',
                             {'path': '/ws'}) == 0.0
    assert _rejections(kind='websocket') == 0.0
    assert prometheus_middleware.sent == [{'type': 'websocket.accept'}]


@pytest.mark.asyncio
async def test_websocket_rejected_by_close_is_counted_as_a_403(
        prometheus_middleware):
    """A close before the accept is what servers render as an empty 403."""
    inner = _ws_app(
        [{
            'type': 'websocket.close',
            'code': 4401,
            'reason': 'Unauthorized'
        }],
        state_updates={
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_UNAUTHORIZED
        })
    await prometheus_middleware.run(inner, '/ws', scope_type='websocket')

    assert _handshakes(path='/ws', outcome='rejected',
                       client_status='403') == 1.0
    assert _rejections(reason=middleware_utils.REJECT_REASON_UNAUTHORIZED,
                       status='403',
                       kind='websocket') == 1.0


@pytest.mark.asyncio
async def test_websocket_rejected_with_http_response_keeps_its_status(
        prometheus_middleware):
    """Rejections through the websocket.http.response extension carry the
    real status the client received."""
    inner = _ws_app(
        [{
            'type': 'websocket.http.response.start',
            'status': 503,
            'headers': []
        }, {
            'type': 'websocket.http.response.body',
            'body': b'{"detail":"busy"}'
        }],
        state_updates={
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED
        })
    await prometheus_middleware.run(inner, '/ws', scope_type='websocket')

    assert _handshakes(path='/ws', outcome='rejected',
                       client_status='503') == 1.0
    assert _rejections(
        reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
        status='503',
        kind='websocket') == 1.0


@pytest.mark.asyncio
async def test_websocket_close_without_a_reason_is_still_a_rejection(
        prometheus_middleware):
    inner = _ws_app([{'type': 'websocket.close', 'code': 1008}])
    await prometheus_middleware.run(inner, '/ws', scope_type='websocket')
    assert _rejections(reason=middleware_utils.REJECT_REASON_UNSPECIFIED,
                       status='403',
                       kind='websocket') == 1.0


@pytest.mark.asyncio
async def test_websocket_exception_before_accept_is_counted_as_a_500(
        prometheus_middleware):
    with pytest.raises(RuntimeError):
        await prometheus_middleware.run(_ws_app([], exc=RuntimeError('boom')),
                                        '/ws',
                                        scope_type='websocket')
    assert _handshakes(path='/ws', outcome='rejected',
                       client_status='500') == 1.0
    assert _rejections(
        reason=middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION,
        status='500',
        kind='websocket') == 1.0


@pytest.mark.asyncio
async def test_websocket_close_after_accept_is_not_a_rejection(
        prometheus_middleware):
    inner = _ws_app([{
        'type': 'websocket.accept'
    }, {
        'type': 'websocket.close',
        'code': 1000
    }])
    await prometheus_middleware.run(inner, '/ws', scope_type='websocket')
    assert _handshakes(path='/ws', outcome='accepted',
                       client_status='101') == 1.0
    assert _handshakes(outcome='rejected') == 0.0


# ─────────────────────────────────────────────────────────────────────────
# path label: route templates bound the cardinality
# ─────────────────────────────────────────────────────────────────────────


def _routed_app():
    """A FastAPI app with the route shapes the label resolver must handle."""
    app = fastapi.FastAPI()

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {}

    @app.get('/items/{item_id}')
    async def item(item_id: str):  # pylint: disable=unused-variable
        return {'item_id': item_id}

    @app.get('/dashboard/{full_path:path}')
    async def dashboard(full_path: str):  # pylint: disable=unused-variable
        return {'full_path': full_path}

    @app.websocket('/ws/{session_id}')
    async def ws(websocket: fastapi.WebSocket, session_id: str):  # pylint: disable=unused-variable
        del session_id
        await websocket.accept()

    sub = fastapi.FastAPI()

    @sub.get('/things/{name}')
    async def thing(name: str):  # pylint: disable=unused-variable
        return {'name': name}

    app.mount('/plugins/demo', sub)

    class _Static:
        """Stands in for StaticFiles: an ASGI app with no routes."""

        async def __call__(self, scope, receive, send):
            pass

    app.mount('/static', _Static())
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path, expected',
    [
        ('/status', '/status'),
        ('/items/42', '/items/{item_id}'),
        ('/items/someone-elses-name', '/items/{item_id}'),
        ('/dashboard/_next/static/chunks/abc.js',
         '/dashboard/{full_path:path}'),
        # Mounted sub-application with routes of its own: resolved through.
        ('/plugins/demo/things/x', '/plugins/demo/things/{name}'),
        # Mounted app without routes (static files): mount path + fixed tail.
        ('/static/css/site.css', '/static/{path}'),
        # Not an endpoint: one fixed label, never the raw path.
        ('/wp-admin/login.php', metrics.UNMATCHED_PATH_LABEL),
        # The dashboard proxy prefix is stripped before matching, so a request
        # rejected before InternalDashboardPrefixMiddleware rewrote it lands
        # in the same series as the ones that got through.
        ('/internal/dashboard/items/7', '/items/{item_id}'),
    ])
async def test_path_label_is_the_route_template(prometheus_middleware, path,
                                                expected):
    await prometheus_middleware.run(_responding_app(200),
                                    path,
                                    app=_routed_app())
    assert _get_metric_value('sky_apiserver_requests_total', {
        'path': expected,
        'method': 'GET',
        'status': '2xx'
    }) == 1.0
    if expected != path:
        assert _get_metric_value('sky_apiserver_requests_total',
                                 {'path': path}) == 0.0


@pytest.mark.asyncio
async def test_path_label_method_mismatch_still_maps_to_the_route(
        prometheus_middleware):
    """A 405 belongs to the route it hit, as in Starlette's partial match."""
    await prometheus_middleware.run(_responding_app(405),
                                    '/items/1',
                                    method='DELETE',
                                    app=_routed_app())
    assert _get_metric_value('sky_apiserver_requests_total', {
        'path': '/items/{item_id}',
        'method': 'DELETE',
        'status': '4xx'
    }) == 1.0


@pytest.mark.asyncio
async def test_path_label_for_websocket_routes(prometheus_middleware):
    inner = _ws_app([{'type': 'websocket.accept'}])
    await prometheus_middleware.run(inner,
                                    '/ws/abc123',
                                    scope_type='websocket',
                                    app=_routed_app())
    assert _handshakes(path='/ws/{session_id}', outcome='accepted') == 1.0
    await prometheus_middleware.run(inner,
                                    '/ws-not-here',
                                    scope_type='websocket',
                                    app=_routed_app())
    assert _handshakes(path=metrics.UNMATCHED_PATH_LABEL,
                       outcome='accepted') == 1.0


@pytest.mark.asyncio
async def test_path_label_falls_back_to_the_raw_path_without_a_router(
        prometheus_middleware):
    """No app on the scope (a bare ASGI callable): nothing to match against."""
    await prometheus_middleware.run(_responding_app(200), '/whatever/1')
    assert _get_metric_value('sky_apiserver_requests_total',
                             {'path': '/whatever/1'}) == 1.0


def test_route_template_cache_is_bounded():
    middleware = metrics.PrometheusMiddleware(_responding_app(200))
    app = _routed_app()
    for i in range(metrics._ROUTE_TEMPLATE_CACHE_SIZE + 10):
        scope = {'type': 'http', 'method': 'GET', 'path': f'/x/{i}', 'app': app}
        middleware._path_label(scope)
    assert len(middleware._route_template_cache) <= \
        metrics._ROUTE_TEMPLATE_CACHE_SIZE


# ─────────────────────────────────────────────────────────────────────────
# path label: end to end through the real router, routes registered late
# ─────────────────────────────────────────────────────────────────────────


class _RejectOnHeader:
    """A pure-ASGI inner middleware standing in for an auth layer.

    When the `x-reject` header is present it answers the request itself --
    403 for HTTP, a pre-accept close for a WebSocket handshake -- so the
    router never runs; otherwise it passes the request through.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers') or [])
        if scope['type'] in ('http', 'websocket') and b'x-reject' in headers:
            if scope['type'] == 'http':
                await send({
                    'type': 'http.response.start',
                    'status': 403,
                    'headers': []
                })
                await send({'type': 'http.response.body', 'body': b''})
            else:
                await send({'type': 'websocket.close', 'code': 1008})
            return
        await self.app(scope, receive, send)


async def _ok():
    return {}


async def _plain_ok(request):
    del request
    return starlette.responses.JSONResponse({})


async def _accept_and_close(websocket: fastapi.WebSocket):
    await websocket.accept()
    await websocket.close()


def _late_registered_app(static_dir) -> fastapi.FastAPI:
    """Metrics layer first, every route after it -- the production order.

    server.py adds the metrics middleware after all core and plugin
    middlewares and only then includes the core routers; plugins register
    their routes in between, and Starlette instantiates the middleware stack
    on the first request. So every route here is added after the middleware
    exists, in the shapes plugins and the core use: plain routes, routers
    with their own prefix or one given at include time (nested too), a
    mounted sub-application, a mounted plain router, WebSocket routes and
    static files.
    """
    app = fastapi.FastAPI()
    app.add_middleware(_RejectOnHeader)  # inner, like an auth middleware
    app.add_middleware(metrics.PrometheusMiddleware)  # outermost
    app.build_middleware_stack()
    # Instantiate the stack before anything is registered.
    TestClient(app).get('/__probe__')

    # (a) plain routes; an HTTP catch-all and a WebSocket route share a path.
    app.add_api_route('/items/{item_id}', _ok, methods=['GET'])
    app.add_api_route('/proxy/{name}/{path:path}', _ok, methods=['GET', 'POST'])
    app.add_api_websocket_route('/proxy/{name}/{path:path}', _accept_and_close)
    # (b) a router with its own prefix (HTTP + WebSocket), one prefixed at
    #     include time, and a prefixed router nested in a prefixed router.
    alpha = fastapi.APIRouter(prefix='/ext/api/alpha')
    alpha.add_api_route('/things/{name}', _ok, methods=['GET'])
    alpha.add_api_websocket_route('/attach', _accept_and_close)
    app.include_router(alpha)
    beta = fastapi.APIRouter()
    beta.add_api_route('/jobs/{job_id}/cancel', _ok, methods=['POST'])
    beta.add_api_websocket_route('/jobs/{job_id}/attach', _accept_and_close)
    app.include_router(beta, prefix='/ext/api/beta')
    inner = fastapi.APIRouter(prefix='/v1')
    inner.add_api_route('/status', _ok, methods=['GET'])
    gamma = fastapi.APIRouter(prefix='/ext/api/gamma')
    gamma.include_router(inner)
    app.include_router(gamma)
    # (c) a mounted sub-application with routes of its own.
    sub = fastapi.FastAPI()
    sub.add_api_route('/inner', _ok, methods=['GET'])
    sub.add_api_route('/inner/{name}', _ok, methods=['GET'])
    sub.add_api_websocket_route('/ws', _accept_and_close)
    app.mount('/sub', sub)
    # (c2) a mounted plain Starlette router.
    app.router.routes.append(
        starlette.routing.Mount(
            '/raw', routes=[starlette.routing.Route('/r/{x}', _plain_ok)]))
    # (d) a top-level WebSocket route with a parameter.
    app.add_api_websocket_route('/ws/{session_id}', _accept_and_close)
    # (e) static files.
    (static_dir / 'site.css').write_text('body {}')
    app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')
    _clear_request_metrics()
    return app


def _requests_total(**labels):
    return _get_metric_value('sky_apiserver_requests_total', labels)


@pytest.mark.parametrize(
    'method, path, status, expected',
    [
        ('GET', '/items/42', 200, '/items/{item_id}'),
        ('POST', '/proxy/dev1/a/b/c', 200, '/proxy/{name}/{path:path}'),
        ('GET', '/ext/api/alpha/things/x', 200, '/ext/api/alpha/things/{name}'),
        ('POST', '/ext/api/beta/jobs/7/cancel', 200,
         '/ext/api/beta/jobs/{job_id}/cancel'),
        ('GET', '/ext/api/gamma/v1/status', 200, '/ext/api/gamma/v1/status'),
        ('GET', '/sub/inner', 200, '/sub/inner'),
        ('GET', '/sub/inner/zz', 200, '/sub/inner/{name}'),
        ('GET', '/raw/r/1', 200, '/raw/r/{x}'),
        ('GET', '/static/site.css', 200, '/static/{path}'),
        # FastAPI does not add HEAD to GET routes: a 405, on the route it hit.
        ('HEAD', '/items/1', 405, '/items/{item_id}'),
        ('OPTIONS', '/items/1', 405, '/items/{item_id}'),
        ('GET', '/wp-admin/login.php', 404, metrics.UNMATCHED_PATH_LABEL),
    ])
def test_late_registered_routes_resolve_to_their_template(
        tmp_path, method, path, status, expected):
    app = _late_registered_app(tmp_path)
    response = TestClient(app,
                          raise_server_exceptions=False).request(method, path)
    assert response.status_code == status
    assert _requests_total(path=expected,
                           method=method,
                           status=f'{status // 100}xx') == 1.0
    if expected != path:
        # Never the raw path.
        assert _requests_total(path=path) == 0.0
    if expected != metrics.UNMATCHED_PATH_LABEL:
        # And never `unmatched` for a registered route.
        assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL) == 0.0


def test_requests_rejected_before_the_router_land_in_the_route_series(tmp_path):
    """A middleware answering first: same template as the successes."""
    app = _late_registered_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    cases = [
        ('/items/9', '/items/{item_id}'),
        ('/ext/api/alpha/things/x', '/ext/api/alpha/things/{name}'),
        ('/ext/api/gamma/v1/status', '/ext/api/gamma/v1/status'),
        ('/sub/inner/zz', '/sub/inner/{name}'),
        ('/raw/r/1', '/raw/r/{x}'),
        ('/static/site.css', '/static/{path}'),
        ('/internal/dashboard/items/9', '/items/{item_id}'),
    ]
    for path, _ in cases:
        assert client.get(path, headers={'x-reject': '1'}).status_code == 403
    for path, expected in cases:
        assert _requests_total(path=expected, method='GET',
                               status='4xx') >= 1.0, path
    assert _requests_total(path='/items/{item_id}', status='4xx') == 2.0
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL) == 0.0
    assert client.get('/wp-admin/x', headers={
        'x-reject': '1'
    }).status_code == 403
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL,
                           status='4xx') == 1.0


def test_late_registered_websocket_routes_resolve_to_their_template(tmp_path):
    app = _late_registered_app(tmp_path)
    client = TestClient(app)
    accepted = [
        ('/ws/abc123', '/ws/{session_id}'),
        ('/sub/ws', '/sub/ws'),
        ('/ext/api/alpha/attach', '/ext/api/alpha/attach'),
        ('/ext/api/beta/jobs/7/attach', '/ext/api/beta/jobs/{job_id}/attach'),
        ('/proxy/dev1/x/y', '/proxy/{name}/{path:path}'),
    ]
    for path, _ in accepted:
        with client.websocket_connect(path):
            pass
    for path, expected in accepted:
        assert _handshakes(path=expected, outcome='accepted') == 1.0, path
    # Rejected by the inner middleware before the router ran.
    with pytest.raises(starlette.websockets.WebSocketDisconnect):
        with client.websocket_connect('/sub/ws', headers={'x-reject': '1'}):
            pass
    assert _handshakes(path='/sub/ws', outcome='rejected',
                       client_status='403') == 1.0
    # No such route: the router closes the handshake; one fixed label.
    with pytest.raises(starlette.websockets.WebSocketDisconnect):
        with client.websocket_connect('/ws-not-here'):
            pass
    assert _handshakes(path=metrics.UNMATCHED_PATH_LABEL,
                       outcome='rejected') == 1.0
    assert _handshakes(path='/ws-not-here') == 0.0


def test_a_route_registered_after_its_path_was_labeled_is_resolved(tmp_path):
    """The memo is dropped when the route table grows."""
    app = _late_registered_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get('/late/1').status_code == 404
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL,
                           status='4xx') == 1.0
    app.add_api_route('/late/{n}', _ok, methods=['GET'])
    assert client.get('/late/1').status_code == 200
    assert _requests_total(path='/late/{n}', method='GET', status='2xx') == 1.0
    assert _requests_total(path='/late/1') == 0.0


# ─────────────────────────────────────────────────────────────────────────
# path label: resolution cost does not depend on the number of routes
# ─────────────────────────────────────────────────────────────────────────


def _distinct_ok():
    """A fresh handler function: one endpoint per route, as in the server."""

    async def ok():
        return {}

    return ok


def _wide_app(num_literal=300) -> fastapi.FastAPI:
    """Many parameterless routes, a few parameterized ones, one catch-all."""
    app = fastapi.FastAPI()
    app.add_middleware(metrics.PrometheusMiddleware)
    for i in range(num_literal):
        app.add_api_route(f'/literal/{i}', _distinct_ok(), methods=['GET'])
    app.add_api_route('/items/{item_id}', _distinct_ok(), methods=['GET'])
    app.add_api_route('/items/{item_id}/status',
                      _distinct_ok(),
                      methods=['GET'])
    app.add_api_route('/pools/{name}', _distinct_ok(), methods=['GET'])
    app.add_api_route('/dashboard/{full_path:path}',
                      _distinct_ok(),
                      methods=['GET'])
    return app


def _metrics_layer(app: fastapi.FastAPI) -> metrics.PrometheusMiddleware:
    """The PrometheusMiddleware instance in the app's built stack."""
    layer = app.middleware_stack.app  # inside Starlette's ServerErrorMiddleware
    assert isinstance(layer, metrics.PrometheusMiddleware)
    return layer


def _match_scope(path, method='GET', root_path=''):
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'root_path': root_path,
        'path_params': {},
    }


def test_routed_requests_resolve_from_the_dispatched_endpoint():
    """No per-path matching for routed requests: the router already chose."""
    app = _wide_app()
    client = TestClient(app)
    for i in range(50):
        assert client.get(f'/items/cluster-{i}/status').status_code == 200
    for i in range(20):
        assert client.get(f'/literal/{i}').status_code == 200
    assert _requests_total(path='/items/{item_id}/status',
                           method='GET',
                           status='2xx') == 50.0
    assert _requests_total(path='/items/cluster-1/status') == 0.0
    # The unrouted memo was never needed: every request carried an endpoint.
    assert len(_metrics_layer(app)._route_template_cache) == 0


def test_unrouted_requests_try_only_the_candidates_that_can_match(monkeypatch):
    app = _wide_app()
    routes = app.router.routes
    index = metrics._RouteIndex(routes)
    tried = []
    real = metrics._match_candidates

    def counting(candidates, match_scope):
        tried.append(len(candidates))
        return real(candidates, match_scope)

    monkeypatch.setattr(metrics, '_match_candidates', counting)
    cases = [
        # (path, expected template, max candidates tried)
        ('/literal/7', '/literal/7', 1),
        ('/items/9', '/items/{item_id}', 2),
        ('/items/9/status', '/items/{item_id}/status', 2),
        ('/pools/p1', '/pools/{name}', 1),
        ('/dashboard/a/b.js', '/dashboard/{full_path:path}', 1),
        ('/wp-admin/setup.php', None, 0),
        ('/literal/7/extra', None, 0),
    ]
    for path, expected, max_tried in cases:
        tried.clear()
        assert index.resolve_unrouted(_match_scope(path)) == expected, path
        assert tried == [len(routes)] or tried[0] <= max_tried, (path, tried)
        # And always the same answer as scanning the whole table.
        assert metrics._match_route_template(
            routes, _match_scope(path)) == expected, path


def test_unrouted_resolution_keeps_the_dispatch_order():
    """A literal route behind a parameterized one that also matches its path
    is shadowed at dispatch; the label says so too. And the other way round."""
    shadowed = fastapi.FastAPI()
    shadowed.add_api_route('/items/{item_id}', _ok, methods=['GET'])
    shadowed.add_api_route('/items/all', _ok, methods=['GET'])
    index = metrics._RouteIndex(shadowed.router.routes)
    assert index.resolve_unrouted(_match_scope('/items/all')) == \
        '/items/{item_id}'
    first = fastapi.FastAPI()
    first.add_api_route('/items/all', _ok, methods=['GET'])
    first.add_api_route('/items/{item_id}', _ok, methods=['GET'])
    index = metrics._RouteIndex(first.router.routes)
    assert index.resolve_unrouted(_match_scope('/items/all')) == '/items/all'
    assert index.resolve_unrouted(_match_scope('/items/7')) == \
        '/items/{item_id}'


def test_root_path_is_removed_before_matching():
    index = metrics._RouteIndex(_routed_app().router.routes)
    assert index.resolve_unrouted(
        _match_scope('/prefix/status', root_path='/prefix')) == '/status'
    assert index.resolve_unrouted(
        _match_scope('/prefix/items/1', root_path='/prefix')) == \
        '/items/{item_id}'


def test_one_function_under_two_templates_is_resolved_by_path():
    app = fastapi.FastAPI()
    app.add_middleware(metrics.PrometheusMiddleware)
    app.add_api_route('/a/{x}', _ok, methods=['GET'])
    app.add_api_route('/b/{x}', _ok, methods=['GET'])
    client = TestClient(app)
    assert client.get('/a/1').status_code == 200
    assert client.get('/b/2').status_code == 200
    assert _requests_total(path='/a/{x}', method='GET', status='2xx') == 1.0
    assert _requests_total(path='/b/{x}', method='GET', status='2xx') == 1.0
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL) == 0.0


def test_a_route_added_to_an_included_router_later_is_resolved():
    """Adding to an already-included router keeps the top-level table's
    size; the index versions on the whole tree. The new route reuses the
    function of an indexed one on purpose: a stale endpoint map would label
    it `/ext/first`."""
    app = fastapi.FastAPI()
    app.add_middleware(metrics.PrometheusMiddleware)
    router = fastapi.APIRouter()
    router.add_api_route('/first', _ok, methods=['GET'])
    app.include_router(router, prefix='/ext')
    client = TestClient(app)
    assert client.get('/ext/first').status_code == 200
    router.add_api_route('/late/{n}', _ok, methods=['GET'])
    response = client.get('/ext/late/1')
    if response.status_code == 404:
        pytest.skip('this FastAPI flattens included routers on include')
    assert response.status_code == 200
    assert _requests_total(path='/ext/late/{n}', method='GET',
                           status='2xx') == 1.0
    assert _requests_total(path='/ext/late/1') == 0.0
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL) == 0.0


def test_a_foreign_endpoint_rebuilds_the_index_once(monkeypatch):
    app = _wide_app()
    layer = metrics.PrometheusMiddleware(_responding_app(200))
    builds = []
    real_init = metrics._RouteIndex.__init__

    def counting_init(self, routes):
        builds.append(len(routes))
        real_init(self, routes)

    monkeypatch.setattr(metrics._RouteIndex, '__init__', counting_init)
    foreign = object()  # an endpoint no route in the table owns
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/literal/1',
        'app': app,
        'endpoint': foreign,
    }
    # Falls back to matching the path; one rebuild for the unknown endpoint.
    assert layer._path_label(dict(scope)) == '/literal/1'
    assert len(builds) == 2
    assert layer._path_label(dict(scope)) == '/literal/1'
    scope['path'] = '/literal/2'
    assert layer._path_label(dict(scope)) == '/literal/2'
    assert len(builds) == 2


@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Clean up metrics after each test to avoid interference."""
    yield
    # Clear all metrics after each test
    _clear_request_metrics()


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


def test_multiproc_collector_wrapped_once_and_shared(tmp_path):
    """The multiprocess merge is wrapped, and shared across scrapes."""
    with patch.object(metrics, '_multiproc_collector', None), \
         patch.object(metrics, '_resilient_collectors', []), \
         patch.dict(os.environ,
                    {'PROMETHEUS_MULTIPROC_DIR': str(tmp_path)}):
        first = metrics._get_multiproc_collector()
        second = metrics._get_multiproc_collector()

        assert first is second
        assert isinstance(first, metrics.ResilientCollector)
        assert isinstance(first._wrapped, multiprocess.MultiProcessCollector)
        assert metrics._resilient_collectors == [first]


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
