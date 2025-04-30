"""SkyPilot context for threads and coroutines."""

import asyncio
import contextvars
import os
import pathlib
import sys
import typing
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
        self._log_file = None
        self._log_file_handle = None
        self._env_overrides = {}

    def cancel(self):
        """Cancel the context."""
        self._canceled.set()

    def is_canceled(self):
        """Check if the context is canceled."""
        return self._canceled.is_set()

    def redirect_log(
            self, log_file: Optional[pathlib.Path]) -> Optional[pathlib.Path]:
        """Redirect the stdout and stderr of current context to a file.

        Args:
            log_file: The log file to redirect to. If None, the stdout and
            stderr will be restored to the original streams.

        Returns:
            The old log file, or None if the stdout and stderr were not
            redirected.
        """
        original_log_file = self._log_file
        original_log_handle = self._log_file_handle
        if log_file is None:
            self._log_file_handle = None
        else:
            self._log_file_handle = open(log_file, 'a', encoding='utf-8')
        self._log_file = log_file
        if original_log_file is not None:
            original_log_handle.close()
        return original_log_file

    def output_stream(self, fallback: TextIO) -> TextIO:
        if self._log_file_handle is None:
            return fallback
        else:
            return self._log_file_handle

    def override_envs(self, envs: Dict[str, str]):
        for k, v in envs.items():
            self._env_overrides[k] = v

    def getenv(self, key: str, fallback: Optional[str] = None):
        if key in self._env_overrides:
            return self._env_overrides[key]
        return os.getenv(key, fallback)


_CONTEXT = contextvars.ContextVar('sky_context', default=None)


def get() -> Optional[Context]:
    """Get the current SkyPilot context.

    If the context is not initialized, get() will return None. This helps
    sync code to check whether it runs in a cancellable context and avoid
    polling the cancellation event if it is not.
    """
    return _CONTEXT.get()


@typing.overload
def getenv(env_var: str, default: str) -> str:
    ...


@typing.overload
def getenv(env_var: str, default: None = None) -> Optional[str]:
    ...


def getenv(env_var: str, default: Optional[str] = None) -> Optional[str]:
    ctx = get()
    if ctx is not None:
        return ctx.getenv(env_var, default)
    return os.getenv(env_var, default)


def initialize():
    """Initialize the current SkyPilot context."""
    _CONTEXT.set(Context())


class _ContextualStream:
    """A base class for streams that are contextually aware.

    This class implements the TextIO interface via __getattr__ to delegate
    attribute access to the original or contextual stream.
    """
    _original_stream: TextIO

    def __init__(self, original_stream: TextIO):
        self._original_stream = original_stream

    def __getattr__(self, attr: str):
        return getattr(self._active_stream(), attr)

    def _active_stream(self) -> TextIO:
        ctx = get()
        if ctx is None:
            return self._original_stream
        return ctx.output_stream(self._original_stream)


class Stdout(_ContextualStream):

    def __init__(self):
        super().__init__(sys.stdout)


class Stderr(_ContextualStream):

    def __init__(self):
        super().__init__(sys.stderr)
