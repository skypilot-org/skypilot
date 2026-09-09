"""Client-visible failures are counted, whichever layer produced them.

Regression tests for the metrics layer being the OUTERMOST middleware. The
production incident these come from: the auth executor saturated and every
request got a 503 from the auth middleware; the request counter, sitting
inside that middleware, never saw one of them, so dashboards showed
successful traffic *dropping* instead of errors *rising*, and no error-rate
alert fired. Rejected WebSocket handshakes were not counted at all and
reached ssh clients as a bare 403 ("please log in again").

These tests wire the REAL auth-proxy middleware and the REAL metrics
middleware in the production order onto a small FastAPI app, stub the auth
DB call the way the incident broke it (executor exhausted / deadline hit),
and check what the client sees and what the counters recorded.
"""
import asyncio
import os
import subprocess
import sys
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest
import starlette.middleware.base
from starlette.testclient import WebSocketDenialResponse

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.server import config as server_config
from sky.server import metrics
from sky.server import middleware_utils
from sky.server import server
from sky.server.auth import db_lookup

_AUTH_HEADER = {'X-Auth-Request-Email': 'bob@example.com'}
_PROXY_CONFIG = server_config.ExternalProxyConfig(
    enabled=True, header_name='X-Auth-Request-Email', header_format='plaintext')


def _sample(counter, **labels) -> float:
    """Current value of the `counter` child with exactly these labels."""
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


@pytest.fixture
def clear_metrics():
    for counter in (metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL):
        counter.clear()
    yield
    for counter in (metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL):
        counter.clear()


def _app() -> fastapi.FastAPI:
    """A small app with the production middleware order.

    server.py adds the metrics middleware last, i.e. outermost; the auth
    middlewares sit inside it. `InitializeRequestAuthUserMiddleware` is what
    makes `request.state.auth_user` exist for `AuthProxyMiddleware`.
    """
    app = fastapi.FastAPI()

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {'ok': True}

    @app.websocket('/kubernetes-pod-ssh-proxy')
    async def ssh_proxy(  # pylint: disable=unused-variable
            websocket: fastapi.WebSocket):
        await websocket.accept()
        await websocket.send_text('hello')
        await websocket.close()

    with mock.patch.object(server_config,
                           'load_external_proxy_config',
                           return_value=_PROXY_CONFIG):
        app.add_middleware(server.AuthProxyMiddleware)
        app.add_middleware(server.InitializeRequestAuthUserMiddleware)
        app.add_middleware(metrics.PrometheusMiddleware)
        # Middleware classes are instantiated when the stack is built.
        app.build_middleware_stack()
        # Force instantiation now, while the config is patched.
        TestClient(app).get('/status')
    return app


def _client(app: fastapi.FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.usefixtures('clear_metrics')
def test_baseline_success_is_counted_on_the_route_template():
    app = _app()
    for counter in (metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL):
        counter.clear()
    with mock.patch.object(db_lookup,
                           'call_with_deadline',
                           new=mock.AsyncMock(return_value=False)), \
         mock.patch.object(db_lookup,
                           'ensure_role_for_authenticated_user',
                           new=mock.AsyncMock(return_value=None)):
        response = _client(app).get('/status', headers=_AUTH_HEADER)
    assert response.status_code == 200
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/status',
                   method='GET',
                   status='2xx') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
                   user='bob@example.com',
                   status='2xx') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


@pytest.mark.usefixtures('clear_metrics')
@pytest.mark.parametrize('failure, reason, detail_fragment', [
    (exceptions.ConcurrentWorkerExhaustedError('32 of 32'),
     middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
     'exhausted its concurrent worker limit'),
    (asyncio.TimeoutError(), middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT,
     'Authentication lookup timed out'),
])
def test_a_middleware_503_is_counted_with_its_reason(failure, reason,
                                                     detail_fragment):
    """The incident's shape: the auth upsert cannot get a thread (or its
    deadline elapses), the auth middleware answers 503 itself, and the route
    handler never runs. The client sees a 503; so must the counters."""
    app = _app()
    for counter in (metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL):
        counter.clear()
    with mock.patch.object(db_lookup,
                           'call_with_deadline',
                           new=mock.AsyncMock(side_effect=failure)):
        response = _client(app).get('/status', headers=_AUTH_HEADER)

    assert response.status_code == 503
    assert detail_fragment in response.json()['detail']
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/status',
                   method='GET',
                   status='5xx') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=reason,
                   status='503',
                   kind='http') == 1.0
    # Unauthenticated at the time of the answer: the per-user series says so.
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
                   user='anonymous',
                   status='5xx') == 1.0


@pytest.mark.usefixtures('clear_metrics')
def test_a_rejected_websocket_handshake_is_counted_and_carries_its_status():
    """ssh goes over a WebSocket. During the incident every handshake was
    refused; the refusal reached the client as an empty HTTP 403 (which the
    ssh client reports as "please log in again") and no counter moved."""
    app = _app()
    for counter in (metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL):
        counter.clear()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')
    with mock.patch.object(db_lookup,
                           'call_with_deadline',
                           new=mock.AsyncMock(side_effect=failure)):
        with pytest.raises(WebSocketDenialResponse) as denied:
            with _client(app).websocket_connect('/kubernetes-pod-ssh-proxy',
                                                headers=_AUTH_HEADER):
                pass

    # The client gets the real status and the server's explanation.
    assert denied.value.status_code == 503
    assert b'exhausted its concurrent worker limit' in denied.value.content
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path='/kubernetes-pod-ssh-proxy',
                   outcome='rejected',
                   client_status='503') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
                   status='503',
                   kind='websocket') == 1.0


@pytest.mark.usefixtures('clear_metrics')
def test_an_accepted_websocket_handshake_is_counted():
    app = _app()
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL.clear()
    with mock.patch.object(db_lookup,
                           'call_with_deadline',
                           new=mock.AsyncMock(return_value=False)), \
         mock.patch.object(db_lookup,
                           'ensure_role_for_authenticated_user',
                           new=mock.AsyncMock(return_value=None)):
        with _client(app).websocket_connect('/kubernetes-pod-ssh-proxy',
                                            headers=_AUTH_HEADER) as websocket:
            assert websocket.receive_text() == 'hello'
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path='/kubernetes-pod-ssh-proxy',
                   outcome='accepted',
                   client_status='101') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   outcome='rejected') == 0.0


@pytest.mark.usefixtures('clear_metrics')
def test_route_level_executor_exhaustion_is_counted_with_its_reason():
    """The other exhaustion 503: raised inside a route handler and converted
    by the app-level exception handler. Always was counted as a 5xx; now it
    is attributed too."""
    app = fastapi.FastAPI()
    app.add_exception_handler(exceptions.ConcurrentWorkerExhaustedError,
                              server.handle_concurrent_worker_exhausted_error)

    @app.get('/logs')
    async def logs():  # pylint: disable=unused-variable
        raise exceptions.ConcurrentWorkerExhaustedError('128 of 128')

    app.add_middleware(metrics.PrometheusMiddleware)
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.clear()
    response = _client(app).get('/logs')
    assert response.status_code == 503
    assert _sample(
        metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
        reason=middleware_utils.REJECT_REASON_REQUEST_WORKER_EXHAUSTED,
        status='503',
        kind='http') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/logs',
                   status='5xx') == 1.0


@pytest.mark.usefixtures('clear_metrics')
def test_unauthenticated_scanner_paths_do_not_create_series():
    """Now that rejected requests are counted, their paths must not become
    labels: an unauthenticated client can send any path it likes."""
    app = _app()
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')
    with mock.patch.object(db_lookup,
                           'call_with_deadline',
                           new=mock.AsyncMock(side_effect=failure)):
        client = _client(app)
        for i in range(5):
            assert client.get(f'/wp-admin/{i}.php',
                              headers=_AUTH_HEADER).status_code == 503
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path=metrics.UNMATCHED_PATH_LABEL,
                   status='5xx') == 5.0
    for i in range(5):
        assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                       path=f'/wp-admin/{i}.php') == 0.0


@pytest.mark.usefixtures('clear_metrics')
@pytest.mark.parametrize('helper', [
    db_lookup.db_timeout_response,
    db_lookup.worker_exhausted_response,
])
def test_a_middleware_calling_the_503_helpers_bare_still_answers_503(helper):
    """Compatibility with middlewares that call the helpers with no request.

    Out-of-tree middlewares do `return db_lookup.db_timeout_response()`. If the
    request became a required argument, that line would raise inside the
    middleware and reach the client as a bare 500 (not retried) on exactly the
    failure path these helpers exist for. The 503 must survive; only the
    per-reason attribution is missing until the caller passes the request.
    """
    app = fastapi.FastAPI()

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {'ok': True}

    class BareCaller(starlette.middleware.base.BaseHTTPMiddleware):

        async def dispatch(self, request, call_next):
            del request, call_next
            return helper()

    app.add_middleware(BareCaller)
    app.add_middleware(metrics.PrometheusMiddleware)

    response = _client(app).get('/status')
    assert response.status_code == 503
    assert 'detail' in response.json()
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/status',
                   status='5xx') == 1.0
    # Not a crash: nothing escaped to Starlette's error handler.
    assert _sample(
        metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
        reason=middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION) == 0.0
    # And nothing attributed either: the caller passed no request.
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   kind='http') == 0.0


def test_the_real_server_registers_the_metrics_layer_outermost():
    """Order regression guard on sky.server.server itself.

    Imported in a subprocess because the metrics gate is read at import time
    and the module is imported once per process. `user_middleware[0]` is the
    outermost layer (Starlette wraps the list in reverse).
    """
    code = (
        'from sky.server import server\n'
        'print(",".join(m.cls.__name__ for m in server.app.user_middleware))')
    env = dict(os.environ)
    env['SKY_API_SERVER_METRICS_ENABLED'] = 'true'
    out = subprocess.run([sys.executable, '-c', code],
                         env=env,
                         check=True,
                         capture_output=True,
                         text=True,
                         timeout=300).stdout.strip().splitlines()[-1]
    names = out.split(',')
    assert names[0] == 'PrometheusMiddleware', names
    # ...and nothing else in the stack is a second copy of it.
    assert names.count('PrometheusMiddleware') == 1, names
