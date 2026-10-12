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
import ast
import asyncio
import http
import os
import subprocess
import sys
import tempfile
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest
import starlette.middleware
import starlette.middleware.base
import starlette.routing
from starlette.websockets import WebSocketDisconnect

from sky import exceptions
from sky.metrics import utils as metrics_utils
from sky.server import common as server_common
from sky.server import config as server_config
from sky.server import metrics
from sky.server import middleware_utils
from sky.server import server
from sky.server.auth import db_lookup
from sky.server.auth import oauth2_proxy
from sky.skylet import constants

_AUTH_HEADER = {'X-Auth-Request-Email': 'bob@example.com'}
_PROXY_CONFIG = server_config.ExternalProxyConfig(
    enabled=True, header_name='X-Auth-Request-Email', header_format='plaintext')
_COUNTERS = (
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL,
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


# The ssh-proxy route with its required parameter supplied.
_SSH_PROXY = '/kubernetes-pod-ssh-proxy?cluster_name=c'


def _routes(app: fastapi.FastAPI) -> None:

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {'ok': True}

    # `cluster_name` is required, as it is on the two real ssh-proxy routes
    # (`session_id` on the third). FastAPI validates it *after* the
    # middlewares and closes the connection itself when it is missing, which
    # is the one way a handshake ends up neither refused nor accepted.
    @app.websocket('/kubernetes-pod-ssh-proxy')
    async def ssh_proxy(  # pylint: disable=unused-variable
            websocket: fastapi.WebSocket, cluster_name: str):
        del cluster_name
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
            with _client(app).websocket_connect(_SSH_PROXY,
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
    # The refusal is also counted as an attempt, by design: the outcome
    # split reads off the two counters.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
        path='/kubernetes-pod-ssh-proxy') == 1.0
    # Refused before any route ran, so nothing was accepted.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 0.0
    # Handshakes are not HTTP requests: the request counter is untouched.
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL) == 0.0


def test_an_accepted_websocket_handshake_is_counted():
    app = _app()
    with _healthy_auth():
        with _client(app).websocket_connect(_SSH_PROXY,
                                            headers=_AUTH_HEADER) as websocket:
            assert websocket.receive_text() == 'hello'
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 0.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0
    # The attempt is counted by route, once, and so is the accept.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
        path='/kubernetes-pod-ssh-proxy') == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL,
        path='/kubernetes-pod-ssh-proxy') == 1.0


def test_a_handshake_closed_by_route_validation_is_not_an_accept():
    """The case subtraction gets wrong, and the reason accepts are counted.

    Every real ssh route takes a required query parameter. FastAPI validates
    it after the middleware stack and closes the connection itself, so no
    middleware refuses the handshake and no handler accepts it. Counting the
    accept off the wire is what tells this apart from a served session;
    `attempts - refusals` cannot.
    """
    app = _app()
    with _healthy_auth():
        with pytest.raises(WebSocketDisconnect):
            # No `cluster_name`.
            with _client(app).websocket_connect('/kubernetes-pod-ssh-proxy',
                                                headers=_AUTH_HEADER):
                pass

    attempts = _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
        path='/kubernetes-pod-ssh-proxy')
    refusals = _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL)
    accepts = _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL)

    assert attempts == 1.0, 'it did reach the middleware stack'
    assert refusals == 0.0, 'no middleware refused it'
    assert accepts == 0.0, 'and it was never accepted'
    # Stated as the regression it is: the derived quantity says one accepted
    # session here, and there was none.
    assert attempts - refusals == 1.0


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


@pytest.mark.parametrize(
    'setting, expect_published',
    [
        ('true', True),
        # Enabled by a value the metrics endpoint itself accepts. Before the
        # predicate was unified, this served /metrics with the instruments behind
        # it switched off, so the series this change promises were absent.
        ('1', True),
        # The control arm, and a bug of its own before the unification: a bare
        # truthiness check made this install the endpoint anyway.
        ('false', False),
    ])
def test_importing_the_middleware_module_publishes_the_handshake_series(
        setting, expect_published):
    """The production trigger for pre-initialisation, in a real process.

    Every in-process test calls `preinitialize_websocket_metrics()` itself,
    so deleting the module-level call leaves all of them green while the
    mechanism is dead where it matters -- and the test environment has
    metrics off, so nothing in-process can notice either. A fresh
    interpreter with metrics enabled and a multiprocess directory is the
    only form that exercises the real trigger.
    """
    code = (
        'from prometheus_client import CollectorRegistry, multiprocess\n'
        # The import IS the trigger; nothing here calls the function.
        'from sky.server import middleware_utils\n'
        'del middleware_utils\n'
        'registry = CollectorRegistry()\n'
        'multiprocess.MultiProcessCollector(registry)\n'
        'print(sorted(\n'
        '    (s.name, s.labels["path"], s.value)\n'
        '    for m in registry.collect() for s in m.samples\n'
        '    if s.name.endswith("_handshake_attempts_total")\n'
        '    or s.name.endswith("_handshake_accepts_total")))')
    env = dict(os.environ)
    env['SKY_API_SERVER_METRICS_ENABLED'] = setting
    with tempfile.TemporaryDirectory() as multiproc_dir:
        env['PROMETHEUS_MULTIPROC_DIR'] = multiproc_dir
        out = subprocess.run([sys.executable, '-c', code],
                             env=env,
                             check=True,
                             capture_output=True,
                             text=True,
                             timeout=300).stdout.strip().splitlines()[-1]
    published = ast.literal_eval(out)

    paths = {
        '/kubernetes-pod-ssh-proxy',
        '/slurm-job-ssh-proxy',
        '/ssh-interactive-auth',
        'other',
    } if expect_published else set()
    for name in ('sky_apiserver_websocket_handshake_attempts_total',
                 'sky_apiserver_websocket_handshake_accepts_total'):
        assert {p for n, p, _ in published if n == name} == paths, published
    # Published, not incremented.
    assert {v for _, _, v in published
           } == ({0.0} if expect_published else set()), published


@pytest.mark.parametrize('setting, enabled', [
    ('true', True),
    ('1', True),
    ('false', False),
    ('', False),
])
def test_one_predicate_decides_metrics_everywhere(setting, enabled):
    """The endpoint and the instruments behind it agree, for every spelling.

    They did not: the middleware gate and the metrics server used a bare
    truthiness check while `METRICS_ENABLED` compared against the literal
    `true`, so `=1` served /metrics with nothing recording into it and
    `=false` served it too. Read in a subprocess because all three are
    decided at import time.

    Three of the four consumers are covered here and in the launcher test
    below. The fourth, the metrics-server startup, sits inside
    `if __name__ == '__main__'` and no test can reach it; it reads the same
    `METRICS_ENABLED` this asserts.
    """
    code = ('from sky.metrics import db as metrics_db\n'
            'from sky.metrics import utils as metrics_utils\n'
            'from sky.server import server\n'
            'installed = any(m.cls.__name__ == "PrometheusMiddleware"\n'
            '                for m in server.app.user_middleware)\n'
            'print(metrics_utils.METRICS_ENABLED, installed,\n'
            '      metrics_db.ENABLED)')
    env = dict(os.environ)
    env['SKY_API_SERVER_METRICS_ENABLED'] = setting
    out = subprocess.run([sys.executable, '-c', code],
                         env=env,
                         check=True,
                         capture_output=True,
                         text=True,
                         timeout=300).stdout.strip().splitlines()[-1]

    assert out == f'{enabled} {enabled} {enabled}', out


@pytest.mark.parametrize(
    'setting, enabled',
    [
        ('true', True),
        # The worst of the old spellings. This site compared `== 'true'` with no
        # `.lower()`, unlike the two that did, so `=TRUE` switched on the
        # middleware and the metrics server while leaving the multiprocess
        # directory unset -- and `/metrics` then serves the default registry:
        # the main process only, no collectors, nothing from any worker.
        ('TRUE', True),
        ('1', True),
        ('false', False),
        # Previously truthy, because `os.environ.get` returns the string '0'.
        ('0', False),
        ('yes', False),
        ('', False),
    ])
def test_the_launcher_sets_up_metrics_for_the_same_spellings(
        setting, enabled, monkeypatch, tmp_path):
    """The launcher decides whether PROMETHEUS_MULTIPROC_DIR exists at all.

    Without it the metrics endpoint still serves, from the default registry,
    which is a silently partial deployment rather than a broken one.
    """
    # The real function wipes and recreates <tmpdir>/metrics, which on a
    # developer machine is a running server's multiprocess directory.
    monkeypatch.setattr(server_common.tempfile, 'gettempdir',
                        lambda: str(tmp_path))
    monkeypatch.setenv('SKY_API_SERVER_METRICS_ENABLED', setting)
    env: dict = {}

    server_common._set_metrics_env_var(env, False, False)  # pylint: disable=protected-access

    if enabled:
        assert env.get('SKY_API_SERVER_METRICS_ENABLED') == 'true'
        assert env.get('PROMETHEUS_MULTIPROC_DIR') == str(tmp_path / 'metrics')
    else:
        assert env == {}


@pytest.mark.parametrize('value, enabled', [
    ('true', True),
    ('TRUE', True),
    ('True', True),
    ('1', True),
    ('false', False),
    ('0', False),
    ('yes', False),
    ('on', False),
    ('', False),
])
def test_the_accepted_spellings_are_the_repo_convention(value, enabled,
                                                        monkeypatch):
    """The predicate itself, stated once.

    `('true', '1')` is what `sky/utils/env_options.py` and
    `common_utils.get_using_remote_api_server` accept; widening it here
    without widening those would make one variable behave unlike the rest.
    """
    monkeypatch.setenv('SKY_API_SERVER_METRICS_ENABLED', value)
    assert constants.server_metrics_enabled() is enabled


def test_the_handshake_counters_keep_their_exported_names():
    """A metric name is an API, and renaming one fails silently.

    Nothing in a rule or a dashboard errors when the series it reads stops
    existing: a ratio guarded with `or 0 * <denominator>` -- the standard way
    to write one that tolerates older servers -- reads zero instead, forever,
    and the alert simply never fires again.

    The three handshake counters had asymmetric protection before this test:
    renaming attempts or accepts turned one test red, as a byproduct of the
    pre-initialisation test reading published series by name, while renaming
    the rejections counter left the whole suite green. It is not
    pre-initialised, by design -- its `status` domain is open -- so it
    inherited no name pin at all.
    """
    expected = {
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL: 'sky_apiserver_websocket_handshake_attempts_total',
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL: 'sky_apiserver_websocket_handshake_accepts_total',
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL: 'sky_apiserver_websocket_handshake_rejections_total',
        metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL: 'sky_apiserver_request_rejections_total',
    }
    for counter, series in expected.items():
        # The family name, plus the suffix the client library appends: what a
        # PromQL rule actually types.
        families = [family.name for family in counter.collect()]
        assert families == [series[:-len('_total')]], (families, series)


def test_the_bounded_path_labels_match_the_registered_websocket_routes():
    """`WEBSOCKET_ROUTE_PATHS` is load-bearing twice: it bounds the label and
    it decides which series are published at zero. A new ws route missing
    from it lands in `other` with no pre-published series, silently."""
    registered = {
        route.path
        for route in server.app.routes
        if isinstance(route, starlette.routing.WebSocketRoute)
    }
    assert registered == set(middleware_utils.WEBSOCKET_ROUTE_PATHS)


import starlette.middleware


def _break(target: str):
    """Make one recording call site raise."""
    return mock.patch(target, side_effect=RuntimeError('multiproc dir full'))


@pytest.fixture
def recording_log():
    """A fresh rate limiter, so failure counts are per test."""
    original = middleware_utils._recording_failure_log  # pylint: disable=protected-access
    fresh = middleware_utils.RecordingFailureLog()
    middleware_utils._recording_failure_log = fresh  # pylint: disable=protected-access
    yield fresh
    middleware_utils._recording_failure_log = original  # pylint: disable=protected-access


@pytest.mark.parametrize('broken', [
    'sky.metrics.utils.SKY_APISERVER_REQUESTS_TOTAL.labels',
    'sky.metrics.utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL.labels',
    'sky.metrics.utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.labels',
    'sky.metrics.utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.labels',
])
def test_a_recording_failure_never_reaches_the_client(broken, recording_log):
    """This layer is outermost, so it sees 100% of responses: a fault while
    recording must not turn a served request into a 500. Compared against the
    same requests with recording healthy, byte for byte."""
    app = _app()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')

    def exchange():
        client = _client(app)
        ok = client.get('/status', headers=_AUTH_HEADER)
        with _broken_auth(failure):
            rejected = client.get('/status', headers=_AUTH_HEADER)
        return [
            (r.status_code, r.content, dict(r.headers)) for r in (ok, rejected)
        ]

    with _healthy_auth():
        baseline = exchange()
    with mock.patch.object(middleware_utils.logger, 'warning'):
        with _break(broken):
            with _healthy_auth():
                faulted = exchange()

    for (want, got) in zip(baseline, faulted):
        assert want[0] == got[0]
        assert want[1] == got[1]
        # `date` moves between the two exchanges; everything else must not.
        assert {k: v for k, v in want[2].items() if k != 'date'} == \
               {k: v for k, v in got[2].items() if k != 'date'}
    assert recording_log.failures > 0, 'the failure was not even noticed'


def test_a_broken_path_resolver_still_counts_the_request(recording_log):
    """The path label is the one part of recording that reads state this
    layer does not own (the app's route table). Mislabelled beats uncounted:
    the request must still land in the counter, in the catch-all bucket."""
    app = _app()
    with mock.patch.object(middleware_utils.logger, 'warning'):
        with _break('sky.server.metrics._unrouted_path_label'):
            with _broken_auth(
                    exceptions.ConcurrentWorkerExhaustedError('32 of 32')):
                response = _client(app).get('/status', headers=_AUTH_HEADER)

    assert response.status_code == 503
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path=metrics.OTHER_PATH_LABEL,
                   status='5xx') == 1.0
    assert recording_log.failures == 1


def test_an_application_exception_still_propagates(recording_log):
    """`record_safely` swallows recording faults only. A handler that raises
    must still reach the client as a 500 and be counted as one."""

    app = _app()

    @app.get('/boom')
    async def boom():  # pylint: disable=unused-variable
        raise RuntimeError('handler exploded')

    app.build_middleware_stack()
    with _healthy_auth():
        response = _client(app).get('/boom', headers=_AUTH_HEADER)

    assert response.status_code == 500
    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/boom',
                   status='5xx') == 1.0
    assert recording_log.failures == 0, 'no recording call should have failed'


class TestRecordingFailureLog:
    """The rate limiter: a persistent fault must not log at request rate."""

    @staticmethod
    def _log(interval=300.0):
        clock = {'now': 1000.0}
        log = middleware_utils.RecordingFailureLog(interval_seconds=interval,
                                                   clock=lambda: clock['now'])
        return log, clock

    def test_the_first_failure_is_logged_with_its_traceback(self):
        log, _ = self._log()
        with mock.patch.object(middleware_utils.logger, 'warning') as warn:
            log.note('the request counter', RuntimeError('boom'))
        assert warn.call_count == 1
        assert warn.call_args.kwargs['exc_info'] is not None
        assert log.failures == 1

    def test_failures_inside_the_interval_are_counted_but_not_logged(self):
        log, clock = self._log()
        with mock.patch.object(middleware_utils.logger, 'warning') as warn:
            log.note('x', RuntimeError('1'))
            for _ in range(50):
                clock['now'] += 1.0
                log.note('x', RuntimeError('n'))
        assert warn.call_count == 1, 'a persistent fault flooded the log'
        assert log.failures == 51

    def test_the_next_message_reports_what_was_dropped(self):
        log, clock = self._log(interval=10.0)
        with mock.patch.object(middleware_utils.logger, 'warning') as warn:
            log.note('x', RuntimeError('1'))
            for _ in range(3):
                clock['now'] += 1.0
                log.note('x', RuntimeError('n'))
            clock['now'] += 100.0
            log.note('x', RuntimeError('last'))
        assert warn.call_count == 2
        assert '3 more failure(s) were dropped' in warn.call_args.args[0]
        # Only the first message carries a traceback.
        assert warn.call_args.kwargs['exc_info'] is None


class TestOutermostGuard:
    """The runtime counterpart of the ordering test: a deployment whose
    plugins register middleware is not covered by a unit test on the
    plugin-less app, and being wrong is silent."""

    def test_the_production_order_passes_without_a_warning(self):
        app = _app()
        with mock.patch.object(metrics.logger, 'warning') as warn:
            assert metrics.warn_unless_outermost(app) is True
        warn.assert_not_called()

    def test_a_layer_outside_the_metrics_middleware_is_reported(self):
        """How the invariant actually breaks in a deployment: the plugin API
        (`add_middleware_last`) appends straight to `app.user_middleware`, so
        it bypasses `add_middleware` -- which would refuse after startup --
        and can seat a layer outside this one with no error anywhere."""
        app = fastapi.FastAPI()
        app.add_middleware(metrics.PrometheusMiddleware)
        app.user_middleware.insert(
            0,
            starlette.middleware.Middleware(
                server.InitializeRequestAuthUserMiddleware))

        with mock.patch.object(metrics.logger, 'warning') as warn:
            assert metrics.warn_unless_outermost(app) is False
        assert warn.call_count == 1
        message = warn.call_args.args[0]
        assert 'InitializeRequestAuthUserMiddleware is' in message
        assert 'PrometheusMiddleware' in message

    def test_an_app_without_the_metrics_layer_is_not_reported(self):
        """Metrics off (or registration skipped): there is no invariant to
        hold, so the startup log must stay quiet."""
        with mock.patch.object(metrics.logger, 'warning') as warn:
            assert metrics.warn_unless_outermost(fastapi.FastAPI()) is True
        warn.assert_not_called()


@pytest.mark.asyncio
async def test_a_client_disconnect_is_counted_in_a_known_bucket():
    """A `BaseException` (the cancellation a client disconnect produces)
    unwinds past `except Exception`, so nothing sets a status and the request
    records `0xx`. Counting it beats dropping it -- a disconnect is a real
    request -- but it is a label value operators will see, so pin it
    deliberately instead of leaving it to `_get_status_code_group(0)`."""
    middleware = metrics.PrometheusMiddleware(app=mock.Mock())
    request = fastapi.Request({
        'type': 'http',
        'method': 'GET',
        'path': '/status',
        'headers': [],
        'query_string': b'',
        'state': {},
        'router': object(),
    })

    async def cancelled(_):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await middleware.dispatch(request, cancelled)

    assert _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                   path='/status',
                   status='0xx') == 1.0


class TestOutermostGuardIsNotEnvGated:
    """Regression: the guard used to read `metrics_utils.METRICS_ENABLED`
    (`== 'true'`) while the registration used the env var's truthiness, so
    `SKY_API_SERVER_METRICS_ENABLED=1` installed the layer and skipped the
    check -- an enabled deployment with the check silently off. It now reads
    the stack, so no predicate can drift."""

    def test_no_metrics_layer_means_nothing_to_check(self):
        app = fastapi.FastAPI()
        app.add_middleware(server.InitializeRequestAuthUserMiddleware)
        with mock.patch.object(metrics.logger, 'warning') as warn:
            assert metrics.warn_unless_outermost(app) is True
        warn.assert_not_called()

    def test_an_installed_but_displaced_layer_is_reported(self):
        app = fastapi.FastAPI()
        app.add_middleware(metrics.PrometheusMiddleware)
        app.user_middleware.insert(
            0,
            starlette.middleware.Middleware(
                server.InitializeRequestAuthUserMiddleware))
        with mock.patch.object(metrics.logger, 'warning') as warn:
            assert metrics.warn_unless_outermost(app) is False
        warn.assert_called_once()

    def test_the_call_site_is_not_gated_by_an_env_var(self):
        """The defect was the call site, not the function: a gate on the
        metrics env var meant the check never ran under
        `SKY_API_SERVER_METRICS_ENABLED=1` (truthy for the registration,
        not the literal `true` that `METRICS_ENABLED` requires). Patch the
        guard, import the real server module in a fresh process, and assert
        it was called -- any gate that is false in this environment, which
        is every gate on that variable, fails this.
        """
        code = (
            'import sys\n'
            'import unittest.mock as mock\n'
            'from sky.server import metrics\n'
            # The patch has to be in place before the module-level call runs,
            # so assert it has not been imported already: if the import graph
            # ever pulls it in, say so instead of failing as though the call
            # site had regressed.
            "assert 'sky.server.server' not in sys.modules, (\n"
            "    'importing sky.server.metrics now pulls in '\n"
            "    'sky.server.server; this test can no longer observe the '\n"
            "    'module-level call and needs rewriting')\n"
            'with mock.patch.object(metrics, "warn_unless_outermost") '
            'as guard:\n'
            '    from sky.server import server\n'
            '    del server\n'
            'assert guard.call_count == 1, guard.call_count\n'
            'print("called")')
        env = {
            k: v
            for k, v in os.environ.items()
            if k != 'SKY_API_SERVER_METRICS_ENABLED'
        }
        out = subprocess.run([sys.executable, '-c', code],
                             env=env,
                             check=True,
                             capture_output=True,
                             text=True,
                             timeout=300)
        assert out.stdout.strip().splitlines()[-1] == 'called', out.stdout


class _StubAuthResponse:
    """The oauth2-proxy `/oauth2/auth` answer, as an async context manager."""

    def __init__(self, status):
        self.status = status
        self.headers = {}
        self.cookies = {}
        self.text = ''

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StubSession:

    def __init__(self, status):
        self._status = status

    def request(self, **kwargs):
        del kwargs
        return _StubAuthResponse(self._status)


class TestAuthProxyReasonsAreDistinct:
    """`auth_proxy_unavailable` used to cover two different failures. They
    need different fixes -- one is "the proxy is down", the other is "the
    proxy answered and we cannot use it" -- so they are separate reasons.
    """

    @staticmethod
    async def _run(status, get_auth_user):
        request = fastapi.Request({
            'type': 'http',
            'method': 'GET',
            'path': '/status',
            'headers': [],
            'query_string': b'',
            'scheme': 'http',
            'server': ('testserver', 80),
            'state': {},
        })
        # `websocket_aware` wraps the class, so the real middleware -- the
        # one carrying `get_auth_user` and `_authenticate` -- is the
        # instance it holds.
        middleware = oauth2_proxy.OAuth2ProxyMiddleware(
            app=mock.Mock()).middleware
        with mock.patch.object(middleware, 'get_auth_user', get_auth_user):
            response = await middleware._authenticate(  # pylint: disable=protected-access
                request, mock.AsyncMock(), _StubSession(status))
        return request, response

    @pytest.mark.asyncio
    async def test_authenticated_without_user_info_is_a_bad_response(self):
        """The proxy is reachable and says the user is authenticated; it just
        did not send the user info. A setup problem, not an outage."""
        request, response = await self._run(http.HTTPStatus.ACCEPTED,
                                            lambda _: None)
        assert response.status_code == 500
        assert middleware_utils.get_rejection_reason(request.scope) == (
            middleware_utils.REJECT_REASON_AUTH_PROXY_BAD_RESPONSE)

    @pytest.mark.asyncio
    async def test_an_unexpected_proxy_status_is_a_bad_response_too(self):
        """The other half of the same axis, which the first pass missed: the
        proxy answered something this server cannot act on."""
        request, response = await self._run(http.HTTPStatus.IM_A_TEAPOT,
                                            lambda _: None)
        assert response.status_code == int(http.HTTPStatus.IM_A_TEAPOT)
        assert middleware_utils.get_rejection_reason(request.scope) == (
            middleware_utils.REJECT_REASON_AUTH_PROXY_BAD_RESPONSE)


@pytest.mark.parametrize('broken, expect_accept', [
    ('sky.metrics.utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL.'
     'labels', True),
    ('sky.metrics.utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL.'
     'labels', True),
    ('sky.metrics.utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL.'
     'labels', False),
    ('sky.metrics.utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.labels', False),
])
def test_a_recording_failure_never_reaches_a_handshake(broken, expect_accept,
                                                       recording_log):
    """The ws half of the matrix above: recording runs immediately before the
    close frame is sent, so a fault there would leave the client with no
    answer at all rather than the refusal it was owed.

    `.labels()` is the boundary that matters: in multiprocess mode -- the mode
    production runs in -- the first call for a label set opens a file under
    `PROMETHEUS_MULTIPROC_DIR`, so a full or unwritable directory raises
    exactly here.
    """
    app = _app()
    failure = exceptions.ConcurrentWorkerExhaustedError('32 of 32')

    with mock.patch.object(middleware_utils.logger, 'warning'):
        with _break(broken):
            if expect_accept:
                with _healthy_auth():
                    with _client(app).websocket_connect(
                            _SSH_PROXY, headers=_AUTH_HEADER) as websocket:
                        assert websocket.receive_text() == 'hello'
            else:
                with _broken_auth(failure):
                    with pytest.raises(WebSocketDisconnect) as refused:
                        with _client(app).websocket_connect(
                                _SSH_PROXY, headers=_AUTH_HEADER):
                            pass
                # The same 1011 close as with recording healthy.
                assert refused.value.code == 1011

    assert recording_log.failures > 0, 'the failure was not even noticed'
