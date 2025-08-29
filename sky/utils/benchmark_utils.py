"""Utility functions for benchmarking."""

import functools
import logging
import time
from typing import Callable, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def log_execution_time(func: Optional[Callable] = None,
                       *,
                       name: Optional[str] = None,
                       level: int = logging.DEBUG,
                       precision: int = 4) -> Callable:
    """Mark a function and log its execution time.

    Args:
        func: Function to decorate.
        name: Name of the function.
        level: Logging level.
        precision: Number of decimal places (default: 4).

    Usage:
        from sky.utils import benchmark_utils

        @benchmark_utils.log_execution_time
        def my_function():
            pass

        @benchmark_utils.log_execution_time(name='my_module.my_function2')
        def my_function2():
            pass
    """

    def decorator(f: Callable) -> Callable:

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            nonlocal name
            name = name or f.__name__
            start_time = time.perf_counter()
            try:
                result = f(*args, **kwargs)
                return result
            finally:
                end_time = time.perf_counter()
                execution_time = end_time - start_time
                log = (f'Method {name} executed in '
                       f'{execution_time:.{precision}f}')
                logger.log(level, log)

        return wrapper

    if func is None:
        return decorator
    else:
        return decorator(func)
