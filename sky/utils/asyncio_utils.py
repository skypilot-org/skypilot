"""Asyncio utilities."""

import asyncio
import functools
import random
from typing import Set

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_background_tasks: Set[asyncio.Task] = set()

# Upper bound on the random delay applied before a periodic housekeeping
# daemon runs its first pass. See sleep_startup_jitter().
DEFAULT_STARTUP_JITTER_SECONDS = 1800


async def sleep_startup_jitter(
        name: str, max_seconds: float = DEFAULT_STARTUP_JITTER_SECONDS) -> None:
    """Sleep a random offset before a periodic daemon's first pass.

    Periodic housekeeping daemons are written as::

        while True:
            do_the_pass()
            await asyncio.sleep(interval)

    so the first pass runs at t=0 of the process. That is fine for a single
    server, but it means every API server that boots at the same moment also
    starts its housekeeping at the same moment, and the passes stay aligned
    from then on: a fleet that is restarted together (a rolling upgrade, a
    node drain, an eviction) has its daily retention sweeps permanently
    phase-locked, so they all land on the shared database at once.

    Sleeping a random offset in [0, max_seconds) before entering the loop
    spreads those first passes out, and because the interval is unchanged the
    offset persists for the lifetime of the process.

    Only safe for work that is pure housekeeping -- nothing whose result is
    needed for the server to start serving correctly.
    """
    if max_seconds <= 0:
        return
    delay = random.uniform(0, max_seconds)
    logger.debug(
        '%s: delaying first pass by %.0fs to avoid a synchronized '
        'startup burst across servers', name, delay)
    await asyncio.sleep(delay)


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
