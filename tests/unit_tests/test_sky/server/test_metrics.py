"""Unit tests for the metrics system."""

import os
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import fastapi
from prometheus_client import CollectorRegistry
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest
import pytest

from sky.metrics import utils as metrics_utils
from sky.server import metrics


def test_get_status_code_group():
    """Test status code grouping"""
    assert metrics._get_status_code_group(200) == "2xx"
    assert metrics._get_status_code_group(201) == "2xx"
    assert metrics._get_status_code_group(299) == "2xx"

    assert metrics._get_status_code_group(400) == "4xx"
    assert metrics._get_status_code_group(404) == "4xx"
    assert metrics._get_status_code_group(499) == "4xx"

    assert metrics._get_status_code_group(500) == "5xx"
    assert metrics._get_status_code_group(503) == "5xx"
    assert metrics._get_status_code_group(599) == "5xx"


def test_is_streaming_api():
    assert metrics._is_streaming_api("/api/v1/logs") is True
    assert metrics._is_streaming_api("/api/v1/logs/") is True
    assert metrics._is_streaming_api("/logs") is True
    assert metrics._is_streaming_api("/logs/") is True

    assert metrics._is_streaming_api("/api/stream") is True
    assert metrics._is_streaming_api("/api/stream/") is True
    assert metrics._is_streaming_api("/v1/api/stream") is True

    assert metrics._is_streaming_api("/api/v1/status") is False
    assert metrics._is_streaming_api("/health") is False
    assert metrics._is_streaming_api("/api/v1/jobs") is False
    assert metrics._is_streaming_api("/metrics") is False


@pytest.mark.asyncio
async def test_metrics_endpoint_without_multiprocess():
    """Test metrics endpoint in single process mode."""
    with patch.dict(os.environ, {}, clear=False):
        # Remove PROMETHEUS_MULTIPROC_DIR if it exists
        if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
            del os.environ['PROMETHEUS_MULTIPROC_DIR']

        with patch('sky.server.metrics.generate_latest') as mock_gen:
            mock_gen.return_value = b"# HELP test_metric Test metric\n"

            response = metrics.metrics()

            assert isinstance(response, fastapi.Response)
            assert response.media_type == CONTENT_TYPE_LATEST
            assert response.headers['Cache-Control'] == 'no-cache'
            assert b"# HELP test_metric Test metric" in response.body
            mock_gen.assert_called_once()


@pytest.mark.asyncio
async def test_metrics_endpoint_with_multiprocess():
    """Test metrics endpoint in multiprocess mode."""
    with patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': '/tmp/prom'}):
        with patch('sky.server.metrics.prom.CollectorRegistry') as \
                mock_registry, \
             patch('sky.server.metrics.multiprocess.'
                   'MultiProcessCollector') as mock_collector, \
             patch('sky.server.metrics.generate_latest') as mock_gen:

            mock_registry_instance = MagicMock()
            mock_registry.return_value = mock_registry_instance
            mock_gen.return_value = b"# HELP multiproc_metric Test\n"

            response = metrics.metrics()

            assert isinstance(response, fastapi.Response)
            mock_registry.assert_called_once()
            mock_collector.assert_called_once_with(mock_registry_instance)
            mock_gen.assert_called_once_with(mock_registry_instance)


@pytest.fixture
def prometheus_middleware():
    """Create PrometheusMiddleware instance for testing."""
    middleware = metrics.PrometheusMiddleware(app=MagicMock())

    # Clear metric values before each test
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()

    return middleware


def _get_metric_value_from_registry(metric_name, labels=None):
    """Helper function to get metric value from the prometheus registry."""
    registry = CollectorRegistry()
    # Register the actual metrics to the test registry
    registry.register(metrics_utils.SKY_APISERVER_REQUESTS_TOTAL)
    registry.register(metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS)

    # Generate the metrics output
    output = generate_latest(registry).decode('utf-8')

    # Parse the output to find the specific metric value
    lines = output.split('\n')
    for line in lines:
        if line.startswith(metric_name):
            if labels:
                # Check if all labels are present in the line
                if all(f'{k}="{v}"' in line for k, v in labels.items()):
                    # Extract the value at the end of the line
                    value = line.split()[-1]
                    try:
                        return float(value)
                    except ValueError:
                        continue
            else:
                # No labels specified, just get the first match
                value = line.split()[-1]
                try:
                    return float(value)
                except ValueError:
                    continue
    return 0.0


@pytest.mark.asyncio
async def test_middleware_successful_request(prometheus_middleware):
    """Test middleware with successful non-streaming request."""
    request = MagicMock()
    request.url.path = "/api/v1/status"
    request.method = "GET"

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    start_time = time.time()
    result = await prometheus_middleware.dispatch(request, call_next)
    end_time = time.time()

    assert result == response
    call_next.assert_called_once_with(request)

    # Check that request count was recorded
    total_requests = _get_metric_value_from_registry(
        'sky_apiserver_requests_total', {
            'path': '/api/v1/status',
            'method': 'GET',
            'status': '2xx'
        })
    assert total_requests == 1.0

    # Check that duration was recorded for non-streaming APIs
    duration_count = _get_metric_value_from_registry(
        'sky_apiserver_request_duration_seconds_count', {
            'path': '/api/v1/status',
            'method': 'GET',
            'status': '2xx'
        })
    assert duration_count == 1.0

    # Check that the duration sum is reasonable
    duration_sum = _get_metric_value_from_registry(
        'sky_apiserver_request_duration_seconds_sum', {
            'path': '/api/v1/status',
            'method': 'GET',
            'status': '2xx'
        })
    assert 0 <= duration_sum <= (end_time - start_time + 1)


@pytest.mark.asyncio
async def test_middleware_streaming_request(prometheus_middleware):
    """Test middleware with streaming API request."""
    request = MagicMock()
    request.url.path = "/api/v1/logs"
    request.method = "GET"

    response = MagicMock()
    response.status_code = 200

    call_next = AsyncMock(return_value=response)

    result = await prometheus_middleware.dispatch(request, call_next)

    assert result == response

    # Check that request count was recorded
    total_requests = _get_metric_value_from_registry(
        'sky_apiserver_requests_total', {
            'path': '/api/v1/logs',
            'method': 'GET',
            'status': '2xx'
        })
    assert total_requests == 1.0

    # Check that duration was NOT recorded for streaming APIs
    duration_count = _get_metric_value_from_registry(
        'sky_apiserver_request_duration_seconds_count', {
            'path': '/api/v1/logs',
            'method': 'GET',
            'status': '2xx'
        })
    assert duration_count == 0.0


@pytest.mark.asyncio
async def test_middleware_exception_handling(prometheus_middleware):
    """Test middleware handles exceptions properly."""
    request = MagicMock()
    request.url.path = "/api/v1/failing"
    request.method = "POST"

    call_next = AsyncMock(side_effect=Exception("Test error"))

    with pytest.raises(Exception, match="Test error"):
        await prometheus_middleware.dispatch(request, call_next)

    # Check that 5xx metric was recorded even with exception
    total_requests = _get_metric_value_from_registry(
        'sky_apiserver_requests_total', {
            'path': '/api/v1/failing',
            'method': 'POST',
            'status': '5xx'
        })
    assert total_requests == 1.0


@pytest.mark.asyncio
async def test_middleware_different_status_codes(prometheus_middleware):
    """Test middleware with different HTTP status codes."""
    test_cases = [
        (404, "4xx"),
        (500, "5xx"),
        (201, "2xx"),
    ]

    for status_code, expected_group in test_cases:
        request = MagicMock()
        request.url.path = f"/test/{status_code}"
        request.method = "GET"

        response = MagicMock()
        response.status_code = status_code

        call_next = AsyncMock(return_value=response)

        await prometheus_middleware.dispatch(request, call_next)

        # Verify the correct status group was recorded
        total_requests = _get_metric_value_from_registry(
            'sky_apiserver_requests_total', {
                'path': f'/test/{status_code}',
                'method': 'GET',
                'status': expected_group
            })
        assert total_requests == 1.0


@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Clean up metrics after each test to avoid interference."""
    yield
    # Clear all metrics after each test
    metrics_utils.SKY_APISERVER_REQUESTS_TOTAL.clear()
    metrics_utils.SKY_APISERVER_REQUEST_DURATION_SECONDS.clear()
