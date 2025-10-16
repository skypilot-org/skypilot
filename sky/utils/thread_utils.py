"""Thread utilities for SkyPilot.

This module provides thread-based utilities that avoid the semaphore leaks
that can occur with multiprocessing.pool.ThreadPool when processes are killed.
"""
import threading
from typing import Any, Callable, Dict, Optional, Tuple


class ThreadWithResult:
    """A thread wrapper that provides a Future-like interface.

    This class mimics the interface of multiprocessing.pool.AsyncResult
    but uses pure threading to avoid creating semaphores in /dev/shm that
    can leak when processes are killed.

    Example:
        >>> def my_func(x, y):
        ...     return x + y
        >>> future = ThreadWithResult(target=my_func, args=(1, 2))
        >>> result = future.get()  # blocks until complete
        >>> print(result)  # 3
    """

    def __init__(self,
                 target: Callable,
                 args: Tuple = (),
                 kwargs: Optional[Dict[str, Any]] = None):
        self._result: Optional[Any] = None
        self._exception: Optional[Exception] = None
        self._has_result: bool = False
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        try:
            self._result = self._target(*self._args, **self._kwargs)
            self._has_result = True
        except Exception as e:  # pylint: disable=broad-except
            self._exception = e

    def get(self) -> Any:
        self._thread.join()
        if self._exception is not None:
            raise self._exception
        return self._result

    def wait(self) -> None:
        self._thread.join()

    def is_alive(self) -> bool:
        return self._thread.is_alive()
