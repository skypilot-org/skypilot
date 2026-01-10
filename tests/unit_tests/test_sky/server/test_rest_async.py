"""Unit tests for async functions in sky/server/rest.py."""

import asyncio
from unittest import mock

import aiohttp
import pytest

from sky import exceptions
from sky.server import rest


class TestIsTransientErrorAsync:
    """Tests for _is_transient_error_async() function."""

    def test_client_connector_error_is_transient(self):
        """Test that ClientConnectorError is considered transient."""
        error = aiohttp.ClientConnectorError(
            connection_key=mock.Mock(), os_error=OSError('Connection refused'))

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_client_timeout_is_transient(self):
        """Test that ClientTimeout errors are considered transient."""
        error = aiohttp.ServerTimeoutError('Timeout')

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_client_response_error_5xx_is_transient(self):
        """Test that 5xx ClientResponseError is transient."""
        error = aiohttp.ClientResponseError(request_info=mock.Mock(),
                                            history=(),
                                            status=500,
                                            message='Internal Server Error')

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_client_response_error_502_is_transient(self):
        """Test that 502 Bad Gateway is transient."""
        error = aiohttp.ClientResponseError(request_info=mock.Mock(),
                                            history=(),
                                            status=502,
                                            message='Bad Gateway')

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_client_response_error_503_is_transient(self):
        """Test that 503 Service Unavailable is transient."""
        error = aiohttp.ClientResponseError(request_info=mock.Mock(),
                                            history=(),
                                            status=503,
                                            message='Service Unavailable')

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_client_response_error_4xx_is_not_transient(self):
        """Test that 4xx ClientResponseError is not transient."""
        error = aiohttp.ClientResponseError(request_info=mock.Mock(),
                                            history=(),
                                            status=404,
                                            message='Not Found')

        result = rest._is_transient_error_async(error)

        assert result is False

    def test_client_response_error_400_is_not_transient(self):
        """Test that 400 Bad Request is not transient."""
        error = aiohttp.ClientResponseError(request_info=mock.Mock(),
                                            history=(),
                                            status=400,
                                            message='Bad Request')

        result = rest._is_transient_error_async(error)

        assert result is False

    def test_server_temporarily_unavailable_is_transient(self):
        """Test that ServerTemporarilyUnavailableError is transient."""
        error = exceptions.ServerTemporarilyUnavailableError('Server down')

        result = rest._is_transient_error_async(error)

        assert result is True

    def test_unknown_error_is_transient(self):
        """Test that unknown errors are considered transient (safe default)."""
        error = RuntimeError('Unknown error')

        result = rest._is_transient_error_async(error)

        # Unknown errors should be considered transient for safety
        assert result is True


class TestRequestAsync:
    """Tests for request_async() function."""

    @pytest.mark.asyncio
    async def test_successful_request(self):
        """Test successful async request."""
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.headers = {}

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        mock_session.request = mock.AsyncMock(return_value=mock_response)

        result = await rest.request_async(mock_session, 'GET',
                                          'http://test.com/api')

        assert result == mock_response
        mock_session.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_request_interrupted(self):
        """Test that RequestInterruptedError triggers immediate retry."""
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.headers = {}

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        mock_session.request = mock.AsyncMock(side_effect=[
            exceptions.RequestInterruptedError('Connection dropped'),
            mock_response
        ])

        result = await rest.request_async(mock_session, 'GET',
                                          'http://test.com/api')

        assert result == mock_response
        assert mock_session.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        """Test that transient errors are retried with backoff."""
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.headers = {}

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        error = aiohttp.ClientConnectorError(
            connection_key=mock.Mock(), os_error=OSError('Connection refused'))
        mock_session.request = mock.AsyncMock(
            side_effect=[error, mock_response])

        with mock.patch('asyncio.sleep', return_value=None):
            result = await rest.request_async(mock_session, 'GET',
                                              'http://test.com/api')

        assert result == mock_response
        assert mock_session.request.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        """Test that error is raised after max retries."""
        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        # Use ServerTimeoutError which is transient but NOT converted to
        # RequestInterruptedError (ClientConnectorError gets converted)
        error = aiohttp.ServerTimeoutError('Timeout error')
        mock_session.request = mock.AsyncMock(side_effect=error)

        with mock.patch('asyncio.sleep', return_value=None):
            with pytest.raises(aiohttp.ServerTimeoutError):
                await rest.request_async(mock_session, 'GET',
                                         'http://test.com/api')

        # Should have tried max_retries times
        assert mock_session.request.call_count == 3


class TestRequestWithoutRetryAsync:
    """Tests for request_without_retry_async() function."""

    @pytest.mark.asyncio
    async def test_adds_version_headers(self):
        """Test that API version headers are added to request."""
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.headers = {}

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        mock_session.request = mock.AsyncMock(return_value=mock_response)

        await rest.request_without_retry_async(mock_session, 'GET',
                                               'http://test.com/api')

        # Check that headers were added
        call_kwargs = mock_session.request.call_args[1]
        assert 'headers' in call_kwargs
        from sky.server import constants
        assert constants.API_VERSION_HEADER in call_kwargs['headers']
        assert constants.VERSION_HEADER in call_kwargs['headers']

    @pytest.mark.asyncio
    async def test_handles_503_status(self):
        """Test that 503 status raises ServerTemporarilyUnavailableError."""
        mock_response = mock.Mock()
        mock_response.status = 503
        mock_response.json = mock.AsyncMock(
            return_value={'detail': 'Server unavailable'})
        mock_response.headers = {}

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        mock_session.request = mock.AsyncMock(return_value=mock_response)

        with pytest.raises(exceptions.ServerTemporarilyUnavailableError):
            await rest.request_without_retry_async(mock_session, 'GET',
                                                   'http://test.com/api')

    @pytest.mark.asyncio
    async def test_sets_remote_api_version(self):
        """Test that remote API version is set from response headers."""
        from sky.server import constants
        from sky.server import versions

        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.headers = {
            constants.API_VERSION_HEADER: '5',
            constants.VERSION_HEADER: '1.2.3'
        }

        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        mock_session.request = mock.AsyncMock(return_value=mock_response)

        with mock.patch.object(versions, 'set_remote_api_version') as mock_set_api, \
             mock.patch.object(versions, 'set_remote_version') as mock_set_ver:
            await rest.request_without_retry_async(mock_session, 'GET',
                                                   'http://test.com/api')

            mock_set_api.assert_called_once_with(5)
            mock_set_ver.assert_called_once_with('1.2.3')

    @pytest.mark.asyncio
    async def test_handles_client_connector_error(self):
        """Test that ClientConnectorError is converted to RequestInterruptedError."""
        mock_session = mock.Mock(spec=aiohttp.ClientSession)
        error = aiohttp.ClientConnectorError(
            connection_key=mock.Mock(), os_error=OSError('Connection refused'))
        mock_session.request = mock.AsyncMock(side_effect=error)

        with pytest.raises(exceptions.RequestInterruptedError):
            await rest.request_without_retry_async(mock_session, 'GET',
                                                   'http://test.com/api')


class TestRetryContext:
    """Tests for RetryContext class."""

    def test_creates_with_zero_lines_processed(self):
        """Test that RetryContext initializes with zero lines processed."""
        ctx = rest.RetryContext()

        assert ctx.line_processed == 0

    def test_tracks_line_progress(self):
        """Test that line progress can be tracked."""
        ctx = rest.RetryContext()
        ctx.line_processed = 10

        assert ctx.line_processed == 10

        ctx.line_processed += 5
        assert ctx.line_processed == 15


class TestGetRetryContext:
    """Tests for get_retry_context() function."""

    def test_returns_none_when_not_in_context(self):
        """Test that None is returned outside of retry context."""
        result = rest.get_retry_context()

        assert result is None

    def test_returns_context_when_in_retry(self):
        """Test that context is returned inside _retry_in_context."""
        with rest._retry_in_context() as ctx:
            result = rest.get_retry_context()
            assert result is ctx
            assert result.line_processed == 0

    def test_context_is_cleared_after_exit(self):
        """Test that context is cleared after exiting _retry_in_context."""
        with rest._retry_in_context():
            pass

        result = rest.get_retry_context()
        assert result is None


class TestHandleServerUnavailableAsync:
    """Tests for handle_server_unavailable_async() function."""

    @pytest.mark.asyncio
    async def test_raises_on_503_with_json_detail(self):
        """Test that 503 with JSON detail raises exception."""
        mock_response = mock.Mock()
        mock_response.status = 503
        mock_response.json = mock.AsyncMock(
            return_value={'detail': 'Custom error message'})

        with pytest.raises(exceptions.ServerTemporarilyUnavailableError) as exc:
            await rest.handle_server_unavailable_async(mock_response)

        assert 'Custom error message' in str(exc.value)

    @pytest.mark.asyncio
    async def test_raises_on_503_with_text_fallback(self):
        """Test that 503 with text fallback raises exception."""
        mock_response = mock.Mock()
        mock_response.status = 503
        mock_response.json = mock.AsyncMock(
            side_effect=Exception('JSON parse failed'))
        mock_response.text = mock.AsyncMock(return_value='Plain text error')

        with pytest.raises(exceptions.ServerTemporarilyUnavailableError) as exc:
            await rest.handle_server_unavailable_async(mock_response)

        assert 'Plain text error' in str(exc.value)

    @pytest.mark.asyncio
    async def test_raises_on_503_with_both_failing(self):
        """Test that 503 raises exception even if json and text fail."""
        mock_response = mock.Mock()
        mock_response.status = 503
        mock_response.json = mock.AsyncMock(
            side_effect=Exception('JSON parse failed'))
        mock_response.text = mock.AsyncMock(
            side_effect=Exception('Text parse failed'))

        with pytest.raises(exceptions.ServerTemporarilyUnavailableError):
            await rest.handle_server_unavailable_async(mock_response)

    @pytest.mark.asyncio
    async def test_does_nothing_on_200(self):
        """Test that 200 status does not raise exception."""
        mock_response = mock.Mock()
        mock_response.status = 200

        # Should not raise
        await rest.handle_server_unavailable_async(mock_response)

    @pytest.mark.asyncio
    async def test_does_nothing_on_500(self):
        """Test that 500 status does not raise exception (only 503)."""
        mock_response = mock.Mock()
        mock_response.status = 500

        # Should not raise
        await rest.handle_server_unavailable_async(mock_response)


class TestConnectionPoolConfig:
    """Tests for connection pool configuration."""

    def test_session_has_http_adapter(self):
        """Test that session has HTTPAdapter with connection pooling."""
        # The _session should have adapters mounted
        assert 'http://' in rest._session.adapters
        assert 'https://' in rest._session.adapters

    def test_adapter_has_connection_pool_settings(self):
        """Test that adapter has proper connection pool settings."""
        adapter = rest._session.adapters['http://']

        # HTTPAdapter stores pool settings in private attributes
        assert adapter._pool_connections == 50
        assert adapter._pool_maxsize == 200

    def test_session_has_version_headers(self):
        """Test that session has API version headers set."""
        from sky.server import constants

        assert constants.API_VERSION_HEADER in rest._session.headers
        assert constants.VERSION_HEADER in rest._session.headers


class TestHandleResponseText:
    """Extended tests for handle_response_text()."""

    def test_extracts_title_from_html(self):
        """Test extracting title from HTML response."""
        mock_response = mock.Mock()
        mock_response.text = '<html><head><title>Error Page</title></head></html>'
        mock_response.headers = {'Content-Type': 'text/html'}

        result = rest.handle_response_text(mock_response)

        assert result == 'Error Page'

    def test_returns_plain_text_as_is(self):
        """Test that plain text is returned as-is."""
        mock_response = mock.Mock()
        mock_response.text = 'Plain error message'
        mock_response.headers = {'Content-Type': 'text/plain'}

        result = rest.handle_response_text(mock_response)

        assert result == 'Plain error message'

    def test_handles_empty_text(self):
        """Test handling empty text response."""
        mock_response = mock.Mock()
        mock_response.text = ''
        mock_response.headers = {}

        result = rest.handle_response_text(mock_response)

        assert result == ''

    def test_handles_string_input(self):
        """Test handling string input directly."""
        result = rest.handle_response_text('Simple string')

        assert result == 'Simple string'

    def test_handles_html_with_special_chars(self):
        """Test handling HTML with HTML entities."""
        mock_response = mock.Mock()
        mock_response.text = '<title>Error &amp; Warning</title>'
        mock_response.headers = {'Content-Type': 'text/html'}

        result = rest.handle_response_text(mock_response)

        assert result == 'Error & Warning'

    def test_detects_html_without_content_type(self):
        """Test that HTML is detected even without Content-Type header."""
        mock_response = mock.Mock()
        mock_response.text = '<html><title>Detected HTML</title></html>'
        mock_response.headers = {}

        result = rest.handle_response_text(mock_response)

        assert result == 'Detected HTML'
