"""Utility functions for sky."""
import contextlib
import sys


@contextlib.contextmanager
def print_exception_no_traceback():
    """A context manager that prints out an exception without traceback.

    Mainly for UX: user-facing errors, e.g., ValueError, should suppress long
    tracebacks.

    Example usage:

        with print_exception_no_traceback():
            if error():
                raise ValueError('...')
    """
    sys.tracebacklimit = 0
    yield
    sys.tracebacklimit = 1000
