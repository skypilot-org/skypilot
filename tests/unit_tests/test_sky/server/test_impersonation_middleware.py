"""An admin service account can act as another user via a request header."""

import unittest.mock as mock

import fastapi
import pytest

from sky import models
from sky.server import constants as server_constants
from sky.server import server
from sky.skylet import constants
from sky.users import rbac

_ADMIN_SA = models.User(id='sa-admin', name='platform')
_ALICE = models.User(id='a1b2c3d4',
                     name='alice@example.com',
                     user_type=models.UserType.SSO.value)
_USERS = {
    _ALICE.id: _ALICE,
    'sa-other': models.User(id='sa-other',
                            name='other-sa',
                            user_type=models.UserType.SA.value),
    constants.SKYPILOT_SYSTEM_USER_ID: models.User(
        id=constants.SKYPILOT_SYSTEM_USER_ID, name='skypilot-system'),
}


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


def _users_by_name(name):
    if name == 'dup':
        return [_ALICE, models.User(id='e5f6a7b8', name='dup')]
    return [u for u in _USERS.values() if u.name == name]


async def _dispatch(request,
                    caller=_ADMIN_SA,
                    roles=(rbac.RoleName.ADMIN.value,),
                    roles_in_memory=None,
                    call_next=_call_next):
    request.state.auth_user = caller
    request.state.impersonator = None
    middleware = server.ImpersonationMiddleware(app=mock.Mock())
    with mock.patch('sky.users.permission.permission_service') as perm, \
            mock.patch('sky.global_user_state.get_user',
                       side_effect=_USERS.get), \
            mock.patch('sky.global_user_state.get_user_by_name',
                       side_effect=_users_by_name), \
            mock.patch.object(server, 'logger') as logger:
        perm.get_user_roles.return_value = list(roles)
        perm.roles_in_memory.return_value = list(
            roles if roles_in_memory is None else roles_in_memory)
        perm.check_endpoint_permission.return_value = False  # not blocked
        response = await middleware.dispatch(request, call_next)
    return response, logger, perm


@pytest.mark.asyncio
async def test_without_header_the_caller_is_kept():
    request = _request()
    response, logger, _ = await _dispatch(request)
    assert response.status_code == 200
    assert request.state.auth_user is _ADMIN_SA
    assert request.state.impersonator is None
    logger.info.assert_not_called()


@pytest.mark.parametrize('target', [_ALICE.name, _ALICE.id])
@pytest.mark.asyncio
async def test_admin_service_account_acts_as_user(target):
    request = _request(target)
    response, logger, _ = await _dispatch(request)
    assert response.status_code == 200
    assert request.state.auth_user.id == _ALICE.id
    assert request.state.auth_user.name == _ALICE.name
    assert request.state.impersonator is _ADMIN_SA
    # Every impersonated request names the service account behind it.
    logger.info.assert_called_once()
    message = logger.info.call_args.args[0]
    assert _ADMIN_SA.id in message and _ALICE.id in message


@pytest.mark.parametrize('caller', [
    None,
    models.User(id='a1b2c3d4', name='alice@example.com'),
])
@pytest.mark.asyncio
async def test_only_service_accounts_can_impersonate(caller):
    request = _request(_ALICE.name)
    response, _, _ = await _dispatch(request, caller=caller)
    assert response.status_code == 403
    assert request.state.auth_user is caller


@pytest.mark.parametrize('roles', [
    (rbac.RoleName.USER.value,),
    (rbac.RoleName.VIEWER.value,),
    (),
])
@pytest.mark.asyncio
async def test_non_admin_service_account_cannot_impersonate(roles):
    request = _request(_ALICE.name)
    response, logger, _ = await _dispatch(request, roles=roles)
    assert response.status_code == 403
    assert request.state.auth_user is _ADMIN_SA
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_admin_role_is_read_from_memory_on_the_hot_path():
    request = _request(_ALICE.name)
    response, _, perm = await _dispatch(request)
    assert response.status_code == 200
    perm.get_user_roles.assert_not_called()


@pytest.mark.asyncio
async def test_admin_role_granted_on_another_worker_is_honored():
    # This worker's policy has not seen the role yet; the reload has.
    request = _request(_ALICE.name)
    response, _, _ = await _dispatch(request, roles_in_memory=())
    assert response.status_code == 200
    assert request.state.auth_user.id == _ALICE.id


@pytest.mark.parametrize('target,status_code', [
    ('', 400),
    ('nobody', 400),
    ('dup', 400),
    ('sa-other', 403),
    (constants.SKYPILOT_SYSTEM_USER_ID, 403),
])
@pytest.mark.asyncio
async def test_rejected_targets(target, status_code):
    request = _request(target)
    response, logger, _ = await _dispatch(request)
    assert response.status_code == status_code
    assert request.state.auth_user is _ADMIN_SA
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_rbac_checks_the_impersonated_user():
    request = _request(_ALICE.name)
    rbac_middleware = server.RBACMiddleware(app=mock.Mock())

    async def run_rbac(req):
        return await rbac_middleware.dispatch(req, _call_next)

    response, _, perm = await _dispatch(request, call_next=run_rbac)
    assert response.status_code == 200
    perm.check_endpoint_permission.assert_called_once_with(
        _ALICE.id, '/launch', 'POST')


def test_runs_after_auth_and_before_rbac():
    # Starlette lists user middleware outermost first.
    order = [m.cls for m in server.app.user_middleware]
    assert (order.index(server.BearerTokenMiddleware) < order.index(
        server.ImpersonationMiddleware) < order.index(server.RBACMiddleware))
