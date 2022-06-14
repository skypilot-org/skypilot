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


@contextlib.contextmanager
def train_step():
    if _sky_callback is not None:
        _sky_callback.on_train_step_begin()
    yield
    if _sky_callback is not None:
        _sky_callback.on_train_step_end()
