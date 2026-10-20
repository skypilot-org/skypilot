"""The OSS RBAC layer records its allow so a later RBAC layer can reuse it."""

import unittest.mock as mock

import fastapi
import pytest

from sky import models
from sky.server import middleware_utils
from sky.server import server


def _request(path='/users', method='GET', state=None):
    return fastapi.Request({
        'type': 'http',
        'method': method,
        'path': path,
        'headers': [],
        'query_string': b'',
        'state': {} if state is None else state,
    })


async def _call_next(request):
    del request
    return fastapi.responses.JSONResponse({'message': 'success'})


async def _dispatch(request, blocked):
    request.state.auth_user = models.User(id='u-1', name='tester')
    middleware = server.RBACMiddleware(app=mock.Mock())
    with mock.patch('sky.users.permission.permission_service') as perm:
        perm.check_endpoint_permission.return_value = blocked
        return await middleware.dispatch(request, _call_next)


@pytest.mark.asyncio
async def test_allow_is_recorded_for_user_path_and_method():
    request = _request()
    response = await _dispatch(request, blocked=False)
    assert response.status_code == 200
    assert middleware_utils.rbac_allowed(request, 'u-1')
    assert not middleware_utils.rbac_allowed(request, 'u-2')


@pytest.mark.asyncio
async def test_block_is_not_recorded():
    request = _request()
    response = await _dispatch(request, blocked=True)
    assert response.status_code == 403
    assert not middleware_utils.rbac_allowed(request, 'u-1')


@pytest.mark.asyncio
async def test_allow_does_not_carry_over_to_another_path_or_method():
    state = {}
    await _dispatch(_request(state=state), blocked=False)
    # Later layers build their own Request over the same scope state.
    assert middleware_utils.rbac_allowed(_request(state=state), 'u-1')
    assert not middleware_utils.rbac_allowed(
        _request(path='/users/delete', state=state), 'u-1')
    assert not middleware_utils.rbac_allowed(
        _request(method='POST', state=state), 'u-1')


def test_nothing_recorded_by_default():
    assert not middleware_utils.rbac_allowed(_request(), 'u-1')
