"""Unit tests for sync calls: results returned in the response itself."""
# pylint: disable=protected-access
import asyncio
import types
from unittest import mock

import pytest

from sky import exceptions
from sky.client import sdk
from sky.client import sdk_async
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import rest
from sky.server import versions
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib

_SYNC = server_constants.SYNC_CALL_HEADER


def _entrypoint():
    """Module-level entrypoint so Request.encode() can pickle it."""


def _request(request_id='req-1', value=None, error=None):
    request = requests_lib.Request(request_id=request_id,
                                   name='enabled_clouds',
                                   entrypoint=_entrypoint,
                                   request_body=payloads.RequestBody(),
                                   status=requests_lib.RequestStatus.SUCCEEDED,
                                   created_at=0.0,
                                   user_id='user-1')
    if error is not None:
        request.status = requests_lib.RequestStatus.FAILED
        request.set_error(error)
    else:
        request.set_return_value(value)
    return request


def _response(status_code, headers=None, json_body=None):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def _sync_response(status_code, json_body=None, headers=None):
    return _response(status_code, {_SYNC: '1', **(headers or {})}, json_body)


@pytest.fixture(autouse=True)
def no_sync_server(monkeypatch):
    """Each test starts against a server that does not answer sync calls."""
    monkeypatch.setattr(rest, '_sync_call_max_limit', None)


@pytest.fixture
def sync_server(monkeypatch):
    """The server answers sync calls with limits up to 100."""
    monkeypatch.setattr(rest, '_sync_call_max_limit', 100)


def _sent_headers(call):
    return call.kwargs.get('headers') or {}


class TestRequestLayer:
    """The header, refusals and busy responses."""

    @mock.patch('sky.server.rest._session')
    def test_no_header_without_capability(self, mock_session):
        mock_session.request.return_value = _response(200)
        rest.request_without_retry('GET', 'http://test.com', sync_call=True)
        assert _SYNC not in _sent_headers(mock_session.request.call_args)

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest._session')
    def test_no_header_without_opt_in(self, mock_session):
        mock_session.request.return_value = _response(200)
        rest.request('GET', 'http://test.com', stream=True)
        call = mock_session.request.call_args
        assert _SYNC not in _sent_headers(call)
        assert 'sync_call' not in call.kwargs

    @pytest.mark.usefixtures('sync_server')
    @pytest.mark.parametrize('limit,sync', [(None, False), (0, False),
                                            (101, False), (1, True),
                                            (100, True)])
    @mock.patch('sky.server.rest._session')
    def test_header_only_within_limit(self, mock_session, limit, sync):
        mock_session.request.return_value = _response(200)
        rest.request_without_retry('POST',
                                   'http://test.com',
                                   sync_call=True,
                                   json={'limit': limit})
        assert (_SYNC in _sent_headers(mock_session.request.call_args)) is sync

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest._session')
    def test_header_with_capability(self, mock_session):
        mock_session.request.return_value = _sync_response(200, {})
        rest.request_without_retry('POST',
                                   'http://test.com',
                                   sync_call=True,
                                   headers={'Authorization': 'Bearer x'},
                                   json={'a': 1})
        call = mock_session.request.call_args
        assert _sent_headers(call) == {
            'Authorization': 'Bearer x',
            _SYNC: '1',
        }
        assert call.kwargs['json'] == {'a': 1}

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest._session')
    def test_refused_sync_call_is_resent_without_header(self, mock_session):
        queued = _response(200, {'X-Skypilot-Request-ID': 'req-1'})
        mock_session.request.side_effect = [_sync_response(400, {}), queued]
        assert rest.request_without_retry(
            'POST', 'http://test.com', sync_call=True) is queued
        first, second = mock_session.request.call_args_list
        assert _sent_headers(first)[_SYNC] == '1'
        assert _SYNC not in _sent_headers(second)

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest._session')
    def test_unmarked_400_is_returned(self, mock_session):
        bad_request = _response(400, json_body={'detail': 'bad'})
        mock_session.request.return_value = bad_request
        assert rest.request_without_retry(
            'POST', 'http://test.com', sync_call=True) is bad_request
        assert mock_session.request.call_count == 1

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest.time.sleep')
    @mock.patch('sky.server.rest._session')
    def test_busy_is_retried_after_retry_after(self, mock_session, mock_sleep):
        answered = _sync_response(200, {})
        mock_session.request.side_effect = [
            _sync_response(429, headers={'Retry-After': '2'}),
            answered,
        ]
        assert rest.request_without_retry(
            'GET', 'http://test.com', sync_call=True) is answered
        mock_sleep.assert_called_once_with(2.0)

    @pytest.mark.usefixtures('sync_server')
    @mock.patch('sky.server.rest.time.sleep')
    @mock.patch('sky.server.rest._session')
    def test_busy_gives_up(self, mock_session, mock_sleep):
        del mock_sleep
        mock_session.request.return_value = _sync_response(429)
        with pytest.raises(exceptions.ServerTemporarilyUnavailableError):
            rest.request_without_retry('GET', 'http://test.com', sync_call=True)
        assert mock_session.request.call_count == rest._SYNC_CALL_BUSY_RETRIES

    def test_retry_after_is_bounded(self):
        assert rest._retry_after_seconds(_response(
            429, {'Retry-After': '3600'
                 })) == (rest._SYNC_CALL_MAX_RETRY_AFTER_SECONDS)
        assert rest._retry_after_seconds(_response(
            429, {'Retry-After': 'soon'})) == 1.0


class TestCapability:
    """The sync call capability in /api/health."""

    @pytest.mark.parametrize('health,max_limit', [({
        'sync_call_max_limit': 100
    }, 100), ({}, None)])
    @mock.patch('sky.server.common.set_api_cookie_jar')
    @mock.patch('sky.server.common.get_cookies_from_response')
    @mock.patch('sky.server.versions.check_compatibility_at_client',
                return_value=versions.VersionInfo(api_version=1,
                                                  version='1.0.0'))
    @mock.patch('sky.server.common.get_api_server_status_response')
    def test_health_sets_capability(self, mock_health, mock_compatibility,
                                    mock_cookies, mock_cookie_jar, health,
                                    max_limit):
        del mock_compatibility, mock_cookies, mock_cookie_jar
        rest.set_sync_call_max_limit(50)
        mock_health.return_value = _response(200,
                                             json_body={
                                                 'status': 'healthy',
                                                 'api_version': '1',
                                                 'version': '1.0.0',
                                                 'commit': 'abc',
                                                 **health,
                                             })
        info = server_common.get_api_server_status()
        assert info.sync_call_max_limit == max_limit
        assert rest._sync_call_max_limit == max_limit

    @pytest.mark.parametrize('max_limit', [None, 100])
    def test_health_advertises_capability(self, max_limit):
        # pylint: disable=import-outside-toplevel
        from sky.server import server
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(auth_user=None, anonymous_user=False))
        executor.set_sync_calls(max_limit)
        try:
            response = asyncio.run(server.health(request))
        finally:
            executor.set_sync_calls(None)
        assert response.sync_call_max_limit == max_limit


class TestResults:
    """Results returned in the response, without /api/get."""

    def test_queued_response_has_no_sync_result(self):
        request_id = server_common.get_request_id(
            _response(200, {'X-Skypilot-Request-ID': 'req-1'}))
        assert request_id == 'req-1'
        assert request_id.sync_response is None

    def test_sync_result_without_api_get(self):
        body = _request(value=['aws']).encode().model_dump()
        request_id = server_common.get_request_id(_sync_response(200, body))
        assert request_id == 'req-1'
        with mock.patch('sky.server.common.make_authenticated_request'
                       ) as mock_make_request:
            assert sdk.get(request_id) == ['aws']
            assert sdk.stream_and_get(request_id) == ['aws']
            assert sdk.stream_and_get(request_id, follow=False) is None
            assert sdk.stream_and_get(request_id, None, None, False) is None
        mock_make_request.assert_not_called()

    def test_sync_error_raises_like_api_get(self):
        error = ValueError('boom')
        body = {'detail': _request(error=error).encode().model_dump()}
        request_id = server_common.get_request_id(_sync_response(500, body))
        with mock.patch('sky.server.common.make_authenticated_request'
                       ) as mock_make_request:
            mock_make_request.return_value = _response(500, json_body=body)
            with pytest.raises(ValueError, match='boom'):
                sdk.get('req-1')
            with pytest.raises(ValueError, match='boom'):
                sdk.get(request_id)
        assert mock_make_request.call_count == 1

    def test_non_json_500_from_api_get(self):
        response = _response(500)
        response.json.side_effect = ValueError('not json')
        with mock.patch('sky.server.common.make_authenticated_request',
                        return_value=response):
            with pytest.raises(RuntimeError, match='Failed to get request'):
                sdk.get('req-1')

    def test_async_sync_result_without_api_get(self):
        body = _request(value={'a': 1}).encode().model_dump()
        request_id = server_common.get_request_id(_sync_response(200, body))
        with mock.patch('sky.server.common.make_authenticated_request_async'
                       ) as mock_make_request:
            assert asyncio.run(sdk_async.get(request_id)) == {'a': 1}
            assert asyncio.run(sdk_async.stream_and_get(request_id)) == {'a': 1}
        mock_make_request.assert_not_called()
