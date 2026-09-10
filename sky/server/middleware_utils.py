"""Utilities for building middlewares."""
import enum
import http
import threading
import time
from typing import Any, Callable, Optional, Tuple, Type

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


def websocket_aware(
        middleware_cls: Type[starlette.middleware.base.BaseHTTPMiddleware]):
    """Decorator to adapt BaseHTTPMiddleware to handle WebSockets.

    It assembles an HTTP-style request like the HTTP upgrade request during
    websocket handshake and then delegates it to the real HTTP middleware.
    The websocket connection will be rejected if the HTTP middleware returns
    a 4xx or 5xx status code.

    A refused handshake is counted in
    `sky_apiserver_websocket_handshake_rejections_total{path,outcome}` and,
    when the HTTP middleware stamped a reason (`mark_rejection`), in
    `sky_apiserver_request_rejections_total{kind="websocket"}` with the
    status the middleware answered with. Only the outermost middleware that
    refuses runs, so each refused handshake is counted once. What the client
    receives is unchanged: the close codes below, which ASGI servers render
    as an empty HTTP 403.

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
            self._count_rejection(scope, decision, response)
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
        def _count_rejection(scope: starlette.types.Scope,
                             decision: WebSocketDecision,
                             response: Optional[fastapi.Response]) -> None:
            """Count a refused handshake; the client's answer is unchanged."""
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL \
                .labels(path=websocket_path_label(scope),
                        outcome=decision.value).inc()
            if response is not None:
                # The status the HTTP middleware answered with (e.g. 503 for
                # an exhausted auth executor), not the 403 the client sees.
                record_rejection(scope, response.status_code,
                                 REJECTION_KIND_WEBSOCKET)

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
                return WebSocketDecision.ERROR, None

            if response is None:
                response = stub_response

            status_code = response.status_code

            if call_next_called and 200 <= status_code < 400:
                return WebSocketDecision.ACCEPT, response
            if status_code == http.HTTPStatus.UNAUTHORIZED:
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
