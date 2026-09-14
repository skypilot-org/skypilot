"""Utilities for building middlewares."""
import enum
import http
import threading
import time
from typing import Any, Callable, List, Optional, Tuple, Type

import fastapi
import starlette.middleware.base
import starlette.types

from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

# The metrics middleware is the outermost one, so it observes 100% of
# responses. A failure while *recording* -- a full or unwritable
# PROMETHEUS_MULTIPROC_DIR, a label value the client library rejects -- must
# never reach a client, or a monitoring bug becomes an outage on the very
# path the metrics exist to watch. Recording on the request path runs through
# `record_safely`: the observation is dropped, the response is returned
# unchanged, and the failure is logged.
#
# These helpers live here, next to `record_rejection`, rather than in
# sky/server/metrics.py: that module imports this one, so anything here that
# needed them would close an import cycle. This module's dependency on
# sky.metrics.utils is not new -- `record_rejection` and the handshake
# counter already have it.
#
# The log is rate-limited per process. The first failure is logged at WARNING
# with its traceback; later ones at most once per
# `RECORDING_FAILURE_LOG_INTERVAL_SECONDS`, with a count of the failures
# dropped silently in between, so a persistent fault cannot flood the log at
# request rate.
RECORDING_FAILURE_LOG_INTERVAL_SECONDS = 300.0


class RecordingFailureLog:
    """Rate-limited WARNING for metrics-recording failures."""

    def __init__(
            self,
            interval_seconds: float = RECORDING_FAILURE_LOG_INTERVAL_SECONDS,
            clock: Callable[[], float] = time.time):
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_logged_at: Optional[float] = None
        self._suppressed = 0
        # Total failures noted in this process, logged or not.
        self.failures = 0

    def note(self, what: str, exc: BaseException) -> None:
        """Log that recording `what` failed with `exc`, unless rate-limited."""
        now = self._clock()
        with self._lock:
            self.failures += 1
            if (self._last_logged_at is not None and
                    now - self._last_logged_at < self._interval_seconds):
                self._suppressed += 1
                return
            first = self._last_logged_at is None
            suppressed = self._suppressed
            self._suppressed = 0
            self._last_logged_at = now
        detail = (f' {suppressed} more failure(s) were dropped silently '
                  'since the previous message.' if suppressed else '')
        logger.warning(
            f'Failed to record API server metrics for {what}: '
            f'{type(exc).__name__}: {exc}. The request was served '
            f'normally and this observation was dropped.{detail} Further '
            f'failures are logged at most once every '
            f'{self._interval_seconds:g} seconds.',
            exc_info=exc if first else None)


_recording_failure_log = RecordingFailureLog()


def note_recording_failure(what: str, exc: BaseException) -> None:
    """Log a metrics-recording failure through the process rate limiter."""
    _recording_failure_log.note(what, exc)


def record_safely(what: str, record: Callable[..., Any], *args: Any,
                  **kwargs: Any) -> None:
    """Call `record(*args, **kwargs)`; log and swallow any exception.

    For metrics recording on the request path only: the caller must be able
    to carry on exactly as if the call had succeeded.
    """
    try:
        record(*args, **kwargs)
    except Exception as e:  # pylint: disable=broad-except
        note_recording_failure(what, e)


# Reasons a middleware answers a request itself instead of letting a route
# handler run. Stamped on the request with `mark_rejection` by the code that
# produces the response and read back by whoever records the response: the
# metrics middleware for HTTP requests, `websocket_aware` for handshakes. This
# is a closed set on purpose: the reason is a metric label
# (`sky_apiserver_request_rejections_total{reason}`), so every value added
# here is a new series per (status, kind).
REJECT_REASON_AUTH_WORKER_EXHAUSTED = 'auth_worker_exhausted'
REJECT_REASON_AUTH_DB_TIMEOUT = 'auth_db_timeout'
REJECT_REASON_ROLE_SEED_UNAVAILABLE = 'role_seed_unavailable'
REJECT_REASON_JWT_SECRET_UNAVAILABLE = 'jwt_secret_unavailable'
REJECT_REASON_UNAUTHORIZED = 'unauthorized'
REJECT_REASON_FORBIDDEN = 'forbidden'
REJECT_REASON_SHUTTING_DOWN = 'shutting_down'
REJECT_REASON_API_VERSION = 'api_version'
# Could not reach the auth proxy at all (connection error, timeout).
REJECT_REASON_AUTH_PROXY_UNAVAILABLE = 'auth_proxy_unavailable'
# Reached it, and the answer was unusable: authenticated but no user info, or
# a status this server does not know how to act on. A different failure from
# unavailability and a different fix, so it gets its own reason. Note that
# the `status` recorded alongside it can be the proxy's own status code --
# the one label value in this counter that upstream chooses rather than this
# code (bounded by the HTTP status set).
REJECT_REASON_AUTH_PROXY_BAD_RESPONSE = 'auth_proxy_bad_response'
REJECT_REASON_REQUEST_WORKER_EXHAUSTED = 'request_worker_exhausted'
# Stamped by `websocket_aware` when a middleware raises while judging a
# WebSocket handshake: the refusal goes out as a 500-class answer.
REJECT_REASON_UNHANDLED_EXCEPTION = 'unhandled_exception'

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

    Call it right before returning the response from a middleware.
    `request.state` is backed by the ASGI scope's `state` dict, which every
    middleware layer shares, so the layer that records the response sees the
    value. Explicit parameter on purpose: no contextvars.
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
# `www-authenticate` is not in the list on purpose: it belongs to a 401, and
# every authentication refusal leaves here as a 403 (`_AUTH_REFUSAL_STATUS`).
_REJECTION_HEADERS_TO_FORWARD = frozenset(('content-type', 'retry-after'))

# The status put on the wire for a refused handshake whose cause is
# authentication or authorization, whatever the middleware's own status (401,
# 403, or a 2xx/3xx sign-in redirect answered without `call_next`).
#
# Servers have always rendered a refused handshake as an empty HTTP 403, and
# every ssh client shipped before this code (`sky/templates/websocket_proxy.py`)
# maps exactly that status to "Authentication required ... run `sky api login`"
# and prints a bare status code for anything else. Sending the middleware's
# 401 would turn the hint into `HTTP 401` for every older client, and an
# expired or revoked token is the everyday refusal. Newer clients treat 401
# and 403 alike, so nothing is lost for them. The metrics still tell the two
# apart: the reason stamped on the scope stays `unauthorized` / `forbidden`;
# `status` records the 403 the client saw.
_AUTH_REFUSAL_STATUS = int(http.HTTPStatus.FORBIDDEN)

# ASGI extension through which a server lets the application answer a
# WebSocket handshake with an arbitrary HTTP response instead of a 101 or a
# close. uvicorn advertises it with every WebSocket implementation it ships
# (`websockets`, `wsproto`, `websockets-sansio`); so does Starlette's
# TestClient.
_WS_HTTP_RESPONSE_EXTENSION = 'websocket.http.response'

# Key in `scope['state']` marking an accepted handshake as already counted:
# every `websocket_aware` middleware in the stack forwards an accepted
# handshake, so the accept message passes back out through each wrapper.
_WS_ACCEPT_COUNTED_KEY = 'websocket_accept_counted'


def supports_websocket_http_response(scope: starlette.types.Scope) -> bool:
    """Whether the server accepts `websocket.http.response.*` messages.

    `extensions` is optional in the ASGI spec; a server may omit the key or
    set it to None. Both mean "not supported".
    """
    extensions = scope.get('extensions') or {}
    return _WS_HTTP_RESPONSE_EXTENSION in extensions


def _renderable_status(status_code: int) -> int:
    """A status the server can put on a rejected handshake.

    uvicorn's default `websockets` implementation looks the status up in
    `http.HTTPStatus`; a code that is not a member (460, 599, ...) makes the
    lookup raise inside the server, which then logs a traceback and answers
    with a bare 500. Such a code carries no meaning a WebSocket client could
    act on anyway, so send what the client would have seen before the
    extension was used (an empty 403) for a 4xx, and a plain 500 for a 5xx.
    """
    try:
        http.HTTPStatus(status_code)
    except ValueError:
        if status_code < 500:
            return int(http.HTTPStatus.FORBIDDEN)
        return int(http.HTTPStatus.INTERNAL_SERVER_ERROR)
    return int(status_code)


def _refusal_wire_status(decision: WebSocketDecision,
                         response: Optional[fastapi.Response]) -> int:
    """The status a refused handshake will put on the wire.

    What `_reject_with_http_response` sends, so the counters record the
    status the client actually receives rather than the middleware's own.
    Without the `websocket.http.response` extension every server renders a
    pre-accept close as an empty 403; the caller handles that case.
    """
    if response is None:
        # The middleware raised; mirror what Starlette's error handler would
        # have sent for an HTTP request.
        return int(http.HTTPStatus.INTERNAL_SERVER_ERROR)
    if decision in (WebSocketDecision.UNAUTHORIZED,
                    WebSocketDecision.FORBIDDEN):
        return _AUTH_REFUSAL_STATUS
    return _renderable_status(response.status_code)


def _forwardable_parts(
        response: fastapi.Response) -> Tuple[bytes, List[Tuple[bytes, bytes]]]:
    """The body and the headers of a rejection worth sending to a client."""
    body = bytes(getattr(response, 'body', b'') or b'')
    headers = [(k.encode('latin-1'), v.encode('latin-1'))
               for k, v in response.headers.items()
               if k.lower() in _REJECTION_HEADERS_TO_FORWARD]
    return body, headers


def websocket_aware(
        middleware_cls: Type[starlette.middleware.base.BaseHTTPMiddleware]):
    """Decorator to adapt BaseHTTPMiddleware to handle WebSockets.

    It assembles an HTTP-style request like the HTTP upgrade request during
    websocket handshake and then delegates it to the real HTTP middleware.
    The websocket connection will be rejected if the HTTP middleware returns
    a 4xx or 5xx status code, or if it answers the handshake itself with any
    other status instead of calling ``call_next`` (in practice a redirect to
    a sign-in page): that counts as an authentication refusal, since a
    WebSocket client cannot follow an http(s) redirect and has to
    authenticate first.

    When the ASGI server supports the ``websocket.http.response`` extension
    (uvicorn does, with either WebSocket implementation), a rejected
    handshake is answered with the middleware's real status code, JSON body
    and ``Retry-After``, so a client sees e.g.
    ``503 {"detail": "...exhausted..."}`` and can retry, instead of a bare
    403 that reads as "log in again". The one exception is an
    authentication or authorization refusal: it keeps the 403 every shipped
    client already maps to the login hint, with the JSON body (see
    `_AUTH_REFUSAL_STATUS`). Without the extension (key absent, None, or
    listing other extensions only) the connection is closed with
    4401 / 4403 / 1011 as before; servers render any pre-accept close as an
    empty HTTP 403. A status the server cannot render is replaced by one it
    can (see `_renderable_status`).

    Counting: a refused handshake is counted in
    `sky_apiserver_websocket_handshake_rejections_total{path,outcome}` and,
    when a reason was stamped (`mark_rejection`), in
    `sky_apiserver_request_rejections_total{kind="websocket"}` -- with the
    status the client actually receives, so the counter never disagrees
    with the wire. An accepted handshake is counted once in
    `sky_apiserver_websocket_handshake_accepts_total{path}`. Only the
    outermost middleware that refuses runs, so each refused handshake is
    counted once; the accept is counted by the first wrapper to see the
    accept message (see `_WS_ACCEPT_COUNTED_KEY`).

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
                await self._forward_counting_accept(scope, receive, send)
                return
            # Refused. Count what the client will actually receive, then
            # send it: the extension path below puts the real (possibly
            # mapped) status on the wire; without it every server renders a
            # pre-accept close as an empty HTTP 403.
            if supports_websocket_http_response(scope):
                self._count_rejection(scope, decision,
                                      _refusal_wire_status(decision, response))
                await self._reject_with_http_response(send, decision, response)
                return
            self._count_rejection(scope, decision,
                                  int(http.HTTPStatus.FORBIDDEN))
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

        async def _forward_counting_accept(self, scope: starlette.types.Scope,
                                           receive: starlette.types.Receive,
                                           send: starlette.types.Send):
            """Forward an accepted handshake, counting its acceptance once.

            Every `websocket_aware` middleware in the stack forwards an
            accepted handshake, so the accept message passes back out
            through each wrapper; the first one to see it counts and marks
            the shared scope state, so the handshake is counted once no
            matter how many wrappers are stacked.
            """
            state = scope.setdefault('state', {})

            async def send_wrapper(message: starlette.types.Message):
                if (message.get('type') == 'websocket.accept' and
                        not state.get(_WS_ACCEPT_COUNTED_KEY)):
                    state[_WS_ACCEPT_COUNTED_KEY] = True
                    record_safely(
                        'accepted WebSocket handshake',
                        metrics_utils.
                        SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL.labels(
                            path=websocket_path_label(scope)).inc)
                await send(message)

            await self.app(scope, receive, send_wrapper)

        @staticmethod
        def _count_rejection(scope: starlette.types.Scope,
                             decision: WebSocketDecision,
                             wire_status: int) -> None:
            """Count a refused handshake with the status the client sees."""
            record_safely(
                'refused WebSocket handshake',
                metrics_utils.
                SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL.labels(
                    path=websocket_path_label(scope),
                    outcome=decision.value).inc)
            # The status the client receives (e.g. the 403 an authentication
            # refusal is mapped to, the renderable stand-in for a code the
            # server cannot send), not the middleware's own: the counter must
            # never disagree with the wire. A no-op when nothing stamped a
            # reason; the exception path stamps `unhandled_exception` with no
            # response at all.
            record_safely('refused WebSocket handshake reason',
                          record_rejection, scope, wire_status,
                          REJECTION_KIND_WEBSOCKET)

        @staticmethod
        async def _reject_with_http_response(
                send: starlette.types.Send, decision: WebSocketDecision,
                response: Optional[fastapi.Response]) -> None:
            """Reject the handshake with an HTTP response.

            The status is the middleware's own, except for an authentication
            or authorization refusal, which goes out as the 403 older clients
            understand (`_AUTH_REFUSAL_STATUS`) whatever the middleware
            answered with.
            """
            status_code = _refusal_wire_status(decision, response)
            body: bytes
            headers: List[Tuple[bytes, bytes]]
            if response is None:
                # The middleware raised; mirror what Starlette's error handler
                # would have sent for an HTTP request.
                body = b'{"detail":"Internal Server Error"}'
                headers = [(b'content-type', b'application/json')]
            elif decision in (WebSocketDecision.UNAUTHORIZED,
                              WebSocketDecision.FORBIDDEN):
                if 400 <= response.status_code < 600:
                    # A 401 or 403 from the middleware: keep its explanation.
                    body, headers = _forwardable_parts(response)
                else:
                    # The middleware answered the handshake itself with a
                    # 2xx/3xx instead of letting it through -- the oauth2
                    # sign-in redirect for an expired session is the real
                    # case. Forwarding that status would only confuse the
                    # client: `websockets` follows redirects to ws(s):// URLs
                    # only, and a 2xx/3xx is not a rejection it can explain
                    # (the ssh client would print "HTTP 307"). Say what it
                    # needs to do instead: authenticate.
                    body = b'{"detail":"Authentication required"}'
                    headers = [(b'content-type', b'application/json')]
            else:
                body, headers = _forwardable_parts(response)
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
            (None when it raised).
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
