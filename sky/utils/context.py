"""SkyPilot context for threads and coroutines."""

import asyncio
import contextvars
import functools
import logging
import sys
from typing import Dict, Optional, TextIO


class Context(object):
    """SkyPilot typed context vars for threads and coroutines.
    
    This is a wrapper around `contextvars.ContextVar` that provides a typed
    interface for the SkyPilot specific context variables that can be accessed
    at any layer of the call stack. ContextVar is coroutine local, an empty
    Context will be intialized for each coroutine when it is created.

    Adding a new context variable for a new feature is as simple as:
    1. Add a new instance variable to the Context class.
    2. (Optional) Add new accessor methods if the variable should be protected. 

    To propagate the context to a new thread/coroutine, use
    `contextvars.copy_context()`.

    Example:
        import asyncio
        import contextvars
        import time
        from sky.utils import context

        def sync_task():
            while True:
                if context.get().is_canceled():
                    break
                time.sleep(1)
            
        async def fastapi_handler():
            # context.initialize() has been called in lifespan
            ctx = contextvars.copy_context()
            # asyncio.to_thread copies current context implicitly
            task = asyncio.to_thread(sync_task)
            # Or explicitly:
            # loop = asyncio.get_running_loop()
            # ctx = contextvars.copy_context()
            # task = loop.run_in_executor(None, ctx.run, sync_task)
            await asyncio.sleep(1)
            context.get().cancel()
            await task
    """

    def __init__(self):
        self._canceled = asyncio.Event()
        self.log_handler = None

    def cancel(self):
        self._canceled.set()

    def is_canceled(self):
        return self._canceled.is_set()


_CONTEXT = contextvars.ContextVar('sky_context', default=None)


def get() -> Optional[Context]:
    """Get the current SkyPilot context.
    
    If the context is not initialized, get() will return None. This helps
    sync code to check whether it runs in a cancellable context and avoid
    polling the cancellation event if it is not.
    """
    return _CONTEXT.get()


def initialize():
    """Initialize the current SkyPilot context."""
    _CONTEXT.set(Context())


def cancellation_guard(func):
    """Decorator to make a synchronous function cancellable via context.
    
    Guards the function execution by checking context.is_canceled() before
    executing the function and raises asyncio.CancelledError if the context
    is already cancelled.

    This basically mimics the behavior of asyncio, which checks coroutine
    cancelled in await call.
    
    Args:
        func: The function to be decorated.
        
    Returns:
        The wrapped function that checks cancellation before execution.
        
    Raises:
        asyncio.CancelledError: If the context is cancelled before execution.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ctx = get()
        if ctx is not None and ctx.is_canceled():
            raise asyncio.CancelledError(
                f'Function {func.__name__} cancelled before execution')
        return func(*args, **kwargs)

    return wrapper
