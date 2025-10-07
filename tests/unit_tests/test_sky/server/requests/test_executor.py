"""Unit tests for sky.server.requests.executor module."""
import asyncio
import time
import unittest.mock as mock

import pytest

from sky.server.requests import executor
from sky.server.requests import requests as api_requests


def dummy_entrypoint():
    """Dummy entrypoint function for testing."""
    return 'success'


@pytest.mark.asyncio
async def test_execute_request_coroutine_ctx_cancelled_on_cancellation():
    """Test that context is always cancelled when execute_request_coroutine
    is cancelled."""
    # Create a mock request
    request = api_requests.Request(
        request_id='test-request-id',
        name='test-request-name',
        status=api_requests.RequestStatus.PENDING,
        created_at=time.time(),
        user_id='test-user-id',
        entrypoint=dummy_entrypoint,
        request_body=mock.Mock(),
    )

    # Mock the context and its methods
    mock_ctx = mock.Mock()
    mock_ctx.is_canceled.return_value = False

    with mock.patch('sky.utils.context.initialize'), \
         mock.patch('sky.utils.context.get', return_value=mock_ctx), \
         mock.patch('sky.server.requests.requests._add_or_update_request_no_lock'), \
         mock.patch('sky.server.requests.requests._get_request_no_lock',
                   return_value=request), \
         mock.patch('sky.utils.context_utils.to_thread') as mock_to_thread:

        # Create a future that will never complete naturally
        never_completing_future = asyncio.Future()
        mock_to_thread.return_value = never_completing_future

        task = asyncio.create_task(
            executor.execute_request_in_coroutine(request))

        await asyncio.sleep(0.1)
        task.cancel()
        await task
        # Verify the context is actually cancelled
        mock_ctx.cancel.assert_called()
