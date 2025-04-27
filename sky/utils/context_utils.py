"""Utilities for SkyPilot context."""
import asyncio
import functools
import multiprocessing
import shutil
import subprocess
import sys
from typing import Callable, Optional, TextIO

from sky import sky_logging
from sky.utils import context
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

def pipe_and_wait_process(ctx: context.Context,
                          proc: subprocess.Popen,
                          poll_interval: float = 0.5,
                          cancel_callback: Optional[Callable[[], None]] = None):
    """Wait for the process to finish or cancel it if the context is cancelled.

    Args:
        proc: The process to wait for.
        poll_interval: The interval to poll the process.
        cancel_callback: The callback to call if the context is cancelled.
    """
    def pipe(stream: TextIO, output_stream: TextIO):
        logger.info(f'Piping {stream} to {output_stream}')
        while True:
            line = stream.readline()
            if line:
                output_stream.write(line)
            else:
                break

    # Threads are lazily created, so no harm if stderr is None
    with multiprocessing.pool.ThreadPool(processes=2) as pool:
        # Context will be lost in the new thread, capture current output stream
        # and pass it to the new thread directly.
        futs = []
        logger.info("Start piping")
        futs.append(
            pool.apply_async(pipe,
                             (proc.stdout, ctx.output_stream(sys.stdout))))
        if proc.stderr is not None:
            futs.append(
                pool.apply_async(pipe,
                                 (proc.stderr, ctx.output_stream(sys.stderr))))
        try:
            wait_process(ctx,
                         proc,
                         poll_interval=poll_interval,
                         cancel_callback=cancel_callback)
        finally:
            for fut in futs:
                fut.wait()


def wait_process(ctx: context.Context,
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
            subprocess_utils.kill_process_with_grace_period(proc)
            raise asyncio.CancelledError()
        try:
            proc.wait(poll_interval)
        except subprocess.TimeoutExpired:
            pass
        else:
            # Process exited
            break


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
        ctx = context.get()
        if ctx is not None and ctx.is_canceled():
            raise asyncio.CancelledError(
                f'Function {func.__name__} cancelled before execution')
        return func(*args, **kwargs)

    return wrapper
