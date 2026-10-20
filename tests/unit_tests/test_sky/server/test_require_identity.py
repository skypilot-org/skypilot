"""RequireIdentityMiddleware refuses a request that no middleware identified.

With the external auth proxy, a request that arrives without the proxy's
identity header reaches the handlers with ``request.state.auth_user`` unset:
RBAC is skipped, request visibility is admin scope, and the executor takes
the user id from the request body. The middleware is opt-in
(``auth.external_proxy.require_identity``) and enforces only while the proxy
is enabled.

These tests wire the real ``InitializeRequestAuthUserMiddleware``,
``AuthProxyMiddleware`` and ``RequireIdentityMiddleware`` in the production
order onto a small FastAPI app, stub the auth DB calls, and check what a
client sees for each pass rule and for the refusal, over HTTP and over a
websocket handshake.
"""
import os
from typing import Optional, Tuple
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from sky.server import config as server_config
from sky.server import server
from sky.server.auth import db_lookup
from sky.skylet import constants
from sky.users import permission
from sky.users import token_service

_HEADER = 'X-Auth-Request-Email'
_IDENTITY = {_HEADER: 'bob@example.com'}
# The ssh-proxy route with its required parameter supplied.
_SSH_PROXY = '/kubernetes-pod-ssh-proxy?cluster_name=c'
_LOOPBACK = ('127.0.0.1', 50000)
_REMOTE = ('203.0.113.7', 50000)


def _proxy_config(
        enabled: bool,
        require: bool,
        header_name: str = _HEADER) -> server_config.ExternalProxyConfig:
    return server_config.ExternalProxyConfig(enabled=enabled,
                                             header_name=header_name,
                                             header_format='plaintext',
                                             require_identity=require)


def _routes(app: fastapi.FastAPI) -> None:

    @app.get('/status')
    async def status():  # pylint: disable=unused-variable
        return {'ok': True}

    @app.get('/api/health')
    async def health():  # pylint: disable=unused-variable
        return {'status': 'healthy'}

    @app.websocket('/kubernetes-pod-ssh-proxy')
    async def ssh_proxy(  # pylint: disable=unused-variable
            websocket: fastapi.WebSocket, cluster_name: str):
        del cluster_name
        await websocket.accept()
        await websocket.send_text('hello')
        await websocket.close()


def _app(require: bool = True,
         proxy_enabled: bool = True,
         header_name: str = _HEADER,
         rbac: bool = False,
         bearer: bool = False) -> fastapi.FastAPI:
    """A small app with the production middleware order.

    server.py adds ``RequireIdentityMiddleware`` before the authentication
    middlewares, i.e. inside them, so on the request path it runs after each
    of them had its chance to set ``request.state.auth_user``. The proxy
    config is read when the stack is built. With ``rbac``
    the real ``RBACMiddleware`` is added first, i.e. innermost, as in
    server.py; with ``bearer`` the real ``BearerTokenMiddleware`` is added
    outside the auth-proxy one, also as in server.py.
    """
    app = fastapi.FastAPI()
    _routes(app)
    with mock.patch.object(server_config,
                           'load_external_proxy_config',
                           return_value=_proxy_config(proxy_enabled, require,
                                                      header_name)):
        if rbac:
            app.add_middleware(server.RBACMiddleware)
        app.add_middleware(server.RequireIdentityMiddleware)
        app.add_middleware(server.AuthProxyMiddleware)
        if bearer:
            app.add_middleware(server.BearerTokenMiddleware)
        app.add_middleware(server.InitializeRequestAuthUserMiddleware)
        # Middleware classes are instantiated when the stack is built, which
        # Starlette defers to the first request; build and keep it now, while
        # the config is patched.
        app.middleware_stack = app.build_middleware_stack()
    return app


def _client(app: fastapi.FastAPI,
            peer: Optional[Tuple[str, int]] = None) -> TestClient:
    return TestClient(app,
                      raise_server_exceptions=False,
                      client=peer or _REMOTE)


def _healthy_auth():
    """The auth-proxy user upsert and role seeding, succeeding."""
    return mock.patch.multiple(
        db_lookup,
        call_with_deadline=mock.AsyncMock(return_value=False),
        ensure_role_for_authenticated_user=mock.AsyncMock(return_value=None))


def _assert_refused(response) -> None:
    assert response.status_code == 401
    assert response.json() == {'detail': 'Authentication required'}
    # The same no-store the other auth refusals carry, so a CDN never caches
    # the refusal on a cacheable URL.
    assert response.headers['cache-control'] == 'no-store'


def test_a_request_with_no_identity_is_refused():
    _assert_refused(_client(_app()).get('/status'))


def test_the_proxy_identity_header_passes():
    with _healthy_auth():
        response = _client(_app()).get('/status', headers=_IDENTITY)
    assert response.status_code == 200


def test_the_header_name_comes_from_the_proxy_config():
    app = _app(header_name='X-Custom-Identity')
    with _healthy_auth():
        passed = _client(app).get('/status',
                                  headers={'X-Custom-Identity': 'bob'})
        refused = _client(app).get('/status', headers=_IDENTITY)
    assert passed.status_code == 200
    _assert_refused(refused)


def test_a_service_account_token_is_left_to_the_bearer_middleware():
    """``BearerTokenMiddleware`` runs outside this one and has already
    validated or refused a ``sky_`` token; the middleware does not refuse
    it again. Any other bearer token carries no identity it can trust."""
    app = _app()
    assert _client(app).get('/status',
                            headers={
                                'Authorization': 'Bearer sky_token'
                            }).status_code == 200
    _assert_refused(
        _client(app).get('/status',
                         headers={'Authorization': 'Bearer not-a-sa-token'}))


def test_an_invalid_service_account_token_is_refused_by_the_stack():
    """The ``sky_`` rule above is not a bypass. In the production order
    ``BearerTokenMiddleware`` sits outside this middleware and refuses a
    ``sky_`` token that does not verify, so the request never gets here."""
    app = _app(bearer=True)
    service = token_service.token_service
    with mock.patch.dict(os.environ,
                         {constants.ENV_VAR_ENABLE_SERVICE_ACCOUNTS: 'true'}), \
            mock.patch.object(service, 'secret_loaded', return_value=True), \
            mock.patch.object(service, 'verify_token', return_value=None):
        response = _client(app).get(
            '/status', headers={'Authorization': 'Bearer sky_forged'})
    assert response.status_code == 401
    assert response.json() == {
        'detail': 'Invalid or expired service account token'
    }


def test_loopback_passes_unless_it_came_through_a_proxy():
    """The same exemption ``BasicAuthMiddleware`` grants: a loopback peer
    passes, but not when forwarding headers show the hop was a proxy."""
    app = _app()
    assert _client(app, peer=_LOOPBACK).get('/status').status_code == 200
    _assert_refused(
        _client(app,
                peer=_LOOPBACK).get('/status',
                                    headers={'X-Forwarded-For': '203.0.113.7'}))


def test_the_health_probe_passes():
    app = _app()
    assert _client(app).get('/api/health').status_code == 200
    # Only that path: the exemption is not a prefix on the whole API.
    _assert_refused(_client(app).get('/status'))


def _permission_checks(deadline: mock.AsyncMock) -> list:
    """The RBAC permission checks among the calls to the deadline helper.

    ``RBACMiddleware`` and the auth-proxy user upsert both go through
    ``db_lookup.call_with_deadline``; the function passed tells them apart.
    """
    check = permission.permission_service.check_endpoint_permission
    return [
        call.args[1:]
        for call in deadline.await_args_list
        if call.args and call.args[0] == check
    ]


@pytest.mark.parametrize('require,status', [(False, 200), (True, 401)])
def test_an_unidentified_request_skips_rbac_unless_refused_first(
        require, status):
    """The gap this middleware closes. ``RBACMiddleware`` checks the
    endpoint permission only for a request that has an auth user; one that
    has none goes straight to the handler, with no check at all. Without
    ``require_identity`` that is still what happens; with it the request is
    refused before RBAC runs. An identified request is checked either way,
    which shows RBAC is wired and the skip is real."""
    app = _app(require=require, rbac=True)
    deadline = mock.AsyncMock(return_value=False)
    with mock.patch.object(db_lookup, 'call_with_deadline', deadline), \
            mock.patch.object(db_lookup,
                              'ensure_role_for_authenticated_user',
                              mock.AsyncMock(return_value=None)):
        unidentified = _client(app).get('/status')
        assert unidentified.status_code == status
        assert _permission_checks(deadline) == []

        identified = _client(app).get('/status', headers=_IDENTITY)
        assert identified.status_code == 200
        checks = _permission_checks(deadline)
        assert len(checks) == 1
        _, path, method = checks[0]
        assert (path, method) == ('/status', 'GET')


def test_nothing_is_enforced_unless_configured():
    assert _client(_app(require=False)).get('/status').status_code == 200


def test_warns_and_enforces_nothing_when_the_proxy_is_disabled():
    """Another auth scheme refuses unauthenticated requests in that case;
    the setting is a misconfiguration, logged once at startup."""
    with mock.patch.object(server.logger, 'warning') as warning:
        app = _app(proxy_enabled=False)
    assert warning.call_count == 1
    assert 'require_identity' in warning.call_args.args[0]
    assert _client(app).get('/status').status_code == 200


def test_a_websocket_handshake_with_no_identity_is_refused():
    """ssh goes over a websocket, so the middleware is websocket-aware: the
    handshake is closed with 4401 before the route runs."""
    with pytest.raises(WebSocketDisconnect) as refused:
        with _client(_app()).websocket_connect(_SSH_PROXY):
            pass
    assert refused.value.code == 4401


def test_a_websocket_handshake_with_identity_is_accepted():
    with _healthy_auth():
        with _client(_app()).websocket_connect(_SSH_PROXY,
                                               headers=_IDENTITY) as ws:
            assert ws.receive_text() == 'hello'


def test_it_sits_inside_every_authentication_middleware_in_the_server():
    """``user_middleware`` lists the stack outermost first. The middleware
    must be inside (later than) each middleware that can set the auth user,
    and outside RBAC, which is where an unidentified request is let through."""
    names = [m.cls.__name__ for m in server.app.user_middleware]
    at = names.index('RequireIdentityMiddleware')
    for setter in ('InitializeRequestAuthUserMiddleware',
                   'BearerTokenMiddleware', 'AuthProxyMiddleware',
                   'OAuth2ProxyMiddleware'):
        assert names.index(setter) < at, names
    if 'BasicAuthMiddleware' in names:
        assert names.index('BasicAuthMiddleware') < at, names
    assert at < names.index('RBACMiddleware'), names
