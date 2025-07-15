"""Temporary storage context manager."""

import contextlib
import contextvars
import functools
import os
import tempfile
import typing
from typing import Any, Callable, Iterator, Optional, TypeVar

_TEMP_DIR: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'temp_store_dir', default=None)


@contextlib.contextmanager
def tempdir() -> Iterator[str]:
    """Context manager for temporary directory of current context.

    This wraps tempfile.TemporaryDirectory and makes the temp dir available
    throughout the context, eliminating the need to pass the temp dir to
    the nested functions that need it.

    This context manager is nestable - nested calls will create new temp dirs
    and restore the previous temp dir when exiting.
    """
    with tempfile.TemporaryDirectory(prefix='sky-tmp') as temp_dir:
        token = _TEMP_DIR.set(temp_dir)
        try:
            yield temp_dir
        finally:
            _TEMP_DIR.reset(token)


# Keep the function signature same as tempfile.mkdtemp.
# pylint: disable=redefined-builtin
def mkdtemp(suffix: Optional[str] = None,
            prefix: Optional[str] = None,
            dir: Optional[str] = None) -> str:
    """Create a temporary directory in the temp dir of current context.

    The directory will be cleaned when the current context exits.
    If there is no temp dir in current context, this function is equivalent to
    tempfile.mkdtemp.
    """
    context_temp_dir = _TEMP_DIR.get()

    if context_temp_dir is not None and dir is None:
        dir = context_temp_dir
    elif context_temp_dir is not None and dir is not None:
        dir = os.path.join(context_temp_dir, dir)
        os.makedirs(dir, exist_ok=True)

    return tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)


F = TypeVar('F', bound=Callable[..., Any])


def with_tempdir(func: F) -> F:
    """Decorator that wraps a function call with tempdir() context manager.

    Refer to `tempdir` for more details.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with tempdir():
            return func(*args, **kwargs)

    return typing.cast(F, wrapper)
