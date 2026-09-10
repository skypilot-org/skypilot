"""Rejected WebSocket handshakes on a real uvicorn transport.

`websocket_aware` answers a refused handshake with the middleware's real
HTTP status through the ASGI `websocket.http.response` extension, and falls
back to a pre-accept close (which servers render as an empty 403) when the
server does not advertise the extension. These tests drive that path through
the real server: an in-process uvicorn with each WebSocket implementation it
ships, a real `websockets` client, and the production middleware order
(metrics layer outermost). They check what the client receives, what the
counters record, and that the server never logs an ASGI exception.

The server itself pins `uvicorn[standard] >=0.33.0, <0.36.0`, which selects
the `websockets` implementation; `wsproto` and `websockets-sansio` are
covered when their packages are importable.
"""
import asyncio
import contextlib
import http
import logging
from typing import List

import fastapi
import pytest
import starlette.middleware.base
import uvicorn
import websockets
from websockets.asyncio.client import connect

from sky.metrics import utils as metrics_utils
from sky.server import metrics
from sky.server import middleware_utils

_GATE_HEADER = 'x-test-gate'
_WS_PATH = '/kubernetes-pod-ssh-proxy'
_CONNECT_TIMEOUT_SECONDS = 15


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
def clear_counters():
    counters = (metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL)
    for counter in counters:
        counter.clear()
    yield
    for counter in counters:
        counter.clear()


class GateMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Stands in for the auth middlewares: refuses per a request header."""

    async def dispatch(self, request, call_next):
        gate = request.headers.get(_GATE_HEADER, 'accept')
        if gate == 'accept':
            return await call_next(request)
        if gate == 'unavailable':
            # The shape db_lookup's helpers produce when the auth executor is
            # saturated: JSON 503, Retry-After, stamped reason.
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED)
            return fastapi.responses.JSONResponse(
                status_code=http.HTTPStatus.SERVICE_UNAVAILABLE,
                headers={'Retry-After': '5'},
                content={'detail': 'auth worker pool exhausted'})
        if gate == 'unauthorized':
            # The shape the bearer/basic auth middlewares produce.
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_UNAUTHORIZED)
            return fastapi.responses.JSONResponse(
                status_code=http.HTTPStatus.UNAUTHORIZED,
                headers={'WWW-Authenticate': 'Bearer'},
                content={'detail': 'Authentication required'})
        if gate == 'unrenderable':
            # A status no WebSocket client can act on and uvicorn's
            # `websockets` implementation cannot even put on the wire.
            middleware_utils.mark_rejection(
                request, middleware_utils.REJECT_REASON_FORBIDDEN)
            return fastapi.responses.JSONResponse(status_code=460,
                                                  content={'detail': 'odd'})
        raise AssertionError(f'unknown gate {gate!r}')


def _build_app() -> fastapi.FastAPI:
    app = fastapi.FastAPI()

    @app.websocket(_WS_PATH)
    async def ssh_proxy(websocket: fastapi.WebSocket):  # pylint: disable=unused-variable
        await websocket.accept()
        await websocket.send_text('hello')
        await websocket.close()

    # Production order (server.py): the websocket-aware auth middlewares,
    # then the metrics layer added LAST, i.e. outermost.
    app.add_middleware(middleware_utils.websocket_aware(GateMiddleware))
    app.add_middleware(metrics.PrometheusMiddleware)
    return app


class _WithoutExtension:
    """A server that does not offer `websocket.http.response`.

    Drops the extension from the scope before the application sees it, so
    `websocket_aware` has to take the close path. uvicorn still receives the
    close and renders it the way every server does: an empty HTTP 403.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            scope = dict(scope)
            scope.pop('extensions', None)
        await self.app(scope, receive, send)


class _AsgiErrors(logging.Handler):
    """Collects what uvicorn logs about the application at ERROR or above."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: List[str] = []

    def emit(self, record):
        self.records.append(self.format(record))


@contextlib.asynccontextmanager
async def _serve(app, ws_impl: str):
    """Run `app` under uvicorn in this event loop; yields (ws_url, errors)."""
    errors = _AsgiErrors()
    uvicorn_logger = logging.getLogger('uvicorn.error')
    uvicorn_logger.addHandler(errors)
    config = uvicorn.Config(app,
                            host='127.0.0.1',
                            port=0,
                            ws=ws_impl,
                            lifespan='off',
                            log_config=None,
                            log_level='error',
                            ws_per_message_deflate=False)
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    try:
        deadline = asyncio.get_running_loop().time() + _CONNECT_TIMEOUT_SECONDS
        while not server.started:
            if task.done():
                task.result()  # raises the startup error
                raise AssertionError('uvicorn exited before starting')
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError('uvicorn did not start in time')
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f'ws://127.0.0.1:{port}{_WS_PATH}', errors.records
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, _CONNECT_TIMEOUT_SECONDS)
        uvicorn_logger.removeHandler(errors)


async def _handshake(url: str, gate: str):
    """Connect; return ('accepted', first message) or ('rejected', response)."""
    headers = {_GATE_HEADER: gate}
    try:
        async with connect(url,
                           additional_headers=headers,
                           open_timeout=_CONNECT_TIMEOUT_SECONDS) as ws:
            return 'accepted', await ws.recv()
    except websockets.exceptions.InvalidStatus as e:
        return 'rejected', e.response


# uvicorn 0.35.0's `websockets-sansio` implementation does not mark the
# handshake complete after a `websocket.http.response` body, so it logs this
# line after every rejection through the extension although the client got
# the full response. It is not an application error; the two implementations
# SkyPilot can select (`websockets`, the default, and `wsproto`) log nothing.
_SANSIO_SPURIOUS_LINE = 'ASGI callable returned without completing handshake.'


def _assert_clean_server_log(errors: List[str], ws_impl: str) -> None:
    unexpected = [
        line for line in errors if not (
            ws_impl == 'websockets-sansio' and _SANSIO_SPURIOUS_LINE in line)
    ]
    assert unexpected == []
    assert not any('Exception in ASGI application' in line for line in errors)


def _ws_impls():
    impls = ['websockets']
    for impl, module in (('wsproto', 'wsproto'), (
            'websockets-sansio',
            'uvicorn.protocols.websockets.websockets_sansio_impl')):
        try:
            __import__(module)
        except ImportError:
            impls.append(
                pytest.param(
                    impl,
                    marks=pytest.mark.skip(reason=f'{module} not importable')))
        else:
            impls.append(impl)
    return impls


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_the_server_advertises_the_extension(ws_impl):
    seen = {}

    async def app(scope, receive, send):
        del receive
        seen['extensions'] = scope.get('extensions')
        await send({'type': 'websocket.close', 'code': 1011})

    async with _serve(app, ws_impl) as (url, _):
        outcome, response = await _handshake(url, 'accept')
    assert outcome == 'rejected'
    assert response.status_code == 403
    assert 'websocket.http.response' in seen['extensions']


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_accepted_handshake_still_works(ws_impl):
    async with _serve(_build_app(), ws_impl) as (url, errors):
        outcome, message = await _handshake(url, 'accept')
    assert (outcome, message) == ('accepted', 'hello')
    assert errors == []
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path=_WS_PATH,
                   outcome='accepted',
                   status='101') == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_rejection_reaches_the_client_with_status_headers_and_body(
        ws_impl):
    async with _serve(_build_app(), ws_impl) as (url, errors):
        outcome, response = await _handshake(url, 'unavailable')
    assert outcome == 'rejected'
    assert response.status_code == 503
    assert bytes(response.body) == b'{"detail":"auth worker pool exhausted"}'
    assert response.headers.get_all('retry-after') == ['5']
    assert 'application/json' in response.headers.get_all('content-type')
    _assert_clean_server_log(errors, ws_impl)
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path=_WS_PATH,
                   outcome='rejected',
                   status='503') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
                   status='503',
                   kind='websocket') == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_rejection_without_the_extension_is_the_old_empty_403(ws_impl):
    """A server without the extension: the client sees exactly what it saw
    before (empty 403), the counters record 403, nothing raises."""
    app = _WithoutExtension(_build_app())
    async with _serve(app, ws_impl) as (url, errors):
        outcome, response = await _handshake(url, 'unavailable')
    assert outcome == 'rejected'
    assert response.status_code == 403
    assert bytes(response.body or b'') == b''
    assert errors == []
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path=_WS_PATH,
                   outcome='rejected',
                   status='403') == 1.0
    # The stamped reason survives; the status is the one the client saw.
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED,
                   status='403',
                   kind='websocket') == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_unauthorized_rejection_is_the_403_older_clients_understand(
        ws_impl):
    """The middleware said 401; the wire says 403, the status every shipped
    `websocket_proxy.py` maps to the `sky api login` hint (it prints a bare
    `HTTP <code>` for anything else). The JSON body still travels, the 401
    challenge header does not, and the counters record the 403 the client
    saw with the finer-grained reason."""
    async with _serve(_build_app(), ws_impl) as (url, errors):
        outcome, response = await _handshake(url, 'unauthorized')
    assert outcome == 'rejected'
    assert response.status_code == 403
    assert bytes(response.body) == b'{"detail":"Authentication required"}'
    assert 'www-authenticate' not in {k.lower() for k in response.headers}
    _assert_clean_server_log(errors, ws_impl)
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path=_WS_PATH,
                   outcome='rejected',
                   status='403') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_UNAUTHORIZED,
                   status='403',
                   kind='websocket') == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize('ws_impl', _ws_impls())
async def test_unrenderable_status_does_not_break_the_server(ws_impl):
    """Without the guard in websocket_aware the `websockets` implementation
    raises `ValueError: 460 is not a valid HTTPStatus`, logs an ASGI
    exception and answers a bare 500."""
    async with _serve(_build_app(), ws_impl) as (url, errors):
        outcome, response = await _handshake(url, 'unrenderable')
    assert outcome == 'rejected'
    assert response.status_code == 403
    assert bytes(response.body) == b'{"detail":"odd"}'
    _assert_clean_server_log(errors, ws_impl)
    assert _sample(metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKES_TOTAL,
                   path=_WS_PATH,
                   outcome='rejected',
                   status='403') == 1.0
