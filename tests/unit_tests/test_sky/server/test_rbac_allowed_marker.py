"""The OSS RBAC layer records its allow so a later RBAC layer can reuse it."""

import unittest.mock as mock

import fastapi
from fastapi import testclient
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


@pytest.mark.parametrize('path', ['/', '/dashboard', '/dashboard/'])
def test_dashboard_entry_paths_redirect_and_render_for_restricted_user(
        path, tmp_path, monkeypatch):
    dashboard_dir = tmp_path / 'dashboard'
    dashboard_dir.mkdir()
    (dashboard_dir / 'index.html').write_text('<html>SkyPilot dashboard</html>')
    monkeypatch.setattr(server.server_constants, 'DASHBOARD_DIR',
                        str(dashboard_dir))
    app = fastapi.FastAPI()
    app.add_api_route('/', server.root)
    app.add_api_route('/dashboard/{full_path:path}', server.serve_dashboard)
    app.add_middleware(server.RBACMiddleware)

    @app.middleware('http')
    async def authenticate(request, call_next):
        request.state.auth_user = models.User(id='u-1', name='viewer')
        return await call_next(request)

    with mock.patch('sky.users.permission.permission_service') as perm:
        perm.check_endpoint_permission.return_value = True
        with testclient.TestClient(app) as client:
            response = client.get(path)
        assert response.status_code == 200
        assert 'SkyPilot dashboard' in response.text
        assert response.url.path == '/dashboard/'
        assert [r.status_code for r in response.history
               ] == ([] if path == '/dashboard/' else [307])
        perm.check_endpoint_permission.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path', ['/dashboard_config', '/dashboard-other', '/users', '/status'])
async def test_dashboard_exemption_does_not_bypass_api_permissions(path):
    response = await _dispatch(_request(path=path), blocked=True)
    assert response.status_code == 403
