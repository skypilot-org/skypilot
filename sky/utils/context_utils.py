"""Utilities for SkyPilot context."""
import asyncio
import concurrent.futures
import contextvars
import functools
import multiprocessing
import os
import select
import subprocess
import sys
import time
import typing
from typing import Any, Callable, IO, Optional, Tuple, TypeVar

from typing_extensions import ParamSpec

from sky import sky_logging
from sky.utils import context
from sky.utils import subprocess_utils

StreamHandler = Callable[[IO[Any], IO[Any]], str]
PASSTHROUGH_FLUSH_INTERVAL_SECONDS = 0.5

logger = sky_logging.init_logger(__name__)


# TODO(aylei): call hijack_sys_attrs() proactivly in module init at server-side
# once we have context widely adopted.
def hijack_sys_attrs():
    """hijack system attributes to be context aware

    This function should be called at the very beginning of the processes
    that might use sky.utils.context.
    """
    # Modify stdout and stderr of unvicorn process to be contextually aware,
    # use setattr to bypass the TextIO type check.
    setattr(sys, 'stdout', context.Stdout())
    setattr(sys, 'stderr', context.Stderr())
    # Reload logger to apply latest stdout and stderr.
    sky_logging.reload_logger()
    # Hijack os.environ with ContextualEnviron to make env variables
    # contextually aware.
    setattr(os, 'environ', context.ContextualEnviron(os.environ))
    # Hijack subprocess.Popen to pass the contextual environ to subprocess
    # by default.
    setattr(subprocess, 'Popen', context.Popen)


def passthrough_stream_handler(in_stream: IO[Any], out_stream: IO[Any]) -> str:
    """Passthrough the stream from the process to the output stream"""
    last_flush_time = time.time()
    has_unflushed_content = False

    # Use poll() with timeout instead of readline() to avoid blocking.
    # readline() blocks until a newline is available, which can take minutes
    # for tasks that emit logs infrequently (e.g. jupyter lab server).
    # While readline() is blocked, the timing code never executes, so buffered
    # logs never get flushed. poll() with timeout allows us to periodically
    # flush even when no new data is available, ensuring logs appear promptly.
    fd = in_stream.fileno()
    poller = select.poll()
    poller.register(fd, select.POLLIN)

    # Timeout in milliseconds for poll()
    poll_timeout_ms = int(PASSTHROUGH_FLUSH_INTERVAL_SECONDS * 1000)

    while True:
        # Poll with timeout - returns when data available or timeout
        events = poller.poll(poll_timeout_ms)

        current_time = time.time()

        if events:
            # Data is available, read a chunk
            chunk = os.read(fd, 4096)  # Read up to 4KB
            if not chunk:
                break  # EOF
            out_stream.write(chunk.decode('utf-8', errors='replace'))
            has_unflushed_content = True

        # Flush only if we have unflushed content and timeout reached
        if (has_unflushed_content and current_time - last_flush_time >=
                PASSTHROUGH_FLUSH_INTERVAL_SECONDS):
            out_stream.flush()
            last_flush_time = current_time
            has_unflushed_content = False

    poller.unregister(fd)
    # Final flush to ensure all data is written
    if has_unflushed_content:
        out_stream.flush()

    return ''


def pipe_and_wait_process(
        ctx: context.SkyPilotContext,
        proc: subprocess.Popen,
        poll_interval: float = 0.5,
        cancel_callback: Optional[Callable[[], None]] = None,
        stdout_stream_handler: Optional[StreamHandler] = None,
        stderr_stream_handler: Optional[StreamHandler] = None
) -> Tuple[str, str]:
    """Wait for the process to finish or cancel it if the context is cancelled.

    Args:
        proc: The process to wait for.
        poll_interval: The interval to poll the process.
        cancel_callback: The callback to call if the context is cancelled.
        stdout_stream_handler: An optional handler to handle the stdout stream,
            if None, the stdout stream will be passed through.
        stderr_stream_handler: An optional handler to handle the stderr stream,
            if None, the stderr stream will be passed through.
    """

    if stdout_stream_handler is None:
        stdout_stream_handler = passthrough_stream_handler
    if stderr_stream_handler is None:
        stderr_stream_handler = passthrough_stream_handler

    # Threads are lazily created, so no harm if stderr is None
    with multiprocessing.pool.ThreadPool(processes=2) as pool:
        # Context will be lost in the new thread, capture current output stream
        # and pass it to the new thread directly.
        stdout_fut = pool.apply_async(
            stdout_stream_handler, (proc.stdout, ctx.output_stream(sys.stdout)))
        stderr_fut = None
        if proc.stderr is not None:
            stderr_fut = pool.apply_async(
                stderr_stream_handler,
                (proc.stderr, ctx.output_stream(sys.stderr)))
        try:
            wait_process(ctx,
                         proc,
                         poll_interval=poll_interval,
                         cancel_callback=cancel_callback)
        finally:
            # Wait for the stream handler threads to exit when process is done
            # or cancelled
            stdout_fut.wait()
            if stderr_fut is not None:
                stderr_fut.wait()
        stdout = stdout_fut.get()
        stderr = ''
        if stderr_fut is not None:
            stderr = stderr_fut.get()
        return stdout, stderr


def wait_process(ctx: context.SkyPilotContext,
                 proc: subprocess.Popen,
                 poll_interval: float = 0.5,
                 cancel_callback: Optional[Callable[[], None]] = None):
    """Wait for the process to finish or cancel it if the context is cancelled.

    Args:
        proc: The process to wait for.
        poll_interval: The interval to poll the process.
        cancel_callback: The callback to call if the context is cancelled.
    """
    while True:
        if ctx.is_canceled():
            if cancel_callback is not None:
                cancel_callback()
            # Kill the process despite the caller's callback, the utility
            # function gracefully handles the case where the process is
            # already terminated.
            # Bash script typically does not forward SIGTERM to childs, thus
            # cannot be killed gracefully, shorten the grace period for faster
            # termination.
            subprocess_utils.kill_process_with_grace_period(proc,
                                                            grace_period=1)
            raise asyncio.CancelledError()
        try:
            proc.wait(poll_interval)
        except subprocess.TimeoutExpired:
            pass
        else:
            # Process exited
            break


F = TypeVar('F', bound=Callable[..., Any])


def cancellation_guard(func: F) -> F:
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
        ctx = context.get()
        if ctx is not None and ctx.is_canceled():
            raise asyncio.CancelledError(
                f'Function {func.__name__} cancelled before execution')
        return func(*args, **kwargs)

    return typing.cast(F, wrapper)


P = ParamSpec('P')
T = TypeVar('T')


# TODO(aylei): replace this with asyncio.to_thread once we drop support for
# python 3.8
def to_thread(func: Callable[P, T], /, *args: P.args,
              **kwargs: P.kwargs) -> 'asyncio.Future[T]':
    """Asynchronously run function *func* in a separate thread.

    This is same as asyncio.to_thread added in python 3.9
    """
    return to_thread_with_executor(None, func, *args, **kwargs)


def to_thread_with_executor(executor: Optional[concurrent.futures.Executor],
                            func: Callable[P, T], /, *args: P.args,
                            **kwargs: P.kwargs) -> 'asyncio.Future[T]':
    """Asynchronously run function *func* in a separate thread with
    a custom executor."""

    loop = asyncio.get_running_loop()
    pyctx = contextvars.copy_context()
    func_call: Callable[..., T] = functools.partial(pyctx.run, func, *args,
                                                    **kwargs)
    return loop.run_in_executor(executor, func_call)
