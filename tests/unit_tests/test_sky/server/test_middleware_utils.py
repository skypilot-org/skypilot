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
        if self.behavior == 'redirect':
            # What OAuth2ProxyMiddleware returns for an unauthenticated or
            # expired session: a 307 to the sign-in page, without call_next.
            return fastapi.responses.RedirectResponse(
                url='http://x/oauth2/start?rd=%2Fws')
        if self.behavior == 'answered':
            # A middleware that serves the request itself with a 2xx.
            return fastapi.responses.PlainTextResponse('served here',
                                                       status_code=200)
        if self.behavior == 'unavailable':
            # The shape db_lookup's helpers produce: a JSON 503 with a
            # Retry-After and a stamped rejection reason.
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT)
            return fastapi.responses.JSONResponse(
                status_code=http.HTTPStatus.SERVICE_UNAVAILABLE,
                headers={
                    'Retry-After': '5',
                    'X-Not-Forwarded': 'internal'
                },
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
# Rejections carry the real HTTP status when the server offers the
# `websocket.http.response` extension (uvicorn does). Without it, the close
# codes above are the only option and servers render them as an empty 403.
# ─────────────────────────────────────────────────────────────────────────


def _scope_with_extension(**overrides):
    return _make_websocket_scope(extensions={'websocket.http.response': {}},
                                 **overrides)


async def _run(middleware, scope):
    sent = []

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_websocket_unavailable_is_rejected_with_the_real_503():
    app_called = False

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    middleware = _make_middleware(app, behavior='unavailable')
    scope = _scope_with_extension()
    sent = await _run(middleware, scope)

    assert not app_called
    assert [m['type'] for m in sent] == [
        'websocket.http.response.start', 'websocket.http.response.body'
    ]
    start, body = sent
    assert start['status'] == 503
    headers = dict(start['headers'])
    assert headers[b'content-type'] == b'application/json'
    assert headers[b'retry-after'] == b'5'
    # Only the headers a WebSocket client can use are forwarded.
    assert b'x-not-forwarded' not in headers
    assert body['body'] == b'{"detail":"database is slow"}'
    # The reason the middleware stamped is on the shared scope state, where
    # the metrics layer reads it.
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT


@pytest.mark.asyncio
async def test_websocket_unauthorized_is_rejected_with_a_401():
    middleware = _make_middleware(lambda *a: None, behavior='unauthorized')
    sent = await _run(middleware, _scope_with_extension())
    assert sent[0]['type'] == 'websocket.http.response.start'
    assert sent[0]['status'] == 401
    assert sent[1] == {'type': 'websocket.http.response.body', 'body': b''}


@pytest.mark.asyncio
async def test_websocket_forbidden_is_rejected_with_a_403():
    middleware = _make_middleware(lambda *a: None, behavior='forbidden')
    sent = await _run(middleware, _scope_with_extension())
    assert sent[0]['status'] == 403


@pytest.mark.asyncio
async def test_websocket_middleware_exception_is_rejected_with_a_500():
    middleware = _make_middleware(lambda *a: None, behavior='error')
    scope = _scope_with_extension()
    sent = await _run(middleware, scope)
    assert sent[0]['type'] == 'websocket.http.response.start'
    assert sent[0]['status'] == 500
    assert sent[1]['body'] == b'{"detail":"Internal Server Error"}'
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION


@pytest.mark.asyncio
async def test_websocket_accept_ignores_the_extension():
    sent_types = []

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    middleware = _make_middleware(app, behavior='accept')

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        sent_types.append(message['type'])

    await middleware(_scope_with_extension(), receive, send)
    assert sent_types == ['websocket.accept']


@pytest.mark.asyncio
async def test_websocket_rejection_without_the_extension_still_closes():
    """The fallback is the pre-extension behaviour, byte for byte."""
    middleware = _make_middleware(lambda *a: None, behavior='unavailable')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }]


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


# ─────────────────────────────────────────────────────────────────────────
# A middleware that answers the handshake itself with a 2xx/3xx (the oauth2
# sign-in redirect for an expired session) is a rejection the client cannot
# act on as-is: `websockets` follows redirects to ws(s):// URLs only, and the
# ssh client would print "HTTP 307". It is told to authenticate instead.
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior', ['redirect', 'answered'])
async def test_websocket_signin_redirect_is_rejected_with_a_401(behavior):
    app_called = False

    async def app(scope, receive, send):
        del scope, receive, send
        nonlocal app_called
        app_called = True

    middleware = _make_middleware(app, behavior=behavior)
    scope = _scope_with_extension()
    sent = await _run(middleware, scope)

    assert not app_called
    assert [m['type'] for m in sent] == [
        'websocket.http.response.start', 'websocket.http.response.body'
    ]
    start, body = sent
    assert start['status'] == 401
    headers = dict(start['headers'])
    # No Location: a WebSocket client could not follow it anyway, and the
    # status must read as a rejection, not a redirect.
    assert b'location' not in headers
    assert headers == {b'content-type': b'application/json'}
    assert body['body'] == b'{"detail":"Authentication required"}'
    # The metrics layer attributes it as an auth rejection.
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_UNAUTHORIZED


@pytest.mark.asyncio
async def test_websocket_signin_redirect_without_the_extension_closes_4401():
    middleware = _make_middleware(lambda *a: None, behavior='redirect')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{
        'type': 'websocket.close',
        'code': 4401,
        'reason': 'Unauthorized',
    }]


@pytest.mark.asyncio
async def test_websocket_redirect_does_not_override_a_stamped_reason():
    """A middleware that stamped its own reason before redirecting keeps it."""

    class StampingRedirect(starlette.middleware.base.BaseHTTPMiddleware):

        async def dispatch(self, request, call_next):
            del call_next
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_FORBIDDEN)
            return fastapi.responses.RedirectResponse(url='http://x/denied')

    middleware = middleware_utils.websocket_aware(StampingRedirect)(
        lambda *a: None)
    scope = _scope_with_extension()
    sent = await _run(middleware, scope)
    assert sent[0]['status'] == 401
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_FORBIDDEN


# ─────────────────────────────────────────────────────────────────────────
# Refused handshakes are counted by decision, with or without the extension.
# Without it the close frames are the pre-extension ones, byte for byte.
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
async def test_a_refused_handshake_with_the_extension_is_counted_by_decision():
    middleware = _make_middleware(lambda *a: None, behavior='unavailable')
    scope = _scope_with_extension(path='/kubernetes-pod-ssh-proxy')
    sent = await _run(middleware, scope)
    assert sent[0]['status'] == 503
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path='/kubernetes-pod-ssh-proxy',
        outcome='error') == 1.0
    # The stamped reason stays on the scope for the metrics layer outside,
    # which records the rejection with the status the client saw; this layer
    # does not count it a second time.
    assert middleware_utils.get_rejection_reason(scope) == \
        middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT
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
