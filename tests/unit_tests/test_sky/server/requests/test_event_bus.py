"""Test the event bus."""

import asyncio

import pytest

from sky.server.requests import event_bus
from sky.server.requests import requests as requests_lib


@pytest.mark.asyncio
async def test_event_bus():
    callback_called = asyncio.Event()

    async def callback(request_id, status):
        callback_called.set()

    event_bus.start()
    await event_bus.subscribe(
        'test-request-1',
        event_bus.Callback(requests_lib.RequestStatus.SUCCEEDED, callback))
    print('sleep 2')
    await asyncio.sleep(2)
    print('sleep 2 done')
    await event_bus.publish('test-request-1',
                            requests_lib.RequestStatus.SUCCEEDED)
    print('sleep 2')
    await asyncio.sleep(2)
    print('sleep 2 done')
    assert callback_called.is_set()
