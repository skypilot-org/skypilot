"""SkyPilot context for threads and coroutines."""

import asyncio
from collections.abc import Mapping
import contextvars
import copy
import functools
import inspect
import os
import pathlib
import subprocess
import sys
from typing import (Callable, Dict, Iterator, MutableMapping, Optional, TextIO,
                    TYPE_CHECKING, TypeVar)

from typing_extensions import ParamSpec

if TYPE_CHECKING:
    from sky.skypilot_config import ConfigContext


class Context(object):
    """SkyPilot typed context vars for threads and coroutines.

    This is a wrapper around `contextvars.ContextVar` that provides a typed
    interface for the SkyPilot specific context variables that can be accessed
    at any layer of the call stack. ContextVar is coroutine local, an empty
    Context will be initialized for each coroutine when it is created.

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
        self.env_overrides = {}
        self.config_context = None

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
        if original_log_handle is not None:
            original_log_handle.close()
        return original_log_file

    def output_stream(self, fallback: TextIO) -> TextIO:
        if self._log_file_handle is None:
            return fallback
        else:
            return self._log_file_handle

    def override_envs(self, envs: Dict[str, str]):
        for k, v in envs.items():
            self.env_overrides[k] = v

    def cleanup(self):
        """Clean up the context."""
        if self._log_file_handle is not None:
            self._log_file_handle.close()
            self._log_file_handle = None

    def copy(self) -> 'Context':
        """Create a copy of the context.

        Changes to the current context after this call will not affect the copy.
        The new context will get its own handle/fd for the log file.
        The new context will get an independent copy of the env var overrides.
        The new context will get an independent copy of the config context.
        Cancellation of the current context will not be propagated to the copy.
        """
        new_context = Context()
        new_context.redirect_log(self._log_file)
        new_context.env_overrides = self.env_overrides.copy()
        new_context.config_context = copy.deepcopy(self.config_context)
        return new_context


_CONTEXT = contextvars.ContextVar[Optional[Context]]('sky_context',
                                                     default=None)


def get() -> Optional[Context]:
    """Get the current SkyPilot context.

    If the context is not initialized, get() will return None. This helps
    sync code to check whether it runs in a cancellable context and avoid
    polling the cancellation event if it is not.
    """
    return _CONTEXT.get()


class ContextualEnviron(MutableMapping[str, str]):
    """Environment variables wrapper with contextual overrides.

    An instance of ContextualEnviron will typically be used to replace
    os.environ to make the envron access of current process contextual
    aware.

    Behavior of spawning a subprocess:
    - The contexual overrides will not be applied to the subprocess by
      default.
    - When using env=os.environ to pass the environment variables to the
      subprocess explicitly. The subprocess will inherit the contextual
      environment variables at the time of the spawn, that is, it will not
      see the updates to the environment variables after the spawn. Also,
      os.environ of the subprocess will not be a ContextualEnviron unless
      the subprocess hijacks os.environ explicitly.
    - Optionally, context.Popen() can be used to automatically pass
      os.environ with overrides to subprocess.


    Example:
    1. Parent process:
       # Hijack os.environ to be a ContextualEnviron
       os.environ = ContextualEnviron(os.environ)
       ctx = context.get()
       ctx.override_envs({'FOO': 'BAR1'})
       proc = subprocess.Popen(..., env=os.environ)
       # Or use context.Popen instead
       # proc = context.Popen(...)
       ctx.override_envs({'FOO': 'BAR2'})
    2. Subprocess:
       assert os.environ['FOO'] == 'BAR1'
       ctx = context.get()
       # Override the contextual env var in the subprocess does not take
       # effect since the os.environ is not hijacked.
       ctx.override_envs({'FOO': 'BAR3'})
       assert os.environ['FOO'] == 'BAR1'
    """

    def __init__(self, environ: 'os._Environ[str]') -> None:
        self._environ = environ

    def __getitem__(self, key: str) -> str:
        ctx = get()
        if ctx is not None:
            if key in ctx.env_overrides:
                value = ctx.env_overrides[key]
                # None is used to indicate that the key is deleted in the
                # context.
                if value is None:
                    raise KeyError(key)
                return value
        return self._environ[key]

    def __iter__(self) -> Iterator[str]:

        def iter_from_context(ctx: Context) -> Iterator[str]:
            deleted_keys = set()
            for key, value in ctx.env_overrides.items():
                if value is None:
                    deleted_keys.add(key)
                yield key
            for key in self._environ:
                # Deduplicate the keys
                if key not in ctx.env_overrides and key not in deleted_keys:
                    yield key

        ctx = get()
        if ctx is not None:
            return iter_from_context(ctx)
        else:
            return self._environ.__iter__()

    def __len__(self) -> int:
        return len(dict(self))

    def __setitem__(self, key: str, value: str) -> None:
        ctx = get()
        if ctx is not None:
            ctx.env_overrides[key] = value
        else:
            self._environ.__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        ctx = get()
        if ctx is not None:
            if key in ctx.env_overrides:
                del ctx.env_overrides[key]
            elif key in self._environ:
                # If the key is not set in the context but set in the environ
                # of the process, we mark it as deleted in the context by
                # setting the value to None.
                ctx.env_overrides[key] = None
            else:
                # The key is not set in the context nor the process.
                raise KeyError(key)
        else:
            self._environ.__delitem__(key)

    def __repr__(self) -> str:
        # Adapted from os._Environ.__repr__
        formatted_items = ', '.join(
            f'{key!r}: {value!r}' for key, value in self.items())
        return f'ctx_environ({{{formatted_items}}})'

    def copy(self) -> Dict[str, str]:
        copied = self._environ.copy()
        ctx = get()
        if ctx is not None:
            for key in ctx.env_overrides:
                if ctx.env_overrides[key] is None:
                    copied.pop(key)
                else:
                    copied[key] = ctx.env_overrides[key]
        return copied

    def setdefault(self, key: str, default: str) -> str:
        return self._environ.setdefault(key, default)

    def __ior__(self, other):
        if not isinstance(other, Mapping):
            return NotImplemented
        self.update(other)
        return self

    def __or__(self, other):
        if not isinstance(other, Mapping):
            return NotImplemented
        new = dict(self)
        new.update(other)
        return new

    def __ror__(self, other):
        if not isinstance(other, Mapping):
            return NotImplemented
        new = dict(other)
        new.update(self)
        return new


class Popen(subprocess.Popen):

    def __init__(self, *args, **kwargs):
        env = kwargs.pop('env', None)
        if env is None:
            # Pass a copy of current context.environ to avoid race condition
            # when the context is updated after the Popen is created.
            env = os.environ.copy()
        super().__init__(*args, env=env, **kwargs)


P = ParamSpec('P')
T = TypeVar('T')


def contextual(func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to initialize a context before executing the function.

    If a context is already initialized, this decorator will create a new
    context that inherits the values from the existing context.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        original_ctx = get()
        initialize(original_ctx)
        ctx = get()
        cleanup_after_await = False

        def cleanup():
            try:
                if ctx is not None:
                    ctx.cleanup()
            finally:
                # Note: _CONTEXT.reset() is not reliable - may fail with
                # ValueError: <Token ... at ...> was created in a different
                # Context
                # We must make sure this happens because otherwise we may try to
                # write to the wrong log.
                _CONTEXT.set(original_ctx)

        # There are two cases:
        # 1. The function is synchronous (that is, return type is not awaitable)
        #    In this case, we use a finally block to cleanup the context.
        # 2. The function is asynchronous (that is, return type is awaitable)
        #    In this case, we need to construct an async def wrapper and await
        #    the value, then call the cleanup function in the finally block.

        async def await_with_cleanup(awaitable):
            try:
                return await awaitable
            finally:
                cleanup()

        try:
            ret = func(*args, **kwargs)
            if inspect.isawaitable(ret):
                cleanup_after_await = True
                return await_with_cleanup(ret)
            else:
                return ret
        finally:
            if not cleanup_after_await:
                cleanup()

    return wrapper


def initialize(base_context: Optional[Context] = None) -> None:
    """Initialize the current SkyPilot context."""
    new_context = base_context.copy() if base_context is not None else Context()
    _CONTEXT.set(new_context)


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
