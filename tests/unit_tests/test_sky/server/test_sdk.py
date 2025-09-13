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

from sky import skypilot_config
from sky.client import sdk as client_sdk
from sky.server import common as server_common
from sky.server.constants import API_COOKIE_FILE_ENV_VAR
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

    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
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

    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
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

    def __init__(self, line_processed: int = 0):
        self.line_processed = line_processed


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
        'X-Request-ID': 'test_request_id_2',
    }
    mock_response.status_code = 200
    mock_response.reason = 'OK'
    request_id = server_common.get_request_id(mock_response)
    assert request_id == 'test_request_id'
