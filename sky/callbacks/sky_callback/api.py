import contextlib
from typing import Optional

from sky_callback import base

_SKY_CALLBACK = None


def init(global_rank: int = 0, enable: bool = True) -> None:
    if global_rank == 0 and enable:
        global _SKY_CALLBACK
        _SKY_CALLBACK = base.BaseCallback()


def config(total_train_steps: Optional[int] = None) -> None:
    if _SKY_CALLBACK is not None:
        _SKY_CALLBACK.config(total_train_steps=total_train_steps)


def on_step_begin() -> None:
    if _SKY_CALLBACK is not None:
        _SKY_CALLBACK.on_step_begin()


def on_step_end() -> None:
    if _SKY_CALLBACK is not None:
        _SKY_CALLBACK.on_step_end()


@contextlib.contextmanager
def train_step():
    on_step_begin()
    yield
    on_step_end()


# FIXME: Needs a better name.
# FIXME: Fix type annotation.
class step:

    def __init__(self, iterable) -> None:
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
