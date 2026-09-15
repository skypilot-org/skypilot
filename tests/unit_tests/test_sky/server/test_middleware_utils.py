"""Unit tests for the middleware utilities."""

import http

import fastapi
import pytest
import starlette.middleware.base

from sky.metrics import utils as metrics_utils
from sky.server import middleware_utils


class RecordingMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware used for testing websocket adaptation."""

    def __init__(self, app, behavior):
        super().__init__(app)
        self.behavior = behavior
        self.dispatch_calls = 0
        self.call_next_calls = 0
        self.last_scope_type = None

    async def dispatch(self, request, call_next):
        self.dispatch_calls += 1
        self.last_scope_type = request.scope['type']

        if self.behavior == 'accept':
            self.call_next_calls += 1
            return await call_next(request)
        if self.behavior == 'unauthorized':
            return fastapi.Response(status_code=http.HTTPStatus.UNAUTHORIZED)
        if self.behavior == 'forbidden':
            return fastapi.Response(status_code=http.HTTPStatus.FORBIDDEN)
        if self.behavior == 'error':
            raise RuntimeError('middleware failure')
        if self.behavior == 'unavailable':
            # The shape db_lookup's helpers produce: a JSON 503 with a stamped
            # rejection reason.
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT)
            return fastapi.responses.JSONResponse(
                status_code=http.HTTPStatus.SERVICE_UNAVAILABLE,
                headers={'Retry-After': '5'},
                content={'detail': 'database is slow'})
        return None


def _make_middleware(app, behavior):
    wrapper_cls = middleware_utils.websocket_aware(RecordingMiddleware)
    return wrapper_cls(app, behavior=behavior)


def _make_websocket_scope(**overrides):
    scope = {
        'type': 'websocket',
        'scheme': 'ws',
        'http_version': '1.1',
        'path': '/ws',
        'root_path': '',
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 80),
        'server': ('127.0.0.1', 80),
        'state': {},
    }
    scope.update(overrides)
    return scope


@pytest.mark.asyncio
async def test_websocket_accept_invokes_app():
    app_scopes = []
    sent_messages = []

    async def app(scope, receive, send):
        app_scopes.append(scope['type'])
        await send({'type': 'websocket.accept'})

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent_messages.append(message)

    middleware = _make_middleware(app, behavior='accept')
    scope = _make_websocket_scope()

    await middleware(scope, receive, send)

    assert app_scopes == ['websocket']
    assert sent_messages == [{'type': 'websocket.accept'}]
    assert middleware.middleware.dispatch_calls == 1
    assert middleware.middleware.call_next_calls == 1
    assert middleware.middleware.last_scope_type == 'http'


@pytest.mark.asyncio
async def test_websocket_unauthorized_closes_connection():
    app_called = False
    sent_messages = []

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent_messages.append(message)

    middleware = _make_middleware(app, behavior='unauthorized')
    scope = _make_websocket_scope()

    await middleware(scope, receive, send)

    assert not app_called
    assert sent_messages == [{
        'type': 'websocket.close',
        'code': 4401,
        'reason': 'Unauthorized',
    }]


@pytest.mark.asyncio
async def test_websocket_forbidden_closes_connection():
    app_called = False
    sent_messages = []

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent_messages.append(message)

    middleware = _make_middleware(app, behavior='forbidden')
    scope = _make_websocket_scope()

    await middleware(scope, receive, send)

    assert not app_called
    assert sent_messages == [{
        'type': 'websocket.close',
        'code': 4403,
        'reason': 'Forbidden',
    }]


@pytest.mark.asyncio
async def test_websocket_error_closes_connection():
    app_called = False
    sent_messages = []

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent_messages.append(message)

    middleware = _make_middleware(app, behavior='error')
    scope = _make_websocket_scope()

    await middleware(scope, receive, send)

    assert not app_called
    assert sent_messages == [{
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }]


@pytest.mark.asyncio
async def test_lifespan_scope_passes_through():
    scope = {'type': 'lifespan'}
    seen_scopes = []

    async def app(received_scope, receive, send):
        del receive, send
        seen_scopes.append(received_scope['type'])

    async def receive():
        return {'type': 'lifespan.startup'}

    async def send(message):
        del message

    middleware = _make_middleware(app, behavior='accept')
    await middleware(scope, receive, send)

    assert seen_scopes == ['lifespan']


def test_build_http_scope_converts_scheme():
    wrapper_cls = middleware_utils.websocket_aware(RecordingMiddleware)
    state = {}
    scope = {
        'type': 'websocket',
        'scheme': 'wss',
        'path': '/ws',
        'headers': [],
        'state': state,
    }

    http_scope = wrapper_cls._build_http_scope(scope)

    assert http_scope['type'] == 'http'
    assert http_scope['scheme'] == 'https'
    assert http_scope['method'] == 'GET'
    assert http_scope['http_version'] == '1.1'
    assert http_scope['state'] is state


# ─────────────────────────────────────────────────────────────────────────
# Refused handshakes are counted. What the client receives is unchanged: the
# close frames asserted above, which ASGI servers render as an empty HTTP 403.
# ─────────────────────────────────────────────────────────────────────────


def _sample(counter, **labels) -> float:
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


@pytest.fixture(autouse=True)
def clear_rejection_counters():
    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
            metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL):
        counter.clear()
    yield
    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
            metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL):
        counter.clear()


async def _run(middleware, scope):
    sent = []

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior, outcome, close_frame', [
    ('unauthorized', 'unauthorized', {
        'type': 'websocket.close',
        'code': 4401,
        'reason': 'Unauthorized',
    }),
    ('forbidden', 'forbidden', {
        'type': 'websocket.close',
        'code': 4403,
        'reason': 'Forbidden',
    }),
    ('error', 'error', {
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }),
    ('unavailable', 'error', {
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }),
])
async def test_a_refused_handshake_is_counted_and_the_close_frame_is_unchanged(
        behavior, outcome, close_frame):
    app_called = False

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    middleware = _make_middleware(app, behavior=behavior)
    sent = await _run(middleware,
                      _make_websocket_scope(path='/kubernetes-pod-ssh-proxy'))

    assert not app_called
    assert sent == [close_frame]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path='/kubernetes-pod-ssh-proxy',
        outcome=outcome) == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_a_refused_handshake_with_a_stamped_reason_is_attributed():
    """The auth helpers stamp why they answered 503; the handshake carries
    it into the rejection counter with the status the middleware produced
    (the client still sees a 403 close on the wire)."""
    middleware = _make_middleware(lambda *a: None, behavior='unavailable')
    await _run(middleware,
               _make_websocket_scope(path='/kubernetes-pod-ssh-proxy'))
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT,
                   status='503',
                   kind='websocket') == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior', ['unauthorized', 'forbidden', 'error'])
async def test_a_refused_handshake_without_a_reason_is_not_attributed(behavior):
    middleware = _make_middleware(lambda *a: None, behavior=behavior)
    await _run(middleware, _make_websocket_scope())
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


@pytest.mark.asyncio
async def test_an_accepted_handshake_is_not_counted_as_refused():

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    middleware = _make_middleware(app, behavior='accept')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{'type': 'websocket.accept'}]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 0.0


@pytest.mark.asyncio
async def test_handshake_paths_outside_the_websocket_routes_are_bounded():
    middleware = _make_middleware(lambda *a: None, behavior='forbidden')
    for i in range(3):
        await _run(middleware, _make_websocket_scope(path=f'/probe/{i}'))
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path=middleware_utils.OTHER_WEBSOCKET_PATH_LABEL,
        outcome='forbidden') == 3.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path='/probe/0') == 0.0


def test_websocket_path_label():
    for path in middleware_utils.WEBSOCKET_ROUTE_PATHS:
        assert middleware_utils.websocket_path_label({'path': path}) == path
    assert middleware_utils.websocket_path_label({'path': '/x'}) == \
        middleware_utils.OTHER_WEBSOCKET_PATH_LABEL
    assert middleware_utils.websocket_path_label({}) == \
        middleware_utils.OTHER_WEBSOCKET_PATH_LABEL


def test_mark_rejection_round_trips_through_the_scope_state():
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/',
        'headers': [],
        'state': {}
    }
    request = fastapi.Request(scope)
    assert middleware_utils.get_rejection_reason(scope) is None
    middleware_utils.mark_rejection(request,
                                    middleware_utils.REJECT_REASON_FORBIDDEN)
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_FORBIDDEN
    assert request.state.reject_reason == \
        middleware_utils.REJECT_REASON_FORBIDDEN


def test_record_rejection_is_a_noop_without_a_stamp():
    middleware_utils.record_rejection({'state': {}}, 503,
                                      middleware_utils.REJECTION_KIND_HTTP)
    middleware_utils.record_rejection({}, 503,
                                      middleware_utils.REJECTION_KIND_HTTP)
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0
    middleware_utils.record_rejection(
        {'state': {
            'reject_reason': middleware_utils.REJECT_REASON_FORBIDDEN
        }}, 403, middleware_utils.REJECTION_KIND_HTTP)
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason='forbidden',
                   status='403',
                   kind='http') == 1.0


def test_record_rejection_ignores_a_stamp_on_a_success():
    """A middleware that stamps a reason and then calls `call_next` leaves a
    stamp on a 2xx/3xx. That is a misuse, not a rejection; the counter must
    not say `forbidden` about a request that succeeded."""
    scope = {
        'state': {
            'reject_reason': middleware_utils.REJECT_REASON_FORBIDDEN
        }
    }
    for status_code in (200, http.HTTPStatus.CREATED, 307):
        middleware_utils.record_rejection(scope, status_code,
                                          middleware_utils.REJECTION_KIND_HTTP)
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0
    # The same stamp on a refusal is counted.
    middleware_utils.record_rejection(scope, 403,
                                      middleware_utils.REJECTION_KIND_HTTP)
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason='forbidden',
                   status='403') == 1.0


def test_record_rejection_labels_an_http_status_enum_by_number():
    """Responses are often built with `http.HTTPStatus` members; the label
    must be the number, not `HTTPStatus.SERVICE_UNAVAILABLE`."""
    middleware_utils.record_rejection(
        {
            'state': {
                'reject_reason': middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT
            }
        }, http.HTTPStatus.SERVICE_UNAVAILABLE,
        middleware_utils.REJECTION_KIND_WEBSOCKET)
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason='auth_db_timeout',
                   status='503',
                   kind='websocket') == 1.0
