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


class TestRetryTransientErrorsDecorator:
    """Test cases for retry_transient_errors decorator."""

    def test_successful_function_call_no_retry(self):
        """Test that successful function calls execute without retry."""

        @rest.retry_transient_errors()
        def dummy_function(x, y=None):
            return x + (y or 0)

        result = dummy_function(5, y=3)
        assert result == 8

    def test_retry_on_transient_http_error(self):
        """Test retry behavior for transient HTTP errors (status >= 500)."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=3, initial_backoff=0.1)
        def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # Create HTTP error with status >= 500 (transient)
                mock_response = mock.Mock()
                mock_response.status_code = 500
                http_error = rest.requests.exceptions.HTTPError()
                http_error.response = mock_response
                raise http_error
            return "success"

        with mock.patch('time.sleep'):  # Speed up test by mocking sleep
            result = failing_then_succeeding_function()
            assert result == "success"
            assert call_count == 3

    def test_no_retry_on_non_transient_http_error(self):
        """Test no retry for non-transient HTTP errors (status < 500)."""
        call_count = 0

        @rest.retry_transient_errors()
        def function_with_client_error():
            nonlocal call_count
            call_count += 1
            # Create HTTP error with status < 500 (non-transient)
            mock_response = mock.Mock()
            mock_response.status_code = 404
            http_error = rest.requests.exceptions.HTTPError()
            http_error.response = mock_response
            raise http_error

        with pytest.raises(rest.requests.exceptions.HTTPError):
            function_with_client_error()

        assert call_count == 1  # Should not retry

    def test_retry_on_other_exceptions(self):
        """Test retry behavior for other non-HTTP exceptions."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=2, initial_backoff=0.1)
        def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Network error")
            return "success"

        with mock.patch('time.sleep'):
            result = failing_then_succeeding_function()
            assert result == "success"
            assert call_count == 2

    def test_max_retries_exhausted(self):
        """Test that function fails after max retries are exhausted."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=2, initial_backoff=0.1)
        def always_failing_function():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Persistent error")

        with mock.patch('time.sleep'), \
             pytest.raises(ConnectionError) as exc_info:
            always_failing_function()

        assert 'Persistent error' in str(exc_info.value)
        assert call_count == 2  # Should have retried exactly max_retries times

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""

        @rest.retry_transient_errors()
        def documented_function(x, y=1):
            """This is a test function with parameters."""
            return x + y

        assert documented_function.__name__ == 'documented_function'
        assert 'test function with parameters' in documented_function.__doc__

    @mock.patch('sky.server.rest.logger')
    def test_debug_logging_during_retries(self, mock_logger):
        """Test that debug messages are logged during retries."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=3, initial_backoff=0.1)
        def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Test error")
            return "success"

        with mock.patch('time.sleep'):
            result = failing_then_succeeding_function()
            assert result == "success"

        # Check that debug logging was called
        assert mock_logger.debug.call_count == 2  # Two retries
        debug_calls = mock_logger.debug.call_args_list
        for call in debug_calls:
            assert 'Retry failing_then_succeeding_function due to' in call[0][0]

    @mock.patch('sky.server.rest.logger')
    def test_retry_context_is_reset_when_progress_is_made(self, mock_logger):
        """Test that retry context is reset when progress is made."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=3, initial_backoff=0.1)
        def function_making_progress():
            nonlocal call_count
            retry_context = rest.get_retry_context()
            call_count += 1
            if call_count < 10:
                retry_context.line_processed += 1
                raise ValueError("Test error")
            return "success"

        with mock.patch('time.sleep'):
            result = function_making_progress()
            assert result == "success"

        # Check that debug logging was called
        assert mock_logger.debug.call_count == 9  # 9 retries
        debug_calls = mock_logger.debug.call_args_list
        for call in debug_calls:
            assert 'Retry function_making_progress due to' in call[0][0]

    @mock.patch('sky.server.rest.logger')
    def test_repeated_failure_after_progress(self, mock_logger):
        """Test that retry_transient_errors fails if function fails after making progress."""
        call_count = 0

        @rest.retry_transient_errors(max_retries=3, initial_backoff=0.1)
        def function_failing_after_making_progress():
            nonlocal call_count
            retry_context = rest.get_retry_context()
            call_count += 1
            if call_count < 10:
                retry_context.line_processed += 1
                raise ValueError("Test error")

            raise ValueError("Test error")

        with mock.patch('time.sleep'):
            with pytest.raises(ValueError):
                function_failing_after_making_progress()

        # Check that debug logging was called
        # 11 retries, 9 retries after progress, 2 retries after failure
        assert mock_logger.debug.call_count == 11
        debug_calls = mock_logger.debug.call_args_list
        for call in debug_calls:
            assert 'Retry function_failing_after_making_progress due to' in call[0][0]

    def test_different_http_error_status_codes(self):
        """Test behavior with different HTTP error status codes."""

        def create_http_error_function(status_code):
            call_count = 0

            @rest.retry_transient_errors(max_retries=2, initial_backoff=0.1)
            def function_with_http_error():
                nonlocal call_count
                call_count += 1
                mock_response = mock.Mock()
                mock_response.status_code = status_code
                http_error = rest.requests.exceptions.HTTPError()
                http_error.response = mock_response
                raise http_error

            return function_with_http_error, lambda: call_count

        # Test non-transient errors (< 500) - should not retry
        for status_code in [400, 401, 403, 404, 429]:
            func, get_count = create_http_error_function(status_code)
            with pytest.raises(rest.requests.exceptions.HTTPError):
                with mock.patch('time.sleep'):
                    func()
            assert get_count() == 1  # No retries

        # Test transient errors (>= 500) - should retry
        for status_code in [500, 502, 503, 504]:
            func, get_count = create_http_error_function(status_code)
            with pytest.raises(rest.requests.exceptions.HTTPError):
                with mock.patch('time.sleep'):
                    func()
            assert get_count() == 2  # Retried max_retries times


class TestRetryOnServerUnavailableDecorator:
    """Test cases for retry_on_server_unavailable decorator."""

    def test_successful_function_call_no_retry(self):
        """Test that successful function calls execute without retry."""

        @rest._retry_on_server_unavailable()
        def dummy_function(x, y=None):
            return x + (y or 0)

        result = dummy_function(5, y=3)
        assert result == 8

    def test_retry_on_server_unavailable_error(self):
        """Test retry behavior when ServerTemporarilyUnavailableError is raised."""
        call_count = 0

        @rest._retry_on_server_unavailable(max_wait_seconds=5,
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

        @rest._retry_on_server_unavailable(max_wait_seconds=2,
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

        @rest._retry_on_server_unavailable()
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

        @rest._retry_on_server_unavailable(max_wait_seconds=10,
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

    @mock.patch('sky.utils.rich_utils.client_status')
    def test_status_message_displayed_during_retry(self, mock_status):
        """Test that status message is displayed during retries."""
        call_count = 0

        @rest._retry_on_server_unavailable(max_wait_seconds=5,
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
        assert 'API server is temporarily' in call_args

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""

        @rest._retry_on_server_unavailable()
        def documented_function(x, y=1):
            """This is a test function."""
            return x + y

        assert documented_function.__name__ == 'documented_function'
        assert documented_function.__doc__ == 'This is a test function.'

    def test_custom_retry_parameters(self):
        """Test retry decorator with custom parameters."""
        call_count = 0

        @rest._retry_on_server_unavailable(max_wait_seconds=1,
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


class TestRestRequestFunctions:
    """Test cases for REST request functions (request and request_without_retry)."""

    @mock.patch('sky.server.rest._session')
    def test_post_successful_request(self, mock_session):
        """Test successful POST request."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_session.request.return_value = mock_response

        result = rest.request('POST', 'http://test.com', json={'key': 'value'})

        assert result == mock_response
        mock_session.request.assert_called_once_with('POST',
                                                     'http://test.com',
                                                     json={'key': 'value'})

    @mock.patch('sky.server.rest._session')
    def test_get_successful_request(self, mock_session):
        """Test successful GET request."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_session.request.return_value = mock_response

        result = rest.request('GET',
                              'http://test.com',
                              params={'param': 'value'})

        assert result == mock_response
        mock_session.request.assert_called_once_with('GET',
                                                     'http://test.com',
                                                     params={'param': 'value'})

    @mock.patch('sky.server.rest._session')
    def test_post_with_503_response_retries(self, mock_session):
        """Test POST request retries on 503 response."""
        # First call returns 503, second call succeeds
        mock_response_503 = mock.Mock()
        mock_response_503.status_code = 503
        mock_response_503.headers = {}
        mock_response_200 = mock.Mock()
        mock_response_200.status_code = 200
        mock_response_200.headers = {}

        mock_session.request.side_effect = [
            mock_response_503, mock_response_200
        ]

        with mock.patch('time.sleep'):  # Speed up test
            result = rest.request('POST',
                                  'http://test.com',
                                  json={'key': 'value'})

        assert result == mock_response_200
        assert mock_session.request.call_count == 2

    @mock.patch('sky.server.rest._session')
    def test_get_with_503_response_retries(self, mock_session):
        """Test GET request retries on 503 response."""
        # First call returns 503, second call succeeds
        mock_response_503 = mock.Mock()
        mock_response_503.status_code = 503
        mock_response_503.headers = {}
        mock_response_200 = mock.Mock()
        mock_response_200.status_code = 200
        mock_response_200.headers = {}

        mock_session.request.side_effect = [
            mock_response_503, mock_response_200
        ]

        with pytest.raises(exceptions.ServerTemporarilyUnavailableError):
            rest.request_without_retry('GET', 'http://test.com')

    @mock.patch('sky.server.rest._session')
    def test_post_passes_through_kwargs(self, mock_session):
        """Test that POST passes through additional kwargs."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_session.request.return_value = mock_response

        result = rest.request('POST',
                              'http://test.com',
                              json={'key': 'value'},
                              headers={'Authorization': 'Bearer token'},
                              timeout=30)

        assert result == mock_response
        mock_session.request.assert_called_once_with(
            'POST',
            'http://test.com',
            json={'key': 'value'},
            headers={'Authorization': 'Bearer token'},
            timeout=30)

    @mock.patch('sky.server.rest._session')
    def test_get_passes_through_kwargs(self, mock_session):
        """Test that GET passes through additional kwargs."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_session.request.return_value = mock_response

        result = rest.request_without_retry('GET',
                                            'http://test.com',
                                            params={'param': 'value'},
                                            headers={'User-Agent': 'SkyPilot'},
                                            timeout=30)

        assert result == mock_response
        mock_session.request.assert_called_once_with(
            'GET',
            'http://test.com',
            params={'param': 'value'},
            headers={'User-Agent': 'SkyPilot'},
            timeout=30)
