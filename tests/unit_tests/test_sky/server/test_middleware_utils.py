"""Unit tests for the middleware utilities."""

import enum
import http

import fastapi
import pytest
import starlette.middleware.base

from sky.metrics import utils as metrics_utils
from sky.server import middleware_utils


class NamedIntStatus(enum.IntEnum):
    """An IntEnum whose str() is its name, as `http.HTTPStatus` is before 3.11.

    On 3.11+ `str(http.HTTPStatus.SERVICE_UNAVAILABLE)` is already '503', so a
    label built with str() alone looks right on a modern interpreter and only
    breaks on the 3.9 CI leg. This makes the difference testable everywhere.
    """
    SERVICE_UNAVAILABLE = 503

    def __str__(self) -> str:
        return 'HTTPStatus.SERVICE_UNAVAILABLE'


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
        if self.behavior == 'enum_status':
            return fastapi.Response(
                status_code=NamedIntStatus.SERVICE_UNAVAILABLE)
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


def _label_sets(counter) -> set:
    """The label sets this counter currently publishes.

    `_sample` sums, so it returns 0.0 both for a series that sits at zero and
    for one that does not exist. Pre-initialisation is the claim that the
    series exists *at* zero, and only a membership check can tell those
    apart.
    """
    published = set()
    for family in counter.collect():
        for sample in family.samples:
            if sample.name.endswith('_total'):
                published.add(tuple(sorted(sample.labels.items())))
    return published


@pytest.fixture(autouse=True)
def clear_rejection_counters():
    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL,
            metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL):
        counter.clear()
    yield
    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL,
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
@pytest.mark.parametrize('behavior, outcome, status, close_frame', [
    ('unauthorized', 'unauthorized', '401', {
        'type': 'websocket.close',
        'code': 4401,
        'reason': 'Unauthorized',
    }),
    ('forbidden', 'forbidden', '403', {
        'type': 'websocket.close',
        'code': 4403,
        'reason': 'Forbidden',
    }),
    ('error', 'error', '500', {
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }),
    ('unavailable', 'error', '503', {
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }),
])
async def test_a_refused_handshake_is_counted_and_the_close_frame_is_unchanged(
        behavior, outcome, status, close_frame):
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
    # `status` is the one the middleware answered with, not the close code
    # the client sees, and not `outcome`: the last two rows share an outcome
    # and differ only here (503 from the auth helpers vs 500 from a crash),
    # which is the distinction an error-ratio alert runs on. Asserted as a
    # decimal string because RecordingMiddleware answers with `http.HTTPStatus`
    # members, whose str() is the member name before Python 3.11 -- so this
    # also pins the int() conversion on the 3.9 CI leg.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        path='/kubernetes-pod-ssh-proxy',
        outcome=outcome,
        status=status) == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 1.0
    # A refused handshake is an attempt too, by design: the outcome split
    # reads off these two counters.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
        path='/kubernetes-pod-ssh-proxy') == 1.0


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
@pytest.mark.parametrize('behavior', ['unauthorized', 'forbidden'])
async def test_a_refused_handshake_without_a_reason_is_not_attributed(behavior):
    """A plain 401/403 with no stamped reason is counted by decision only.
    (The `error` behavior is not here: a crash in dispatch stamps
    `unhandled_exception`, which is attributed -- see below.)"""
    middleware = _make_middleware(lambda *a: None, behavior=behavior)
    await _run(middleware, _make_websocket_scope())
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL) == 0.0


@pytest.mark.asyncio
async def test_an_accepted_handshake_is_counted_as_an_attempt():

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    middleware = _make_middleware(app, behavior='accept')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{'type': 'websocket.accept'}]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 0.0
    # The attempt is counted, by bounded path, and so is the accept --
    # observed on the wire, not inferred from the middlewares' verdict.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
        path='other') == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL,
        path='other') == 1.0


@pytest.mark.asyncio
async def test_a_handshake_attempt_is_counted_once_through_the_stack():
    """Every websocket_aware middleware in the stack sees the same
    connection scope; the attempt must still be counted once."""

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    inner = middleware_utils.websocket_aware(RecordingMiddleware)
    outer = middleware_utils.websocket_aware(RecordingMiddleware)
    middleware = outer(inner(app, behavior='accept'), behavior='accept')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{'type': 'websocket.accept'}]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_a_handshake_closed_by_the_handler_is_still_an_attempt():
    """The count is the attempt, not the verdict: a handler that closes the
    connection before accepting is an attempt, no refusal, and no accept."""

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.close', 'code': 1000})

    middleware = _make_middleware(app, behavior='accept')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{'type': 'websocket.close', 'code': 1000}]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == 0.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 0.0


@pytest.mark.asyncio
async def test_a_crash_judging_a_handshake_is_attributed():
    """A middleware exception while judging the handshake is stamped
    `unhandled_exception`, so the reason counter says what the `error`
    outcome was instead of leaving it unexplained. The client still gets
    the 1011 close."""
    middleware = _make_middleware(lambda *a: None, behavior='error')
    sent = await _run(middleware, _make_websocket_scope())
    assert sent == [{
        'type': 'websocket.close',
        'code': 1011,
        'reason': 'Internal Server Error',
    }]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == 1.0
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL,
        outcome='error') == 1.0
    assert _sample(metrics_utils.SKY_APISERVER_REQUEST_REJECTIONS_TOTAL,
                   reason=middleware_utils.REJECT_REASON_UNHANDLED_EXCEPTION,
                   status='500',
                   kind='websocket') == 1.0


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


# ─────────────────────────────────────────────────────────────────────────
# The attempt series exist from process start, so that absence means one
# thing only: a build without the counter.
# ─────────────────────────────────────────────────────────────────────────


def test_the_attempt_series_are_published_before_any_handshake(monkeypatch):
    """Every `path` value is published at zero, with nothing incremented.

    Without this, the first handshake on a pod creates the series *at 1* and
    `increase()` over it returns no sample at all -- so a rule reading this
    as a denominator reads no data rather than zero.
    """
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL.clear()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)

    middleware_utils.preinitialize_websocket_metrics()

    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL):
        assert _label_sets(counter) == {
            (('path', '/kubernetes-pod-ssh-proxy'),),
            (('path', '/slurm-job-ssh-proxy'),),
            (('path', '/ssh-interactive-auth'),),
            (('path', 'other'),),
        }, counter._name  # pylint: disable=protected-access
    # Published, not incremented -- both of them.
    for counter in (
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL,
            metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL):
        assert _sample(counter) == 0.0
    # Control: the rejection counter's `status` domain is open, so it is
    # deliberately left unpublished -- and it is what an unpublished counter
    # looks like, which is what makes the assertion above mean something.
    assert _label_sets(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL
    ) == set()


def test_no_series_are_published_with_metrics_off(monkeypatch):
    """With metrics off, counter children are not materialised at all."""
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL.clear()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', False)

    middleware_utils.preinitialize_websocket_metrics()

    assert _label_sets(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == set(
        )


def test_publishing_the_attempt_series_is_idempotent(monkeypatch):
    """Called at import and again by a test; the counts stay at zero."""
    metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL.clear()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)

    middleware_utils.preinitialize_websocket_metrics()
    middleware_utils.preinitialize_websocket_metrics()

    assert len(
        _label_sets(metrics_utils.
                    SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL)) == 4
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == 0.0


@pytest.mark.asyncio
async def test_the_recorded_status_is_a_number_not_an_enum_name():
    """A status carried as an IntEnum is labelled '503', not its name.

    Responses are built with `http.HTTPStatus` members all over the server
    (`RecordingMiddleware`'s other behaviors do it too), and str() of one is
    the member name before Python 3.11 -- one label value per member name,
    and no rule matching `status=~"5.."` would ever see them.
    """
    middleware = _make_middleware(lambda *a: None, behavior='enum_status')
    await _run(middleware, _make_websocket_scope())

    assert _label_sets(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_REJECTIONS_TOTAL) == {
            (('outcome', 'error'), ('path', 'other'), ('status', '503')),
        }


@pytest.mark.asyncio
async def test_an_accept_is_counted_once_through_the_stack():
    """Every wrapper wraps `send`, so the accept message passes through all
    of them on its way out; it must still be counted once."""

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    inner = middleware_utils.websocket_aware(RecordingMiddleware)
    outer = middleware_utils.websocket_aware(RecordingMiddleware)
    middleware = outer(inner(app, behavior='accept'), behavior='accept')

    sent = await _run(middleware, _make_websocket_scope())

    assert sent == [{'type': 'websocket.accept'}]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_messages_after_the_accept_are_passed_through_unchanged():
    """The wrapped `send` only observes. Everything the handler sends, in
    order, reaches the client unmodified."""

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})
        await send({'type': 'websocket.send', 'text': 'hello'})
        await send({'type': 'websocket.close', 'code': 1000})

    middleware = _make_middleware(app, behavior='accept')

    sent = await _run(middleware, _make_websocket_scope())

    assert sent == [
        {
            'type': 'websocket.accept'
        },
        {
            'type': 'websocket.send',
            'text': 'hello'
        },
        {
            'type': 'websocket.close',
            'code': 1000
        },
    ]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_an_accept_the_transport_could_not_deliver_is_not_counted():
    """A client that vanishes mid-handshake was never accepted.

    The accept is counted after `send` returns, so a transport that raises
    leaves the counter alone rather than claiming a session that does not
    exist.
    """

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})

    middleware = _make_middleware(app, behavior='accept')

    async def receive():
        return {'type': 'websocket.connect'}

    async def send(message):
        del message
        raise ConnectionResetError('client went away')

    with pytest.raises(ConnectionResetError):
        await middleware(_make_websocket_scope(), receive, send)

    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 0.0
    # The attempt still happened, and is still counted.
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_a_handler_that_raises_after_accepting_still_counted_the_accept():
    """The accept reached the client; what the handler did next is a
    different event and does not retract it."""

    async def app(scope, receive, send):
        del scope, receive
        await send({'type': 'websocket.accept'})
        raise RuntimeError('handler blew up after accepting')

    middleware = _make_middleware(app, behavior='accept')

    with pytest.raises(RuntimeError):
        await _run(middleware, _make_websocket_scope())

    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 1.0


@pytest.mark.asyncio
async def test_the_send_channel_is_wrapped_once_per_connection():
    """Only the outermost wrapper wraps `send`.

    The wrapper is never removed -- there is no end-of-handshake message --
    so on an SSH session it sits on the path of every outbound frame for the
    life of the connection. One wrapper is a pointer call; one per
    middleware is a dozen, for a count that happens once.
    """
    wrapper_cls = middleware_utils.websocket_aware(RecordingMiddleware)
    middleware = wrapper_cls(lambda *a: None, behavior='accept')
    scope = _make_websocket_scope()

    async def send(message):
        del message

    first = middleware._counting_send(scope, send)  # pylint: disable=protected-access
    assert first is not send, 'the outermost wrapper must wrap'
    # An inner wrapper, handed the already-wrapped channel, must pass it
    # through untouched rather than add a layer.
    assert middleware._counting_send(scope, first) is first  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_only_the_accept_message_counts_as_an_accept():
    """A denial response is not an accept.

    ASGI lets a server answer a failed handshake with
    `websocket.http.response.*` instead of a close. Those pass through and
    count nothing -- the only message that means the socket is open is
    `websocket.accept`.
    """

    async def app(scope, receive, send):
        del scope, receive
        await send({
            'type': 'websocket.http.response.start',
            'status': 403,
            'headers': [],
        })
        await send({'type': 'websocket.http.response.body', 'body': b''})

    middleware = _make_middleware(app, behavior='accept')

    sent = await _run(middleware, _make_websocket_scope())

    assert [message['type'] for message in sent] == [
        'websocket.http.response.start',
        'websocket.http.response.body',
    ]
    assert _sample(
        metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ACCEPTS_TOTAL) == 0.0


def test_a_fault_publishing_one_series_does_not_lose_the_others(monkeypatch):
    """Pre-initialisation is guarded per series, not once around the loop.

    The `path` whose child fails is the one that goes missing; the other
    three must still be published, or one unwritable moment at import costs
    the whole mechanism.
    """
    counter = metrics_utils.SKY_APISERVER_WEBSOCKET_HANDSHAKE_ATTEMPTS_TOTAL
    counter.clear()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    real_labels = counter.labels

    def labels(**kwargs):
        if kwargs.get('path') == '/slurm-job-ssh-proxy':
            raise ValueError('failed to open PROMETHEUS_MULTIPROC_DIR')
        return real_labels(**kwargs)

    monkeypatch.setattr(counter, 'labels', labels)

    middleware_utils.preinitialize_websocket_metrics()

    assert _label_sets(counter) == {
        (('path', '/kubernetes-pod-ssh-proxy'),),
        (('path', '/ssh-interactive-auth'),),
        (('path', 'other'),),
    }
