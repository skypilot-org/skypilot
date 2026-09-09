"""Utilities for building middlewares."""
import enum
import http
from typing import Optional, Tuple, Type

import fastapi
import starlette.middleware.base
import starlette.types

from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

# Reasons a middleware (or the app-level exhaustion handler) answers a request
# itself instead of letting a route handler run. Stamped on the request with
# `mark_rejection` and read back by the metrics middleware, which records them
# in `sky_apiserver_request_rejections_total{reason}`. This is a closed set on
# purpose: the reason is a metric label, so every value added here is a new
# series per (status, kind).
REJECT_REASON_AUTH_WORKER_EXHAUSTED = 'auth_worker_exhausted'
REJECT_REASON_AUTH_DB_TIMEOUT = 'auth_db_timeout'
REJECT_REASON_ROLE_SEED_UNAVAILABLE = 'role_seed_unavailable'
REJECT_REASON_JWT_SECRET_UNAVAILABLE = 'jwt_secret_unavailable'
REJECT_REASON_UNAUTHORIZED = 'unauthorized'
REJECT_REASON_FORBIDDEN = 'forbidden'
REJECT_REASON_SHUTTING_DOWN = 'shutting_down'
REJECT_REASON_API_VERSION = 'api_version'
REJECT_REASON_AUTH_PROXY_UNAVAILABLE = 'auth_proxy_unavailable'
REJECT_REASON_REQUEST_WORKER_EXHAUSTED = 'request_worker_exhausted'
# Set by the metrics middleware itself when an exception escapes every
# middleware and Starlette turns it into a bare 500.
REJECT_REASON_UNHANDLED_EXCEPTION = 'unhandled_exception'
# Used by the metrics middleware for a WebSocket handshake that was rejected
# without anyone stamping a reason (e.g. a route handler closing before it
# accepted).
REJECT_REASON_UNSPECIFIED = 'unspecified'

# Key in `scope['state']` (i.e. `request.state`) the reason is stored under.
REJECT_REASON_STATE_KEY = 'reject_reason'

# `kind` label values of the rejection counter.
REJECTION_KIND_HTTP = 'http'
REJECTION_KIND_WEBSOCKET = 'websocket'

# `path` label of the handshake-rejection counter: the registered WebSocket
# routes (sky/server/server.py) or this fixed value. Handshake paths are
# chosen by the client, so the raw path cannot be a label value.
WEBSOCKET_ROUTE_PATHS = frozenset((
    '/kubernetes-pod-ssh-proxy',
    '/slurm-job-ssh-proxy',
    '/ssh-interactive-auth',
))
OTHER_WEBSOCKET_PATH_LABEL = 'other'


def mark_rejection(request: fastapi.Request, reason: str) -> None:
    """Record why this request is being answered with a canned response.

    Call it right before returning the response from a middleware (or an
    app-level exception handler). `request.state` is backed by the ASGI
    scope's `state` dict, which every middleware layer shares, so the
    outermost metrics middleware sees the value when the response passes
    through it. Explicit parameter on purpose: no contextvars.
    """
    setattr(request.state, REJECT_REASON_STATE_KEY, reason)


def get_rejection_reason(scope: starlette.types.Scope) -> Optional[str]:
    """The reason stamped by `mark_rejection`, from a raw ASGI scope."""
    state = scope.get('state')
    if not state:
        return None
    return state.get(REJECT_REASON_STATE_KEY)


def record_rejection(scope: starlette.types.Scope, status_code: int,
                     kind: str) -> None:
    """Count a rejection in `sky_apiserver_request_rejections_total`.

    A no-op when nothing stamped a reason on the request: the response was
    not one of the canned rejections this counter is about (or came from
    code that does not attribute its answers yet).
    """
    reason = get_rejection_reason(scope)
    if reason is None:
        return
    if int(status_code) < 400:
        # A stamp without a refusal: the middleware marked a reason and then
        # let the request through, and the route answered it. Not a
        # rejection; this counter must never disagree with the status.
        return
    # int() first: responses built with an `http.HTTPStatus` member would
    # otherwise label the series `HTTPStatus.SERVICE_UNAVAILABLE`.
    metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL.labels(
        reason=reason, status=str(int(status_code)), kind=kind).inc()


def websocket_path_label(scope: starlette.types.Scope) -> str:
    """Bounded `path` label for a WebSocket handshake."""
    path = scope.get('path', '')
    if path in WEBSOCKET_ROUTE_PATHS:
        return path
    return OTHER_WEBSOCKET_PATH_LABEL


class WebSocketDecision(enum.Enum):
    ACCEPT = 'accept'
    UNAUTHORIZED = 'unauthorized'
    FORBIDDEN = 'forbidden'
    ERROR = 'error'


# Response headers worth forwarding on a rejected handshake. Anything else the
# HTTP middleware set (security headers, CORS, ...) is dropped: the handshake
# response is consumed by a WebSocket client, not a browser page.
_REJECTION_HEADERS_TO_FORWARD = frozenset(
    ('content-type', 'retry-after', 'www-authenticate'))


def websocket_aware(
        middleware_cls: Type[starlette.middleware.base.BaseHTTPMiddleware]):
    """Decorator to adapt BaseHTTPMiddleware to handle WebSockets.

    It assembles an HTTP-style request like the HTTP upgrade request during
    websocket handshake and then delegates it to the real HTTP middleware.
    The websocket connection will be rejected if the HTTP middleware returns
    a 4xx or 5xx status code, or if it answers the handshake itself with any
    other status instead of calling ``call_next`` (in practice a redirect to
    a sign-in page): that counts as 401, since a WebSocket client cannot
    follow an http(s) redirect and has to authenticate first.

    When the ASGI server supports the ``websocket.http.response`` extension
    (uvicorn does, with either WebSocket implementation), a rejected
    handshake is answered with the HTTP middleware's real status code and
    JSON body, so a client sees e.g. ``503 {"detail": "...exhausted..."}``
    and can retry, instead of a bare 403 that reads as "log in again".
    Without the extension the connection is closed with 4401 / 4403 / 1011
    as before; servers render any pre-accept close as an empty HTTP 403.
    Either way a refused handshake is counted by decision in
    `sky_apiserver_websocket_handshake_rejections_total{path,outcome}`; the
    metrics layer records the client-visible status and the stamped reason.

    Note: for websocket connection, the mutation made by the underlying HTTP
    middleware on the request and response will be discarded.
    """

    class WebSocketAwareMiddleware:
        """WebSocket-aware middleware wrapper."""

        def __init__(self, app: starlette.types.ASGIApp, *args, **kwargs):
            self.app = app
            self.middleware = middleware_cls(app, *args, **kwargs)

        async def __call__(self, scope: starlette.types.Scope,
                           receive: starlette.types.Receive,
                           send: starlette.types.Send):
            scope_type = scope.get('type')
            if scope_type == 'websocket':
                await self._handle_websocket(scope, receive, send)
            else:
                # Delegate other scopes to the underlying HTTP middleware.
                await self.middleware(scope, receive, send)

        async def dispatch(
                self, request: fastapi.Request,
                call_next: starlette.middleware.base.RequestResponseEndpoint):
            """Implement dispatch method to keep compatibility."""
            return await self.middleware.dispatch(request, call_next)

        async def _handle_websocket(self, scope: starlette.types.Scope,
                                    receive: starlette.types.Receive,
                                    send: starlette.types.Send):
            """Handle websocket connection by delegating to HTTP middleware."""
            decision, response = await self._run_websocket_dispatch(scope)
            if decision == WebSocketDecision.ACCEPT:
                await self.app(scope, receive, send)
                return
            # By decision; the metrics layer outside records the status the
            # client saw and the stamped reason.
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL \
                .labels(path=websocket_path_label(scope),
                        outcome=decision.value).inc()
            if 'websocket.http.response' in scope.get('extensions', {}):
                await self._reject_with_http_response(send, response)
                return
            if decision == WebSocketDecision.UNAUTHORIZED:
                await send({
                    'type': 'websocket.close',
                    'code': 4401,
                    'reason': 'Unauthorized',
                })
            elif decision == WebSocketDecision.FORBIDDEN:
                await send({
                    'type': 'websocket.close',
                    'code': 4403,
                    'reason': 'Forbidden',
                })
            else:
                await send({
                    'type': 'websocket.close',
                    'code': 1011,
                    'reason': 'Internal Server Error',
                })

        @staticmethod
        async def _reject_with_http_response(
                send: starlette.types.Send,
                response: Optional[fastapi.Response]) -> None:
            """Reject the handshake with the middleware's real HTTP response."""
            if response is None:
                # The middleware raised; mirror what Starlette's error handler
                # would have sent for an HTTP request.
                status_code = http.HTTPStatus.INTERNAL_SERVER_ERROR
                body = b'{"detail":"Internal Server Error"}'
                headers = [(b'content-type', b'application/json')]
            elif 400 <= response.status_code < 600:
                status_code = response.status_code
                body = bytes(getattr(response, 'body', b'') or b'')
                headers = [(k.encode('latin-1'), v.encode('latin-1'))
                           for k, v in response.headers.items()
                           if k.lower() in _REJECTION_HEADERS_TO_FORWARD]
            else:
                # The middleware answered the handshake itself with a 2xx/3xx
                # instead of letting it through -- the oauth2 sign-in redirect
                # for an expired session is the real case. Forwarding that
                # status would only confuse the client: `websockets` follows
                # redirects to ws(s):// URLs only, and a 2xx/3xx is not a
                # rejection it can explain (the ssh client would print
                # "HTTP 307"). Say what it needs to do instead: authenticate.
                # This also keeps the handshake metric's client_status label
                # to real rejection statuses.
                status_code = http.HTTPStatus.UNAUTHORIZED
                body = b'{"detail":"Authentication required"}'
                headers = [(b'content-type', b'application/json')]
            await send({
                'type': 'websocket.http.response.start',
                'status': int(status_code),
                'headers': headers,
            })
            await send({
                'type': 'websocket.http.response.body',
                'body': body,
            })

        async def _run_websocket_dispatch(
            self, scope: starlette.types.Scope
        ) -> Tuple[WebSocketDecision, Optional[fastapi.Response]]:
            """Run the HTTP middleware against the handshake.

            Returns the decision and the response the middleware produced
            (None when it raised), so a rejection can carry the real status.
            """
            http_scope = self._build_http_scope(scope)
            http_receive = self._http_receive_adapter()
            request = fastapi.Request(http_scope, receive=http_receive)
            call_next_called = False
            stub_response = fastapi.Response(status_code=http.HTTPStatus.OK)

            async def call_next(req):
                del req
                # Capture whether call_next() is called in the underlying
                # HTTP middleware to determine if we can proceed with current
                # websocket connection.
                nonlocal call_next_called
                call_next_called = True
                return stub_response

            try:
                response = await self.dispatch(request, call_next)
            except Exception as e:  # pylint: disable=broad-except
                logger.error('Exception occurred in middleware dispatch for '
                             f'WebSocket scope: {e}')
                mark_rejection(request, REJECT_REASON_UNHANDLED_EXCEPTION)
                return WebSocketDecision.ERROR, None

            if response is None:
                response = stub_response

            status_code = response.status_code

            if call_next_called and 200 <= status_code < 400:
                return WebSocketDecision.ACCEPT, response
            if status_code == http.HTTPStatus.UNAUTHORIZED:
                return WebSocketDecision.UNAUTHORIZED, response
            if status_code < 400:
                # Answered without `call_next`: the middleware served the
                # handshake itself (a sign-in redirect, typically). The client
                # must authenticate; see `_reject_with_http_response`. Without
                # the extension this closes with 4401 rather than 1011 -- both
                # reach the client as an empty HTTP 403, as before.
                if get_rejection_reason(scope) is None:
                    mark_rejection(request, REJECT_REASON_UNAUTHORIZED)
                return WebSocketDecision.UNAUTHORIZED, response
            if status_code == http.HTTPStatus.FORBIDDEN:
                return WebSocketDecision.FORBIDDEN, response
            return WebSocketDecision.ERROR, response

        @staticmethod
        def _build_http_scope(
                scope: starlette.types.Scope) -> starlette.types.Scope:
            state = scope.setdefault('state', {})
            scheme = scope.get('scheme', 'ws')
            if scheme == 'ws':
                http_scheme = 'http'
            elif scheme == 'wss':
                http_scheme = 'https'
            else:
                http_scheme = scheme
            http_scope = dict(scope)
            http_scope['type'] = 'http'
            http_scope['scheme'] = http_scheme
            http_scope['method'] = 'GET'
            http_scope['http_version'] = scope.get('http_version', '1.1')
            http_scope['state'] = state
            return http_scope

        @staticmethod
        def _http_receive_adapter() -> starlette.types.Receive:
            """Adapter thatmimics the sequence produced by Starlette for an HTTP
            request: a single http.request event followed by a http.disconnect
            """
            sent = False

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {
                        'type': 'http.request',
                        'body': b'',
                        'more_body': False,
                    }
                return {
                    'type': 'http.disconnect',
                }

            return receive

    WebSocketAwareMiddleware.__name__ = middleware_cls.__name__
    WebSocketAwareMiddleware.__qualname__ = middleware_cls.__qualname__
    WebSocketAwareMiddleware.__module__ = middleware_cls.__module__
    WebSocketAwareMiddleware.__doc__ = middleware_cls.__doc__
    return WebSocketAwareMiddleware
