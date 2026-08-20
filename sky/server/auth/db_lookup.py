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
"""
import asyncio
import os
from typing import Any, Callable, Optional

import fastapi

from sky import exceptions
from sky import sky_logging
from sky.server.requests import executor
from sky.users import permission
from sky.utils import context_utils

logger = sky_logging.init_logger(__name__)

# Total client-side deadline on each auth DB lookup. Healthy lookups are
# small indexed queries; this only trips when the DB layer is in real
# trouble, while staying well below the SQLAlchemy pool checkout timeout
# (30s) and typical pooler queue-wait timeouts so requests fail fast
# before executor threads pile up.
AUTH_DB_TIMEOUT_SECONDS = float(
    os.environ.get('SKYPILOT_AUTH_DB_TIMEOUT_SECONDS', '5'))


async def call_with_deadline(func: Callable[..., Any], *args: Any) -> Any:
    """Run a sync auth DB lookup on the auth executor with a total deadline.

    Raises ``asyncio.TimeoutError`` when the deadline elapses and
    ``ConcurrentWorkerExhaustedError`` when the executor is saturated.
    """
    return await asyncio.wait_for(context_utils.to_thread_with_executor(
        executor.get_auth_thread_executor(), func, *args),
                                  timeout=AUTH_DB_TIMEOUT_SECONDS)


async def ensure_role_for_authenticated_user(
        user_id: str,
        newly_added: bool) -> Optional[fastapi.responses.JSONResponse]:
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
            await call_with_deadline(permission.seed_new_user_role, user_id)
        except asyncio.TimeoutError:
            logger.error(f'Seeding a role for new user {user_id} timed out; '
                         f'queueing it off the request')
            permission.permission_service.queue_role_repair(user_id)
            return db_timeout_response()
        except exceptions.ConcurrentWorkerExhaustedError as e:
            logger.error(f'Concurrent worker exhausted seeding a role for '
                         f'{user_id}: {e}')
            permission.permission_service.queue_role_repair(user_id)
            return worker_exhausted_response()
        return None
    if not permission.permission_service.probably_has_role(user_id):
        permission.permission_service.queue_role_repair(user_id)
    return None


def db_timeout_response() -> fastapi.responses.JSONResponse:
    """503 for an auth lookup that timed out on a degraded database.

    503 (not 504/429) because the client maps exactly 503 to
    ``ServerTemporarilyUnavailableError`` and retries with backoff (see
    ``sky/server/rest.py``); any other status surfaces as a hard error.
    The detail message is distinct from the worker-exhausted 503 so
    operators can tell the two apart in logs.
    """
    return fastapi.responses.JSONResponse(
        status_code=503,
        headers={'Retry-After': str(max(1, int(AUTH_DB_TIMEOUT_SECONDS)))},
        content={
            'detail': ('Authentication lookup timed out because the server '
                       'database is slow or unavailable. Please try again.')
        })


def worker_exhausted_response() -> fastapi.responses.JSONResponse:
    """503 for an auth lookup rejected by a saturated auth executor.

    Mirrors ``handle_concurrent_worker_exhausted_error`` in
    ``sky/server/server.py`` — that app-level handler cannot see
    exceptions raised in middlewares, so middlewares must convert the
    error themselves or it surfaces as a bare 500.
    """
    return fastapi.responses.JSONResponse(
        status_code=503,
        content={
            'detail':
                ('The server has exhausted its concurrent worker limit. '
                 'Please try again or scale the server if the load persists.')
        })
