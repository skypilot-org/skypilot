"""Utility functions for UX."""
import contextlib
import functools
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
    sys.tracebacklimit = 1
    yield
    sys.tracebacklimit = original_tracelimit


def print_exception_no_traceback_decorator(func):
    """A decorator that prints out an exception without traceback.

    It makes print_exception_no_traceback() a decorator for a function.

    Example usage:

        @print_exception_no_traceback_decorator
        def func():
            raise Error('...')
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with print_exception_no_traceback():
            return func(*args, **kwargs)

    return wrapper
