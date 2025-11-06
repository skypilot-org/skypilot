"""Unit tests for the middleware utilities."""

import http

import fastapi
import pytest
import starlette.middleware.base

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
