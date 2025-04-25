"""Utilities for SkyPilot context."""
import asyncio
import subprocess
from typing import Callable, Optional

from sky.utils import context
from sky.utils import subprocess_utils


def wait_or_cancel_process(ctx: context.Context,
                           proc: subprocess.Popen,
                           poll_interval: float = 0.5,
                           cancel_callback: Optional[Callable[[],
                                                              None]] = None):
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
            # function gracefully handles the case where the process is already
            # terminated.
            subprocess_utils.kill_process_with_grace_period(proc)
            raise asyncio.CancelledError()
        try:
            proc.wait(poll_interval)
        except subprocess.TimeoutExpired:
            pass
        else:
            # Process exited
            break
