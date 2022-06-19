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
from typing import Optional

from sky_callback import base
from sky_callback import utils

DISABLE_CALLBACK = utils.DISABLE_CALLBACK
_sky_callback = None
_initialized = False


def init(global_rank: int = 0,
         log_dir: Optional[str] = None,
         total_steps: Optional[int] = None) -> None:
    if DISABLE_CALLBACK:
        return

    global _sky_callback, _initialized
    if _initialized:
        raise RuntimeError(
            'sky_callback is already initialized. '
            'Please call `sky_callback.init` only once.')
 
    if global_rank == 0:
        _sky_callback = base.BaseCallback(log_dir=log_dir,
                                          total_steps=total_steps)
    # Processes with non-zero ranks should also set this flag.
    _initialized = True


def on_step_begin() -> None:
    if DISABLE_CALLBACK:
        return
    if not _initialized:
        raise RuntimeError(
            'sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        # Only rank-0 process should call this function.
        _sky_callback.on_step_begin()


def on_step_end() -> None:
    if DISABLE_CALLBACK:
        return
    if not _initialized:
        raise RuntimeError(
            'sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        # Only rank-0 process should call this function.
        _sky_callback.on_step_end()


@contextlib.contextmanager
def step():
    on_step_begin()
    yield
    on_step_end()


class timer:

    def __init__(self, iterable: collections.Iterable) -> None:
        self._iterable = iterable
        if DISABLE_CALLBACK:
            return
        if not _initialized:
            raise RuntimeError(
                'sky_callback is not initialized. '
                'Please call `sky_callback.init` before using sky_callback.')

    def __iter__(self):
        # Inlining for speed optimization.
        iterable = self._iterable
        if DISABLE_CALLBACK or _sky_callback is None:
            for obj in iterable:
                yield obj
        else:
            # Only rank-0 process should execute below.
            for obj in iterable:
                _sky_callback.on_step_begin()
                yield obj
                _sky_callback.on_step_end()
