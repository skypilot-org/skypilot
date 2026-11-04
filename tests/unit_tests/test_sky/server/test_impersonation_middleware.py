"""The impersonation header runs a request as another user."""

import json
import unittest.mock as mock

import fastapi
import pytest

from sky import models
from sky.server import constants as server_constants
from sky.server import server
from sky.server.auth import impersonation

_ADMIN_SA = models.User(id='sa-admin', name='platform')
_ALICE = models.User(id='a1b2c3d4', name='alice@example.com')


def _request(target=None, path='/launch', method='POST'):
    headers = []
    if target is not None:
        headers.append(
            (server_constants.IMPERSONATE_USER_HEADER.lower().encode(),
             target.encode()))
    return fastapi.Request({
        'type': 'http',
        'method': method,
        'path': path,
        'headers': headers,
        'query_string': b'',
        'state': {},
    })


async def _call_next(request):
    del request
    return fastapi.responses.JSONResponse({'message': 'success'})


async def _dispatch(request,
                    caller=_ADMIN_SA,
                    check=None,
                    call_next=_call_next):
    request.state.auth_user = caller
    request.state.impersonator = None
    middleware = server.ImpersonationMiddleware(app=mock.Mock())
    with mock.patch.object(impersonation, 'check',
                           side_effect=check or
                           (lambda *_: _ALICE)) as check_mock, \
            mock.patch.object(server, 'logger') as logger:
        response = await middleware.dispatch(request, call_next)
    return response, logger, check_mock


@pytest.mark.asyncio
async def test_without_header_the_caller_is_kept():
    request = _request()
    response, logger, check = await _dispatch(request)
    assert response.status_code == 200
    assert request.state.auth_user is _ADMIN_SA
    assert request.state.impersonator is None
    check.assert_not_called()
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_allowed_request_runs_as_the_user():
    request = _request(f'  {_ALICE.name} ')
    response, logger, check = await _dispatch(request)
    assert response.status_code == 200
    check.assert_called_once_with(_ADMIN_SA, _ALICE.name, '/launch', 'POST')
    assert request.state.auth_user.id == _ALICE.id
    assert request.state.auth_user.name == _ALICE.name
    assert request.state.impersonator is _ADMIN_SA
    # Every impersonated request names the caller behind it.
    logger.info.assert_called_once()
    message = logger.info.call_args.args[0]
    assert _ADMIN_SA.id in message and _ALICE.id in message


@pytest.mark.asyncio
async def test_refusal_is_a_403_with_the_reason():

    def refuse(*_):
        raise impersonation.Refused('User \'nobody\' not found.')

    request = _request('nobody')
    response, logger, _ = await _dispatch(request, check=refuse)
    # 403 with a detail is what clients render as a permission error.
    assert response.status_code == 403
    assert json.loads(response.body)['detail'] == 'User \'nobody\' not found.'
    assert request.state.auth_user is _ADMIN_SA
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_unauthenticated_caller_is_refused():
    request = _request(_ALICE.name)
    response, _, check = await _dispatch(request, caller=None)
    assert response.status_code == 403
    check.assert_not_called()


@pytest.mark.parametrize('caller', [_ADMIN_SA, None])
@pytest.mark.asyncio
async def test_health_check_ignores_the_header(caller):
    request = _request('nobody', path='/api/health', method='GET')
    response, logger, check = await _dispatch(request, caller=caller)
    assert response.status_code == 200
    assert request.state.auth_user is caller
    check.assert_not_called()
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_rbac_checks_the_impersonated_user():
    request = _request(_ALICE.name)
    rbac_middleware = server.RBACMiddleware(app=mock.Mock())

    async def run_rbac(req):
        return await rbac_middleware.dispatch(req, _call_next)

    with mock.patch('sky.users.permission.permission_service') as perm:
        perm.check_endpoint_permission.return_value = False  # not blocked
        response, _, _ = await _dispatch(request, call_next=run_rbac)
    assert response.status_code == 200
    perm.check_endpoint_permission.assert_called_once_with(
        _ALICE.id, '/launch', 'POST')


def test_runs_after_auth_and_before_rbac():
    # Starlette lists user middleware outermost first.
    order = [m.cls for m in server.app.user_middleware]
    assert (order.index(server.BearerTokenMiddleware) < order.index(
        server.ImpersonationMiddleware) < order.index(server.RBACMiddleware))
