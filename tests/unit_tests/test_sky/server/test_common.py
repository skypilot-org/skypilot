"""Unit tests for the SkyPilot API server common module."""
from http.cookiejar import Cookie
from http.cookiejar import MozillaCookieJar
import pathlib
import sys
import tempfile
import time
from unittest import mock

import pytest
import requests

import sky
from sky import exceptions
from sky import skypilot_config
from sky.server import common
from sky.server import constants as server_constants
from sky.server.common import ApiServerInfo
from sky.server.common import ApiServerStatus


def _create_test_cookie(name: str = 'test-cookie', value: str = 'test-value'):
    """Create a test cookie."""

    server_domain = common.get_server_url().split('://')[1].split(':')[0]

    # write a cookie to the file
    test_cookie = Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=server_domain,
        domain_specified=True,
        domain_initial_dot=False,
        path='/',
        path_specified=True,
        secure=False,
        expires=time.time() + 1000,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )

    return test_cookie


@mock.patch('sky.server.common.get_api_server_status')
def test_healthy_server(mock_get_status):
    """Test when server is healthy."""
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.HEALTHY,
        api_version=server_constants.API_VERSION,
        version=sky.__version__,
        commit=sky.__commit__)

    # Should not raise any exception
    common.check_server_healthy()


@mock.patch('sky.server.common.get_api_server_status')
def test_unhealthy_server(mock_get_status):
    """Test when server is unhealthy."""
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.UNHEALTHY)

    with pytest.raises(exceptions.ApiServerConnectionError):
        common.check_server_healthy()


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_local_client_server_mismatch(mock_is_local, mock_get_status):
    """Test when local client and server version mismatch."""
    mock_is_local.return_value = True

    def expect_error_with_hints():
        with pytest.raises(RuntimeError) as exc_info:
            common.check_server_healthy()

        # Correct error message
        assert 'The local SkyPilot API server is not compatible with the client' in str(
            exc_info.value)
        # Should hint user to restart local API server
        assert 'sky api stop; sky api start' in str(exc_info.value)

    # Test when client is newer than server
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='0',  # Always older than client version
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()

    # Test when client is older than server
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version=str(sys.maxsize),  # Always newer than client version
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()

    # Test when server version format is unknown, i.e.
    # a newer version with unknown format
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='unknown',
        version=sky.__version__,
        commit=sky.__commit__)
    expect_error_with_hints()


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_remote_server_older(mock_is_local, mock_get_status):
    """Test when remote server version is older than client."""
    mock_is_local.return_value = False
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version='0',
        version='1.0.0-dev20250415',
        commit='abc123',
        error='SkyPilot API server is too old')

    with pytest.raises(RuntimeError) as exc_info:
        common.check_server_healthy()

    # Correct error message
    assert 'SkyPilot API server is too old' in str(exc_info.value)


@mock.patch('sky.server.common.get_api_server_status')
@mock.patch('sky.server.common.is_api_server_local')
def test_client_older(mock_is_local, mock_get_status):
    """Test when client version is older than server."""
    mock_is_local.return_value = False
    mock_get_status.return_value = ApiServerInfo(
        status=ApiServerStatus.VERSION_MISMATCH,
        api_version=str(sys.maxsize),
        version='1.0.0-dev20250415',
        commit='abc123',
        error='Your SkyPilot client is too old')

    with pytest.raises(RuntimeError) as exc_info:
        common.check_server_healthy()

    # Correct error message
    assert 'Your SkyPilot client is too old' in str(exc_info.value)


@pytest.fixture
def mock_all_dependencies():
    """Mock all dependencies used in reload_for_new_request."""
    with mock.patch('sky.utils.common_utils.set_request_context') as mock_status, \
         mock.patch('sky.usage.usage_lib.messages.reset') as mock_reset, \
         mock.patch('sky.sky_logging.reload_logger') as mock_logger:
        yield {
            'set_status': mock_status,
            'reset_messages': mock_reset,
            'reload_logger': mock_logger
        }


def test_reload_config_for_new_request(mock_all_dependencies, tmp_path,
                                       monkeypatch):
    """Test basic functionality with all parameters provided."""
    config_path = tmp_path / 'config.yaml'
    config_path.write_text('''
allowed_clouds:
  - aws
''')

    # Set env var to point to the temp config
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       str(config_path))
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
        user=mock.Mock(id='test_user'),
        request_id='dummy-request-id',
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['aws']
    config_path.write_text('''
allowed_clouds:
  - gcp
''')
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
        user=mock.Mock(id='test_user'),
        request_id='dummy-request-id',
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['gcp']


def test_get_dashboard_url():
    """Test get_dashboard_url with default URL."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='http://127.0.0.1:46580'
                                   ) == 'http://127.0.0.1:46580/dashboard'
    """Test get_dashboard_url with basic URL."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='http://example.com:8080'
                                   ) == 'http://example.com:8080/dashboard'
    """Test get_dashboard_url with URL containing path."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='http://example.com:8080/api/'
                                   ) == 'http://example.com:8080/api/dashboard'
    """Test get_dashboard_url with URL containing credentials."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='https://user:pass@example.com:8080'
    ) == 'https://example.com:8080/dashboard'
    """Test get_dashboard_url with URL containing username."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='https://user@example.com:8080'
                                   ) == 'https://example.com:8080/dashboard'
    """Test get_dashboard_url with host parameter."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='http://custom-host:8080'
                                   ) == 'http://custom-host:8080/dashboard'
    """Test get_dashboard_url with complex path."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='https://user:pass@example.com:8080/api/v1'
    ) == 'https://example.com:8080/api/v1/dashboard'
    """Test get_dashboard_url without port."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='http://example.com') == 'http://example.com/dashboard'


def test_cookies_get_no_file(monkeypatch):
    """Test getting cookies from local file."""

    # make a up a temporary cookie file
    temp_cookie_dir = tempfile.TemporaryDirectory(prefix='sky_cookies')
    temp_cookie_path = pathlib.Path(temp_cookie_dir.name) / 'cookies.txt'

    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: temp_cookie_path)

    test_cookie_jar = common.get_api_cookie_jar()

    assert not temp_cookie_path.exists()
    assert isinstance(test_cookie_jar, requests.cookies.RequestsCookieJar)


def test_cookies_get_with_file(monkeypatch):
    """Test getting cookies from local file."""

    # make a up a temporary cookie file
    temp_cookie_dir = tempfile.TemporaryDirectory(prefix='sky_cookies')
    temp_cookie_path = pathlib.Path(temp_cookie_dir.name) / 'cookies.txt'

    test_cookie = _create_test_cookie()
    cookie_jar = MozillaCookieJar(temp_cookie_path)
    cookie_jar.set_cookie(test_cookie)
    cookie_jar.save()

    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: temp_cookie_path)

    test_cookie_jar = common.get_api_cookie_jar()

    assert isinstance(test_cookie_jar, requests.cookies.RequestsCookieJar)
    assert len(test_cookie_jar) == 1
    assert test_cookie_jar['test-cookie'] == test_cookie.value

    temp_cookie_dir.cleanup()


def test_cookies_set_with_no_file(monkeypatch):
    """Test setting cookies to local file.
    No file exists, so a new file is created.
    """

    # make a up a temporary cookie file
    temp_cookie_dir = tempfile.TemporaryDirectory(prefix='sky_cookies')
    temp_cookie_path = pathlib.Path(temp_cookie_dir.name) / 'cookies.txt'

    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: temp_cookie_path)
    cookie = _create_test_cookie(name='test-cookie-2', value='test-value-2')
    cookie_jar = requests.cookies.RequestsCookieJar()
    cookie_jar.set_cookie(cookie)
    common.set_api_cookie_jar(cookie_jar, create_if_not_exists=True)

    assert temp_cookie_path.exists()

    temp_cookie_dir.cleanup()


def test_cookies_set_empty(monkeypatch):
    """Test setting an empty cookie should be a no-op."""
    temp_cookie_dir = tempfile.TemporaryDirectory(prefix='sky_cookies')
    temp_cookie_path = pathlib.Path(temp_cookie_dir.name) / 'cookies.txt'

    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: temp_cookie_path)
    common.set_api_cookie_jar(requests.cookies.RequestsCookieJar(),
                              create_if_not_exists=True)

    assert not temp_cookie_path.exists()


def test_cookies_set_with_file(monkeypatch):
    """Test setting cookies to local file.
    A file exists, so the cookies are added to the file.
    """

    # make a up a temporary cookie file
    temp_cookie_dir = tempfile.TemporaryDirectory(prefix='sky_cookies')
    temp_cookie_path = pathlib.Path(temp_cookie_dir.name) / 'cookies.txt'

    monkeypatch.setattr('sky.server.common.get_api_cookie_jar_path',
                        lambda: temp_cookie_path)

    # write a cookie to the file
    cookie = _create_test_cookie()
    cookie_jar = MozillaCookieJar(temp_cookie_path)
    cookie_jar.set_cookie(cookie)
    cookie_jar.save()

    # create a new cookie jar and add a new cookie
    expected_cookie = _create_test_cookie(name='test-cookie-2',
                                          value='test-value-2')
    expected_cookie_jar = requests.cookies.RequestsCookieJar()
    expected_cookie_jar.set_cookie(expected_cookie)

    common.set_api_cookie_jar(expected_cookie_jar, create_if_not_exists=False)

    assert temp_cookie_path.exists()

    # read the cookie file
    _found_cookie_jar = MozillaCookieJar(temp_cookie_path)
    _found_cookie_jar.load()
    # convert to RequestsCookieJar to use the RequestsCookieJar API for reading cookies
    found_cookie_jar = requests.cookies.RequestsCookieJar()
    found_cookie_jar.update(_found_cookie_jar)

    assert len(found_cookie_jar) == 2
    assert found_cookie_jar['test-cookie'] == cookie.value
    assert found_cookie_jar['test-cookie-2'] == expected_cookie.value

    temp_cookie_dir.cleanup()


def test__create_token_persists_and_returns():
    """_create_token should call TokenService and persist metadata."""
    from sky.server import common as common_mod

    initial_token = {
        'token_id': 'tok_1',
        'token_hash': 'hash_abc',
        'token': 'sky_testtoken',
        'expires_at': 1234567890,
    }

    with mock.patch('sky.server.common.token_lib.TokenService') as MockSvc, \
            mock.patch('sky.server.common.global_user_state') as mock_state:
        MockSvc.return_value.create_token.return_value = initial_token

        result = common_mod._create_token(
            creator_user_id='creator_u',
            service_account_user_id='sa_u',
            token_name='my-sa',
            expires_in_days=30,
        )

        # Returns the token dict
        assert result == initial_token
        # TokenService.create_token called with args
        MockSvc.return_value.create_token.assert_called_once_with(
            creator_user_id='creator_u',
            service_account_user_id='sa_u',
            token_name='my-sa',
            expires_in_days=30,
        )
        # Metadata persisted to global_user_state
        mock_state.add_service_account_token.assert_called_once_with(
            token_id='tok_1',
            token_name='my-sa',
            token_hash='hash_abc',
            creator_user_hash='creator_u',
            service_account_user_id='sa_u',
            expires_at=1234567890,
        )


def test__create_user_and_token_creates_user_and_role():
    """_create_user_and_token should create token, user, and grant ADMIN."""
    from sky.server import common as common_mod
    from sky.skylet import constants

    token_dict = {
        'token_id': 'tok_x',
        'token_hash': 'hash_x',
        'token': 'sky_x',
        'expires_at': 2222,
    }

    with mock.patch('sky.server.common._create_token') as mock_create_token, \
            mock.patch('sky.server.common.global_user_state') as mock_state, \
            mock.patch('sky.server.common.permission_service') as mock_perm:
        mock_create_token.return_value = token_dict

        result = common_mod._create_user_and_token(
            user_id='sa-user-1',
            user_name='sa-name',
            creator_user_id=constants.SKYPILOT_SYSTEM_USER_ID,
            expires_in_days=constants.SKYPILOT_SYSTEM_SA_TOKEN_DURATION_DAYS,
        )

        assert result == token_dict
        mock_create_token.assert_called_once_with(
            creator_user_id=constants.SKYPILOT_SYSTEM_USER_ID,
            service_account_user_id='sa-user-1',
            token_name='sa-name',
            expires_in_days=constants.SKYPILOT_SYSTEM_SA_TOKEN_DURATION_DAYS,
        )
        # User should be created and stored; verify fields
        assert mock_state.add_or_update_user.call_count == 1
        args, kwargs = mock_state.add_or_update_user.call_args
        created_user = args[0]
        assert created_user.id == 'sa-user-1'
        assert created_user.name == 'sa-name'
        assert kwargs.get('allow_duplicate_name') is False
        # Role should be ADMIN
        mock_perm.update_role.assert_called_once()
        role_args, _ = mock_perm.update_role.call_args
        assert role_args[0] == 'sa-user-1'
        # Role name value is checked via value string 'admin'
        assert isinstance(role_args[1], str) and role_args[1].lower() == 'admin'


def test__initialize_token_for_existing_users_oauth2_enabled():
    """When OAuth2 proxy enabled, do nothing."""
    with mock.patch('sky.server.common.auth_utils.is_oauth2_proxy_enabled',
                    return_value=True), \
            mock.patch('sky.server.common.global_user_state') as mock_state, \
            mock.patch('sky.server.common._create_token') as mock_create_token:
        common._initialize_token_for_existing_users()
        mock_state.get_all_users.assert_not_called()
        mock_create_token.assert_not_called()


def test__initialize_token_for_existing_users_creates_missing_tokens():
    """Create tokens only for users without any token."""
    user_with_token = mock.Mock()
    user_with_token.id = 'u1'
    user_with_token.name = 'name1'
    user_without_token = mock.Mock()
    user_without_token.id = 'u2'
    user_without_token.name = 'name2'

    def get_tokens_side_effect(user_id):
        return [{'token_id': 'existing'}] if user_id == 'u1' else []

    with mock.patch('sky.server.common.auth_utils.is_oauth2_proxy_enabled',
                    return_value=False), \
            mock.patch('sky.server.common.global_user_state.get_all_users',
                       return_value=[user_with_token, user_without_token]), \
            mock.patch('sky.server.common.global_user_state.get_tokens_by_user_id',
                       side_effect=get_tokens_side_effect) as mock_get_tokens, \
            mock.patch('sky.server.common._create_token') as mock_create_token:
        common._initialize_token_for_existing_users()

        # get_tokens called for both users
        assert mock_get_tokens.call_count == 2
        # create_token only called for the user without tokens
        args, kwargs = mock_create_token.call_args
        assert kwargs['service_account_user_id'] == 'u2'
        assert kwargs['token_name'] == 'name2'


def test__initialize_tokens_disabled_noop(tmp_path):
    """If service account tokens disabled, function should return early."""
    with mock.patch('sky.server.common.common_utils.is_service_account_token_enabled',
                    return_value=False), \
            mock.patch('sky.server.common._initialize_token_for_existing_users') as mock_init_existing:
        common._initialize_tokens()
        mock_init_existing.assert_not_called()


def test__initialize_tokens_existing_file_skip(tmp_path):
    """If token file exists and non-empty, skip initialization."""
    token_path = tmp_path / 'sa' / 'token.txt'
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text('already_there')

    with mock.patch('sky.server.common.common_utils.is_service_account_token_enabled',
                    return_value=True), \
            mock.patch('sky.server.common._initialize_token_for_existing_users') as mock_init_existing, \
            mock.patch('sky.server.common.constants') as mock_constants:
        mock_constants.SKYPILOT_SYSTEM_SA_TOKEN_PATH = str(token_path)
        common._initialize_tokens()
        mock_init_existing.assert_not_called()
        assert token_path.read_text() == 'already_there'


def test__initialize_tokens_uses_existing_system_token(tmp_path):
    """If system SA token exists in DB, use it and write to file."""
    token_path = tmp_path / 'sa' / 'token.txt'
    existing_token = {
        'token': 'sky_existing',
        'token_id': 'id1',
        'expires_at': 1
    }

    with mock.patch('sky.server.common.common_utils.is_service_account_token_enabled',
                    return_value=True), \
            mock.patch('sky.server.common._initialize_token_for_existing_users') as mock_init_existing, \
            mock.patch('sky.server.common.global_user_state.get_tokens_by_user_id',
                       return_value=[existing_token]), \
            mock.patch('sky.server.common.constants') as mock_constants:
        mock_constants.SKYPILOT_SYSTEM_SA_TOKEN_PATH = str(token_path)
        # IDs used inside implementation
        mock_constants.SKYPILOT_SYSTEM_SA_ID = 'sa-system'
        common._initialize_tokens()

        mock_init_existing.assert_called_once()
        assert token_path.exists()
        assert token_path.read_text() == 'sky_existing'


def test__initialize_tokens_creates_new_system_token(tmp_path):
    """If no system SA token exists, create a new one and write to file."""
    token_path = tmp_path / 'sa' / 'token.txt'
    new_token = {'token': 'sky_new', 'token_id': 'id2', 'expires_at': 2}

    with mock.patch('sky.server.common.common_utils.is_service_account_token_enabled',
                    return_value=True), \
            mock.patch('sky.server.common._initialize_token_for_existing_users') as mock_init_existing, \
            mock.patch('sky.server.common.global_user_state.get_tokens_by_user_id',
                       return_value=[]), \
            mock.patch('sky.server.common._create_user_and_token',
                       return_value=new_token) as mock_create_user_token, \
            mock.patch('sky.server.common.constants') as mock_constants:
        mock_constants.SKYPILOT_SYSTEM_SA_TOKEN_PATH = str(token_path)
        mock_constants.SKYPILOT_SYSTEM_USER_ID = 'sys'
        mock_constants.SKYPILOT_SYSTEM_SA_ID = 'sa-system'
        mock_constants.SKYPILOT_SYSTEM_SA_TOKEN_DURATION_DAYS = 30

        common._initialize_tokens()

        mock_init_existing.assert_called_once()
        mock_create_user_token.assert_called_once_with(
            creator_user_id='sys',
            user_id='sa-system',
            user_name='sa-system',
            expires_in_days=30,
        )
        assert token_path.exists()
        assert token_path.read_text() == 'sky_new'
