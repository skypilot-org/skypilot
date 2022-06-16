import contextlib
from typing import Optional

from sky_callback import SkyCallback

_sky_callback = None


def init(global_rank: int = 0, enable: bool = True) -> None:
    if global_rank == 0 and enable:
        global _sky_callback
        _sky_callback = SkyCallback()


def config(total_train_steps: Optional[int] = None) -> None:
    if _sky_callback is not None:
        _sky_callback.config(total_train_steps=total_train_steps)


def on_step_begin():
    if _sky_callback is not None:
        _sky_callback.on_train_step_begin()


def on_step_end():
    if _sky_callback is not None:
        _sky_callback.on_train_step_end()


@contextlib.contextmanager
def train_step():
    on_step_begin()
    yield
    on_step_end()


# FIXME: Needs a better name.
class step:

    def __init__(self, iterable):
        self.iterable = iterable

    def __iter__(self):
        # Inlining for speed optimization.
        iterable = self.iterable
        if _sky_callback is None:
            for obj in iterable:
                yield obj
        else:
            for obj in iterable:
                _sky_callback.on_train_step_begin()
                yield obj
                _sky_callback.on_train_step_end()
