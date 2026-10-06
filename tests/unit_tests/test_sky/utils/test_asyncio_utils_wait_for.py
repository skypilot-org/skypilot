"""Unit tests for sky.utils.asyncio_utils.wait_for.

`asyncio.wait_for` on Python 3.9 to 3.11 swallows a cancel of the calling task
that lands as the inner future completes. `asyncio_utils.wait_for` keeps the
stdlib contract without that race.
"""

import asyncio
from typing import List, Optional

import pytest

from sky.utils import asyncio_utils


@pytest.mark.asyncio
async def test_returns_inner_result():

    async def inner():
        await asyncio.sleep(0)
        return 42

    assert await asyncio_utils.wait_for(inner(), timeout=5) == 42
    assert await asyncio_utils.wait_for(inner(), timeout=None) == 42


@pytest.mark.asyncio
async def test_accepts_an_executor_future():
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, lambda: 'from a thread')
    assert await asyncio_utils.wait_for(fut, timeout=5) == 'from a thread'


@pytest.mark.asyncio
async def test_propagates_inner_exception():

    async def inner():
        await asyncio.sleep(0)
        raise ValueError('boom')

    with pytest.raises(ValueError, match='boom'):
        await asyncio_utils.wait_for(inner(), timeout=5)


@pytest.mark.asyncio
async def test_timeout_cancels_inner_and_waits_for_it():
    cleaned_up: List[bool] = []

    async def inner():
        try:
            await asyncio.sleep(10)
        finally:
            # Cleanup that itself awaits: wait_for must not return before it
            # finishes.
            await asyncio.sleep(0)
            cleaned_up.append(True)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio_utils.wait_for(inner(), timeout=0.05)
    assert cleaned_up == [True]


@pytest.mark.asyncio
async def test_timeout_returns_what_the_inner_finished_with():
    """Like asyncio.wait_for: an inner that ends without being cancelled
    after the deadline still delivers its outcome."""

    async def inner():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return 'finished anyway'

    assert await asyncio_utils.wait_for(inner(),
                                        timeout=0.05) == 'finished anyway'


@pytest.mark.asyncio
async def test_caller_cancel_cancels_inner_and_waits_for_it():
    started = asyncio.Event()
    cleaned_up: List[bool] = []

    async def inner():
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0)
            cleaned_up.append(True)

    caller = asyncio.ensure_future(asyncio_utils.wait_for(inner(), timeout=10))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleaned_up == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('hops', range(6))
async def test_cancel_landing_as_the_inner_completes_stops_the_loop(hops):
    """A loop bounded by wait_for stops when cancelled, every time.

    The managed jobs controller's shape: a loop bounds each blocking status
    fetch (`asyncio.to_thread`) with wait_for, and another task cancels the
    loop once. The fetch thread queues the cancel to run `hops` event-loop
    iterations after it returns. With asyncio.wait_for on Python 3.9 to 3.11,
    1 to 3 hops land the cancel as the inner completes, and the cancel is
    lost: the loop keeps running.
    """
    loop = asyncio.get_running_loop()
    task: Optional[asyncio.Task] = None
    fetches = 0

    def cancel_after(remaining: int) -> None:
        if remaining:
            loop.call_soon(cancel_after, remaining - 1)
        else:
            assert task is not None
            task.cancel()

    def fetch() -> str:
        nonlocal fetches
        fetches += 1
        if fetches == 2:
            loop.call_soon_threadsafe(cancel_after, hops)
        return 'RUNNING'

    async def poll_loop():
        while True:
            await asyncio.sleep(0.001)
            await asyncio_utils.wait_for(asyncio.to_thread(fetch), timeout=5)

    task = asyncio.ensure_future(poll_loop())
    done, _ = await asyncio.wait({task}, timeout=2)
    if task not in done:
        while not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.1)
        pytest.fail(f'The loop kept running after task.cancel() ({fetches} '
                    'fetches); wait_for swallowed the cancel.')
    assert task.cancelled()
