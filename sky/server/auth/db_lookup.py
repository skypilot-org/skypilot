"""Bounded DB lookups for the authentication middlewares.

Authentication middlewares perform DB lookups on the request path (user
rows, service-account tokens, RBAC policies). Without a deadline, a
degraded database — slow queries, a starved connection pool, or time
queued inside a transaction pooler — holds each lookup, and the executor
thread running it, for as long as the DB layer allows. At a hold time of
minutes the bounded auth executor saturates within seconds and every
authenticated endpoint fails for the duration of the DB incident.

``call_with_deadline`` puts a client-side total deadline on each lookup.
``asyncio.wait_for`` is deliberately the bounding layer: a server-side
``statement_timeout`` cannot cover time spent queued inside a transaction
pooler (no server connection is assigned yet) or waiting for a pool
checkout. On timeout the request fails fast with a 503 the client retries
with backoff; note the executor thread itself keeps running until the DB
layer releases it, so the auth executor still acts as a saturation
buffer — requests beyond it fail fast with the worker-exhausted 503.

Both timeout and executor exhaustion must be converted to responses
*inside* the middleware: app-level exception handlers wrap the router
only, so an exception raised in a middleware surfaces as a bare 500,
which clients do not retry.

Every response helper here accepts the request it answers and, when given
one, stamps the rejection reason on it (``middleware_utils.mark_rejection``)
so the metrics middleware can count these 503s by cause: a middleware
answering a request itself is otherwise invisible to request-level metrics.
The request is optional so that callers outside this repository, which call
the helpers with no arguments, keep getting the same 503; they only lose the
per-reason attribution until they pass the request too.
"""
import asyncio
from typing import Any, Callable, Optional

import fastapi

from sky import exceptions
from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.server import middleware_utils
from sky.server.requests import executor
from sky.users import permission
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils.db import db_utils

logger = sky_logging.init_logger(__name__)

# Total client-side deadline on each auth DB lookup. Healthy lookups are
# small indexed queries; this only trips when the DB layer is in real
# trouble, while staying well below the SQLAlchemy pool checkout timeout
# (30s) and typical pooler queue-wait timeouts so requests fail fast
# before executor threads pile up.
#
# Configured by `constants.ENV_VAR_AUTH_DB_TIMEOUT_SECONDS` (default 5 s),
# read through the shared helper so the server-side timeouts the users
# upsert derives from the same value cannot drift from this one. A value
# that is not a positive number fails here, at server startup, with a
# message naming the variable.
AUTH_DB_TIMEOUT_SECONDS = db_utils.get_auth_db_timeout_seconds()

# Postgres SQLSTATE codes raised when the database itself gives up on an
# auth-path call at a server-side timeout. The users upsert sets these
# timeouts on its own transaction (see `global_user_state.add_or_update_user`),
# derived from the same configured deadline as `AUTH_DB_TIMEOUT_SECONDS` and
# strictly below or equal to it, so the database normally fails the call
# *before* `asyncio.wait_for` does -- and, unlike `wait_for`, actually
# releases the executor thread. Such an error is the same condition as the
# client-side deadline (a slow or locked database) and gets the same
# retryable 503; anything else still propagates unchanged.
_SERVER_TIMEOUT_PGCODES = {
    '55P03': 'lock_timeout',  # LockNotAvailable
    '57014': 'statement_timeout',  # QueryCanceled
    '25P03': 'idle_in_transaction_session_timeout',
}


class AuthDBTimeoutError(asyncio.TimeoutError):
    """An auth DB call was cut off by the database's own timeout.

    Subclasses ``asyncio.TimeoutError`` so every ``except asyncio.TimeoutError``
    in the auth middlewares keeps answering ``db_timeout_response()`` unchanged.
    """


def _server_timeout_pgcode(exc: BaseException) -> Optional[str]:
    """``exc``'s SQLSTATE if it is one of the server-side timeouts, else None.

    ``sqlalchemy.exc.DBAPIError`` wraps the driver's error as ``.orig``; a
    driver error that escaped unwrapped (raw connections) carries ``pgcode``
    itself. Duck-typed so this module does not import psycopg2, which is a
    server-only dependency.
    """
    orig = getattr(exc, 'orig', exc)
    pgcode = getattr(orig, 'pgcode', None)
    if pgcode in _SERVER_TIMEOUT_PGCODES:
        return pgcode
    return None


# `cause` of a timeout the client-side deadline produced, as opposed to one
# the database itself ended (those are named by their Postgres setting, see
# `_SERVER_TIMEOUT_PGCODES`).
TIMEOUT_CAUSE_DEADLINE = 'deadline'

# `pool` label values: which executor's slot the timed-out call is holding.
# Spelled as `sky_apiserver_threads_exhausted_total{name}` spells it, so a
# timeout can be read next to that pool's exhaustion and saturation.
POOL_AUTH = 'auth_thread_executor'
POOL_REQUEST = 'request_thread_executor'


def _count_timeout(func: Callable[..., Any], cause: str, pool: str) -> None:
    """Count one auth-path timeout, on the pool whose slot it holds.

    A no-op when metrics are off, like every other instrument outside the
    metrics middleware (see `OnDemandThreadExecutor`, which does not even
    materialise its counter children, and `record_federation_phase`).

    Recorded through `record_safely` because this runs while an exception from
    the auth path is in flight: a failure to record must not replace it, which
    would turn a retryable 503 into a bare 500 during exactly the incident the
    counter exists to make visible.
    """
    if not metrics_utils.METRICS_ENABLED:
        return
    site = getattr(func, '__name__', 'unknown')
    middleware_utils.record_safely('an auth-path timeout', _record_timeout,
                                   site, cause, pool)


def _record_timeout(site: str, cause: str, pool: str) -> None:
    metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL.labels(site=site,
                                                           cause=cause,
                                                           pool=pool).inc()


async def _run_with_deadline(pool: Any, pool_name: str,
                             func: Callable[..., Any], *args: Any) -> Any:
    try:
        return await asyncio.wait_for(context_utils.to_thread_with_executor(
            pool, func, *args),
                                      timeout=AUTH_DB_TIMEOUT_SECONDS)
    except Exception as e:  # pylint: disable=broad-except
        pgcode = _server_timeout_pgcode(e)
        if pgcode is None:
            if isinstance(e, asyncio.TimeoutError):
                # The deadline elapsed. Counted here, at the one choke point
                # every auth-path call goes through, rather than at the call
                # sites: several of them convert the timeout to a 503 and one
                # (the /api/health probe) swallows it entirely, so counting
                # per caller would miss the cases with no client-visible
                # symptom -- which are the early ones.
                _count_timeout(func, TIMEOUT_CAUSE_DEADLINE, pool_name)
            raise
        reason = _SERVER_TIMEOUT_PGCODES[pgcode]
        _count_timeout(func, reason, pool_name)
        logger.warning(f'Auth DB call {getattr(func, "__name__", func)} was '
                       f'cut off by the database\'s {reason} '
                       f'(pgcode {pgcode}): '
                       f'{common_utils.format_exception(e)}')
        raise AuthDBTimeoutError(reason) from e


async def call_with_deadline(func: Callable[..., Any], *args: Any) -> Any:
    """Run a sync auth DB lookup on the auth executor with a total deadline.

    Raises ``asyncio.TimeoutError`` when the deadline elapses (or when the
    database cut the call off at its own, aligned timeout -- see
    `_SERVER_TIMEOUT_PGCODES`) and ``ConcurrentWorkerExhaustedError`` when
    the executor is saturated.
    """
    return await _run_with_deadline(executor.get_auth_thread_executor(),
                                    POOL_AUTH, func, *args)


async def _call_on_request_pool(func: Callable[..., Any], *args: Any) -> Any:
    """Like `call_with_deadline`, on the request executor instead.

    For auth-path work that is *not* a short lookup. The auth executor is 32
    threads for exactly that, and its docstring is explicit that anything whose
    exhaustion must not lock users out belongs on the request executor (128).
    The deadline releases the caller, never the thread -- `wait_for` cannot
    cancel a thread parked on a lock -- so work that can hold a thread for
    `POLICY_UPDATE_LOCK_TIMEOUT_SECONDS` would otherwise let a burst of first
    logins exhaust authentication for every other request on this worker.
    """
    return await _run_with_deadline(executor.get_request_thread_executor(),
                                    POOL_REQUEST, func, *args)


def _mark(request: Optional[fastapi.Request], reason: str) -> None:
    """Attribute a canned rejection for the metrics layer, if we know which
    request it answers."""
    if request is not None:
        middleware_utils.mark_rejection(request, reason)


async def ensure_role_for_authenticated_user(
    user_id: str,
    newly_added: bool,
    request: Optional[fastapi.Request] = None
) -> Optional[fastapi.responses.JSONResponse]:
    """Give an authenticated principal a role. A response to send, or None.

    Both auth front-ends (the auth-proxy middleware and the oauth2-proxy one)
    need the same two branches, so they live here rather than twice:

    A **brand-new** user's seed is awaited: the RBAC gate denies a principal
    with no role, so their first request has to find one. Bounded, because the
    seed takes the distributed policy lock (up to
    `POLICY_UPDATE_LOCK_TIMEOUT_SECONDS`) and an unbounded await hangs the login
    for that long -- and on the bounded auth executor, so a burst of signups
    cannot drain the default thread pool every other `to_thread` caller shares.
    On a timeout the account is queued for repair and the caller gets a
    retryable 503: proceeding would hand the gate a role-less principal and 403
    their first request, and dropping the seed would leave the account broken
    until something else noticed.

    A **returning** user whose seed never completed is only queued. The repair
    wants the same contended lock that stranded them, nothing about answering
    this request depends on it, and the gate denies them until it lands.
    """
    if newly_added:
        try:
            await _call_on_request_pool(permission.seed_new_user_role, user_id)
        except exceptions.ConcurrentWorkerExhaustedError as e:
            logger.error(f'Concurrent worker exhausted seeding a role for '
                         f'{user_id}: {e}')
            permission.permission_service.queue_role_repair(user_id)
            return worker_exhausted_response(request=request)
        except asyncio.TimeoutError:
            # Separated from the clause below for the log, not the answer: the
            # deadline elapsed but the seed is still running in its thread and
            # usually lands, whereas the cases below have already failed. The
            # caller gets the same 503 either way -- from a client's side both
            # are "not ready yet, come back".
            logger.error(f'Seeding a role for new user {user_id} did not '
                         f'finish within {AUTH_DB_TIMEOUT_SECONDS}s; queueing '
                         f'it off the request')
            permission.permission_service.queue_role_repair(user_id)
            return role_seed_unavailable_response(request=request)
        except Exception as e:  # pylint: disable=broad-except
            # Everything else, because an exception raised in a middleware
            # surfaces as a bare 500, which clients do not retry (see this
            # module's docstring). Note a contended policy lock arrives here
            # rather than above: `_policy_lock` converts its own timeout into a
            # RuntimeError. That is the common case for this branch, and what
            # the response's wording is written for.
            logger.error(f'Seeding a role for new user {user_id} failed; '
                         f'queueing it off the request: '
                         f'{common_utils.format_exception(e)}')
            permission.permission_service.queue_role_repair(user_id)
            return role_seed_unavailable_response(request=request)
        return None
    if not permission.permission_service.probably_has_role(user_id):
        permission.permission_service.queue_role_repair(user_id)
    return None


def db_timeout_response(
    request: Optional[fastapi.Request] = None
) -> fastapi.responses.JSONResponse:
    """503 for an auth lookup that timed out on a degraded database.

    503 (not 504/429) because the client maps exactly 503 to
    ``ServerTemporarilyUnavailableError`` and retries with backoff (see
    ``sky/server/rest.py``); any other status surfaces as a hard error.
    The detail message is distinct from the worker-exhausted 503 so
    operators can tell the two apart in logs.
    """
    _mark(request, middleware_utils.REJECT_REASON_AUTH_DB_TIMEOUT)
    return fastapi.responses.JSONResponse(
        status_code=503,
        headers={'Retry-After': str(max(1, int(AUTH_DB_TIMEOUT_SECONDS)))},
        content={
            'detail': ('Authentication lookup timed out because the server '
                       'database is slow or unavailable. Please try again.')
        })


def role_seed_unavailable_response(
    request: Optional[fastapi.Request] = None
) -> fastapi.responses.JSONResponse:
    """503 for a new user whose role could not be assigned in time.

    Not `db_timeout_response`: that one names a slow database, and the reason
    this path gives up is usually contention on the policy lock, which is a
    healthy database doing exactly what it should. Same 503 so the client
    retries with backoff, and by then the queued repair has normally landed.
    """
    _mark(request, middleware_utils.REJECT_REASON_ROLE_SEED_UNAVAILABLE)
    return fastapi.responses.JSONResponse(
        status_code=503,
        headers={'Retry-After': str(max(1, int(AUTH_DB_TIMEOUT_SECONDS)))},
        content={
            'detail': ('Could not finish setting up your account in time '
                       '(the server is busy assigning roles). It is being '
                       'completed in the background -- please try again.')
        })


def jwt_secret_unavailable_response(
    request: Optional[fastapi.Request] = None
) -> fastapi.responses.JSONResponse:
    """503 for service-account auth that cannot load its signing secret.

    Not a 401: the token is well-formed and the server simply cannot reach the
    secret to check it. A 401 reads as "this credential is bad" and pushes
    operators to rotate tokens over what is a transient database problem.
    """
    _mark(request, middleware_utils.REJECT_REASON_JWT_SECRET_UNAVAILABLE)
    return fastapi.responses.JSONResponse(
        status_code=503,
        headers={'Retry-After': str(max(1, int(AUTH_DB_TIMEOUT_SECONDS)))},
        content={
            'detail': ('Service account authentication is temporarily '
                       'unavailable: the token signing secret could not be '
                       'read from the server database. Your token is still '
                       'valid -- please try again.')
        })


def worker_exhausted_response(
    request: Optional[fastapi.Request] = None
) -> fastapi.responses.JSONResponse:
    """503 for auth-path work rejected by a saturated thread executor.

    Either pool: the auth executor for lookups, or the request executor for a
    new user's role seed, which is deliberately not on the auth pool.

    Mirrors ``handle_concurrent_worker_exhausted_error`` in
    ``sky/server/server.py`` — that app-level handler cannot see
    exceptions raised in middlewares, so middlewares must convert the
    error themselves or it surfaces as a bare 500.
    """
    _mark(request, middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED)
    return fastapi.responses.JSONResponse(
        status_code=503,
        content={
            'detail':
                ('The server has exhausted its concurrent worker limit. '
                 'Please try again or scale the server if the load persists.')
        })
