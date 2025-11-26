"""Annotations for public APIs."""

import functools
import threading
from typing import Any, Callable, List, Literal, TypeVar
import weakref

import cachetools
from typing_extensions import ParamSpec

# Whether the current process is a SkyPilot API server process.
is_on_api_server = True
_FUNCTIONS_NEED_RELOAD_CACHE_LOCK = threading.Lock()
# Caches can be thread-local, use weakref to avoid blocking the GC when the
# thread is destroyed.
_FUNCTIONS_NEED_RELOAD_CACHE: List[weakref.ReferenceType] = []

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


class _ReloadWrapper:
    """Wrapper for functions that need to be reloaded for a new request."""

    def __init__(self, target: Callable[..., Any]):
        self._target = target

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target(*args, **kwargs)

    def cache_clear(self):
        if hasattr(self._target, 'cache_clear'):
            self._target.cache_clear()


def _register_functions_need_reload_cache(func: Callable) -> Callable:
    """Register a cachefunction that needs to be reloaded for a new request.

    The function will be registered as a weak reference to avoid blocking GC.
    """
    try:
        func_ref = weakref.ref(func)
    except TypeError:
        # The function might be not weakrefable (e.g. functools.lru_cache),
        # wrap it in this case.
        func_ref = weakref.ref(_ReloadWrapper(func))

    with _FUNCTIONS_NEED_RELOAD_CACHE_LOCK:
        _FUNCTIONS_NEED_RELOAD_CACHE.append(func_ref)
    return func


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
            return _register_functions_need_reload_cache(cached_func)

    return decorator


def ttl_cache(scope: Literal['global', 'request'], *ttl_cache_args,
              **ttl_cache_kwargs) -> Callable:
    """TTLCache decorator for functions.

    This decorator allows us to track which functions need to be reloaded for a
    new request using the scope argument.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if scope == 'global':
            return cachetools.cached(
                cachetools.TTLCache(*ttl_cache_args, **ttl_cache_kwargs))(func)
        else:
            cached_func = cachetools.cached(
                cachetools.TTLCache(*ttl_cache_args, **ttl_cache_kwargs))(func)
            return _register_functions_need_reload_cache(cached_func)

    return decorator


def clear_request_level_cache():
    """Clear the request-level cache."""
    alive_entries = []
    with _FUNCTIONS_NEED_RELOAD_CACHE_LOCK:
        for entry in _FUNCTIONS_NEED_RELOAD_CACHE:
            func = entry()
            if func is None:
                # Has been GC'ed, drop the reference.
                continue
            func.cache_clear()
            alive_entries.append(entry)
        _FUNCTIONS_NEED_RELOAD_CACHE[:] = alive_entries
