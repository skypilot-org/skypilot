# Tests for the sky/client/sdk.py file
from http.cookiejar import Cookie
from http.cookiejar import MozillaCookieJar
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
    with mock.patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "healthy",
            "api_version": "1",
            "commit": "abc1234567890",
            "version": "1.0.0",
        }
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.cookies = requests.cookies.RequestsCookieJar()
        with mock.patch('sky.server.common.check_server_healthy_or_start_fn'
                       ) as mock_server_healthy:
            mock_server_healthy.return_value = None
            response = client_sdk.api_info()
            assert response is not None
            assert response["status"] == "healthy"
            assert response["api_version"] == "1"
            assert response["commit"] is not None
            assert response["version"] is not None
            assert mock_get.call_count == 1
            assert mock_get.call_args[0][0].endswith("/api/health")
            assert mock_get.call_args[1]["cookies"] is not None
            assert isinstance(mock_get.call_args[1]["cookies"],
                              requests.cookies.RequestsCookieJar)


def test_api_info_with_cookie_file(set_api_cookie_jar):
    with mock.patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "healthy",
            "api_version": "1",
            "commit": "abc1234567890",
            "version": "1.0.0",
        }
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.cookies = requests.cookies.RequestsCookieJar()
        with mock.patch('sky.server.common.check_server_healthy_or_start_fn'
                       ) as mock_server_healthy:
            mock_server_healthy.return_value = None
            response = client_sdk.api_info()
            assert response is not None
            assert response["status"] == "healthy"
            assert response["api_version"] == "1"
            assert response["commit"] is not None
            assert response["version"] is not None
            assert mock_get.call_count == 1
            assert mock_get.call_args[0][0].endswith("/api/health")
            assert mock_get.call_args[1]["cookies"] is not None
            assert isinstance(mock_get.call_args[1]["cookies"],
                              requests.cookies.RequestsCookieJar)
            assert mock_get.call_args[1]["cookies"].get(
                "user_name", domain="api.skypilot.co") == "sky-user"


def test_api_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create a temporary config file
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr('sky.skypilot_config.get_user_config_path',
                        lambda: str(config_path))

    test_endpoint = "http://test.skypilot.co"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = None
        client_sdk.api_login(test_endpoint)

        # Verify the endpoint is written to config file
        assert config_path.exists()
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_endpoint
        mock_check.assert_called_once_with(test_endpoint)

    # Test with existing config
    test_endpoint_2 = "http://test2.skypilot.co"
    with mock.patch('sky.server.common.check_server_healthy') as mock_check:
        mock_check.return_value = None
        client_sdk.api_login(test_endpoint_2)

        # Verify the endpoint is updated in config file
        config = skypilot_config.get_user_config()
        assert config["api_server"]["endpoint"] == test_endpoint_2
        mock_check.assert_called_once_with(test_endpoint_2)

    # Test with invalid endpoint
    with pytest.raises(click.BadParameter):
        client_sdk.api_login("invalid_endpoint")
