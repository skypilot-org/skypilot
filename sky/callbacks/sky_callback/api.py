import collections
import contextlib
import os
from typing import Optional

from sky_callback import base

_DISABLE_CALLBACK = os.environ.get('SKY_DISABLE_CALLBACK', 'False').lower() in ('true', '1')
_SKY_CALLBACK = None


def init(global_rank: int = 0, log_dir: Optional[str] = None, total_steps: Optional[int] = None) -> None:
    if global_rank == 0 and not _DISABLE_CALLBACK:
        global _SKY_CALLBACK
        _SKY_CALLBACK = base.BaseCallback(log_dir=log_dir, total_steps=total_steps)


def on_step_begin() -> None:
    if _SKY_CALLBACK is not None:
        _SKY_CALLBACK.on_step_begin()


def on_step_end() -> None:
    if _SKY_CALLBACK is not None:
        _SKY_CALLBACK.on_step_end()


@contextlib.contextmanager
def step():
    on_step_begin()
    yield
    on_step_end()


class timer:

    def __init__(self, iterable: collections.Iterable) -> None:
        self._iterable = iterable

    def __iter__(self):
        # Inlining for speed optimization.
        iterable = self._iterable
        if _SKY_CALLBACK is None:
            for obj in iterable:
                yield obj
        else:
            for obj in iterable:
                _SKY_CALLBACK.on_step_begin()
                yield obj
                _SKY_CALLBACK.on_step_end()
