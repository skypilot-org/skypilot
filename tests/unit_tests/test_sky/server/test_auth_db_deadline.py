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
  failing.
"""

import asyncio
import os
import time
import unittest.mock as mock

import fastapi
import pytest

from sky import exceptions
from sky import models
from sky.server import server
from sky.server.auth import db_lookup
from sky.server.requests import threads
from sky.skylet import constants

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

    async def call_next(_request):
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
    """Time out the seed only. The user upsert above it uses the same helper,
    so failing every call would answer 503 before the seed is ever reached --
    which is how this test first passed for the wrong reason."""
    if getattr(func, '__name__', '') == 'seed_new_user_role':
        raise asyncio.TimeoutError()
    return True


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
                                  'call_with_deadline',
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
                               'call_with_deadline',
                               new=mock.AsyncMock()) as bounded, \
             mock.patch('sky.users.permission.permission_service') as perm:
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-new', True) is None
        assert bounded.await_count == 1
        assert bounded.await_args[0][1] == 'u-new'
        perm.queue_role_repair.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_seed_that_times_out_answers_503_and_queues(self):
        """Never proceed without a role, and never strand them either."""
        with mock.patch.object(db_lookup,
                               'call_with_deadline',
                               side_effect=asyncio.TimeoutError), \
             mock.patch('sky.users.permission.permission_service') as perm:
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-slow', True)
        assert response is not None and response.status_code == 503
        perm.queue_role_repair.assert_called_once_with('u-slow')

    @pytest.mark.asyncio
    async def test_a_saturated_executor_answers_503_and_queues(self):
        with mock.patch.object(
                db_lookup,
                'call_with_deadline',
                side_effect=exceptions.ConcurrentWorkerExhaustedError('busy')
        ), mock.patch('sky.users.permission.permission_service') as perm:
            response = await db_lookup.ensure_role_for_authenticated_user(
                'u-busy', True)
        assert response is not None and response.status_code == 503
        perm.queue_role_repair.assert_called_once_with('u-busy')

    @pytest.mark.asyncio
    async def test_a_returning_user_with_no_known_role_is_only_queued(self):
        """Queued, not awaited: the repair wants the lock that stranded them."""
        with mock.patch('sky.users.permission.permission_service') as perm, \
             mock.patch.object(db_lookup,
                               'call_with_deadline',
                               new=mock.AsyncMock()) as bounded:
            perm.probably_has_role.return_value = False
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-old', False) is None
        perm.queue_role_repair.assert_called_once_with('u-old')
        bounded.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_returning_user_with_a_role_does_nothing(self):
        with mock.patch('sky.users.permission.permission_service') as perm:
            perm.probably_has_role.return_value = True
            assert await db_lookup.ensure_role_for_authenticated_user(
                'u-fine', False) is None
        perm.queue_role_repair.assert_not_called()
