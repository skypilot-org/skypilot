"""Utility functions for UX."""
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
    original_tracelimit = getattr(sys, 'tracebacklimit', 1000)
    sys.tracebacklimit = 0
    yield
    sys.tracebacklimit = original_tracelimit
