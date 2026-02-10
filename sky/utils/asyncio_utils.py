"""Asyncio utilities."""

import asyncio
import functools
from typing import Set

_background_tasks: Set[asyncio.Task] = set()


def shield(func):
    """Shield the decorated async function from cancellation.

    If the outer coroutine is cancelled, the inner decorated function
    will be protected from cancellation by asyncio.shield(). And we will
    maintain a reference to the the inner task to avoid it get GCed before
    it is done.

    For example, filelock.AsyncFileLock is not cancellation safe. The
    following code:

        async def fn_with_lock():
            async with filelock.AsyncFileLock('lock'):
                await asyncio.sleep(1)

    is equivalent to:

        # The lock may leak if the cancellation happens in
        # lock.acquire() or lock.release()
        async def fn_with_lock():
            lock = filelock.AsyncFileLock('lock')
            await lock.acquire()
            try:
                await asyncio.sleep(1)
            finally:
                await lock.release()

    Shilding the function ensures there is no cancellation will happen in the
    function, thus the lock will be released properly:

        @shield
        async def fn_with_lock()

    Note that the resource acquisition and release should usually be protected
    in one @shield block but not separately, e.g.:

        lock = filelock.AsyncFileLock('lock')

        @shield
        async def acquire():
            await lock.acquire()

        @shield
        async def release():
            await lock.release()

        async def fn_with_lock():
            await acquire()
            try:
                do_something()
            finally:
                await release()

    The above code is not safe because if `fn_with_lock` is cancelled,
    `acquire()` and `release()` will be executed in the background
    concurrently and causes race conditions.
    """

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        task = asyncio.create_task(func(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            _background_tasks.add(task)
            task.add_done_callback(lambda _: _background_tasks.discard(task))
            raise

    return async_wrapper
