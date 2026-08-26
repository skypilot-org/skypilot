# Tests for the sky/client/sdk.py file
import base64
from http.cookiejar import Cookie
from http.cookiejar import MozillaCookieJar
import io
import json
import os
from pathlib import Path
import time
from unittest import mock

import click
import pytest
import requests

from sky import exceptions
from sky import skypilot_config
from sky.client import sdk as client_sdk
from sky.server import common as server_common
from sky.server import rest as server_rest
from sky.server.constants import API_COOKIE_FILE_ENV_VAR
from sky.skylet import constants
from sky.utils import common as common_utils


@pytest.fixture
def set_api_cookie_jar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create a temporary file with test cookie content
    # Netscape cookie file format: https://curl.se/docs/http-cookies.html
    # domain, include subdomains, path, useHTTPS, expires at seconds, name of cookie, value
    cookie_file = tmp_path / "test_cookie.txt"
    cookie_jar = MozillaCookieJar(filename=cookie_file)
    cookie_jar.set_cookie(
        Cookie(
            name="user_name",
            value="sky-user",
            domain="api.skypilot.co",
            version=0,
            port=None,
            port_specified=False,
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            comment="Test cookie",
            comment_url=None,
            discard=False,
            expires=int(time.time() + 3600),
            rfc2109=False,
            rest={},
        ))
    cookie_jar.save(filename=cookie_file,
                    ignore_discard=True,
                    ignore_expires=True)

    with mock.patch.dict(os.environ, clear=True):
        envvars = {
            API_COOKIE_FILE_ENV_VAR: cookie_file,
        }
        for k, v in envvars.items():
            monkeypatch.setenv(k, v)
        yield  # This is the magical bit which restore the environment after the test


def test_cookie_jar():
    # Test an empty cookie jar is created if the cookie file does not exist
    cookie_jar = server_common.get_api_cookie_jar()
    assert cookie_jar is not None
    assert isinstance(cookie_jar, requests.cookies.RequestsCookieJar)
    assert cookie_jar.get("user_name", domain="api.skypilot.co") is None


def test_cookie_jar_file(set_api_cookie_jar):
    # Test with insufficient memory
    cookie_jar = server_common.get_api_cookie_jar()
    assert cookie_jar is not None
    assert isinstance(cookie_jar, requests.cookies.RequestsCookieJar)
    assert cookie_jar.get("user_name", domain="api.skypilot.co") == "sky-user"


def test_api_info():
    with mock.patch('sky.server.common.make_authenticated_request'
                   ) as mock_make_request:
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "status": "healthy",
            "api_version": "1",
            "commit": "abc1234567890",
            "version": "1.0.0",
        }
        mock_response.raise_for_status.return_value = None
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_make_request.return_value = mock_response

        with mock.patch('sky.server.common.check_server_healthy_or_start_fn'
                       ) as mock_server_healthy:
            mock_server_healthy.return_value = None
            response = client_sdk.api_info()
            assert response is not None
            assert response["status"] == server_common.ApiServerStatus.HEALTHY
            assert response["api_version"] == "1"
            assert response["commit"] is not None
            assert response["version"] is not None
            assert mock_make_request.call_count == 1
            assert mock_make_request.call_args[0] == ('GET', '/api/health')


def test_api_info_with_cookie_file(set_api_cookie_jar):
    with mock.patch('sky.server.common.make_authenticated_request'
                   ) as mock_make_request:
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "status": "healthy",
            "api_version": "1",
            "commit": "abc1234567890",
            "version": "1.0.0",
        }
        mock_response.raise_for_status.return_value = None
        mock_response.cookies = requests.cookies.RequestsCookieJar()
        mock_make_request.return_value = mock_response

        with mock.patch('sky.server.common.check_server_healthy_or_start_fn'
                       ) as mock_server_healthy:
            mock_server_healthy.return_value = None
            response = client_sdk.api_info()
            assert response is not None
            assert response["status"] == server_common.ApiServerStatus.HEALTHY
            assert response["api_version"] == "1"
            assert response["commit"] is not None
            assert response["version"] is not None
            assert mock_make_request.call_count == 1
            assert mock_make_request.call_args[0] == ('GET', '/api/health')


@pytest.mark.parametrize(
    'deploy,host,expected_host',
    [
        # Deploy always binds a wildcard for remote access.
        (True, '127.0.0.1', '0.0.0.0'),
        (True, 'localhost', '0.0.0.0'),
        (True, '0.0.0.0', '0.0.0.0'),
        # Any IPv6 host under deploy binds the IPv6 wildcard.
        (True, '::', '::'),
        (True, '::1', '::'),
        # A full-length IPv6 literal (no '::') is still detected; the deploy
        # override runs before allowlist validation, so '::' passes.
        (True, '2001:db8:0:0:0:0:0:1', '::'),
        # Non-deploy leaves the host untouched.
        (False, '127.0.0.1', '127.0.0.1'),
        (False, '::1', '::1'),
    ])
def test_api_start_host_resolution(deploy, host, expected_host):
    """api_start resolves/validates the bind host and forwards it to start."""
    with mock.patch('sky.server.common.is_api_server_local',
                    return_value=True), \
         mock.patch('sky.server.common.check_server_healthy_or_start_fn'
                   ) as mock_start:
        client_sdk.api_start(deploy=deploy, host=host)
    assert mock_start.call_count == 1
    # check_server_healthy_or_start_fn(deploy, host, foreground, ...)
    assert mock_start.call_args[0][1] == expected_host


def test_api_start_rejects_invalid_host():
    """api_start rejects hosts outside the local allowlist."""
    with mock.patch('sky.server.common.is_api_server_local',
                    return_value=True), \
         mock.patch('sky.server.common.check_server_healthy_or_start_fn'):
        with pytest.raises(ValueError, match='Invalid host'):
            client_sdk.api_start(deploy=False, host='192.168.1.5')


def test_api_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create a temporary config file
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    test_endpoint = "http://test.skypilot.co"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_endpoint)

        # Verify the endpoint is written to config file
        assert config_path.exists()
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_endpoint
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls(
            [mock.call(test_endpoint),
             mock.call(test_endpoint)])

    # Test with existing config
    test_endpoint_2 = "http://test2.skypilot.co"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_endpoint_2)

        # Verify the endpoint is updated in config file
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_endpoint_2
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls(
            [mock.call(test_endpoint_2),
             mock.call(test_endpoint_2)])

    # Test with invalid endpoint
    with pytest.raises(click.BadParameter):
        client_sdk.api_login("invalid_endpoint")

    # Test with endpoint ending with a slash
    test_endpoint_with_slash = "http://test3.skypilot.co/"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_endpoint_with_slash)
        config = skypilot_config.get_user_config()
        # Endpoint should be stored without the trailing slash
        assert config["api_server"]["endpoint"] == "http://test3.skypilot.co"
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls([
            mock.call("http://test3.skypilot.co"),
            mock.call("http://test3.skypilot.co")
        ])

    # Test with https endpoint
    test_https_endpoint = "https://secure.skypilot.co"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_https_endpoint)
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_https_endpoint
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls(
            [mock.call(test_https_endpoint),
             mock.call(test_https_endpoint)])

    # Test with https endpoint ending with a slash
    test_https_endpoint_with_slash = "https://secure.skypilot.co/"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_https_endpoint_with_slash)
        config = skypilot_config.get_user_config()
        # Endpoint should be stored without the trailing slash
        assert config["api_server"]["endpoint"] == "https://secure.skypilot.co"
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls([
            mock.call("https://secure.skypilot.co"),
            mock.call("https://secure.skypilot.co")
        ])

    # Test with endpoint containing port number
    test_endpoint_with_port = "http://localhost:8080"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_endpoint_with_port)
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_endpoint_with_port
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls([
            mock.call(test_endpoint_with_port),
            mock.call(test_endpoint_with_port)
        ])

    # Test with endpoint containing port number and trailing slash
    test_endpoint_with_port_slash = "http://localhost:8080/"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        test_user = {}
        test_user['id'] = "b673d4fd"
        test_user['name'] = "test"
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                user=test_user,
                basic_auth_enabled=True))
        client_sdk.api_login(test_endpoint_with_port_slash)
        config = skypilot_config.get_user_config()
        # Endpoint should be stored without the trailing slash
        assert config["api_server"]["endpoint"] == "http://localhost:8080"
        # Check that server health is called twice: once during auth flow, once for identity
        assert mock_check.call_count == 2
        mock_check.assert_has_calls([
            mock.call("http://localhost:8080"),
            mock.call("http://localhost:8080")
        ])


def _mock_healthy_check():
    """Returns a mock of check_server_healthy that reports a healthy server."""
    mock_check = mock.patch('sky.server.common.check_server_healthy')
    return mock_check


def test_api_login_with_env_endpoint(monkeypatch: pytest.MonkeyPatch,
                                     tmp_path: Path):
    """Login uses the env var endpoint and does not write it to the config."""
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    env_endpoint = "http://env.skypilot.co"
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR, env_endpoint + '/')

    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login()

    # Logged into the endpoint from the env var, with the trailing slash
    # stripped.
    mock_check.assert_has_calls(
        [mock.call(env_endpoint),
         mock.call(env_endpoint)])
    # The env var already takes precedence for every command, so the endpoint
    # must not be persisted to the config file.
    config = skypilot_config.get_user_config()
    assert 'endpoint' not in config.get('api_server', {})


def test_api_login_env_endpoint_keeps_config_endpoint(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An env var login leaves an existing configured endpoint untouched."""
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    config_endpoint = "http://config.skypilot.co"
    env_endpoint = "http://env.skypilot.co"

    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        # First login without the env var, which configures the endpoint.
        client_sdk.api_login(config_endpoint)
        assert skypilot_config.get_user_config(
        )['api_server']['endpoint'] == config_endpoint

        # Then login again with the env var set.
        monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR, env_endpoint)
        client_sdk.api_login()

    # The configured endpoint is preserved, so unsetting the env var falls back
    # to the server the user logged into earlier.
    assert skypilot_config.get_user_config(
    )['api_server']['endpoint'] == config_endpoint
    mock_check.assert_called_with(env_endpoint)


def test_api_login_env_endpoint_with_conflicting_flag(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`-e` pointing elsewhere than the env var is ambiguous, so it errors."""
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR,
                       "http://env.skypilot.co")

    with pytest.raises(RuntimeError, match='already set to'):
        client_sdk.api_login("http://other.skypilot.co")


def test_api_login_env_endpoint_with_matching_flag(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`-e` matching the env var is not ambiguous, so login proceeds.

    `sky api info` suggests `sky api login --relogin -e <endpoint>` with the
    endpoint it resolved, which can be the one from the env var.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    endpoint = "http://env.skypilot.co"
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR, endpoint)

    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(endpoint + '/')

    mock_check.assert_called_with(endpoint)
    # An explicit --endpoint is persisted even when the variable names the same
    # endpoint: that is what the flag documents, and the config then agrees with
    # what is in effect.
    config = skypilot_config.get_user_config()
    assert config['api_server']['endpoint'] == endpoint


def _login_with_sa_token(endpoint: str, token: str = "sky_test_token") -> None:
    """Logs in with a service account token, so the config file holds one."""
    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(endpoint, service_account_token=token)
    assert skypilot_config.get_user_config(
    )['api_server']['service_account_token'] == token


def test_api_login_env_endpoint_hides_sa_token_from_health_check(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The residual sa token is hidden in memory, not deleted, during login.

    The token is not scoped to an endpoint, so leaving it visible would
    authenticate the health check against the env var endpoint and mask the
    NEEDS_AUTH response that the SSO flow depends on. Deleting it from the
    config file instead would destroy the configured endpoint's credential even
    when the login never completes.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    config_endpoint = "http://config.skypilot.co"
    _login_with_sa_token(config_endpoint)

    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR,
                       "http://env.skypilot.co")
    sa_token_at_health_check = []

    def _unreachable_server(endpoint):
        sa_token_at_health_check.append(
            skypilot_config.get_nested(('api_server', 'service_account_token'),
                                       default_value=None))
        raise exceptions.ApiServerConnectionError(endpoint)

    with mock.patch('sky.server.common.check_server_healthy',
                    side_effect=_unreachable_server):
        with pytest.raises(exceptions.ApiServerConnectionError):
            client_sdk.api_login()

    # Hidden from the health check...
    assert sa_token_at_health_check[0] is None
    # ...but still on disk, since the login never completed.
    config = skypilot_config.get_user_config()
    assert config['api_server']['service_account_token'] == 'sky_test_token'
    assert config['api_server']['endpoint'] == config_endpoint


def test_api_login_env_endpoint_clears_residual_sa_token_on_success(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A completed login removes the residual sa token from disk.

    The cookies it saved are the credential for that endpoint from now on, and
    the token would be sent in their place.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    config_endpoint = "http://config.skypilot.co"
    _login_with_sa_token(config_endpoint)

    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR,
                       "http://env.skypilot.co")
    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login()

    config = skypilot_config.get_user_config()
    assert 'service_account_token' not in config.get('api_server', {})
    # Only the token is dropped; the configured endpoint is kept.
    assert config['api_server']['endpoint'] == config_endpoint


def test_api_login_env_endpoint_with_sa_token_keeps_config_endpoint(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Login with a token and the env var set saves the token, not the endpoint.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    config_endpoint = "http://config.skypilot.co"
    env_endpoint = "http://env.skypilot.co"
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR, env_endpoint)
    config_path.write_text(f'api_server:\n  endpoint: {config_endpoint}\n')
    skypilot_config.reload_config()

    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(service_account_token="sky_test_token")

    mock_check.assert_called_with(env_endpoint)
    config = skypilot_config.get_user_config()
    assert config['api_server']['service_account_token'] == 'sky_test_token'
    assert config['api_server']['endpoint'] == config_endpoint


def test_api_login_prompt_defaults_to_config_endpoint(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Without `-e` or the env var, the prompt defaults to the config endpoint.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    monkeypatch.delenv(constants.SKY_API_SERVER_URL_ENV_VAR, raising=False)
    config_endpoint = "http://config.skypilot.co"
    config_path.write_text(f'api_server:\n  endpoint: {config_endpoint}\n')
    skypilot_config.reload_config()

    prompt_kwargs = {}

    def _fake_prompt(text, **kwargs):
        del text  # Unused.
        prompt_kwargs.update(kwargs)
        # click.prompt returns the default when the user just presses Enter.
        return kwargs['default']

    monkeypatch.setattr('click.prompt', _fake_prompt)
    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login()

    assert prompt_kwargs['default'] == config_endpoint
    mock_check.assert_called_with(config_endpoint)


def test_api_login_redacts_password_in_endpoint(monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path, capsys):
    """Neither the warning nor the conflict error may echo an inline password."""
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR,
                       "http://user:sup3rsecret@env.skypilot.co")

    # The conflict error mentions both endpoints.
    with pytest.raises(RuntimeError) as exc_info:
        client_sdk.api_login("http://user:al5osecret@other.skypilot.co")
    assert 'sup3rsecret' not in str(exc_info.value)
    assert 'al5osecret' not in str(exc_info.value)

    # And so does the warning on the happy path.
    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login()
    warning = [
        line for line in capsys.readouterr().out.splitlines()
        if 'Using endpoint from' in line
    ]
    assert len(warning) == 1
    assert 'sup3rsecret' not in warning[0]
    # Note: the "Logged into ..." line and the dashboard URL still show the
    # endpoint verbatim, which predates this change and is deliberate -- the
    # dashboard URL is meant to be opened, so it needs its credentials.
    # The real endpoint is still the one we authenticate against.
    mock_check.assert_called_with("http://user:sup3rsecret@env.skypilot.co")


def test_api_login_writes_the_config_file_it_reads(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The endpoint is written to the config file that is actually in effect.

    Reads honor SKYPILOT_GLOBAL_CONFIG, so writing the default path instead
    would leave the endpoint in a file nobody reads.
    """
    default_path = tmp_path / "default.yaml"
    override_path = tmp_path / "override.yaml"
    override_path.write_text(
        'api_server:\n  endpoint: http://old.skypilot.co\n')
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(default_path))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG,
                       str(override_path))
    skypilot_config.reload_config()

    new_endpoint = "http://new.skypilot.co"
    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(new_endpoint)

    assert 'endpoint: http://new.skypilot.co' in override_path.read_text()
    # The default path is not touched, so it cannot shadow the override later.
    assert not default_path.exists()
    assert skypilot_config.get_user_config(
    )['api_server']['endpoint'] == new_endpoint


def test_api_login_writes_do_not_clobber_the_other_config(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A write must start from the file it is about to write.

    When the resolved config file cannot be written, login falls back to the
    default path. Seeding the new contents from the resolved file instead would
    dump it over the default one and lose whatever that held.
    """
    default_path = tmp_path / "default.yaml"
    override_path = tmp_path / "override.yaml"
    default_path.write_text('docker:\n  run_options: [--mine]\n')
    override_path.write_text('kubernetes:\n  allowed_contexts: [theirs]\n')
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(default_path))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG,
                       str(override_path))
    # Stand in for the resolved file being unwritable, without depending on
    # file modes (a test running as root can write a read-only file).
    monkeypatch.setattr('sky.client.sdk._writable_user_config_path',
                        lambda: default_path)
    skypilot_config.reload_config()

    with _mock_healthy_check() as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login("http://new.skypilot.co")

    written = default_path.read_text()
    assert 'endpoint: http://new.skypilot.co' in written
    # Its own settings survive, and the other file's do not leak in.
    assert '--mine' in written
    assert 'theirs' not in written
    assert 'theirs' in override_path.read_text()


def test_api_login_rejects_empty_env_endpoint(monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path):
    """An empty env var is set, not unset, and misdirects every command.

    `get_server_url()` returns the empty value rather than falling back to the
    config file, so logging in and reporting success would leave every later
    command resolving to an empty URL.
    """
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR, "")

    with pytest.raises(RuntimeError, match='set to an empty value'):
        client_sdk.api_login("http://newly-set.skypilot.co")
    # Nothing was written on the way out.
    assert not config_path.exists()


def test_api_logout_with_env_endpoint(monkeypatch: pytest.MonkeyPatch):
    """Logout still errors out when the endpoint is set via the env var."""
    monkeypatch.setenv(constants.SKY_API_SERVER_URL_ENV_VAR,
                       "http://env.skypilot.co")
    with pytest.raises(RuntimeError, match='Cannot logout of API server'):
        client_sdk.api_logout()


def test_api_login_user_hash_token(monkeypatch: pytest.MonkeyPatch,
                                   tmp_path: Path):
    # Test that we set the user hash when we have a service account token.
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    user_hash = '11111111'

    user = mock.MagicMock()
    user.get.return_value = user_hash

    test_endpoint = "http://test.skypilot.co"

    # Test with service account token.
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False,
                user=user))
        client_sdk.api_login(test_endpoint, service_account_token="sky_test")

        # Verify the user hash is written to the file.
        assert user_hash_path.exists()
        assert user_hash_path.read_text() == user_hash


def test_api_login_user_hash_needs_auth(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path):
    # Test that we set the user hash when we need auth.
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    user_hash = '11111111'

    user = mock.MagicMock()
    user.get.return_value = user_hash

    test_endpoint = "http://test.skypilot.co"

    # Test needs auth.
    auth_token = base64.b64encode(
        json.dumps({
            'v': 1,
            'user': user_hash,
            'cookies': {}
        }).encode('utf-8')).decode('utf-8')

    with mock.patch('sky.server.common.check_server_healthy') as mock_check, \
         mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=None):
        # On first call, return needs auth.
        first_return_value = (
            server_common.ApiServerStatus.NEEDS_AUTH,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.NEEDS_AUTH,
                basic_auth_enabled=False))

        # On second call, auth has succeeded.
        second_return_value = (server_common.ApiServerStatus.HEALTHY,
                               server_common.ApiServerInfo(
                                   status=server_common.ApiServerStatus.HEALTHY,
                                   basic_auth_enabled=False))

        mock_check.side_effect = [first_return_value, second_return_value]

        def _fake_start_local_auth_server(callback_port, token_container,
                                          remote_endpoint):
            token_container['token'] = auth_token
            return None

        # Set the token container manually.
        monkeypatch.setattr('sky.client.oauth.start_local_auth_server',
                            _fake_start_local_auth_server)
        monkeypatch.setattr('webbrowser.open', lambda url: True)
        client_sdk.api_login(test_endpoint)

        # Verify the user hash is written to the file.
        assert user_hash_path.exists()
        assert user_hash_path.read_text() == user_hash


def test_api_login_user_hash_needs_auth_both(monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path):
    # Test that we set the user hash with the token returned from the
    # api server even if we negotiate a new hash.
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    user_hash = '11111111'

    new_user_hash = '22222222'

    user = mock.MagicMock()
    user.get.return_value = user_hash

    test_endpoint = "http://test.skypilot.co"

    # Test needs auth.
    auth_token = base64.b64encode(
        json.dumps({
            'v': 1,
            'user': new_user_hash,
            'cookies': {}
        }).encode('utf-8')).decode('utf-8')

    with mock.patch('sky.server.common.check_server_healthy') as mock_check, \
         mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=None):
        # On first call, return needs auth.
        first_return_value = (
            server_common.ApiServerStatus.NEEDS_AUTH,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.NEEDS_AUTH,
                basic_auth_enabled=False,
                user=user))

        # On second call, auth has succeeded.
        second_return_value = (server_common.ApiServerStatus.HEALTHY,
                               server_common.ApiServerInfo(
                                   status=server_common.ApiServerStatus.HEALTHY,
                                   basic_auth_enabled=False))

        mock_check.side_effect = [first_return_value, second_return_value]

        def _fake_start_local_auth_server(callback_port, token_container,
                                          remote_endpoint):
            token_container['token'] = auth_token
            return None

        # Set the token container manually.
        monkeypatch.setattr('sky.client.oauth.start_local_auth_server',
                            _fake_start_local_auth_server)
        monkeypatch.setattr('webbrowser.open', lambda url: True)
        client_sdk.api_login(test_endpoint)

        # Verify the user hash is written to the file.
        assert user_hash_path.exists()
        # We should use the old user hash from the api server.
        assert user_hash_path.read_text() == user_hash


def test_api_login_user_hash_server_healthy(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path):
    # Test that we set the user hash when we need auth.
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    user_hash = '11111111'

    user = mock.MagicMock()
    user.get.return_value = user_hash

    test_endpoint = "http://test.skypilot.co"

    # Test needs auth.
    auth_token = base64.b64encode(
        json.dumps({
            'v': 1,
            'user': user_hash,
            'cookies': {}
        }).encode('utf-8')).decode('utf-8')
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                user=user,
                basic_auth_enabled=False))

        def _fake_start_local_auth_server(callback_port, token_container,
                                          remote_endpoint):
            token_container['token'] = auth_token
            return None

        # Set the token container manually.
        monkeypatch.setattr('sky.client.oauth.start_local_auth_server',
                            _fake_start_local_auth_server)
        monkeypatch.setattr('webbrowser.open', lambda url: True)
        client_sdk.api_login(test_endpoint)

        # Verify the user hash is written to the file.
        assert user_hash_path.exists()
        assert user_hash_path.read_text() == user_hash


def test_api_login_clears_residual_sa_token(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path):
    """After login with sa token, a subsequent login without token must not
    authenticate with the residual sa token, so the server can return NEEDS_AUTH
    and trigger the SSO flow. The token is hidden in memory for the duration of
    the login and removed from the config file once the login has succeeded, so
    that a login which fails part way through leaves the credential alone."""
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    sa_user_hash = 'sa11fb39'
    sso_user_hash = '0f6adbca'

    test_endpoint = "http://test.skypilot.co"

    # Step 1: Login with service account token. This writes the sa token
    # into config and sets local user hash to the sa user.
    sa_user = {'id': sa_user_hash, 'name': 'alice'}
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False,
                user=sa_user))
        client_sdk.api_login(test_endpoint,
                             service_account_token="sky_test_token")

    # Verify sa token is in config and local hash is sa user.
    config = skypilot_config.get_user_config()
    assert config['api_server']['service_account_token'] == 'sky_test_token'
    assert user_hash_path.read_text() == sa_user_hash

    # Step 2: Login again without token. The residual sa token must not be
    # visible to the first health check. With the sa token gone, the server
    # returns NEEDS_AUTH, triggering the SSO flow.
    sa_token_at_health_check = []

    def _capture_check_server_healthy(endpoint):
        # Capture whether sa token is still in config at the time of
        # the health check.
        token = skypilot_config.get_nested(
            ('api_server', 'service_account_token'), default_value=None)
        sa_token_at_health_check.append(token)
        raise StopIteration  # Abort to inspect captured state

    with mock.patch('sky.server.common.check_server_healthy',
                    side_effect=_capture_check_server_healthy):
        with pytest.raises(StopIteration):
            client_sdk.api_login(test_endpoint)

    # The sa token must not have been visible to the first health check.
    assert sa_token_at_health_check[0] is None
    # This login was aborted, so the credential is still on disk.
    config = skypilot_config.get_user_config()
    assert config['api_server']['service_account_token'] == 'sky_test_token'

    # Step 3: A login that completes removes it, since the cookies it saved are
    # the credential for this endpoint from now on.
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = (
            server_common.ApiServerStatus.HEALTHY,
            server_common.ApiServerInfo(
                status=server_common.ApiServerStatus.HEALTHY,
                basic_auth_enabled=False))
        client_sdk.api_login(test_endpoint)
    config = skypilot_config.get_user_config()
    assert 'service_account_token' not in config.get('api_server', {})
    assert config['api_server']['endpoint'] == test_endpoint


def test_api_login_syncs_hash_from_final_health_check(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The 2nd health check is the final source of truth for user identity.
    The local user hash must be updated to match it, even if the 1st health
    check or the auth flow set a different hash."""
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    first_user_hash = 'aaaaaaaa'
    final_user_hash = 'bbbbbbbb'

    test_endpoint = "http://test.skypilot.co"

    first_user = {'id': first_user_hash, 'name': 'user_a'}
    final_user = {'id': final_user_hash, 'name': 'user_b'}

    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        # 1st health check: HEALTHY with user_a (e.g. from cookie/sa).
        first_return_value = (server_common.ApiServerStatus.HEALTHY,
                              server_common.ApiServerInfo(
                                  status=server_common.ApiServerStatus.HEALTHY,
                                  basic_auth_enabled=False,
                                  user=first_user))

        # 2nd health check: HEALTHY with user_b (e.g. actual SSO identity
        # after sa token was cleared from config).
        second_return_value = (server_common.ApiServerStatus.HEALTHY,
                               server_common.ApiServerInfo(
                                   status=server_common.ApiServerStatus.HEALTHY,
                                   basic_auth_enabled=False,
                                   user=final_user))

        mock_check.side_effect = [first_return_value, second_return_value]
        client_sdk.api_login(test_endpoint)

    # The local hash must match the 2nd health check's user, not the 1st.
    assert user_hash_path.exists()
    assert user_hash_path.read_text() == final_user_hash


def test_api_login_user_hash_fail(monkeypatch: pytest.MonkeyPatch,
                                  tmp_path: Path):
    # Test that we don't set the user hash if we fail to login.
    config_path = tmp_path / "config.yaml"
    user_hash_path = tmp_path / "user_hash"
    monkeypatch.setattr('sky.utils.common_utils.USER_HASH_FILE',
                        str(user_hash_path))
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    user_hash = '11111111'

    user = mock.MagicMock()
    user.get.return_value = user_hash

    test_endpoint = "http://test.skypilot.co"

    # Make sure if we fail in the try block, the user hash is not written to
    # the file.
    # Make get_dashboard_url raise an exception.
    monkeypatch.setattr('sky.server.common.get_dashboard_url',
                        lambda *args, **kwargs: None)
    with pytest.raises(Exception):
        client_sdk.api_login(test_endpoint, service_account_token="sky_test")

    # Verify the user hash is not written to the file.
    assert not user_hash_path.exists()


class MockRetryContext:
    """Mock retry context for testing resumable functionality."""

    def __init__(self, line_processed: int = 0, progress_count: int = 0):
        self.line_processed = line_processed
        self.progress_count = progress_count


def test_stream_response_non_resumable():
    """Test stream_response when resumable=False."""
    test_lines = ['Line 1\n', 'Line 2\n', 'Line 3\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.client.sdk.get') as mock_get:
            mock_get.return_value = "test_result"

            result = client_sdk.stream_response(request_id="test_request_id",
                                                response=mock_response,
                                                output_stream=output_stream,
                                                resumable=False)

            # Verify all lines were written to output stream
            assert output_stream.getvalue() == "Line 1\nLine 2\nLine 3\n"
            # Verify get was called with the request_id
            mock_get.assert_called_once_with("test_request_id")
            # Verify the result from get is returned
            assert result == "test_result"


def test_stream_response_resumable_no_previous_lines():
    """Test stream_response when resumable=True with no previously
    processed lines."""
    test_lines = ['Line 1\n', 'Line 2\n', 'Line 3\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()
    retry_context = MockRetryContext(line_processed=0)

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.server.rest.get_retry_context') as mock_get_ctx:
            mock_get_ctx.return_value = retry_context
            with mock.patch('sky.client.sdk.get') as mock_get:
                mock_get.return_value = "test_result"

                result = client_sdk.stream_response(
                    request_id="test_request_id",
                    response=mock_response,
                    output_stream=output_stream,
                    resumable=True)

                # Verify all lines were written to output stream
                assert output_stream.getvalue() == "Line 1\nLine 2\nLine 3\n"
                # Verify retry context was updated
                assert retry_context.line_processed == 3
                # Verify get was called with the request_id
                mock_get.assert_called_once_with("test_request_id")
                # Verify the result from get is returned
                assert result == "test_result"


def test_stream_response_resumable_with_previous_lines():
    """Test stream_response when resumable=True with some previously
    processed lines."""
    test_lines = ['Line 1\n', 'Line 2\n', 'Line 3\n', 'Line 4\n', 'Line 5\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()
    # Simulate that first 2 lines were already processed
    retry_context = MockRetryContext(line_processed=2)

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.server.rest.get_retry_context') as mock_get_ctx:
            mock_get_ctx.return_value = retry_context
            with mock.patch('sky.client.sdk.get') as mock_get:
                mock_get.return_value = "test_result"

                result = client_sdk.stream_response(
                    request_id="test_request_id",
                    response=mock_response,
                    output_stream=output_stream,
                    resumable=True)

                # Verify only new lines (3, 4, 5) were written to output
                assert output_stream.getvalue() == "Line 3\nLine 4\nLine 5\n"
                # Verify retry context was updated to total processed lines
                assert retry_context.line_processed == 5
                # Verify get was called with the request_id
                mock_get.assert_called_once_with("test_request_id")
                # Verify the result from get is returned
                assert result == "test_result"


def test_stream_response_resumable_all_lines_processed():
    """Test stream_response when resumable=True and all lines were already
    processed."""
    test_lines = ['Line 1\n', 'Line 2\n', 'Line 3\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()
    # Simulate that all lines were already processed
    retry_context = MockRetryContext(line_processed=3)

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.server.rest.get_retry_context') as mock_get_ctx:
            mock_get_ctx.return_value = retry_context
            with mock.patch('sky.client.sdk.get') as mock_get:
                mock_get.return_value = "test_result"

                result = client_sdk.stream_response(
                    request_id="test_request_id",
                    response=mock_response,
                    output_stream=output_stream,
                    resumable=True)

                # Verify no lines were written to output (all already processed)
                assert output_stream.getvalue() == ""
                # Verify retry context remains unchanged
                assert retry_context.line_processed == 3
                # Verify get was called with the request_id
                mock_get.assert_called_once_with("test_request_id")
                # Verify the result from get is returned
                assert result == "test_result"


def test_stream_response_with_none_lines():
    """Test stream_response handles None lines correctly."""
    test_lines = ['Line 1\n', None, 'Line 2\n', None, 'Line 3\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.client.sdk.get') as mock_get:
            mock_get.return_value = "test_result"

            result = client_sdk.stream_response(request_id="test_request_id",
                                                response=mock_response,
                                                output_stream=output_stream,
                                                resumable=False)

            # Verify only non-None lines were written to output stream
            assert output_stream.getvalue() == "Line 1\nLine 2\nLine 3\n"
            # Verify get was called with the request_id
            mock_get.assert_called_once_with("test_request_id")
            # Verify the result from get is returned
            assert result == "test_result"


def test_stream_response_resumable_with_none_lines():
    """Test stream_response handles None lines correctly in resumable mode."""
    test_lines = ['Line 1\n', None, 'Line 2\n', None, 'Line 3\n', 'Line 4\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()
    # Simulate that first 2 non-None lines were already processed
    retry_context = MockRetryContext(line_processed=2)

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.server.rest.get_retry_context') as mock_get_ctx:
            mock_get_ctx.return_value = retry_context
            with mock.patch('sky.client.sdk.get') as mock_get:
                mock_get.return_value = "test_result"

                result = client_sdk.stream_response(
                    request_id="test_request_id",
                    response=mock_response,
                    output_stream=output_stream,
                    resumable=True)

                # Verify only new non-None lines (3, 4) were written to output
                assert output_stream.getvalue() == "Line 3\nLine 4\n"
                # Verify retry context was updated (4 non-None lines total)
                assert retry_context.line_processed == 4
                # Verify get was called with the request_id
                mock_get.assert_called_once_with("test_request_id")
                # Verify the result from get is returned
                assert result == "test_result"


def test_stream_response_non_resumable_reports_progress():
    """Non-resumable streams should still bump retry_context.progress_count
    so retry_transient_errors can detect forward progress and reset its
    consecutive-failure counter. Regression test for the
    test_cli_auto_retry failure on `sky jobs logs --controller --tail 1000`,
    where retries exhausted because the decorator was inspecting
    line_processed (only updated by resumable streams) instead of
    progress_count.
    """
    test_lines = ['Line 1\n', 'Line 2\n', 'Line 3\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()
    retry_context = MockRetryContext(line_processed=0, progress_count=0)

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.server.rest.get_retry_context') as mock_get_ctx:
            mock_get_ctx.return_value = retry_context
            with mock.patch('sky.client.sdk.get') as mock_get:
                mock_get.return_value = "test_result"

                client_sdk.stream_response(request_id="test_request_id",
                                           response=mock_response,
                                           output_stream=output_stream,
                                           resumable=False)

                # All lines should have been printed, since this is a
                # non-resumable stream (no skipping based on line_processed).
                assert output_stream.getvalue() == "Line 1\nLine 2\nLine 3\n"
                # progress_count should have been incremented per line so
                # the retry decorator sees forward progress.
                assert retry_context.progress_count == 3
                # line_processed must remain 0 for non-resumable streams;
                # it is reserved for resume bookkeeping.
                assert retry_context.line_processed == 0


def test_stream_response_resumable_retry_skips_replayed_lines():
    """Integration test: ``retry_transient_errors`` + resumable
    ``stream_response`` together must (1) not double-print lines that the
    server replays after a mid-stream disconnect, and (2) advance both
    ``progress_count`` and ``line_processed`` correctly across attempts.

    Scenario: first attempt prints lines 1-2 then the connection breaks
    with ``ChunkedEncodingError``. The decorator retries; on the second
    attempt the server replays lines 1-5 from the start. Lines 1-2 must be
    skipped via ``line_processed``, lines 3-5 must be printed exactly once.
    """
    output_stream = io.StringIO()
    decode_call_count = 0

    def decode_side_effect(_response, relay_rich_status=False):
        nonlocal decode_call_count
        decode_call_count += 1
        if decode_call_count == 1:
            # First attempt: emit 2 lines, then disconnect.
            yield 'Line 1\n'
            yield 'Line 2\n'
            raise requests.exceptions.ChunkedEncodingError('disconnected')
        # Retry attempt: server replays from line 1, emits all 5 lines.
        yield 'Line 1\n'
        yield 'Line 2\n'
        yield 'Line 3\n'
        yield 'Line 4\n'
        yield 'Line 5\n'

    @server_rest.retry_transient_errors(max_retries=3, initial_backoff=0.01)
    def streaming_call():
        mock_response = mock.MagicMock()
        return client_sdk.stream_response(request_id='test_request_id',
                                          response=mock_response,
                                          output_stream=output_stream,
                                          resumable=True)

    captured_context = {}

    def get_ctx_passthrough():
        ctx = server_rest._RETRY_CONTEXT.get()
        if ctx is not None:
            captured_context['ctx'] = ctx
        return ctx

    with mock.patch('sky.utils.rich_utils.decode_rich_status',
                    side_effect=decode_side_effect):
        with mock.patch('sky.client.sdk.get') as mock_get:
            mock_get.return_value = 'final_result'
            with mock.patch('sky.client.sdk.rest.get_retry_context',
                            side_effect=get_ctx_passthrough):
                with mock.patch('time.sleep'):
                    result = streaming_call()

    # Each line printed exactly once despite the replay.
    assert output_stream.getvalue() == (
        'Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n')
    # Two attempts total: one failure + one success.
    assert decode_call_count == 2
    # Final result is forwarded from get(request_id).
    assert result == 'final_result'
    # line_processed tracks distinct lines (high-water mark for resumable
    # skip-ahead). progress_count tracks total wire-level messages received
    # across all attempts: 2 from the first attempt + 5 from the retry = 7.
    ctx = captured_context['ctx']
    assert ctx.line_processed == 5
    assert ctx.progress_count == 7


def test_stream_response_no_request_id():
    """Test stream_response when request_id is None."""
    test_lines = ['Line 1\n', 'Line 2\n']
    mock_response = mock.MagicMock()
    output_stream = io.StringIO()

    with mock.patch('sky.utils.rich_utils.decode_rich_status') as mock_decode:
        mock_decode.return_value = test_lines
        with mock.patch('sky.client.sdk.get') as mock_get:

            result = client_sdk.stream_response(request_id=None,
                                                response=mock_response,
                                                output_stream=output_stream,
                                                resumable=False)

            # Verify lines were written to output stream
            assert output_stream.getvalue() == "Line 1\nLine 2\n"
            # Verify get was NOT called when request_id is None
            mock_get.assert_not_called()
            # Verify None is returned when request_id is None
            assert result is None


def test_reload_config():
    with mock.patch('sky.skypilot_config.safe_reload_config') as mock_reload:
        client_sdk.reload_config()
        mock_reload.assert_called_once()


def test_get_request_id():
    """Test that get_request_id returns the request id from the correct
    header."""
    mock_response = mock.MagicMock()
    mock_response.headers = {
        'X-Skypilot-Request-ID': 'test_request_id',
    }
    mock_response.status_code = 200
    mock_response.reason = 'OK'
    request_id = server_common.get_request_id(mock_response)
    assert request_id == 'test_request_id'


def _interrupted_entrypoint():
    """Module-level entrypoint so Request.encode() can pickle it."""


def test_get_interrupted_request_raises_request_interrupted_error():
    """sdk.get() rebuilds the server's 500 payload into
    RequestInterruptedError — not a generic RuntimeError — so callers (and
    the retry decorator) can react to the interruption specifically."""
    from sky import exceptions
    from sky.server.requests import payloads as requests_payloads
    from sky.server.requests import requests as requests_lib

    request = requests_lib.Request(request_id='interrupted-req',
                                   name='sky.launch',
                                   entrypoint=_interrupted_entrypoint,
                                   request_body=requests_payloads.RequestBody(),
                                   status=requests_lib.RequestStatus.CANCELLED,
                                   created_at=0.0,
                                   user_id='user-123',
                                   should_retry=True)
    request.set_error(
        exceptions.RequestInterruptedError(
            'Request was interrupted by an API server restart.'))

    with mock.patch('sky.server.common.make_authenticated_request'
                   ) as mock_make_request:
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            'detail': request.encode().model_dump()
        }
        mock_make_request.return_value = mock_response

        with pytest.raises(exceptions.RequestInterruptedError):
            client_sdk.get('interrupted-req')
