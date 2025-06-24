"""Unit tests for the SkyPilot REST API client module."""
import time
from unittest import mock

import pytest

from sky import exceptions
from sky.server import rest


class TestHandleServerUnavailable:
    """Test cases for handle_server_unavailable function."""

    def test_handle_server_unavailable_with_503_status(self):
        """Test that 503 status code raises ServerTemporarilyUnavailableError."""
        mock_response = mock.Mock()
        mock_response.status_code = 503

        with pytest.raises(
                exceptions.ServerTemporarilyUnavailableError) as exc_info:
            rest.handle_server_unavailable(mock_response)

        assert 'SkyPilot API server is temporarily unavailable' in str(
            exc_info.value)

    def test_handle_server_unavailable_with_non_503_status(self):
        """Test that non-503 status codes do not raise exception."""
        for status_code in [200, 400, 404, 500, 502, 504]:
            mock_response = mock.Mock()
            mock_response.status_code = status_code

            # Should not raise any exception
            rest.handle_server_unavailable(mock_response)


class TestRetryOnServerUnavailableDecorator:
    """Test cases for retry_on_server_unavailable decorator."""

    def test_successful_function_call_no_retry(self):
        """Test that successful function calls execute without retry."""

        @rest.retry_on_server_unavailable()
        def dummy_function(x, y=None):
            return x + (y or 0)

        result = dummy_function(5, y=3)
        assert result == 8

    def test_retry_on_server_unavailable_error(self):
        """Test retry behavior when ServerTemporarilyUnavailableError is raised."""
        call_count = 0

        @rest.retry_on_server_unavailable(max_wait_seconds=5,
                                          initial_backoff=0.1)
        def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise exceptions.ServerTemporarilyUnavailableError(
                    "Server error")
            return "success"

        with mock.patch('time.sleep'):  # Speed up test by mocking sleep
            result = failing_then_succeeding_function()
            assert result == "success"
            assert call_count == 3

    def test_timeout_after_max_wait_seconds(self):
        """Test that function times out after max_wait_seconds."""
        call_count = 0

        @rest.retry_on_server_unavailable(max_wait_seconds=2,
                                          initial_backoff=0.1)
        def always_failing_function():
            nonlocal call_count
            call_count += 1
            raise exceptions.ServerTemporarilyUnavailableError("Server error")

        start_time = time.time()
        with mock.patch('time.sleep'), \
             pytest.raises(exceptions.ServerTemporarilyUnavailableError) as exc_info:
            always_failing_function()

        # Check that timeout message is in the exception
        assert 'Timeout waiting for the API server' in str(exc_info.value)
        assert call_count > 1  # Should have retried at least once

    def test_other_exceptions_not_retried(self):
        """Test that other exceptions are not retried."""
        call_count = 0

        @rest.retry_on_server_unavailable()
        def function_with_other_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Some other error")

        with pytest.raises(ValueError):
            function_with_other_error()

        assert call_count == 1  # Should not retry

    def test_backoff_progression(self):
        """Test that backoff time increases with retries."""
        call_count = 0
        sleep_times = []

        @rest.retry_on_server_unavailable(max_wait_seconds=10,
                                          initial_backoff=5.0,
                                          max_backoff_factor=2)
        def failing_function():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise exceptions.ServerTemporarilyUnavailableError(
                    "Server error")
            return "success"

        def mock_sleep(seconds):
            sleep_times.append(seconds)

        with mock.patch('time.sleep', side_effect=mock_sleep):
            result = failing_function()
            assert result == "success"

        # Check that sleep times increase (exponential backoff)
        assert len(sleep_times) == 3
        assert sleep_times[0] >= 1.0
        assert sleep_times[1] > sleep_times[0]  # Should increase
        assert sleep_times[2] > sleep_times[1]  # Should increase

    @mock.patch('sky.utils.rich_utils.client_status')
    def test_status_message_displayed_during_retry(self, mock_status):
        """Test that status message is displayed during retries."""
        call_count = 0

        @rest.retry_on_server_unavailable(max_wait_seconds=5,
                                          initial_backoff=0.1)
        def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise exceptions.ServerTemporarilyUnavailableError(
                    "Server error")
            return "success"

        with mock.patch('time.sleep'):
            result = failing_then_succeeding_function()
            assert result == "success"

        # Check that status message context manager was used
        mock_status.assert_called_once()
        call_args = mock_status.call_args[0][0]
        assert 'API server is temporarily unavailable' in call_args

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""

        @rest.retry_on_server_unavailable()
        def documented_function(x, y=1):
            """This is a test function."""
            return x + y

        assert documented_function.__name__ == 'documented_function'
        assert documented_function.__doc__ == 'This is a test function.'

    def test_custom_retry_parameters(self):
        """Test retry decorator with custom parameters."""
        call_count = 0

        @rest.retry_on_server_unavailable(max_wait_seconds=1,
                                          initial_backoff=0.05,
                                          max_backoff_factor=3)
        def custom_retry_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise exceptions.ServerTemporarilyUnavailableError(
                    "Server error")
            return "success"

        with mock.patch('time.sleep'):
            result = custom_retry_function()
            assert result == "success"
            assert call_count == 3


class TestRestWrapperFunctions:
    """Test cases for REST wrapper functions (post and get)."""

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_post_successful_request(self, mock_lazy_import):
        """Test successful POST request."""
        mock_requests = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_requests.post.return_value = mock_response
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        result = rest.post('http://test.com', json={'key': 'value'})

        assert result == mock_response
        mock_requests.post.assert_called_once_with('http://test.com',
                                                   data=None,
                                                   json={'key': 'value'})

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_get_successful_request(self, mock_lazy_import):
        """Test successful GET request."""
        mock_requests = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_requests.get.return_value = mock_response
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        result = rest.get('http://test.com', params={'param': 'value'})

        assert result == mock_response
        mock_requests.get.assert_called_once_with('http://test.com',
                                                  params={'param': 'value'})

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_post_with_503_response_retries(self, mock_lazy_import):
        """Test POST request retries on 503 response."""
        mock_requests = mock.Mock()

        # First call returns 503, second call succeeds
        mock_response_503 = mock.Mock()
        mock_response_503.status_code = 503
        mock_response_200 = mock.Mock()
        mock_response_200.status_code = 200

        mock_requests.post.side_effect = [mock_response_503, mock_response_200]
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        with mock.patch('time.sleep'):  # Speed up test
            result = rest.post('http://test.com', json={'key': 'value'})

        assert result == mock_response_200
        assert mock_requests.post.call_count == 2

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_get_with_503_response_retries(self, mock_lazy_import):
        """Test GET request retries on 503 response."""
        mock_requests = mock.Mock()

        # First call returns 503, second call succeeds
        mock_response_503 = mock.Mock()
        mock_response_503.status_code = 503
        mock_response_200 = mock.Mock()
        mock_response_200.status_code = 200

        mock_requests.get.side_effect = [mock_response_503, mock_response_200]
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        with mock.patch('time.sleep'):  # Speed up test
            result = rest.get('http://test.com', params={'param': 'value'})

        assert result == mock_response_200
        assert mock_requests.get.call_count == 2

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_post_passes_through_kwargs(self, mock_lazy_import):
        """Test that POST passes through additional kwargs."""
        mock_requests = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_requests.post.return_value = mock_response
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        result = rest.post('http://test.com',
                           json={'key': 'value'},
                           headers={'Authorization': 'Bearer token'},
                           timeout=30)

        assert result == mock_response
        mock_requests.post.assert_called_once_with(
            'http://test.com',
            data=None,
            json={'key': 'value'},
            headers={'Authorization': 'Bearer token'},
            timeout=30)

    @mock.patch('sky.adaptors.common.LazyImport')
    def test_get_passes_through_kwargs(self, mock_lazy_import):
        """Test that GET passes through additional kwargs."""
        mock_requests = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_requests.get.return_value = mock_response
        mock_lazy_import.return_value = mock_requests

        # Import after mocking to get the mocked requests
        import importlib
        importlib.reload(rest)

        result = rest.get('http://test.com',
                          params={'param': 'value'},
                          headers={'User-Agent': 'SkyPilot'},
                          timeout=30)

        assert result == mock_response
        mock_requests.get.assert_called_once_with(
            'http://test.com',
            params={'param': 'value'},
            headers={'User-Agent': 'SkyPilot'},
            timeout=30)
