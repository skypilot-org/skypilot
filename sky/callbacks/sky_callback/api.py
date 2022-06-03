from typing import Any, Optional

from sky_callback import SkyCallback


_sky_callback = None


def init(global_rank: int = 0) -> None:
    if global_rank == 0:
        global _sky_callback
        _sky_callback = SkyCallback()


def config(total_train_steps: Optional[int] = None) -> None:
    if _sky_callback is not None:
        _sky_callback.config(total_train_steps=total_train_steps)


class train_step:

    def __enter__(self) -> None:
        if _sky_callback is not None:
            _sky_callback.on_train_step_begin()

    def __exit__(self, type: Any, value: Any, traceback: Any) -> None:
        if _sky_callback is not None:
            _sky_callback.on_train_step_end()
