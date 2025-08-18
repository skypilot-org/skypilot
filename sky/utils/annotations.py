"""Annotations for public APIs."""

import functools
from typing import Callable, Literal, TypeVar

from typing_extensions import ParamSpec

# Whether the current process is a SkyPilot API server process.
is_on_api_server = True
FUNCTIONS_NEED_RELOAD_CACHE = []

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
            FUNCTIONS_NEED_RELOAD_CACHE.append(cached_func)
            return cached_func

    return decorator
