"""Asyncio utilities."""

import asyncio
import functools


def shield(func):
    """Shield the decorated async function from cancellation.

    Note that filelock.AsyncFileLock is not cancellation safe, thus the
    function calls filelock.AsyncFileLock must be shielded.
    """

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        return await asyncio.shield(func(*args, **kwargs))

    return async_wrapper
