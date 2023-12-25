"""Utility functions for UX."""
import contextlib
import sys
import traceback
from typing import Callable

import rich.console as rich_console

from sky import sky_logging
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import ux_utils

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


@contextlib.contextmanager
def enable_traceback():
    """Revert the effect of print_exception_no_traceback().

    This is used for usage_lib to collect the full traceback.
    """
    original_tracelimit = getattr(sys, 'tracebacklimit', 1000)
    sys.tracebacklimit = 1000
    yield
    sys.tracebacklimit = original_tracelimit


class RedirectOutputForProcess:
    """Redirect stdout and stderr to a file.

    This class enabled output redirect for multiprocessing.Process.
    Example usage:

    p = multiprocessing.Process(
        target=RedirectOutputForProcess(func, file_name).run, args=...)

    This is equal to:

    p = multiprocessing.Process(target=func, args=...)

    Plus redirect all stdout/stderr to file_name.
    """

    def __init__(self, func: Callable, file: str) -> None:
        self.func = func
        self.file = file

    def run(self, *args, **kwargs):
        with open(self.file, 'w') as f:
            sys.stdout = f
            sys.stderr = f
            # reconfigure logger since the logger is initialized before
            # with previous stdout/stderr
            sky_logging.reload_logger()
            logger = sky_logging.init_logger(__name__)
            # The subprocess_util.run('sky status') inside
            # sky.execution::_execute cannot be redirect, since we cannot
            # directly operate on the stdout/stderr of the subprocess. This
            # is because some code in skypilot will specify the stdout/stderr
            # of the subprocess.
            try:
                self.func(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Failed to run {self.func.__name__}. '
                             f'Details: {common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback:\n{traceback.format_exc()}')
                raise
