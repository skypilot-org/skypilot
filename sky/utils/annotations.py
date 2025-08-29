"""Annotations for public APIs."""

import functools
import logging
import time
from typing import Callable, Literal, Optional, TypeVar

from typing_extensions import ParamSpec

# Whether the current process is a SkyPilot API server process.
is_on_api_server = True
_FUNCTIONS_NEED_RELOAD_CACHE = []

T = TypeVar('T')
P = ParamSpec('P')


def client_api(func: Callable[P, T]) -> Callable[P, T]:
    """Mark a function as a client-side API.

    Code invoked by server-side functions will find annotations.is_on_api_server
    to be True, so they can have some server-side handling.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global is_on_api_server
        is_on_api_server = False
        return func(*args, **kwargs)

    return wrapper


def lru_cache(scope: Literal['global', 'request'], *lru_cache_args,
              **lru_cache_kwargs) -> Callable:
    """LRU cache decorator for functions.

    This decorator allows us to track which functions need to be reloaded for a
    new request using the scope argument.

    Args:
        scope: Whether the cache is global or request-specific, i.e. needs to be
            reloaded for a new request.
        lru_cache_args: Arguments for functools.lru_cache.
        lru_cache_kwargs: Keyword arguments for functools.lru_cache.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if scope == 'global':
            return functools.lru_cache(*lru_cache_args,
                                       **lru_cache_kwargs)(func)
        else:
            cached_func = functools.lru_cache(*lru_cache_args,
                                              **lru_cache_kwargs)(func)
            _FUNCTIONS_NEED_RELOAD_CACHE.append(cached_func)
            return cached_func

    return decorator


def clear_request_level_cache():
    """Clear the request-level cache."""
    for func in _FUNCTIONS_NEED_RELOAD_CACHE:
        func.cache_clear()


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
        @log_execution_time
        def my_function():
            pass

        @log_execution_time(name='my_module.my_function2')
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
                from sky import sky_logging  # pylint: disable=all

                end_time = time.perf_counter()
                execution_time = end_time - start_time
                log = (f'Method {name} executed in '
                       f'{execution_time:.{precision}f}')
                logger = sky_logging.init_logger(__name__)
                logger.log(level, log)

        return wrapper

    if func is None:
        return decorator
    else:
        return decorator(func)
