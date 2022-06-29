"""SkyCallback APIs to benchmark an iterative task on cloud resources.

SkyCallback measures averaged time taken by each 'step', and extrapolates it to
the makespan of the task. The APIs provide:
- `init` function to initialize the callback.
- Three equivalent ways to measure the time taken by each step.

Example:
    1. Use `sky_callback.init` to initialize the callback.
       Optionally, specify the total number of steps that the task will take.
    ```python
        sky_callback.init(total_steps=num_epochs * len(train_dataloader))
    ```

    2. Mark the start and end of each step with SkyCallback APIs.
       Select one of the three equivalent methods.

    1) Wrap your iterable (e.g., dataloader) with `timer`.
    ```python
    from sky_callback import timer

    for batch in timer(train_dataloader):
        ...
    ```

    2) Wrap your loop body with `sky_callback.step`.
    ```python
    for i in range(num_steps):
        with sky_callback.step():
            ...
    ```

    3) Call `sky_callback.step_begin` and `sky_callback.step_end`
       at the beginning and end of each step.
    ```python
    for i in range(num_steps):
        sky_callback.step_begin()
        ...
        sky_callback.step_end()
    ```
"""
import collections
import contextlib
from typing import Optional

from sky_callback import base
from sky_callback import utils

_DISABLE_CALLBACK = utils.DISABLE_CALLBACK
_sky_callback = None
_initialized = False


def init(global_rank: int = 0,
         log_dir: Optional[str] = None,
         total_steps: Optional[int] = None) -> None:
    """Initializes SkyCallback.

    NOTE: This function is not thread-safe. Only one thread per process should
        call this function.
    NOTE: Do not use this function when using framework-integrated SkyCallback
        APIs (e.g., SkyKerasCallback).

    Args:
        global_rank: global rank of the calling process. In non-distributed tasks,
            the rank should be 0. In distributed tasks, only one process should
            have rank 0. Check your framework API to query the global rank
            (e.g., torch.distributed.get_rank()).
        log_dir: A directory to store the logs.
        total_steps: A total number of steps. If None, SkyCallback will not
            estimate the total time to complete the task.
    """
    if _DISABLE_CALLBACK:
        return

    global _sky_callback, _initialized
    if _initialized:
        raise RuntimeError('sky_callback is already initialized. '
                           'Please call `sky_callback.init` only once.')

    if global_rank == 0:
        _sky_callback = base.BaseCallback(log_dir=log_dir,
                                          total_steps=total_steps)
    # Processes with non-zero ranks should also set this flag.
    _initialized = True


def step_begin() -> None:
    """Marks the beginning of a step."""
    if _DISABLE_CALLBACK:
        return
    if not _initialized:
        raise RuntimeError(
            'sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        # Only rank-0 process will execute below.
        _sky_callback.on_step_begin()


def step_end() -> None:
    """Marks the end of a step."""
    if _DISABLE_CALLBACK:
        return
    if not _initialized:
        raise RuntimeError(
            'sky_callback is not initialized. '
            'Please call `sky_callback.init` before using sky_callback.')
    if _sky_callback is not None:
        # Only rank-0 process will execute below.
        _sky_callback.on_step_end()


@contextlib.contextmanager
def step():
    """Marks the beginning and end of a step."""
    step_begin()
    yield
    step_end()


class timer:
    """Wraps an iterable with timer."""

    def __init__(self, iterable: collections.Iterable) -> None:
        self._iterable = iterable
        if _DISABLE_CALLBACK:
            return
        if not _initialized:
            raise RuntimeError(
                'sky_callback is not initialized. '
                'Please call `sky_callback.init` before using sky_callback.')

    def __iter__(self):
        # Inlining for speed optimization.
        iterable = self._iterable
        if _DISABLE_CALLBACK or _sky_callback is None:
            for obj in iterable:
                yield obj
        else:
            # Only rank-0 process will execute below.
            for obj in iterable:
                _sky_callback.on_step_begin()
                yield obj
                _sky_callback.on_step_end()
