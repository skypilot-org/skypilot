"""SkyPilot context for threads and coroutines."""

import asyncio
import contextvars
from typing import Optional


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
