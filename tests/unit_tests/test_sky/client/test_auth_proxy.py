"""Tests for the authenticating-proxy credential helper (api_server.auth_proxy)."""

import base64
import json
import subprocess
import time
import unittest.mock as mock

import pytest

from sky import exceptions
from sky.client import auth_proxy
from sky.server import common as server_common


def _jwt(exp: float) -> str:

    def b64(obj) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(obj).encode()).decode().rstrip('=')

    return f'{b64({"alg": "RS256"})}.{b64({"exp": exp})}.c2ln'


def _completed(stdout: str, returncode: int = 0, stderr: str = ''):
    return subprocess.CompletedProcess(args=[],
                                       returncode=returncode,
                                       stdout=stdout,
                                       stderr=stderr)


@pytest.fixture(autouse=True)
def _clear_cache():
    auth_proxy.clear_cache()
    yield
    auth_proxy.clear_cache()


@pytest.fixture
def config():
    """Patch api_server.auth_proxy; set .value to the config dict."""
    holder = mock.Mock(value=None)

    def get_nested(keys, default_value=None):
        if tuple(keys) == ('api_server', 'auth_proxy'):
            return holder.value if holder.value is not None else default_value
        return default_value

    with mock.patch('sky.skypilot_config.get_nested', side_effect=get_nested):
        yield holder


class TestAuthProxyHeaders:

    def test_not_configured(self, config):
        with mock.patch('subprocess.run') as run:
            assert auth_proxy.get_auth_proxy_headers() == {}
        run.assert_not_called()

    def test_empty_command_is_not_configured(self, config):
        config.value = {'command': ''}
        assert auth_proxy.get_auth_proxy_headers() == {}

    def test_string_command_defaults(self, config):
        config.value = {'command': 'gcloud auth print-identity-token'}
        with mock.patch('subprocess.run',
                        return_value=_completed('tok123\n')) as run:
            headers = auth_proxy.get_auth_proxy_headers()
        assert headers == {'Proxy-Authorization': 'Bearer tok123'}
        assert run.call_args.args[0] == [
            'gcloud', 'auth', 'print-identity-token'
        ]
        # Never through a shell.
        assert run.call_args.kwargs.get('shell') in (None, False)

    def test_list_command_custom_header_and_template(self, config):
        config.value = {
            'command': ['cloudflared', 'access', 'token', '--app', 'x y'],
            'header': 'cf-access-token',
            'value_template': '{token}',
        }
        with mock.patch('subprocess.run',
                        return_value=_completed('abc')) as run:
            headers = auth_proxy.get_auth_proxy_headers()
        assert headers == {'cf-access-token': 'abc'}
        assert run.call_args.args[0] == [
            'cloudflared', 'access', 'token', '--app', 'x y'
        ]

    def test_jwt_cached_until_near_expiry(self, config):
        config.value = {'command': 'print-token'}
        token = _jwt(time.time() + 3600)
        with mock.patch('subprocess.run',
                        return_value=_completed(token)) as run:
            first = auth_proxy.get_auth_proxy_headers()
            second = auth_proxy.get_auth_proxy_headers()
        assert first == second == {'Proxy-Authorization': f'Bearer {token}'}
        assert run.call_count == 1

    def test_jwt_about_to_expire_is_refreshed(self, config):
        config.value = {'command': 'print-token'}
        # Within the one-minute margin: never served from cache.
        token = _jwt(time.time() + 30)
        with mock.patch('subprocess.run',
                        return_value=_completed(token)) as run:
            auth_proxy.get_auth_proxy_headers()
            auth_proxy.get_auth_proxy_headers()
        assert run.call_count == 2

    def test_opaque_token_uses_cache_seconds(self, config):
        config.value = {'command': 'print-token', 'cache_seconds': 0}
        with mock.patch('subprocess.run',
                        return_value=_completed('opaque')) as run:
            auth_proxy.get_auth_proxy_headers()
            auth_proxy.get_auth_proxy_headers()
        assert run.call_count == 2

        auth_proxy.clear_cache()
        config.value = {'command': 'print-token'}  # default 300 s
        with mock.patch('subprocess.run',
                        return_value=_completed('opaque')) as run:
            auth_proxy.get_auth_proxy_headers()
            auth_proxy.get_auth_proxy_headers()
        assert run.call_count == 1

    def test_nonzero_exit_raises(self, config):
        config.value = {'command': 'gcloud auth print-identity-token'}
        with mock.patch('subprocess.run',
                        return_value=_completed('', 1,
                                                'Reauthentication required')):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='Reauthentication required'):
                auth_proxy.get_auth_proxy_headers()

    def test_missing_binary_raises(self, config):
        config.value = {'command': 'no-such-binary'}
        with mock.patch('subprocess.run', side_effect=FileNotFoundError('x')):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='failed to run'):
                auth_proxy.get_auth_proxy_headers()

    @pytest.mark.parametrize('output', ['', '  \n', 'a\nb', 'a\r\nX-Evil: 1'])
    def test_rejects_output_that_is_not_one_line(self, config, output):
        config.value = {'command': 'print-token'}
        with mock.patch('subprocess.run', return_value=_completed(output)):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='exactly one non-empty line'):
                auth_proxy.get_auth_proxy_headers()


class TestAuthenticatedRequestsCarryProxyHeader:

    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers',
                return_value={})
    @mock.patch('sky.client.auth_proxy.get_auth_proxy_headers',
                return_value={'Proxy-Authorization': 'Bearer idtok'})
    def test_proxy_header_with_cookies(self, _proxy, _sa, cookie_jar):
        cookie_jar.return_value = 'jar'
        url, kwargs = server_common._prepare_authenticated_request_params(
            '/api/health', server_url='https://sky.example.com')
        assert url == 'https://sky.example.com/api/health'
        assert kwargs['headers'] == {'Proxy-Authorization': 'Bearer idtok'}
        # The proxy credential doesn't replace SkyPilot's own auth.
        assert kwargs['cookies'] == 'jar'

    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers',
                return_value={'Authorization': 'Bearer sky_sa'})
    @mock.patch('sky.client.auth_proxy.get_auth_proxy_headers',
                return_value={'Proxy-Authorization': 'Bearer idtok'})
    def test_proxy_header_with_service_account(self, _proxy, _sa, cookie_jar):
        _, kwargs = server_common._prepare_authenticated_request_params(
            '/api/health', server_url='https://sky.example.com')
        assert kwargs['headers'] == {
            'Proxy-Authorization': 'Bearer idtok',
            'Authorization': 'Bearer sky_sa',
        }
        cookie_jar.assert_not_called()
