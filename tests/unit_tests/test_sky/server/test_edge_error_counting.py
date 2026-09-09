"""Responses produced by a middleware are counted at the edge.

Regression tests for the metrics middleware being the OUTERMOST middleware.
The production incident these come from: the auth executor saturated and
every request got a 503 from the auth middleware; the request counter,
sitting inside that middleware, never saw one of them, so dashboards showed
successful traffic *dropping* instead of errors *rising*, and no error-rate
alert fired. Rejected WebSocket handshakes were not counted at all.

These tests wire the REAL auth-proxy middleware and the REAL metrics
middleware in the production order onto a small FastAPI app, stub the auth
DB call the way the incident broke it (executor exhausted / deadline hit),
and check that what the client sees is unchanged and that the counters now
record it.
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
from starlette.websockets import WebSocketDisconnect

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
_COUNTERS = (
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
)


def _sample(counter, **labels) -> float:
    """Current value of the `counter` children matching these labels."""
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


@pytest.fixture(autouse=True)
def clear_metrics():
    for counter in _COUNTERS:
        counter.clear()
    yield
    for counter in _COUNTERS:
        counter.clear()


def _routes(app: fastapi.FastAPI) -> None:

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {'ok': True}

    @app.websocket('/kubernetes-pod-ssh-proxy')
    async def ssh_proxy(  # pylint: disable=unused-variable
            websocket: fastapi.WebSocket):
        await websocket.accept()
        await websocket.send_text('hello')
        await websocket.close()


def _app(metrics_outermost: bool = True) -> fastapi.FastAPI:
    """A small app with the production middleware order.

    server.py adds the metrics middleware last, i.e. outermost; the auth
    middlewares sit inside it. `InitializeRequestAuthUserMiddleware` is what
    makes `request.state.auth_user` exist for `AuthProxyMiddleware`. With
    `metrics_outermost=False` the metrics middleware is added first, i.e.
    innermost: the order before this change.
    """
    app = fastapi.FastAPI()
    _routes(app)

    with mock.patch.object(server_config,
                           'load_external_proxy_config',
                           return_value=_PROXY_CONFIG):
        if not metrics_outermost:
            app.add_middleware(metrics.PrometheusMiddleware)
        app.add_middleware(server.AuthProxyMiddleware)
        app.add_middleware(server.InitializeRequestAuthUserMiddleware)
        if metrics_outermost:
            app.add_middleware(metrics.PrometheusMiddleware)
        # Middleware classes are instantiated when the stack is built; force
        # that now, while the config is patched.
        app.build_middleware_stack()
        with _healthy_auth():
            TestClient(app).get('/status')
    for counter in _COUNTERS:
        counter.clear()
    return app


def _client(app: fastapi.FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _healthy_auth():
    return mock.patch.multiple(
        db_lookup,
        call_with_deadline=mock.AsyncMock(return_value=False),
        ensure_role_for_authenticated_user=mock.AsyncMock(return_value=None))


def _broken_auth(failure):
    return mock.patch.object(db_lookup,
                             'call_with_deadline',
                             new=mock.AsyncMock(side_effect=failure))


def test_baseline_success_is_counted_as_before():
    app = _app()
    with _healthy_auth():
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


@pytest.mark.parametrize('failure, reason, detail_fragment, retry_after', [
    (exceptions.ConcurrentWorkerExhaustedError('32 of 32'),
     middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
     'exhausted its concurrent worker limit', False),
    (asyncio.TimeoutError(), middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT,
     'Authentication lookup timed out', True),
])
def test_a_middleware_503_is_counted_with_its_reason(failure, reason,
                                                     detail_fragment,
                                                     retry_after):
    """The incident's shape: the auth upsert cannot get a thread (or its
    deadline elapses), the auth middleware answers 503 itself, and the route
    handler never runs. The client sees the same 503 as before; now the
    counters see it too."""
    app = _app()
    with _broken_auth(failure):
        response = _client(app).get('/status', headers=_AUTH_HEADER)

    assert response.status_code == 503
    assert detail_fragment in response.json()['detail']
    # The helpers' headers are untouched: only the timeout 503 carries one.
    assert ('retry-after' in response.headers) is retry_after
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


def test_the_old_order_misses_the_middleware_503():
    """Why the order matters: with the metrics middleware inside the auth
    middleware (the previous order) the same 503 is never counted."""
    app = _app(metrics_outermost=False)
    with _broken_auth(exceptions.ConcurrentWorkerExhaustedError('32 of 32')):
        response = _client(app).get('/status', headers=_AUTH_HEADER)
    assert response.status_code == 503
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL) == 0.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


def test_a_refused_websocket_handshake_is_counted():
    """ssh goes over a WebSocket. During the incident every handshake was
    refused and no counter moved. The client still gets the same refusal
    (a pre-accept close, which servers render as an empty HTTP 403)."""
    app = _app()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')
    with _broken_auth(failure):
        with pytest.raises(WebSocketDisconnect) as refused:
            with _client(app).websocket_connect('/kubernetes-pod-ssh-proxy',
                                                headers=_AUTH_HEADER):
                pass

    # Unchanged client-visible behaviour: the 1011 close for a 503 verdict.
    assert refused.value.code == 1011
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path='/kubernetes-pod-ssh-proxy',
        outcome='error') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
                   status='503',
                   kind='websocket') == 1.0
    # Handshakes are not HTTP requests: the request counter is untouched.
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL) == 0.0


def test_an_accepted_websocket_handshake_is_not_counted_as_refused():
    app = _app()
    with _healthy_auth():
        with _client(app).websocket_connect('/kubernetes-pod-ssh-proxy',
                                            headers=_AUTH_HEADER) as websocket:
            assert websocket.receive_text() == 'hello'
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 0.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


def test_unauthenticated_scanner_paths_do_not_create_series():
    """Now that rejected requests are counted, their paths must not become
    labels: an unauthenticated client can send any path it likes."""
    app = _app()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')
    with _broken_auth(failure):
        client = _client(app)
        for i in range(5):
            assert client.get(f'/wp-admin/{i}.php',
                              headers=_AUTH_HEADER).status_code == 503
        assert client.get('/api/get', headers=_AUTH_HEADER).status_code == 503
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path=metrics.OTHER_PATH_LABEL,
                   status='5xx') == 5.0
    for i in range(5):
        assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                       path=f'/wp-admin/{i}.php') == 0.0
    # `/api/get` is not a route of this small app, so it folds into the
    # `/api/` prefix bucket, which the `/api/.*` exclusions still match.
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/api/*',
                   status='5xx') == 1.0


def test_a_404_from_the_router_keeps_the_raw_path():
    """Requests that reached the router are recorded exactly as before."""
    app = _app()
    with _healthy_auth():
        assert _client(app).get('/no/such/route',
                                headers=_AUTH_HEADER).status_code == 404
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/no/such/route',
                   status='4xx') == 1.0


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
    _routes(app)

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
    # Nothing attributed: the caller passed no request.
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


def test_the_real_server_registers_the_metrics_middleware_outermost():
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
