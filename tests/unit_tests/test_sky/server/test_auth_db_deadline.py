"""Auth middleware DB lookups must be bounded by a deadline.

Every authenticated request runs DB lookups in the auth middlewares
(bearer token rows, basic-auth user rows, RBAC policies, auth-proxy user
upserts) on the bounded auth thread executor. Without a deadline, a
degraded database holds each lookup — and its executor thread — for as
long as the DB layer allows; at a hold time of minutes the executor
saturates within seconds and every authenticated endpoint fails for the
duration of the DB incident.

These tests pin the containment behavior:

* a lookup that outlives ``AUTH_DB_TIMEOUT_SECONDS`` fails that single
  request fast with a retryable 503 (and ``Retry-After``) instead of
  queueing threads;
* executor exhaustion also surfaces as a 503 from *inside* the
  middleware — app-level exception handlers wrap the router only, so a
  raise from a middleware would surface as a bare 500, which clients do
  not retry;
* the basic-auth path on ``/api/health`` stays best-effort: probes must
  survive a DB incident, so it proceeds unauthenticated instead of
  failing;
* a lookup the *database* cut off at one of the server-side timeouts the
  auth path sets on its own transaction (``lock_timeout``,
  ``statement_timeout``, ``idle_in_transaction_session_timeout``) gets the
  same retryable 503 as the client-side deadline, while every other DB
  error still propagates unchanged.
"""

# pylint: disable=protected-access,redefined-outer-name,missing-class-docstring

import asyncio
import concurrent.futures
import functools
import os
import sqlite3
import time
import unittest.mock as mock

import fastapi
import psycopg2
import pytest
import sqlalchemy.exc

from sky import exceptions
from sky import models
from sky.metrics import utils as metrics_utils
from sky.server import middleware_utils
from sky.server import server
from sky.server.auth import db_lookup
from sky.server.requests import threads
from sky.skylet import constants
from sky.utils import context_utils
from sky.utils.db import deadline as db_deadline

# Lookups sleep _SLOW_DB_SECONDS while the deadline is patched to
# _DEADLINE_SECONDS, so every "slow DB" test trips the deadline quickly and
# the leaked executor thread exits shortly after the test.
_SLOW_DB_SECONDS = 0.3
_DEADLINE_SECONDS = 0.05


def _slow(return_value):
    """A synchronous stand-in for a DB call stuck on a degraded database."""

    def _call(*args, **kwargs):
        del args, kwargs
        time.sleep(_SLOW_DB_SECONDS)
        return return_value

    return _call


@pytest.fixture(autouse=True)
def short_deadline(monkeypatch):
    monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', _DEADLINE_SECONDS)


@pytest.fixture
def mock_request():
    request = mock.Mock(spec=fastapi.Request)
    request.headers = {}
    request.state = mock.Mock()
    request.state.auth_user = None
    request.url = mock.Mock()
    request.url.path = '/users'
    request.method = 'GET'
    request.cookies = {}
    return request


@pytest.fixture
def call_next_sentinel():
    """call_next that records whether the request reached the router."""

    async def call_next(request):
        del request  # unused
        call_next.reached = True
        return fastapi.responses.JSONResponse({'message': 'success'})

    call_next.reached = False
    return call_next


def _assert_retryable_timeout_503(response):
    assert response.status_code == 503, getattr(response, 'body', response)
    assert 'Retry-After' in response.headers
    # The detail is distinct from the worker-exhausted 503 so operators can
    # tell a DB timeout from executor saturation.
    assert b'timed out' in response.body


class TestBearerTokenDeadline:

    @pytest.mark.asyncio
    async def test_slow_token_lookup_times_out_to_503(self, mock_request,
                                                      call_next_sentinel):
        mock_request.headers = {'authorization': 'Bearer sky_token'}
        middleware = server.BearerTokenMiddleware(app=mock.Mock())

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as tks, \
                mock.patch(
                    'sky.global_user_state.get_service_account_token_by_hash',
                    _slow(None)):
            tks.verify_token.return_value = {
                'sub': 'sa-1',
                'name': 'sa',
                'token_id': 'tok-1'
            }
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached

    @pytest.mark.asyncio
    async def test_exhausted_executor_is_503_not_500(self, mock_request,
                                                     call_next_sentinel):
        """A saturated auth executor must surface as a 503 from inside the
        middleware. Raising (the previous behavior) surfaces as a bare 500:
        app-level exception handlers wrap the router only and cannot see
        exceptions raised in middlewares."""
        mock_request.headers = {'authorization': 'Bearer sky_token'}
        middleware = server.BearerTokenMiddleware(app=mock.Mock())
        exhausted = threads.OnDemandThreadExecutor(name='test-exhausted',
                                                   max_workers=0)

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as tks, \
                mock.patch.object(db_lookup.executor,
                                  'get_auth_thread_executor',
                                  return_value=exhausted):
            tks.verify_token.return_value = {
                'sub': 'sa-1',
                'name': 'sa',
                'token_id': 'tok-1'
            }
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        assert response.status_code == 503
        assert b'concurrent worker limit' in response.body
        assert not call_next_sentinel.reached


class TestBasicAuthDeadline:

    @pytest.mark.asyncio
    async def test_slow_user_lookup_times_out_to_503(self, mock_request,
                                                     call_next_sentinel):
        # 'user:pass' base64-encoded.
        mock_request.headers = {'authorization': 'Basic dXNlcjpwYXNz'}
        middleware = server.BasicAuthMiddleware(app=mock.Mock())

        with mock.patch.object(server.loopback,
                               'is_loopback_request',
                               return_value=False), \
                mock.patch('sky.global_user_state.get_user_by_name',
                           _slow([])):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached

    @pytest.mark.asyncio
    async def test_health_path_survives_db_timeout(self, mock_request,
                                                   call_next_sentinel):
        """/api/health must stay available during a DB incident: the
        best-effort basic-auth lookup proceeds unauthenticated on timeout
        instead of failing the probe."""
        mock_request.url.path = '/api/health'
        mock_request.headers = {'authorization': 'Basic dXNlcjpwYXNz'}
        middleware = server.BasicAuthMiddleware(app=mock.Mock())

        with mock.patch.object(server.loopback,
                               'is_loopback_request',
                               return_value=False), \
                mock.patch('sky.global_user_state.get_user_by_name',
                           _slow([])):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        assert response.status_code == 200
        assert call_next_sentinel.reached
        assert mock_request.state.auth_user is None


class TestRBACDeadline:

    @pytest.mark.asyncio
    async def test_slow_permission_check_times_out_to_503(
            self, mock_request, call_next_sentinel):
        """A timed-out RBAC check fails closed with a retryable 503 — never
        an allow."""
        mock_request.state.auth_user = models.User(id='u-1', name='tester')
        middleware = server.RBACMiddleware(app=mock.Mock())

        with mock.patch(
                'sky.users.permission.permission_service') as perm_service:
            perm_service.check_endpoint_permission = _slow(False)
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached


class TestAuthProxyDeadline:

    @pytest.mark.asyncio
    async def test_slow_user_upsert_times_out_to_503(self, mock_request,
                                                     call_next_sentinel):
        proxy_config = mock.Mock()
        proxy_config.enabled = True
        with mock.patch.object(server.server_config,
                               'load_external_proxy_config',
                               return_value=proxy_config):
            middleware = server.AuthProxyMiddleware(app=mock.Mock())

        with mock.patch.object(
                server,
                '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           _slow(False)):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached


async def _seed_times_out(func, *args):
    """Time out the seed. Kept separate from `call_with_deadline`, which the
    user upsert still uses: failing every call answered 503 before the seed was
    ever reached, which is how this test first passed for the wrong reason."""
    del func, args
    raise asyncio.TimeoutError()


class TestAuthProxyRoleRepair:
    """The login path repairs a role that was never seeded.

    The endpoint gate queues a repair too, but only after refusing the request.
    Doing it here means a returning user whose seed failed is fixed before they
    are ever denied.
    """

    def _middleware(self):
        proxy_config = mock.Mock()
        proxy_config.enabled = True
        with mock.patch.object(server.server_config,
                               'load_external_proxy_config',
                               return_value=proxy_config):
            return server.AuthProxyMiddleware(app=mock.Mock())

    @pytest.mark.asyncio
    async def test_a_returning_user_with_no_known_role_is_queued(
            self, mock_request, call_next_sentinel):
        """Queued, not awaited: the repair wants the lock that stranded them."""
        middleware = self._middleware()
        with mock.patch.object(
                server, '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           return_value=False), \
                mock.patch('sky.users.permission.permission_service'
                          ) as perm_service:
            perm_service.probably_has_role.return_value = False
            await middleware.dispatch(mock_request, call_next_sentinel)
        perm_service.queue_role_repair.assert_called_once_with('u-1')

    @pytest.mark.asyncio
    async def test_the_repair_never_blocks_the_login(self, mock_request,
                                                     call_next_sentinel):
        """A login must not wait on the policy lock. Nothing here may await.

        The whole point of queueing: the seed takes a lock held for up to
        POLICY_UPDATE_LOCK_TIMEOUT_SECONDS, and a login that waits for it hangs
        for that long under exactly the contention that caused the strand.
        """
        middleware = self._middleware()
        with mock.patch.object(
                server, '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           return_value=False), \
                mock.patch('sky.users.permission.permission_service'
                          ) as perm_service, \
                mock.patch('asyncio.to_thread') as to_thread:
            perm_service.probably_has_role.return_value = False
            await middleware.dispatch(mock_request, call_next_sentinel)
        to_thread.assert_not_called()
        perm_service.queue_role_repair.assert_called_once_with('u-1')

    @pytest.mark.asyncio
    async def test_no_repair_when_a_role_is_known(self, mock_request,
                                                  call_next_sentinel):
        """The guard is what keeps this off the queue on every request."""
        middleware = self._middleware()
        with mock.patch.object(
                server, '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           return_value=False), \
                mock.patch('sky.users.permission.permission_service'
                          ) as perm_service:
            perm_service.probably_has_role.return_value = True
            await middleware.dispatch(mock_request, call_next_sentinel)
        perm_service.queue_role_repair.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_new_user_is_seeded_not_queued(self, mock_request,
                                                   call_next_sentinel):
        middleware = self._middleware()
        with mock.patch.object(
                server, '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           return_value=True), \
                mock.patch('sky.users.permission.seed_new_user_role') as seed, \
                mock.patch('sky.users.permission.permission_service'
                          ) as perm_service:
            await middleware.dispatch(mock_request, call_next_sentinel)
        seed.assert_called_once_with('u-1')
        perm_service.queue_role_repair.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_seed_that_times_out_answers_503_and_queues(
            self, mock_request, call_next_sentinel):
        """Never proceed without a role, and never strand them either.

        Proceeding would hand the RBAC gate a role-less principal and 403 the
        brand-new user's first request; dropping the seed would leave the
        account broken until something else noticed.
        """
        middleware = self._middleware()
        with mock.patch.object(
                server, '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           return_value=True), \
                mock.patch('sky.users.permission.permission_service'
                          ) as perm_service, \
                mock.patch.object(db_lookup,
                                  '_call_on_request_pool',
                                  side_effect=_seed_times_out):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)
        assert response.status_code == 503
        perm_service.queue_role_repair.assert_called_once_with('u-1')


@pytest.mark.asyncio
async def test_call_with_deadline_returns_fast_results(monkeypatch):
    """The healthy path is unperturbed: fast lookups return their value."""
    monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
    result = await db_lookup.call_with_deadline(lambda: 'ok')
    assert result == 'ok'


@pytest.mark.asyncio
async def test_call_with_deadline_raises_timeout():
    with pytest.raises(asyncio.TimeoutError):
        await db_lookup.call_with_deadline(_slow(None))


def _request() -> fastapi.Request:
    """A bare request for the helpers to stamp their rejection reason on."""
    return fastapi.Request({
        'type': 'http',
        'method': 'GET',
        'path': '/',
        'headers': [],
        'state': {},
    })


class TestEnsureRoleForAuthenticatedUser:
    """The decision both auth front-ends share.

    Tested here rather than through either middleware: the oauth2-proxy class is
    wrapped by `middleware_utils.websocket_aware`, so its real methods are not
    reachable from the exported name and its copy of this branch had no
    coverage at all while it was duplicated.
    """

    @pytest.mark.asyncio
    async def test_a_new_user_is_seeded_before_the_request_proceeds(self):
        with mock.patch.object(db_lookup,
                               '_call_on_request_pool',
                               new=mock.AsyncMock()) as bounded, \
             mock.patch('sky.users.permission.permission_service') as perm:
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-new', True, request=_request()) is None
        assert bounded.await_count == 1
        assert bounded.await_args[0][1] == 'u-new'
        perm.queue_role_repair.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_seed_that_times_out_answers_503_and_queues(self):
        """Never proceed without a role, and never strand them either."""
        with mock.patch.object(db_lookup,
                               '_call_on_request_pool',
                               side_effect=asyncio.TimeoutError), \
             mock.patch('sky.users.permission.permission_service') as perm:
            request = _request()
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-slow', True, request=request)
        assert response is not None and response.status_code == 503
        perm.queue_role_repair.assert_called_once_with('u-slow')
        # The 503 is attributed for the metrics layer: a middleware answering a
        # request itself is otherwise invisible to request counters.
        assert request.state.reject_reason == \
            middleware_utils.REJECT_REASON_ROLE_SEED_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_a_saturated_executor_answers_503_and_queues(self):
        with mock.patch.object(
                db_lookup,
                '_call_on_request_pool',
                side_effect=exceptions.ConcurrentWorkerExhaustedError('busy')
        ), mock.patch('sky.users.permission.permission_service') as perm:
            request = _request()
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-busy', True, request=request)
        assert response is not None and response.status_code == 503
        perm.queue_role_repair.assert_called_once_with('u-busy')
        assert request.state.reject_reason == \
            middleware_utils.REJECT_REASON_AUTH_WORKER_EXHAUSTED

    @pytest.mark.asyncio
    async def test_a_returning_user_with_no_known_role_is_only_queued(self):
        """Queued, not awaited: the repair wants the lock that stranded them."""
        with mock.patch('sky.users.permission.permission_service') as perm, \
             mock.patch.object(db_lookup,
                               '_call_on_request_pool',
                               new=mock.AsyncMock()) as bounded:
            perm.probably_has_role.return_value = False
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-old', False, request=_request()) is None
        perm.queue_role_repair.assert_called_once_with('u-old')
        bounded.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_returning_user_with_a_role_does_nothing(self):
        with mock.patch('sky.users.permission.permission_service') as perm:
            perm.probably_has_role.return_value = True
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-fine', False, request=_request()) is None
        perm.queue_role_repair.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_seed_stays_off_the_auth_pool(self):
        """32 auth threads, and a seed can hold one for the policy lock's 20s.

        `wait_for` releases the caller, never the thread, so a burst of first
        logins under lock contention would exhaust the pool every request
        authenticates through -- which the auth executor's own docstring says
        belongs on the request executor instead.
        """
        with mock.patch('sky.users.permission.permission_service'), \
             mock.patch('sky.users.permission.seed_new_user_role'), \
             mock.patch.object(db_lookup.executor,
                               'get_auth_thread_executor') as auth_pool, \
             mock.patch.object(db_lookup.executor,
                               'get_request_thread_executor') as request_pool:
            await db_lookup.ensure_role_for_authenticated_user(
                'u-new', True, request=_request())
        auth_pool.assert_not_called()
        request_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_any_seed_failure_answers_503_rather_than_500(self):
        """A middleware exception is a bare 500, and clients do not retry those.

        Only the timeout was converted before, so a DB error inside the seed
        escaped as a 500 on a brand-new user's first request.
        """
        with mock.patch.object(db_lookup,
                               '_call_on_request_pool',
                               side_effect=RuntimeError('db is unhappy')), \
             mock.patch('sky.users.permission.permission_service') as perm:
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-broken', True, request=_request())
        assert response is not None and response.status_code == 503
        # And it says why it gave up. `db_timeout_response`'s text blames a slow
        # database, which is wrong for the reason this path usually fails:
        # contention on the policy lock, a healthy database doing its job.
        assert b'assigning roles' in response.body
        perm.queue_role_repair.assert_called_once_with('u-broken')


class TestHelpersWithoutARequest:
    """The response helpers keep working when called with no arguments.

    Middlewares outside this repository call `db_timeout_response()` and
    `worker_exhausted_response()` bare. Making `request` required would turn
    their 503 -- the retryable answer on exactly the auth-pool-exhausted /
    auth-DB-deadline path -- into a TypeError, which a middleware surfaces as
    a bare 500 that clients do not retry. So: same 503 without a request, the
    reason attribution is the only thing a caller gains by passing one.
    """

    @pytest.mark.parametrize('helper', [
        db_lookup.db_timeout_response,
        db_lookup.role_seed_unavailable_response,
        db_lookup.jwt_secret_unavailable_response,
        db_lookup.worker_exhausted_response,
    ])
    def test_no_argument_call_is_the_same_503(self, helper):
        bare = helper()
        request = _request()
        attributed = helper(request)
        assert bare.status_code == attributed.status_code == 503
        assert bare.body == attributed.body
        assert bare.headers.get('retry-after') == \
            attributed.headers.get('retry-after')
        # Only the attributed one carries a reason for the metrics layer.
        assert hasattr(request.state, middleware_utils.REJECT_REASON_STATE_KEY)

    @pytest.mark.asyncio
    async def test_ensure_role_keeps_its_original_positional_signature(self):
        with mock.patch.object(db_lookup,
                               '_call_on_request_pool',
                               side_effect=asyncio.TimeoutError), \
             mock.patch('sky.users.permission.permission_service') as perm:
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-slow', True)
        assert response is not None and response.status_code == 503
        perm.queue_role_repair.assert_called_once_with('u-slow')


class _PgError(Exception):
    """Stand-in for a psycopg2 error: carries the SQLSTATE as ``pgcode``.

    Duck-typed on purpose -- `db_lookup` must not depend on psycopg2 (a
    server-only extra), and a manually constructed psycopg2 error has no
    pgcode anyway; only the C layer sets it from a real server reply.
    """

    def __init__(self, pgcode, message):
        super().__init__(message)
        self.pgcode = pgcode


def _raises_db_error(pgcode, message='canceling statement due to timeout'):
    """A synchronous stand-in for a DB call the database itself cut off."""

    def _call(*args, **kwargs):
        del args, kwargs
        raise sqlalchemy.exc.OperationalError('INSERT INTO users ...', {},
                                              _PgError(pgcode, message))

    return _call


# The three timeouts `global_user_state.add_or_update_user` sets on its own
# Postgres transaction, and the SQLSTATE each produces.
_SERVER_TIMEOUT_PGCODES = (
    '55P03',  # lock_timeout: lock_not_available
    '57014',  # statement_timeout: query_canceled
    '25P03',  # idle_in_transaction_session_timeout (next statement)
)


class TestServerSideTimeoutMapping:
    """A DB-side timeout on the auth path is the deadline path's 503."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('pgcode', _SERVER_TIMEOUT_PGCODES)
    async def test_server_timeout_pgcodes_become_timeout_errors(
            self, monkeypatch, pgcode):
        # The database gives up well before the client deadline here.
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        with pytest.raises(asyncio.TimeoutError) as excinfo:
            await db_lookup.call_with_deadline(_raises_db_error(pgcode))
        assert isinstance(excinfo.value, db_lookup.AuthDBTimeoutError)
        # The original DB error is kept as the cause for the log/debugging.
        assert isinstance(excinfo.value.__cause__,
                          sqlalchemy.exc.OperationalError)

    @pytest.mark.asyncio
    @pytest.mark.parametrize('pgcode', _SERVER_TIMEOUT_PGCODES)
    async def test_request_pool_variant_maps_the_same_way(
            self, monkeypatch, pgcode):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        with pytest.raises(asyncio.TimeoutError):
            await db_lookup._call_on_request_pool(_raises_db_error(pgcode))

    @pytest.mark.asyncio
    async def test_raw_driver_error_with_pgcode_is_mapped_too(
            self, monkeypatch):
        """Raw connections raise the driver error unwrapped (no `.orig`)."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        def _raw(*args, **kwargs):
            del args, kwargs
            raise _PgError('55P03', 'lock timeout')

        with pytest.raises(asyncio.TimeoutError):
            await db_lookup.call_with_deadline(_raw)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'pgcode',
        [
            '23505',  # unique_violation: a real data error
            '08006',  # connection_failure
            '40P01',  # deadlock_detected
            None,  # driver error with no SQLSTATE (e.g. socket EBADF)
        ])
    async def test_other_db_errors_still_propagate_unchanged(
            self, monkeypatch, pgcode):
        """The mapping is narrow: only the aligned server-side timeouts."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        with pytest.raises(sqlalchemy.exc.OperationalError) as excinfo:
            await db_lookup.call_with_deadline(_raises_db_error(pgcode))
        assert not isinstance(excinfo.value, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_non_db_errors_still_propagate_unchanged(self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        def _boom(*args, **kwargs):
            del args, kwargs
            raise RuntimeError('not a database error')

        with pytest.raises(RuntimeError):
            await db_lookup.call_with_deadline(_boom)

    @pytest.mark.asyncio
    async def test_exhaustion_is_not_swallowed_by_the_mapping(
            self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        exhausted = threads.OnDemandThreadExecutor(name='test-exhausted-map',
                                                   max_workers=0)
        with mock.patch.object(db_lookup.executor,
                               'get_auth_thread_executor',
                               return_value=exhausted):
            with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
                await db_lookup.call_with_deadline(lambda: 'never runs')

    @pytest.mark.asyncio
    async def test_auth_proxy_upsert_cut_off_by_lock_timeout_is_503(
            self, monkeypatch, mock_request, call_next_sentinel):
        """The incident shape: the users row is locked by another session
        and this request's upsert waits. With `lock_timeout` set on the
        transaction the database fails the wait (55P03) and the thread is
        freed; the client must see the same retryable 503 as a deadline."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        proxy_config = mock.Mock()
        proxy_config.enabled = True
        with mock.patch.object(server.server_config,
                               'load_external_proxy_config',
                               return_value=proxy_config):
            middleware = server.AuthProxyMiddleware(app=mock.Mock())

        with mock.patch.object(
                server,
                '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch(
                    'sky.global_user_state.add_or_update_user',
                    _raises_db_error(
                        '55P03',
                        'canceling statement due to lock timeout')):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached

    @pytest.mark.asyncio
    async def test_bearer_token_lookup_cut_off_by_statement_timeout_is_503(
            self, monkeypatch, mock_request, call_next_sentinel):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        mock_request.headers = {'authorization': 'Bearer sky_token'}
        middleware = server.BearerTokenMiddleware(app=mock.Mock())

        with mock.patch.dict(
                os.environ,
            {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
                mock.patch('sky.users.token_service.token_service') as tks, \
                mock.patch(
                    'sky.global_user_state.get_service_account_token_by_hash',
                    _raises_db_error('57014')):
            tks.verify_token.return_value = {
                'sub': 'sa-1',
                'name': 'sa',
                'token_id': 'tok-1'
            }
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached


class TestDeadlineHandedToTheDbLayer:
    """`_run_with_deadline` sets the thread-local deadline the DB layer honours.

    Without it the SET LOCAL listener and the wait callback are inert and the
    thread is only ever freed by the database -- the pin this change removes.
    """

    @pytest.mark.asyncio
    async def test_thread_local_deadline_is_set_inside_the_call(
            self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        seen = {}

        def _probe():
            seen['deadline'] = db_deadline.get_deadline()
            seen['now'] = time.monotonic()
            return 'ok'

        assert await db_lookup.call_with_deadline(_probe) == 'ok'
        assert seen[
            'deadline'] is not None, 'no deadline handed to the DB layer'
        remaining = seen['deadline'] - seen['now']
        # The inner budget: the 5s deadline minus the client margin.
        assert 4.0 < remaining <= 5 - db_lookup._CLIENT_DEADLINE_MARGIN_SECONDS

    @pytest.mark.asyncio
    async def test_deadline_origin_is_the_submit_not_the_thread_start(
            self, monkeypatch):
        """Same origin as `wait_for`: time the call spends between submit
        and thread start (a busy pool, a loop stall) comes off the DB
        layer's budget too, so the two deadlines cannot disagree."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        real = context_utils.to_thread_with_executor
        delay = 0.3

        async def _start_late(pool, fn, *args, **kwargs):
            await asyncio.sleep(delay)
            return await real(pool, fn, *args, **kwargs)

        monkeypatch.setattr(context_utils, 'to_thread_with_executor',
                            _start_late)
        seen = {}

        def _probe():
            seen['remaining'] = db_deadline.get_deadline() - time.monotonic()

        await db_lookup.call_with_deadline(_probe)
        inner = 5 - db_lookup._CLIENT_DEADLINE_MARGIN_SECONDS
        # A deadline computed at thread start would show ~inner remaining.
        assert seen['remaining'] < inner - delay + 0.1, seen
        assert seen['remaining'] > inner - delay - 1.0, seen

    @pytest.mark.asyncio
    async def test_request_pool_variant_sets_it_too(self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        seen = {}

        def _probe():
            seen['deadline'] = db_deadline.get_deadline()

        await db_lookup._call_on_request_pool(_probe)
        assert seen['deadline'] is not None

    @pytest.mark.asyncio
    async def test_deadline_is_cleared_after_the_call(self, monkeypatch):
        # One worker thread, so the check runs on the thread the call used.
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:

            def _boom():
                raise sqlalchemy.exc.OperationalError(
                    'stmt', {},
                    db_deadline.DBDeadlineExceeded('x',
                                                   reason='client_deadline'))

            await db_lookup._run_with_deadline(pool, db_lookup.POOL_AUTH,
                                               lambda: 'ok')
            with pytest.raises(db_lookup.AuthDBTimeoutError):
                await db_lookup._run_with_deadline(pool, db_lookup.POOL_AUTH,
                                                   _boom)
            assert pool.submit(db_deadline.get_deadline).result(5) is None
        finally:
            pool.shutdown(wait=True)

    @pytest.mark.asyncio
    async def test_a_tiny_budget_still_leaves_a_positive_deadline(
            self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 0.2)
        seen = {}

        def _probe():
            seen['remaining'] = db_deadline.get_deadline() - time.monotonic()

        await db_lookup.call_with_deadline(_probe)
        assert seen['remaining'] > 0


class TestLifespanWiring:
    """The server lifespan installs the psycopg2 wait callback.

    That call is the one place the client-side half is switched on (never on
    import -- see `TestNoInstallOnImport` in the deadline tests). Everything
    else the lifespan starts is stubbed: this is a test of one line, and must
    not touch the requests DB or start daemons.
    """

    @pytest.mark.asyncio
    async def test_lifespan_installs_the_wait_callback(self, monkeypatch):

        async def _noop(*args, **kwargs):
            del args, kwargs

        monkeypatch.setattr(server.requests_lib,
                            'delete_orphan_internal_daemons_async', _noop)
        monkeypatch.setattr(server.daemons, 'INTERNAL_REQUEST_DAEMONS', [])
        monkeypatch.setattr(server, 'schedule_on_boot_check_async', _noop)
        monkeypatch.setattr(server, 'cleanup_upload_ids', _noop)
        monkeypatch.setattr(server.version_check, 'check_versions_periodically',
                            _noop)
        monkeypatch.setattr(server.loop_stall, 'start_watchdog',
                            lambda **kwargs: None)
        monkeypatch.setattr(server.metrics_utils, 'METRICS_ENABLED', False)
        db_deadline.uninstall_wait_callback()
        try:
            assert db_deadline.get_wait_callback() is None
            async with server.lifespan(mock.Mock()):
                await asyncio.sleep(0)  # let the stubbed startup tasks finish
                assert (db_deadline.get_wait_callback() is
                        db_deadline.wait_callback)
        finally:
            db_deadline.uninstall_wait_callback()


class TestClientSideDeadlineMapping:
    """What the DB layer raises reaches the middleware as the retryable 503.

    The client-side bounds (the wait callback) raise `DBDeadlineExceeded`; a
    connection that dropped under the call raises a plain psycopg2 error with
    no SQLSTATE. Both are the client's bad luck, not a bad request, and must
    not surface as a bare 500. Real faults still propagate unchanged.
    """

    @pytest.fixture(autouse=True)
    def real_deadline(self, monkeypatch, clear_timeouts):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

    @pytest.mark.asyncio
    async def test_client_deadline_becomes_timeout_error(self):

        def _raise():
            orig = db_deadline.DBDeadlineExceeded(
                'x', reason='client_deadline_fd_stolen')
            raise sqlalchemy.exc.OperationalError('stmt', {}, orig)

        with pytest.raises(db_lookup.AuthDBTimeoutError) as ei:
            await db_lookup.call_with_deadline(_raise)
        assert ei.value.reason == 'client_deadline_fd_stolen'
        assert isinstance(ei.value, asyncio.TimeoutError)
        assert isinstance(ei.value.__cause__, sqlalchemy.exc.OperationalError)
        assert _timeouts(cause='client_deadline_fd_stolen',
                         pool=db_lookup.POOL_AUTH) == 1.0

    @pytest.mark.asyncio
    async def test_unwrapped_client_deadline_maps(self):

        def _raise():
            raise db_deadline.DBDeadlineExceeded('x', reason='connect_timeout')

        with pytest.raises(db_lookup.AuthDBTimeoutError) as ei:
            await db_lookup.call_with_deadline(_raise)
        assert ei.value.reason == 'connect_timeout'

    @pytest.mark.asyncio
    async def test_server_timeout_counts_its_reason(self):
        with pytest.raises(db_lookup.AuthDBTimeoutError) as ei:
            await db_lookup.call_with_deadline(_raises_db_error('55P03'))
        assert ei.value.reason == 'lock_timeout'
        assert _timeouts(cause='lock_timeout',
                         pool=db_lookup.POOL_AUTH) == 1.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize('orig', [
        psycopg2.OperationalError('server closed the connection unexpectedly'),
        psycopg2.InterfaceError('connection already closed'),
    ])
    async def test_dropped_connection_becomes_retryable(self, orig):

        def _raise():
            raise sqlalchemy.exc.OperationalError('stmt', {}, orig)

        with pytest.raises(db_lookup.AuthDBTimeoutError) as ei:
            await db_lookup.call_with_deadline(_raise)
        assert ei.value.reason == 'db_error'
        # Not a timeout: the timeout counter stays about timeouts (#10710).
        assert _timeouts(cause='db_error') == 0.0

    @pytest.mark.asyncio
    async def test_dropped_connection_is_a_503_at_the_middleware(
            self, mock_request, call_next_sentinel):
        proxy_config = mock.Mock()
        proxy_config.enabled = True
        with mock.patch.object(server.server_config,
                               'load_external_proxy_config',
                               return_value=proxy_config):
            middleware = server.AuthProxyMiddleware(app=mock.Mock())

        def _dropped(*args, **kwargs):
            del args, kwargs
            raise sqlalchemy.exc.OperationalError(
                'INSERT INTO users ...', {},
                psycopg2.OperationalError('SSL SYSCALL error: EOF detected'))

        with mock.patch.object(
                server,
                '_extract_user_from_header',
                return_value=models.User(id='u-1', name='tester')), \
                mock.patch('sky.global_user_state.add_or_update_user',
                           _dropped):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)
        _assert_retryable_timeout_503(response)
        assert not call_next_sentinel.reached

    @pytest.mark.asyncio
    async def test_sqlite_operational_error_still_propagates(self):
        # sqlite3.OperationalError also covers schema errors ("no such
        # table"); those are real faults, not transient, and stay a 500.

        def _raise():
            raise sqlalchemy.exc.OperationalError(
                'stmt', {}, sqlite3.OperationalError('no such table: users'))

        with pytest.raises(sqlalchemy.exc.OperationalError) as ei:
            await db_lookup.call_with_deadline(_raise)
        assert not isinstance(ei.value, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_programming_error_still_propagates(self):

        def _raise():
            raise sqlalchemy.exc.ProgrammingError(
                'stmt', {}, psycopg2.ProgrammingError('syntax error'))

        with pytest.raises(sqlalchemy.exc.ProgrammingError):
            await db_lookup.call_with_deadline(_raise)

    @pytest.mark.asyncio
    async def test_caller_timeout_still_raises_and_is_counted(
            self, monkeypatch):
        # The thread does not give up (a Python-level block, not DB I/O):
        # wait_for is the backstop, and its firing is the "pinned" alarm.
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 0.1)
        with pytest.raises(asyncio.TimeoutError) as ei:
            await db_lookup.call_with_deadline(lambda: time.sleep(0.6))
        assert not isinstance(ei.value, db_lookup.AuthDBTimeoutError)
        assert _timeouts(cause=db_lookup.TIMEOUT_CAUSE_DEADLINE,
                         pool=db_lookup.POOL_AUTH) == 1.0

    @pytest.mark.asyncio
    async def test_a_late_thread_outcome_is_logged(self, monkeypatch):
        # wait_for released the caller; the thread finishes later. Its outcome
        # lands on a cancelled future, so the only trace is this log line --
        # what an operator correlates with a "stuck thread" report.
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 0.1)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _late_then_fail():
            time.sleep(0.5)
            raise sqlalchemy.exc.OperationalError(
                'stmt', {},
                db_deadline.DBDeadlineExceeded('x', reason='client_deadline'))

        try:
            with mock.patch.object(db_lookup.logger, 'info') as info:
                with pytest.raises(asyncio.TimeoutError):
                    await db_lookup._run_with_deadline(pool, db_lookup.POOL_AUTH,
                                          _late_then_fail)
                pool.shutdown(wait=True)  # let the thread finish
            messages = [str(c.args[0]) for c in info.call_args_list]
            # Other tests in this module leave late threads of their own
            # behind (tiny budgets, sleeping stand-ins); pick out ours.
            late = [m for m in messages if '_late_then_fail' in m]
            assert late, messages
            assert 'finished' in late[0] and 'after' in late[0]
            assert 'client_deadline' in late[0]
        finally:
            pool.shutdown(wait=True)

    @pytest.mark.asyncio
    async def test_an_in_time_outcome_is_not_logged_as_late(self, monkeypatch):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        with mock.patch.object(db_lookup.logger, 'info') as info:
            assert await db_lookup.call_with_deadline(lambda: 'ok') == 'ok'
        assert not [
            c for c in info.call_args_list if 'finished' in str(c.args[0])
        ]


def _never_runs():
    """A call the executor rejects before it ever runs."""
    raise AssertionError('should not have been called')


def _timeouts(**labels) -> float:
    """Current value of the auth-DB-timeout counter for these labels."""
    total = 0.0
    counter = metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith('_total'):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                total += sample.value
    return total


@pytest.fixture
def clear_timeouts(monkeypatch):
    # Recording is gated on METRICS_ENABLED, which is a module constant read
    # at import and false unless the server was started with metrics on.
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    # The rate limiter is process-global and stateful: one test tripping it
    # as a side effect would otherwise decide whether another test sees the
    # first-failure WARNING, and the failure would look like a logging bug
    # rather than an ordering one. Swap in a fresh one, as
    # test_edge_error_counting.py does.
    fresh = middleware_utils.RecordingFailureLog()
    monkeypatch.setattr(middleware_utils, '_recording_failure_log', fresh)
    metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL.clear()
    yield fresh
    metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL.clear()


class TestAuthDBTimeoutCounter:
    """An auth DB call that times out must leave a trace in the metrics.

    The deadline frees the caller but not the thread, so each of these costs
    an auth executor slot until the call returns on its own. It was log-only:
    in the 2026-09-08 incident the first one preceded the first
    client-visible 503 by 13 minutes with nothing to alert on.
    """

    @pytest.mark.asyncio
    async def test_the_client_side_deadline_is_counted(self, monkeypatch,
                                                       clear_timeouts):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 0.05)

        def _parked(*args, **kwargs):
            del args, kwargs
            time.sleep(5)

        with pytest.raises(asyncio.TimeoutError):
            await db_lookup.call_with_deadline(_parked)

        assert _timeouts(site='_parked',
                         cause=db_lookup.TIMEOUT_CAUSE_DEADLINE,
                         pool=db_lookup.POOL_AUTH) == 1.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize('pgcode, cause', [
        ('55P03', 'lock_timeout'),
        ('57014', 'statement_timeout'),
        ('25P03', 'idle_in_transaction_session_timeout'),
    ])
    async def test_a_database_side_timeout_is_counted_by_its_setting(
            self, monkeypatch, clear_timeouts, pgcode, cause):
        """The two kinds mean opposite things -- the thread comes back from a
        DB-side timeout and does not from a deadline -- so `cause` must tell
        them apart, and a `lock_timeout` must be readable on its own."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        with pytest.raises(db_lookup.AuthDBTimeoutError):
            await db_lookup.call_with_deadline(_raises_db_error(pgcode))

        assert _timeouts(cause=cause) == 1.0
        assert _timeouts(cause=db_lookup.TIMEOUT_CAUSE_DEADLINE) == 0.0

    @pytest.mark.asyncio
    async def test_the_request_pool_variant_is_counted_too(
            self, monkeypatch, clear_timeouts):
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 0.05)

        def _parked_on_request_pool(*args, **kwargs):
            del args, kwargs
            time.sleep(5)

        with pytest.raises(asyncio.TimeoutError):
            await db_lookup._call_on_request_pool(_parked_on_request_pool)

        # The larger pool, and the one whose blocker is often a policy
        # lock rather than the database: it must not read as auth pressure.
        assert _timeouts(site='_parked_on_request_pool',
                         cause=db_lookup.TIMEOUT_CAUSE_DEADLINE,
                         pool=db_lookup.POOL_REQUEST) == 1.0
        assert _timeouts(pool=db_lookup.POOL_AUTH) == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize('pgcode', ['23505', '08006', '40P01', None])
    async def test_other_db_errors_are_not_counted(self, monkeypatch,
                                                   clear_timeouts, pgcode):
        """Only timeouts. A unique violation or a dropped connection is a
        different problem and must not dilute this counter."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        with pytest.raises(sqlalchemy.exc.OperationalError):
            await db_lookup.call_with_deadline(_raises_db_error(pgcode))
        assert _timeouts() == 0.0

    @pytest.mark.asyncio
    async def test_executor_exhaustion_is_not_counted(self, monkeypatch,
                                                      clear_timeouts):
        """Exhaustion already has `sky_apiserver_threads_exhausted_total`;
        counting it here too would double-count one event across two
        metrics."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        exhausted = threads.OnDemandThreadExecutor(name='test-exhausted-count',
                                                   max_workers=0)
        with mock.patch.object(db_lookup.executor,
                               'get_auth_thread_executor',
                               return_value=exhausted):
            with pytest.raises(exceptions.ConcurrentWorkerExhaustedError):
                await db_lookup.call_with_deadline(_never_runs)
        assert _timeouts() == 0.0

    @pytest.mark.asyncio
    async def test_nothing_is_counted_when_metrics_are_disabled(
            self, monkeypatch, clear_timeouts):
        """Every instrument outside the metrics middleware is a no-op when
        metrics are off; this one runs on the auth path, so it has to check
        for itself rather than relying on the middleware not being
        registered."""
        monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', False)
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        with pytest.raises(db_lookup.AuthDBTimeoutError):
            await db_lookup.call_with_deadline(_raises_db_error('55P03'))

        assert _timeouts() == 0.0

    @pytest.mark.asyncio
    async def test_a_callable_without_a_name_does_not_widen_the_label(
            self, monkeypatch, clear_timeouts):
        """`site` is claimed to be a closed set. Every call site in the
        server passes a function or a bound method, but a callable with no
        `__name__` (a partial, a class instance) must fall into one fixed
        bucket rather than become an unbounded label value."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)
        partial = functools.partial(_raises_db_error('55P03'))

        with pytest.raises(db_lookup.AuthDBTimeoutError):
            await db_lookup.call_with_deadline(partial)

        assert _timeouts(site='unknown', cause='lock_timeout') == 1.0

    @pytest.mark.asyncio
    async def test_repeated_counting_failures_warn_once(self, monkeypatch,
                                                        clear_timeouts):
        """The faults this guards against are persistent, on a path that
        fires at request rate during the very incident the counter exists
        for, so an unconditional warning would flood the log exactly when it
        needs reading. Rate limiting is the shared one in middleware_utils,
        so this pins that the auth path is wired into it, not that it works.
        """
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        with mock.patch.object(metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL,
                               'labels',
                               side_effect=RuntimeError('multiproc dir full')):
            with mock.patch.object(middleware_utils.logger, 'warning') as warn:
                for _ in range(5):
                    with pytest.raises(db_lookup.AuthDBTimeoutError):
                        await db_lookup.call_with_deadline(
                            _raises_db_error('55P03'))

        recording = [
            c for c in warn.call_args_list
            if 'an auth-path timeout' in c.args[0]
        ]
        assert len(recording) == 1, warn.call_args_list
        # Every failure is still counted, so the suppressed ones are
        # reported rather than lost.
        assert clear_timeouts.failures == 5

    @pytest.mark.asyncio
    async def test_a_counting_failure_does_not_change_the_auth_answer(
            self, monkeypatch, clear_timeouts):
        """This runs with an auth-path exception in flight. A recording fault
        must not replace it: that would turn the retryable 503 the client
        needs into a bare 500, during the very DB incident this counter is
        for."""
        monkeypatch.setattr(db_lookup, 'AUTH_DB_TIMEOUT_SECONDS', 5)

        with mock.patch.object(metrics_utils.SKY_APISERVER_AUTH_TIMEOUTS_TOTAL,
                               'labels',
                               side_effect=RuntimeError('multiproc dir full')):
            with pytest.raises(db_lookup.AuthDBTimeoutError):
                await db_lookup.call_with_deadline(_raises_db_error('55P03'))


class TestHealthProbeTimeoutIsOnlyVisibleHere:
    """The case that motivates counting at the choke point.

    `/api/health` keeps its basic-auth lookup best-effort: on timeout it
    proceeds unauthenticated so probes survive a DB incident. That is the
    right behaviour and it means the request produces no error status, no
    rejection, nothing a request-level metric can see -- while still having
    leaked an auth executor thread. This counter is its only trace.
    """

    @pytest.mark.asyncio
    async def test_a_swallowed_timeout_still_moves_the_counter(
            self, mock_request, call_next_sentinel, clear_timeouts):
        mock_request.url.path = '/api/health'
        mock_request.headers = {'authorization': 'Basic dXNlcjpwYXNz'}
        middleware = server.BasicAuthMiddleware(app=mock.Mock())

        with mock.patch.object(server.loopback,
                               'is_loopback_request',
                               return_value=False), \
                mock.patch('sky.global_user_state.get_user_by_name',
                           _slow([])):
            response = await middleware.dispatch(mock_request,
                                                 call_next_sentinel)

        # Unchanged: the probe still succeeds, unauthenticated.
        assert response.status_code == 200
        assert call_next_sentinel.reached
        assert mock_request.state.auth_user is None
        # ...and the timeout is no longer invisible.
        assert _timeouts(cause=db_lookup.TIMEOUT_CAUSE_DEADLINE) == 1.0
