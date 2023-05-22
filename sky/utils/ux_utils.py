"""Utility functions for UX."""
import contextlib
import sys

import rich.console as rich_console

from sky.utils import env_options

console = rich_console.Console()


def console_newline():
    """Print a newline to the console using rich.

    Useful when catching exceptions inside console.status()
    """
    console.print()


@contextlib.contextmanager
def print_exception_no_traceback():
    """A context manager that prints out an exception without traceback.

    Mainly for UX: user-facing errors, e.g., ValueError, should suppress long
    tracebacks.

    If SKYPILOT_DEBUG environment variable is set, this context manager is a
    no-op and the full traceback will be shown.

    Example usage:

        with print_exception_no_traceback():
            if error():
                raise ValueError('...')
    """
    if env_options.Options.SHOW_DEBUG_INFO.get():
        # When SKYPILOT_DEBUG is set, show the full traceback
        yield
    else:
        original_tracelimit = getattr(sys, 'tracebacklimit', 1000)
        sys.tracebacklimit = 0
        yield
        sys.tracebacklimit = original_tracelimit
