"""Unit tests for the SkyPilot API server common module."""
from http.cookiejar import Cookie
from http.cookiejar import MozillaCookieJar
import os
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


@mock.patch('sky.server.common._start_api_server')
@mock.patch('sky.server.common.set_api_cookie_jar')
@mock.patch('sky.server.common.versions.check_compatibility_at_client')
@mock.patch('sky.server.common.make_authenticated_request')
@mock.patch('sky.server.common.filelock.FileLock')
@mock.patch('sky.server.common.is_api_server_local', return_value=True)
@mock.patch('sky.server.common.get_server_url',
            return_value='http://127.0.0.1:1111')
def test_check_server_healthy_or_start_rechecks_status(
        unused_mock_server_url, unused_mock_is_local, mock_filelock,
        mock_make_request, mock_check_compat, unused_set_cookie,
        mock_start_server):
    """Re-check should observe the fresh server status before starting.

    The test keeps the real `get_api_server_status` cache in place to ensure
    the cache is cleared before re-fetching the server status.
    """
    healthy_response = mock.Mock()
    healthy_response.status_code = 200
    healthy_response.headers = {}
    healthy_response.history = []
    healthy_response.cookies = requests.cookies.RequestsCookieJar()
    healthy_response.json.return_value = {
        'status': ApiServerStatus.HEALTHY.value,
        'api_version': server_constants.API_VERSION,
        'version': sky.__version__,
        'version_on_disk': sky.__version__,
        'commit': sky.__commit__,
        'user': {},
        'basic_auth_enabled': False,
    }

    mock_make_request.side_effect = [
        requests.exceptions.ConnectionError(), healthy_response
    ]
    mock_check_compat.return_value = mock.Mock(error=None)
    mock_filelock.return_value.__enter__.return_value = None
    mock_filelock.return_value.__exit__.return_value = None

    try:
        with mock.patch.object(common.get_api_server_status_response,
                               'cache_clear',
                               wraps=common.get_api_server_status_response.
                               cache_clear) as mock_cache_clear:
            common.check_server_healthy_or_start_fn()

        assert mock_cache_clear.call_count == 1
        assert mock_make_request.call_count == 2
        mock_start_server.assert_not_called()
        common.get_api_server_status_response.cache_clear()
    finally:
        # check_server_healthy() calls versions.set_remote_api_version() and
        # set_remote_version() based on the value returned from
        # check_compatibility_at_client. The mock above returns a bare
        # mock.Mock, so the call propagates a Mock object into the
        # _remote_api_version / _remote_version ContextVars, which then
        # leaks into any later test running on the same xdist worker —
        # e.g. test_sdk.py tests that call api_login() and compare
        # `remote_api_version >= 30`. Reset the ContextVars to their
        # defaults to keep tests isolated.
        from sky.server import versions
        versions.set_remote_api_version(None)
        versions.set_remote_version('unknown')


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
    ) == 'https://user:pass@example.com:8080/dashboard'
    """Test get_dashboard_url with URL containing username."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='https://user@example.com:8080'
    ) == 'https://user@example.com:8080/dashboard'
    """Test get_dashboard_url with host parameter."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(server_url='http://custom-host:8080'
                                   ) == 'http://custom-host:8080/dashboard'
    """Test get_dashboard_url with complex path."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='https://user:pass@example.com:8080/api/v1'
    ) == 'https://user:pass@example.com:8080/api/v1/dashboard'
    """Test get_dashboard_url without port."""
    common.get_server_url.cache_clear()
    assert common.get_dashboard_url(
        server_url='http://example.com') == 'http://example.com/dashboard'


def _isolate_server_url(monkeypatch):
    """Neutralize env/config endpoint overrides so the host arg is honored."""
    monkeypatch.delenv(common.constants.SKY_API_SERVER_URL_ENV_VAR,
                       raising=False)
    # get_server_url() falls back to the constructed endpoint when the config
    # has no api_server.endpoint set; force that fallback path.
    monkeypatch.setattr(
        common.skypilot_config,
        'get_nested',
        lambda keys, default_value=None, *args, **kwargs: default_value)
    common.get_server_url.cache_clear()


def test_host_to_url_host_brackets_ipv6():
    """IPv6 literals are bracketed for URLs; IPv4/hostnames are unchanged."""
    assert common._host_to_url_host('::') == '[::]'
    assert common._host_to_url_host('::1') == '[::1]'
    # Already-bracketed input is left as-is (not double-bracketed).
    assert common._host_to_url_host('[::1]') == '[::1]'
    assert common._host_to_url_host('127.0.0.1') == '127.0.0.1'
    assert common._host_to_url_host('0.0.0.0') == '0.0.0.0'
    assert common._host_to_url_host('localhost') == 'localhost'


def test_get_server_url_ipv6(monkeypatch):
    """get_server_url brackets IPv6 hosts and leaves IPv4/hostnames alone."""
    _isolate_server_url(monkeypatch)
    assert common.get_server_url('::') == 'http://[::]:46580'
    common.get_server_url.cache_clear()
    assert common.get_server_url('::1') == 'http://[::1]:46580'
    common.get_server_url.cache_clear()
    assert common.get_server_url('127.0.0.1') == 'http://127.0.0.1:46580'
    common.get_server_url.cache_clear()
    assert common.get_server_url('0.0.0.0') == 'http://0.0.0.0:46580'
    common.get_server_url.cache_clear()
    assert common.get_server_url('localhost') == 'http://localhost:46580'


def test_available_local_api_server_urls_are_wellformed():
    """The precomputed local URLs bracket IPv6 and parse correctly."""
    from urllib.parse import urlparse

    # IPv6 hosts are present and bracketed; IPv4 hosts are plain.
    assert 'http://[::]:46580' in common.AVAILABLE_LOCAL_API_SERVER_URLS
    assert 'http://[::1]:46580' in common.AVAILABLE_LOCAL_API_SERVER_URLS
    assert 'http://127.0.0.1:46580' in common.AVAILABLE_LOCAL_API_SERVER_URLS
    # Every entry is a well-formed URL that urlparse can decompose.
    for url in common.AVAILABLE_LOCAL_API_SERVER_URLS:
        parsed = urlparse(url)
        assert parsed.scheme == 'http'
        assert parsed.port == 46580
        assert parsed.hostname


def test_is_ipv6_host():
    assert common.is_ipv6_host('::')
    assert common.is_ipv6_host('::1')
    assert common.is_ipv6_host('2001:db8:0:0:0:0:0:1')
    assert not common.is_ipv6_host('127.0.0.1')
    assert not common.is_ipv6_host('0.0.0.0')
    assert not common.is_ipv6_host('localhost')
    assert not common.is_ipv6_host('example.org')


def test_reachable_local_host():
    """Wildcard bind hosts map to loopback; others pass through."""
    assert common._reachable_local_host('0.0.0.0') == '127.0.0.1'
    assert common._reachable_local_host('::') == '::1'
    # Loopback and hostname inputs are unchanged.
    assert common._reachable_local_host('::1') == '::1'
    assert common._reachable_local_host('127.0.0.1') == '127.0.0.1'
    assert common._reachable_local_host('localhost') == 'localhost'


def test_get_local_server_dial_url(monkeypatch):
    """Dial URL maps wildcard hosts to loopback and brackets IPv6."""
    _isolate_server_url(monkeypatch)
    # IPv6 wildcard is dialed via the bracketed IPv6 loopback.
    assert common.get_local_server_dial_url('::') == 'http://[::1]:46580'
    common.get_server_url.cache_clear()
    # IPv4 wildcard is dialed via IPv4 loopback (a valid connect target).
    assert common.get_local_server_dial_url(
        '0.0.0.0') == 'http://127.0.0.1:46580'
    common.get_server_url.cache_clear()
    assert common.get_local_server_dial_url('::1') == 'http://[::1]:46580'
    common.get_server_url.cache_clear()
    assert common.get_local_server_dial_url(
        '127.0.0.1') == 'http://127.0.0.1:46580'


@pytest.mark.parametrize('host,expected_dial_url', [
    ('::', 'http://[::1]:46580'),
    ('::1', 'http://[::1]:46580'),
    ('0.0.0.0', 'http://127.0.0.1:46580'),
    ('127.0.0.1', 'http://127.0.0.1:46580'),
])
def test_start_api_server_polls_reachable_host(monkeypatch, host,
                                               expected_dial_url):
    """The startup poll dials the reachable loopback, not the bind host.

    Regression test: a server bound to ``::1`` does not listen on 127.0.0.1,
    so polling the hard-coded loopback would time out and orphan the server.
    """
    _isolate_server_url(monkeypatch)

    proc = mock.Mock()
    proc.poll.return_value = None  # Process stays alive.
    # Server reports a non-dev version so the dashboard-staleness branch is
    # skipped; only .version is read there.
    status_info = mock.Mock(version='1.0.0')

    monkeypatch.setattr(common, 'is_api_server_local', lambda *a, **k: True)
    monkeypatch.setattr(common.subprocess, 'Popen', lambda *a, **k: proc)
    monkeypatch.setattr(common.os, 'makedirs', lambda *a, **k: None)
    # Avoid probing real host memory (psutil raises KeyError: b'MemTotal:' on
    # some sandboxed CI runners); the value only drives a warning message.
    monkeypatch.setattr(common.common_utils, 'get_mem_size_gb', lambda: 16.0)
    monkeypatch.setattr(common, 'get_api_server_status',
                        lambda url: status_info)
    mock_health = mock.Mock(return_value=(ApiServerStatus.HEALTHY, status_info))
    monkeypatch.setattr(common, 'check_server_healthy', mock_health)

    with mock.patch('builtins.open', mock.mock_open()):
        common._start_api_server(deploy=False, host=host)

    # The poll dials the reachable loopback URL for the bind host.
    mock_health.assert_called_once_with(expected_dial_url)


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


def test_process_mounts_removes_file_mounts_mapping(tmp_path, monkeypatch):
    """Test that file_mounts_mapping is removed after processing.

    This is a regression test for the bug where file_mounts_mapping would
    persist in the task config after translation, causing KeyError when the
    task is submitted again (e.g., in jobs scenarios).
    """
    from sky.skylet import constants as skylet_constants
    from sky.utils import yaml_utils

    # Mock the API_SERVER_CLIENT_DIR to use tmp_path
    api_server_dir = tmp_path / 'api_server_clients'
    monkeypatch.setattr('sky.server.common.API_SERVER_CLIENT_DIR',
                        api_server_dir)

    # Create a task YAML with file_mounts_mapping
    task_yaml = '''
name: test-task
resources:
  cloud: aws
workdir: /local/workdir
file_mounts:
  /remote/script.py: /local/script.py
  /remote/data:
    source: /local/data
file_mounts_mapping:
  /local/workdir: uploaded/workdir
  /local/script.py: uploaded/script.py
  /local/data: uploaded/data
run: python /remote/script.py
'''

    env_vars = {skylet_constants.USER_ID_ENV_VAR: 'test-user'}

    # Call the function
    dag = common.process_mounts_in_task_on_api_server(task=task_yaml,
                                                      env_vars=env_vars,
                                                      workdir_only=False)

    # Find the translated YAML file
    user_hash = 'test-user'
    client_dir = api_server_dir / user_hash

    # Find the translated file (it has _translated.yaml suffix)
    translated_files = list(client_dir.glob('**/*_translated.yaml'))
    assert len(translated_files) == 1, \
        f'Expected 1 translated file, found {len(translated_files)}'

    translated_file = translated_files[0]

    # Read the translated YAML and verify file_mounts_mapping is removed
    translated_configs = yaml_utils.read_yaml_all(str(translated_file))

    for task_config in translated_configs:
        if task_config is None:
            continue
        # The critical assertion: file_mounts_mapping should be removed
        assert 'file_mounts_mapping' not in task_config, \
            'file_mounts_mapping should be removed after processing'

        # Verify the paths were actually translated (workdir should be updated)
        if 'workdir' in task_config:
            assert 'uploaded/workdir' in task_config['workdir'], \
                f'workdir should be translated: {task_config["workdir"]}'

        # Verify file_mounts were translated
        if 'file_mounts' in task_config:
            file_mounts = task_config['file_mounts']
            for dst, src in file_mounts.items():
                if isinstance(src, str):
                    assert 'uploaded/' in src, \
                        f'file_mount should be translated: {src}'
                elif isinstance(src, dict) and 'source' in src:
                    source = src['source']
                    if isinstance(source, str):
                        assert 'uploaded/' in source, \
                            f'file_mount source should be translated: {source}'


def test_process_mounts_without_mapping(tmp_path, monkeypatch):
    """Test processing a task without file_mounts_mapping.

    Tasks without file_mounts_mapping should be processed without error.
    """
    from sky.skylet import constants as skylet_constants

    # Mock the API_SERVER_CLIENT_DIR to use tmp_path
    api_server_dir = tmp_path / 'api_server_clients'
    monkeypatch.setattr('sky.server.common.API_SERVER_CLIENT_DIR',
                        api_server_dir)

    # Create a simple task YAML without file_mounts_mapping
    task_yaml = '''
name: test-task
resources:
  cloud: aws
run: echo "hello world"
'''

    env_vars = {skylet_constants.USER_ID_ENV_VAR: 'test-user'}

    # Call the function - should not raise any errors
    dag = common.process_mounts_in_task_on_api_server(task=task_yaml,
                                                      env_vars=env_vars,
                                                      workdir_only=False)

    # Verify the dag was created successfully
    assert dag is not None
    assert len(dag.tasks) == 1
