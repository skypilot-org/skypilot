"""Tests for the authenticating-proxy credential helper (api_server.auth_proxy)."""

import asyncio
import base64
import json
import subprocess
import threading
import time
import unittest.mock as mock

import httpx
import pytest
import yaml

from sky import exceptions
from sky import skypilot_config
from sky.client import auth_proxy
from sky.client import common as client_common
from sky.client import sdk
from sky.server import common as server_common

ENDPOINT = 'https://sky.example.com'


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


class _UserConfig:
    """The user config file; set() rewrites its api_server section."""

    def __init__(self, path):
        self.path = path

    def set(self, auth=None, endpoint=ENDPOINT, **api_server):
        if endpoint is not None:
            api_server['endpoint'] = endpoint
        if auth is not None:
            api_server['auth_proxy'] = auth
        self.path.write_text(yaml.safe_dump({'api_server': api_server}))
        auth_proxy.clear_cache()

    def api_server(self):
        return yaml.safe_load(self.path.read_text())['api_server']


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    path = tmp_path / 'config.yaml'
    path.write_text('')
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(path))
    monkeypatch.delenv('SKYPILOT_SERVICE_ACCOUNT_TOKEN', raising=False)
    return _UserConfig(path)


class TestAuthProxyHeaders:

    def test_not_configured(self, user_config):
        user_config.set()
        with mock.patch('subprocess.run') as run:
            assert auth_proxy.get_auth_proxy_headers(ENDPOINT) == {}
        run.assert_not_called()

    def test_string_command_defaults(self, user_config):
        user_config.set({'command': 'gcloud auth print-identity-token'})
        with mock.patch('subprocess.run',
                        return_value=_completed('tok123\n')) as run:
            headers = auth_proxy.get_auth_proxy_headers(ENDPOINT + '/api/x')
        assert headers == {'Proxy-Authorization': 'Bearer tok123'}
        assert run.call_args.args[0] == [
            'gcloud', 'auth', 'print-identity-token'
        ]
        # Never through a shell.
        assert run.call_args.kwargs.get('shell') in (None, False)

    def test_list_command_custom_header_and_template(self, user_config):
        user_config.set({
            'command': ['cloudflared', 'access', 'token', '--app', 'x y'],
            'header': 'cf-access-token',
            'value_template': '{token}',
        })
        with mock.patch('subprocess.run',
                        return_value=_completed('abc')) as run:
            headers = auth_proxy.get_auth_proxy_headers(ENDPOINT)
        assert headers == {'cf-access-token': 'abc'}
        assert run.call_args.args[0] == [
            'cloudflared', 'access', 'token', '--app', 'x y'
        ]

    def test_jwt_cached_until_near_expiry(self, user_config):
        user_config.set({'command': 'print-token'})
        token = _jwt(time.time() + 3600)
        with mock.patch('subprocess.run',
                        return_value=_completed(token)) as run:
            first = auth_proxy.get_auth_proxy_headers(ENDPOINT)
            second = auth_proxy.get_auth_proxy_headers(ENDPOINT)
        assert first == second == {'Proxy-Authorization': f'Bearer {token}'}
        assert run.call_count == 1

    def test_jwt_about_to_expire_is_refreshed(self, user_config):
        user_config.set({'command': 'print-token'})
        # Within the one-minute margin: never served from cache.
        token = _jwt(time.time() + 30)
        with mock.patch('subprocess.run',
                        return_value=_completed(token)) as run:
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
        assert run.call_count == 2

    def test_opaque_token_uses_cache_seconds(self, user_config):
        user_config.set({'command': 'print-token', 'cache_seconds': 0})
        with mock.patch('subprocess.run',
                        return_value=_completed('opaque')) as run:
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
        assert run.call_count == 2

        user_config.set({'command': 'print-token'})  # default 300 s
        with mock.patch('subprocess.run',
                        return_value=_completed('opaque')) as run:
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
            auth_proxy.get_auth_proxy_headers(ENDPOINT)
        assert run.call_count == 1

    def test_nonzero_exit_raises(self, user_config):
        user_config.set({'command': 'gcloud auth print-identity-token'})
        with mock.patch('subprocess.run',
                        return_value=_completed('', 1,
                                                'Reauthentication required')):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='Reauthentication required'):
                auth_proxy.get_auth_proxy_headers(ENDPOINT)

    def test_missing_binary_raises(self, user_config):
        user_config.set({'command': 'no-such-binary'})
        with mock.patch('subprocess.run', side_effect=FileNotFoundError('x')):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='failed to run'):
                auth_proxy.get_auth_proxy_headers(ENDPOINT)

    @pytest.mark.parametrize('output', ['', '  \n', 'a\nb', 'a\r\nX-Evil: 1'])
    def test_rejects_output_that_is_not_one_line(self, user_config, output):
        user_config.set({'command': 'print-token'})
        with mock.patch('subprocess.run', return_value=_completed(output)):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='exactly one non-empty line'):
                auth_proxy.get_auth_proxy_headers(ENDPOINT)

    @pytest.mark.parametrize(
        'template', ['Bearer {{token}}', '{token} {user}', '{token} {'])
    def test_rejects_unusable_value_template(self, user_config, template):
        user_config.set({'command': 'print-token', 'value_template': template})
        with mock.patch('subprocess.run') as run:
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='must contain {token}'):
                auth_proxy.get_auth_proxy_headers(ENDPOINT)
        run.assert_not_called()

    def test_literal_braces_in_value_template(self, user_config):
        user_config.set({
            'command': 'print-token',
            'value_template': '{{v1}} {token}'
        })
        with mock.patch('subprocess.run', return_value=_completed('t')):
            assert auth_proxy.get_auth_proxy_headers(ENDPOINT) == {
                'Proxy-Authorization': '{v1} t'
            }


class TestOnlyTheUsersOwnServer:
    """The token goes only to the endpoint configured next to it."""

    @pytest.mark.parametrize('url', [
        'https://sky.example.com/api/v1/status',
        'https://SKY.example.com:443/x',
        'wss://sky.example.com/ssh-proxy?cluster=c',
    ])
    def test_same_server(self, user_config, url):
        user_config.set({'command': 'print-token'})
        with mock.patch('subprocess.run', return_value=_completed('t')):
            assert auth_proxy.get_auth_proxy_headers(url) == {
                'Proxy-Authorization': 'Bearer t'
            }

    @pytest.mark.parametrize('url', [
        'https://evil.example.com/api/health',
        'https://sky.example.com:8443/api/health',
        'http://sky.example.com/api/health',
        'ws://sky.example.com/ssh-proxy',
        'https://sky.example.com.evil.com/',
        'not a url',
    ])
    def test_other_server_gets_nothing(self, user_config, url):
        user_config.set({'command': 'print-token'})
        with mock.patch('subprocess.run') as run:
            assert auth_proxy.get_auth_proxy_headers(url) == {}
        run.assert_not_called()

    def test_no_endpoint_means_no_token(self, user_config):
        user_config.set({'command': 'print-token'}, endpoint=None)
        with mock.patch('subprocess.run') as run:
            assert auth_proxy.get_auth_proxy_headers(ENDPOINT) == {}
        run.assert_not_called()

    def test_project_config_cannot_configure_a_command(self, user_config,
                                                       tmp_path, monkeypatch):
        """A repo's .sky.yaml must not make the client run a command."""
        user_config.set()
        project = tmp_path / 'project.yaml'
        project.write_text(
            yaml.safe_dump({
                'api_server': {
                    'endpoint': ENDPOINT,
                    'auth_proxy': {
                        'command': 'touch /tmp/pwned'
                    },
                }
            }))
        monkeypatch.setenv(skypilot_config.ENV_VAR_PROJECT_CONFIG, str(project))
        skypilot_config.reload_config()
        try:
            # The merged config has it...
            assert skypilot_config.get_nested(('api_server', 'auth_proxy'),
                                              None) is not None
            # ...but only the user's own file counts.
            with mock.patch('subprocess.run') as run:
                assert auth_proxy.get_auth_proxy_headers(ENDPOINT) == {}
                assert auth_proxy.get_auth_headers(ENDPOINT) == {}
            run.assert_not_called()
        finally:
            monkeypatch.delenv(skypilot_config.ENV_VAR_PROJECT_CONFIG)
            skypilot_config.reload_config()


class TestWithServiceAccount:

    def test_both_credentials(self, user_config, monkeypatch):
        user_config.set({'command': 'print-token'})
        monkeypatch.setenv('SKYPILOT_SERVICE_ACCOUNT_TOKEN', 'sky_abc')
        with mock.patch('subprocess.run', return_value=_completed('t')):
            assert auth_proxy.get_auth_headers(ENDPOINT) == {
                'Proxy-Authorization': 'Bearer t',
                'Authorization': 'Bearer sky_abc',
            }

    @pytest.mark.parametrize('header', ['Authorization', 'authorization'])
    def test_same_header_is_an_error(self, user_config, monkeypatch, header):
        user_config.set({'command': 'print-token', 'header': header})
        monkeypatch.setenv('SKYPILOT_SERVICE_ACCOUNT_TOKEN', 'sky_abc')
        with mock.patch('subprocess.run', return_value=_completed('t')):
            with pytest.raises(exceptions.ApiServerAuthProxyError,
                               match='service account token'):
                auth_proxy.get_auth_headers(ENDPOINT)

    def test_authorization_header_alone_is_fine(self, user_config):
        user_config.set({'command': 'print-token', 'header': 'Authorization'})
        with mock.patch('subprocess.run', return_value=_completed('t')):
            assert auth_proxy.get_auth_headers(ENDPOINT) == {
                'Authorization': 'Bearer t'
            }


class TestLoginKeepsSettings:
    """`sky api login` rewrites api_server; auth_proxy must survive it."""

    AUTH = {'command': 'gcloud auth print-identity-token'}

    @pytest.mark.parametrize(
        'endpoint', [ENDPOINT, ENDPOINT + '/', 'https://SKY.example.com'])
    def test_same_server_keeps_auth_proxy(self, user_config, endpoint):
        user_config.set(self.AUTH, service_account_token='sky_old')
        sdk._save_config_updates(endpoint=endpoint)
        assert user_config.api_server() == {
            'endpoint': endpoint,
            'auth_proxy': self.AUTH,
        }

    def test_other_server_drops_auth_proxy(self, user_config):
        user_config.set(self.AUTH)
        sdk._save_config_updates(endpoint='https://other.example.com')
        assert user_config.api_server() == {
            'endpoint': 'https://other.example.com'
        }


class TestRequestsCarryTheHeaders:

    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers',
                return_value={})
    @mock.patch('sky.client.auth_proxy.get_auth_proxy_headers',
                return_value={'Proxy-Authorization': 'Bearer idtok'})
    def test_proxy_header_with_cookies(self, proxy, _sa, cookie_jar):
        cookie_jar.return_value = 'jar'
        url, kwargs = server_common._prepare_authenticated_request_params(
            '/api/health', server_url=ENDPOINT)
        assert url == ENDPOINT + '/api/health'
        assert kwargs['headers'] == {'Proxy-Authorization': 'Bearer idtok'}
        proxy.assert_called_once_with(ENDPOINT)
        # The proxy credential doesn't replace SkyPilot's own auth.
        assert kwargs['cookies'] == 'jar'

    @mock.patch('sky.server.common.get_api_cookie_jar')
    @mock.patch('sky.client.service_account_auth.get_service_account_headers',
                return_value={'Authorization': 'Bearer sky_sa'})
    @mock.patch('sky.client.auth_proxy.get_auth_proxy_headers',
                return_value={'Proxy-Authorization': 'Bearer idtok'})
    def test_proxy_header_with_service_account(self, _proxy, _sa, cookie_jar):
        _, kwargs = server_common._prepare_authenticated_request_params(
            '/api/health', server_url=ENDPOINT)
        assert kwargs['headers'] == {
            'Proxy-Authorization': 'Bearer idtok',
            'Authorization': 'Bearer sky_sa',
        }
        cookie_jar.assert_not_called()

    def test_async_request_gets_a_token_off_the_event_loop(self):
        threads = []

        def fake_headers(url):
            threads.append(threading.current_thread())
            return {}

        class Stop(Exception):
            pass

        async def run():
            with mock.patch('sky.client.auth_proxy.token_needs_refresh',
                            return_value=True), \
                 mock.patch('sky.client.auth_proxy.get_auth_proxy_headers',
                            side_effect=fake_headers), \
                 mock.patch('sky.server.common.'
                            '_prepare_authenticated_request_params',
                            side_effect=Stop):
                with pytest.raises(Stop):
                    await server_common.make_authenticated_request_async(
                        mock.Mock(), 'GET', '/api/health', server_url=ENDPOINT)

        asyncio.run(run())
        assert threads and threads[0] is not threading.main_thread()

    def test_upload_retry_gets_a_new_token(self, tmp_path):
        chunk = tmp_path / 'chunk'
        chunk.write_bytes(b'data')
        rejected = httpx.Response(407, content=b'token expired')
        ok = httpx.Response(200, json={'status': 'completed'})
        client = mock.Mock()
        client.post.side_effect = [rejected, ok]
        params = client_common.UploadChunkParams(client=client,
                                                 upload_id='u',
                                                 chunk_index=0,
                                                 total_chunks=1,
                                                 file_path=str(chunk),
                                                 upload_logger=mock.Mock(),
                                                 log_file='log')
        with mock.patch('sky.server.common.get_server_url',
                        return_value=ENDPOINT), \
             mock.patch('sky.server.common.get_api_cookie_jar'), \
             mock.patch('sky.client.auth_proxy.get_auth_headers',
                        side_effect=[{'Proxy-Authorization': 'Bearer old'},
                                     {'Proxy-Authorization': 'Bearer new'}]), \
             mock.patch('sky.client.auth_proxy.clear_cache') as clear, \
             mock.patch('time.sleep'):
            assert client_common._upload_chunk_with_retry(params) == 'completed'
        sent = [
            c.kwargs['headers']['Proxy-Authorization']
            for c in client.post.call_args_list
        ]
        assert sent == ['Bearer old', 'Bearer new']
        clear.assert_called_once()
