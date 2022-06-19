"""SkyCallback APIs to benchmark an iterative task on cloud resources.

SkyCallback measures averaged time taken by each 'step', and extrapolates it to
the makespan of the task. The APIs provide:
- `init` function to initialize the callback.
- 3 equivalent ways to measure the time taken by each step (see doc).

Example:
    ```python
    import sky_callback
    from sky_callback import timer

    ...
    sky_callback.init(total_steps=num_epochs * len(dataloader))
    for epoch in range(num_epochs):
        for batch in timer(dataloader):
            ...
    ```
"""
import collections
import contextlib
import os
from typing import Optional

from sky_callback import base

_DISABLE_CALLBACK = os.environ.get('SKY_DISABLE_CALLBACK', 'False').lower() in ('true', '1')
_sky_callback = None
_initialized = False


def init(global_rank: int = 0, log_dir: Optional[str] = None, total_steps: Optional[int] = None) -> None:
    if global_rank == 0 and not _DISABLE_CALLBACK:
        global _sky_callback, _initialized
        _sky_callback = base.BaseCallback(log_dir=log_dir, total_steps=total_steps)
    _initialized = True


def on_step_begin() -> None:
    if not _initialized:
        raise RuntimeError('sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        _sky_callback.on_step_begin()


def on_step_end() -> None:
    if not _initialized:
        raise RuntimeError('sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        _sky_callback.on_step_end()


@contextlib.contextmanager
def step():
    on_step_begin()
    yield
    on_step_end()


class timer:

    def __init__(self, iterable: collections.Iterable) -> None:
        if not _initialized:
            raise RuntimeError('sky_callback is not initialized. '
                'Please call `sky_callback.init` before using sky_callback.')
        self._iterable = iterable

    def __iter__(self):
        # Inlining for speed optimization.
        iterable = self._iterable
        if _sky_callback is None:
            for obj in iterable:
                yield obj
        else:
            for obj in iterable:
                _sky_callback.on_step_begin()
                yield obj
                _sky_callback.on_step_end()
