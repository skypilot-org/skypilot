"""The metrics layer fails open: a fault in recording never reaches a client.

`PrometheusMiddleware` is the outermost middleware, in front of every request
and every WebSocket handshake. These tests break every piece of recording it
does -- one counter, the whole recorder, the route-template resolver, the
rejection counter, a "disk full" write -- and check that the ASGI message
stream the client receives is the one it gets without the middleware, that a
streaming body still flows chunk by chunk, that the WebSocket handshake
messages are unchanged, and that an exception raised by the wrapped
application still propagates. The recording failure itself is logged at
WARNING, rate-limited per process.
"""
import asyncio
import errno
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest
import starlette.middleware.base
import starlette.responses

from sky.metrics import utils as metrics_utils
from sky.server import metrics
from sky.server import middleware_utils


class _RecorderFault(RuntimeError):
    """What every injected metrics-path fault raises."""


class _AppError(RuntimeError):
    """What the wrapped application raises in the propagation tests."""


def _boom(*args, **kwargs):
    del args, kwargs
    raise _RecorderFault('metrics path broke')


def _disk_full(*args, **kwargs):
    del args, kwargs
    # What a full disk under PROMETHEUS_MULTIPROC_DIR looks like.
    raise OSError(errno.ENOSPC, 'No space left on device')


def _labels_fault(counter, fault=_boom):
    return lambda mp: mp.setattr(counter, 'labels', fault)


# Every place the middleware records something for an HTTP request, broken
# one at a time. The `.labels` faults stand for the client library refusing a
# value or the multiprocess files failing to write; `route resolver`,
# `path label` and `whole recorder` stand for a bug in the middleware's own
# code.
_HTTP_FAULTS = {
    'requests_total.labels': _labels_fault(
        metrics_utils.SKY_APISERVER_REQUESTS_TOTAL),
    'requests_by_user_total.labels': _labels_fault(
        metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL),
    'request_duration.labels': _labels_fault(
        metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS),
    'disk full': _labels_fault(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
                               _disk_full),
    'record_rejection': lambda mp: mp.setattr(middleware_utils,
                                              'record_rejection', _boom),
    'route resolver': lambda mp: mp.setattr(metrics, '_match_candidates', _boom
                                           ),
    'route index': lambda mp: mp.setattr(metrics, '_RouteIndex', _boom),
    'path label': lambda mp: mp.setattr(metrics.PrometheusMiddleware,
                                        '_path_label', _boom),
    'whole recorder': lambda mp: mp.setattr(metrics.PrometheusMiddleware,
                                            '_record_http', _boom),
}

# The same for a WebSocket handshake.
_WS_FAULTS = {
    'handshakes_total.labels': _labels_fault(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL),
    'rejections_total.labels': _labels_fault(
        metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL),
    'disk full': _labels_fault(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL, _disk_full),
    'route resolver': lambda mp: mp.setattr(metrics, '_match_candidates', _boom
                                           ),
    'route index': lambda mp: mp.setattr(metrics, '_RouteIndex', _boom),
    'path label': lambda mp: mp.setattr(metrics.PrometheusMiddleware,
                                        '_path_label', _boom),
    'whole recorder': lambda mp: mp.setattr(metrics.PrometheusMiddleware,
                                            '_record_handshake', _boom),
}

# `None` = healthy recorder: the middleware must be transparent then too.
_HTTP_CASES = [None] + sorted(_HTTP_FAULTS)
_WS_CASES = [None] + sorted(_WS_FAULTS)


def _inject(faults, name, monkeypatch):
    if name is not None:
        faults[name](monkeypatch)


# ─────────────────────────────────────────────────────────────────────────
# fixtures and harness
# ─────────────────────────────────────────────────────────────────────────

_COUNTERS = (
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL,
    metrics_utils.SKY_APISERVER_REQUESTS_BY_USER_TOTAL,
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS,
    metrics_utils.SKY_APISERVER_REQUEST_GET_DURATION_SECONDS,
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
)


def _clear_counters():
    for counter in _COUNTERS:
        counter.clear()


@pytest.fixture(autouse=True)
def failure_log(monkeypatch):
    """Clean counters and a fresh, un-rate-limited failure log per test."""
    _clear_counters()
    log = middleware_utils.RecordingFailureLog()
    monkeypatch.setattr(middleware_utils, '_recording_failure_log', log)
    yield log
    _clear_counters()


@pytest.fixture
def warning():
    """The WARNING calls the failure log makes."""
    with mock.patch.object(middleware_utils.logger, 'warning') as warn:
        yield warn


def _sample(counter, **labels) -> float:
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


def _requests_total(**labels):
    return _sample(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL, **labels)


def _routed_app() -> fastapi.FastAPI:
    """A FastAPI app whose route table the middleware resolves templates
    against (the scope's `app`)."""
    app = fastapi.FastAPI()

    @app.get('/items/{item_id}')
    async def item(item_id: str):  # pylint: disable=unused-variable
        return {'item': item_id}

    @app.websocket('/ws/{session_id}')
    async def ws(websocket: fastapi.WebSocket, session_id: str):  # pylint: disable=unused-variable
        await websocket.accept()
        await websocket.send_text(f'hello {session_id}')
        await websocket.close()

    return app


def _http_scope(path='/items/9', method='GET', app=None):
    scope = {
        'type': 'http',
        'asgi': {
            'version': '3.0'
        },
        'http_version': '1.1',
        'method': method,
        'scheme': 'http',
        'path': path,
        'raw_path': path.encode(),
        'root_path': '',
        'query_string': b'',
        'headers': [(b'host', b'testserver')],
        'state': {},
    }
    if app is not None:
        scope['app'] = app
    return scope


def _ws_scope(path='/ws/abc', app=None, extension=False):
    scope = {
        'type': 'websocket',
        'asgi': {
            'version': '3.0'
        },
        'http_version': '1.1',
        'scheme': 'ws',
        'path': path,
        'raw_path': path.encode(),
        'root_path': '',
        'query_string': b'',
        'headers': [(b'host', b'testserver')],
        'state': {},
    }
    if extension:
        scope['extensions'] = {'websocket.http.response': {}}
    if app is not None:
        scope['app'] = app
    return scope


class _ScriptedApp:
    """An inner ASGI app that sends a fixed list of messages.

    Keeps the message objects it sent, so a test can check the middleware
    forwarded the very same objects (not copies, not rewritten ones).
    """

    def __init__(self, messages, state_updates=None, exc=None):
        self._messages = messages
        self._state_updates = state_updates
        self._exc = exc
        self.sent = []

    async def __call__(self, scope, receive, send):
        del receive
        if self._state_updates:
            scope['state'].update(self._state_updates)
        if self._exc is not None:
            raise self._exc
        for message in self._messages:
            self.sent.append(message)
            await send(message)


_JSON_BODY = b'{"item":"9"}'
_JSON_HEADERS = [(b'content-type', b'application/json'),
                 (b'content-length', str(len(_JSON_BODY)).encode()),
                 (b'x-request-id', b'abc-123')]
_REJECTION_BODY = b'{"detail":"Authentication database is busy"}'


def _http_messages(kind):
    if kind == '200 json':
        return [{
            'type': 'http.response.start',
            'status': 200,
            'headers': _JSON_HEADERS,
        }, {
            'type': 'http.response.body',
            'body': _JSON_BODY,
        }], None
    if kind == '503 rejection':
        return [{
            'type': 'http.response.start',
            'status': 503,
            'headers': [(b'content-type', b'application/json'),
                        (b'retry-after', b'5')],
        }, {
            'type': 'http.response.body',
            'body': _REJECTION_BODY,
        }], {
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT
        }
    if kind == '404 empty':
        return [{
            'type': 'http.response.start',
            'status': 404,
            'headers': [],
        }, {
            'type': 'http.response.body',
            'body': b'',
        }], None
    raise AssertionError(kind)


def _ws_messages(kind):
    if kind == 'accepted':
        return [{
            'type': 'websocket.accept',
            'subprotocol': None,
            'headers': [(b'x-session', b'abc')],
        }, {
            'type': 'websocket.send',
            'text': 'hello abc',
        }, {
            'type': 'websocket.close',
            'code': 1000,
            'reason': '',
        }], None
    if kind == 'closed before accept':
        return [{
            'type': 'websocket.close',
            'code': 4401,
            'reason': 'Unauthorized',
        }], {
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_UNAUTHORIZED
        }
    if kind == 'rejected with http response':
        return [{
            'type': 'websocket.http.response.start',
            'status': 503,
            'headers': [(b'content-type', b'application/json'),
                        (b'retry-after', b'5')],
        }, {
            'type': 'websocket.http.response.body',
            'body': _REJECTION_BODY,
        }], {
            middleware_utils.REJECT_REASON_STATE_KEY:
                middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT
        }
    raise AssertionError(kind)


async def _run(asgi_app, scope):
    """Run an ASGI app; return the messages it sent and what it raised."""
    sent = []

    async def receive():
        if scope['type'] == 'http':
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        return {'type': 'websocket.connect'}

    async def send(message):
        sent.append(message)

    raised = None
    try:
        await asgi_app(scope, receive, send)
    except Exception as e:  # pylint: disable=broad-except
        raised = e
    return sent, raised


async def _settle(predicate=lambda: True, rounds=200):
    """Yield to the loop until `predicate()` holds (or `rounds` yields)."""
    for _ in range(rounds):
        if predicate():
            return
        await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────
# HTTP: the response the client gets is the one it gets without us
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['200 json', '503 rejection', '404 empty'])
@pytest.mark.parametrize('fault', _HTTP_CASES)
async def test_http_response_is_identical_when_recording_fails(
        kind, fault, monkeypatch, failure_log, warning):
    messages, state_updates = _http_messages(kind)
    app = _routed_app()
    reference, raised = await _run(_ScriptedApp(messages, state_updates),
                                   _http_scope(app=app))
    assert raised is None

    _inject(_HTTP_FAULTS, fault, monkeypatch)
    inner = _ScriptedApp(messages, state_updates)
    sent, raised = await _run(metrics.PrometheusMiddleware(inner),
                              _http_scope(app=app))

    assert raised is None
    assert sent == reference
    # The very same message objects, forwarded in order, not copies.
    assert len(sent) == len(inner.sent)
    assert all(a is b for a, b in zip(sent, inner.sent))
    if fault is None:
        assert failure_log.failures == 0
        warning.assert_not_called()
        assert _requests_total(path='/items/{item_id}') == 1.0
    else:
        assert failure_log.failures >= 1
        assert warning.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', _HTTP_CASES)
async def test_streaming_body_still_streams_chunk_by_chunk(
        fault, monkeypatch, warning):
    """Each chunk reaches the client as soon as the app produces it."""
    chunks = [b'chunk-one', b'chunk-two', b'chunk-three']

    class Streamer:
        """Sends the start, then one chunk each time a gate is opened."""

        def __init__(self):
            self.gates = [asyncio.Event() for _ in chunks]

        async def __call__(self, scope, receive, send):
            del scope, receive
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'text/plain')],
            })
            for gate, chunk in zip(self.gates, chunks):
                await gate.wait()
                await send({
                    'type': 'http.response.body',
                    'body': chunk,
                    'more_body': chunk is not chunks[-1],
                })

    app = _routed_app()
    reference_app = Streamer()
    for gate in reference_app.gates:
        gate.set()
    reference, raised = await _run(reference_app, _http_scope('/logs', app=app))
    assert raised is None
    assert len(reference) == 1 + len(chunks)

    _inject(_HTTP_FAULTS, fault, monkeypatch)
    streamer = Streamer()
    middleware = metrics.PrometheusMiddleware(streamer)
    scope = _http_scope('/logs', app=app)
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        sent.append(message)

    task = asyncio.ensure_future(middleware(scope, receive, send))
    # The response start goes out before any body is available ...
    await _settle(lambda: len(sent) == 1)
    assert sent == reference[:1]
    # ... and every chunk goes out on its own, as soon as it is produced,
    # with the one before it not held back.
    for i, gate in enumerate(streamer.gates):
        gate.set()
        await _settle(lambda: len(sent) == i + 2)  # pylint: disable=cell-var-from-loop
        assert sent == reference[:i + 2], f'after chunk {i}'
    await asyncio.wait_for(task, timeout=5)
    assert sent == reference
    # Streaming paths skip the duration histogram, so that fault never fires.
    fires = fault not in (None, 'request_duration.labels')
    assert warning.call_count == (1 if fires else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', _HTTP_CASES)
async def test_http_app_exception_still_propagates(fault, monkeypatch,
                                                   failure_log):
    """We never swallow the application's own exception -- with a healthy
    recorder (it is counted as a 500) or with a broken one."""
    error = _AppError('handler blew up')
    _inject(_HTTP_FAULTS, fault, monkeypatch)
    inner = _ScriptedApp([], exc=error)
    sent, raised = await _run(metrics.PrometheusMiddleware(inner),
                              _http_scope(app=_routed_app()))

    assert raised is error
    assert sent == []
    if fault is None:
        assert _requests_total(path='/items/{item_id}', status='5xx') == 1.0
        assert _sample(
            metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
            reason=middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION,
            status='500',
            kind=middleware_utils.REJECTION_KIND_HTTP) == 1.0
    else:
        assert failure_log.failures >= 1


@pytest.mark.asyncio
async def test_http_app_exception_after_the_response_started_propagates(
        monkeypatch):
    """An app that fails mid-stream: the start went out, the error goes up."""
    error = _AppError('failed while streaming')
    _inject(_HTTP_FAULTS, 'whole recorder', monkeypatch)

    async def app(scope, receive, send):
        del scope, receive
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': []
        })
        raise error

    sent, raised = await _run(metrics.PrometheusMiddleware(app),
                              _http_scope(app=_routed_app()))
    assert raised is error
    assert sent == [{
        'type': 'http.response.start',
        'status': 200,
        'headers': []
    }]


# ─────────────────────────────────────────────────────────────────────────
# WebSocket: accept / close / http.response messages unchanged
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'kind', ['accepted', 'closed before accept', 'rejected with http response'])
@pytest.mark.parametrize('fault', _WS_CASES)
async def test_websocket_messages_are_identical_when_recording_fails(
        kind, fault, monkeypatch, failure_log, warning):
    messages, state_updates = _ws_messages(kind)
    app = _routed_app()
    extension = kind == 'rejected with http response'
    reference, raised = await _run(_ScriptedApp(messages, state_updates),
                                   _ws_scope(app=app, extension=extension))
    assert raised is None

    _inject(_WS_FAULTS, fault, monkeypatch)
    inner = _ScriptedApp(messages, state_updates)
    sent, raised = await _run(metrics.PrometheusMiddleware(inner),
                              _ws_scope(app=app, extension=extension))

    assert raised is None
    assert sent == reference
    assert len(sent) == len(inner.sent)
    assert all(a is b for a, b in zip(sent, inner.sent))
    # An accepted handshake never touches the rejection counter.
    fires = fault is not None and not (fault == 'rejections_total.labels' and
                                       kind == 'accepted')
    if fault is None:
        assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                       path='/ws/{session_id}') == 1.0
    if fires:
        assert failure_log.failures >= 1
        assert warning.call_count == 1
    else:
        assert failure_log.failures == 0
        warning.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', _WS_CASES)
async def test_websocket_app_exception_still_propagates(fault, monkeypatch,
                                                        failure_log):
    error = _AppError('handshake handler blew up')
    _inject(_WS_FAULTS, fault, monkeypatch)
    inner = _ScriptedApp([], exc=error)
    sent, raised = await _run(metrics.PrometheusMiddleware(inner),
                              _ws_scope(app=_routed_app()))

    assert raised is error
    assert sent == []
    if fault is None:
        assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                       path='/ws/{session_id}',
                       outcome='rejected',
                       client_status='500') == 1.0
    else:
        assert failure_log.failures >= 1


class _Unavailable503(starlette.middleware.base.BaseHTTPMiddleware):
    """An auth-style middleware answering every request with a JSON 503."""

    async def dispatch(self, request, call_next):
        del call_next
        middleware_utils.mark_rejection(
            request, middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT)
        return starlette.responses.JSONResponse(
            status_code=503,
            headers={'Retry-After': '5'},
            content={'detail': 'database is slow'})


@pytest.mark.asyncio
@pytest.mark.parametrize('extension', [False, True])
async def test_websocket_aware_rejection_is_unchanged_when_its_counter_fails(
        extension, monkeypatch, warning):
    """The handshake-rejection counter inside websocket_aware fails open
    too: the client gets the same close frame / HTTP response."""

    async def never_called(scope, receive, send):
        del scope, receive, send
        raise AssertionError('the handshake must be refused before the app')

    wrapper_cls = middleware_utils.websocket_aware(_Unavailable503)
    reference, raised = await _run(
        wrapper_cls(never_called),
        _ws_scope('/kubernetes-pod-ssh-proxy', extension=extension))
    assert raised is None
    assert reference, 'the reference run must have refused the handshake'

    monkeypatch.setattr(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        'labels', _boom)
    sent, raised = await _run(
        wrapper_cls(never_called),
        _ws_scope('/kubernetes-pod-ssh-proxy', extension=extension))

    assert raised is None
    assert sent == reference
    assert warning.call_count == 1
    if extension:
        assert sent[0]['status'] == 503
    else:
        assert sent == [{
            'type': 'websocket.close',
            'code': 1011,
            'reason': 'Internal Server Error',
        }]


# ─────────────────────────────────────────────────────────────────────────
# Through a real FastAPI app and the test client
# ─────────────────────────────────────────────────────────────────────────


def _fastapi_app(with_metrics: bool) -> fastapi.FastAPI:
    app = _routed_app()

    @app.get('/stream')
    async def stream():  # pylint: disable=unused-variable

        async def body():
            for i in range(3):
                yield f'line {i}\n'.encode()

        return starlette.responses.StreamingResponse(body(),
                                                     media_type='text/plain')

    if with_metrics:
        app.add_middleware(metrics.PrometheusMiddleware)
    return app


@pytest.mark.parametrize('fault', _HTTP_CASES)
def test_fastapi_responses_are_identical_through_the_test_client(
        fault, monkeypatch):
    reference = TestClient(_fastapi_app(with_metrics=False),
                           raise_server_exceptions=False)
    _inject(_HTTP_FAULTS, fault, monkeypatch)
    client = TestClient(_fastapi_app(with_metrics=True),
                        raise_server_exceptions=False)
    for path in ('/items/9', '/stream', '/no-such-route'):
        expected = reference.get(path)
        actual = client.get(path)
        assert (actual.status_code, dict(actual.headers),
                actual.content) == (expected.status_code,
                                    dict(expected.headers),
                                    expected.content), path


@pytest.mark.parametrize('fault', _WS_CASES)
def test_fastapi_websocket_is_served_through_the_test_client(
        fault, monkeypatch):
    _inject(_WS_FAULTS, fault, monkeypatch)
    client = TestClient(_fastapi_app(with_metrics=True))
    with client.websocket_connect('/ws/abc') as websocket:
        assert websocket.receive_text() == 'hello abc'


# ─────────────────────────────────────────────────────────────────────────
# Degradation and logging
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_resolver_failure_counts_the_request_as_unmatched(warning):
    """A resolver fault costs the request its template, not its count, and
    the fallback is not memoized."""
    app = _routed_app()
    middleware = metrics.PrometheusMiddleware(
        _ScriptedApp(_http_messages('200 json')[0]))
    with mock.patch.object(metrics, '_match_candidates', _boom):
        sent, raised = await _run(middleware, _http_scope(app=app))
    assert raised is None
    assert len(sent) == 2
    assert _requests_total(path=metrics.UNMATCHED_PATH_LABEL,
                           method='GET',
                           status='2xx') == 1.0
    assert _requests_total(path='/items/{item_id}') == 0.0
    assert warning.call_count == 1
    assert 'route template resolution' in warning.call_args.args[0]

    # Resolver healthy again: the same path resolves to its template.
    sent, raised = await _run(middleware, _http_scope(app=app))
    assert raised is None
    assert _requests_total(path='/items/{item_id}', method='GET',
                           status='2xx') == 1.0


@pytest.mark.asyncio
async def test_recording_failure_is_logged_once_then_rate_limited(
        monkeypatch, warning):
    now = [1000.0]
    log = middleware_utils.RecordingFailureLog(interval_seconds=300,
                                               clock=lambda: now[0])
    monkeypatch.setattr(middleware_utils, '_recording_failure_log', log)
    _inject(_HTTP_FAULTS, 'requests_total.labels', monkeypatch)
    messages, _ = _http_messages('200 json')
    middleware = metrics.PrometheusMiddleware(_ScriptedApp(messages))

    async def request():
        sent, raised = await _run(middleware, _http_scope(app=_routed_app()))
        assert raised is None
        assert len(sent) == 2

    for _ in range(5):
        await request()
    # One WARNING with the traceback for the first failure ...
    assert warning.call_count == 1
    first = warning.call_args
    assert 'Failed to record API server metrics for HTTP response' in \
        first.args[0]
    assert '_RecorderFault' in first.args[0]
    assert isinstance(first.kwargs['exc_info'], _RecorderFault)
    # ... nothing more inside the interval ...
    now[0] += 299
    await request()
    assert warning.call_count == 1
    # ... then one summary line, without a traceback, for what was dropped.
    now[0] += 2
    await request()
    assert warning.call_count == 2
    second = warning.call_args
    assert '5 more failure(s) were dropped silently' in second.args[0]
    assert second.kwargs['exc_info'] is None
    assert log.failures == 7


def test_record_safely_swallows_and_counts_exceptions(failure_log, warning):
    middleware_utils.record_safely('a thing', _boom, 1, key='value')
    assert failure_log.failures == 1
    assert warning.call_count == 1
    assert 'a thing' in warning.call_args.args[0]


def test_record_safely_passes_arguments_through(failure_log, warning):
    calls = []
    middleware_utils.record_safely('a thing',
                                   lambda *a, **k: calls.append((a, k)),
                                   1,
                                   2,
                                   key='value')
    assert calls == [((1, 2), {'key': 'value'})]
    assert failure_log.failures == 0
    warning.assert_not_called()


def test_record_safely_does_not_swallow_base_exceptions(failure_log):
    """Only `Exception`s are a metrics fault; an interrupt is not ours."""

    def interrupt():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        middleware_utils.record_safely('a thing', interrupt)
    assert failure_log.failures == 0
