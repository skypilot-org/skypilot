"""Annotations for public APIs."""

import functools
import threading
import time
from typing import Callable, List, Literal, TypeVar
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


def _register_functions_need_reload_cache(func: Callable) -> Callable:
    """Register a cachefunction that needs to be reloaded for a new request.

    The function will be registered as a weak reference to avoid blocking GC.
    """
    assert hasattr(func, 'cache_clear'), f'{func.__name__} is not cacheable'
    wrapped_fn = func
    try:
        func_ref = weakref.ref(func)
    except TypeError:
        # The function might be not weakrefable (e.g. functools.lru_cache),
        # wrap it in this case.
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.cache_clear = func.cache_clear  # type: ignore[attr-defined]
        func_ref = weakref.ref(wrapper)
        wrapped_fn = wrapper
    with _FUNCTIONS_NEED_RELOAD_CACHE_LOCK:
        _FUNCTIONS_NEED_RELOAD_CACHE.append(func_ref)
    return wrapped_fn


class ThreadLocalTTLCache(threading.local):
    """Thread-local storage for _thread_local_lru_cache decorator."""

    def __init__(self, func, maxsize: int, ttl: int):
        super().__init__()
        self.func = func
        self.maxsize = maxsize
        self.ttl = ttl

    def get_cache(self):
        if not hasattr(self, 'cache'):
            self.cache = ttl_cache(scope='request',
                                   maxsize=self.maxsize,
                                   ttl=self.ttl,
                                   timer=time.time)(self.func)
        return self.cache

    def __del__(self):
        if hasattr(self, 'cache'):
            self.cache.cache_clear()
            self.cache = None


def thread_local_ttl_cache(maxsize=32, ttl=60 * 55):
    """Thread-local TTL cache decorator.

    Args:
        maxsize: Maximum size of the cache.
        ttl: Time to live for the cache in seconds.
             Default is 55 minutes, a bit less than 1 hour
             default lifetime of an STS token.
    """

    def decorator(func):
        # Create thread-local storage for the LRU cache
        local_cache = ThreadLocalTTLCache(func, maxsize, ttl)

        # We can't apply the lru_cache here, because this runs at import time
        # so we will always have the main thread's cache.

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # We are within the actual function call, which may be on a thread,
            # so local_cache.cache will return the correct thread-local cache,
            # which we can now apply and immediately call.
            return local_cache.get_cache()(*args, **kwargs)

        def cache_info():
            # Note that this will only give the cache info for the current
            # thread's cache.
            return local_cache.get_cache().cache_info()

        def cache_clear():
            # Note that this will only clear the cache for the current thread.
            local_cache.get_cache().cache_clear()

        wrapper.cache_info = cache_info  # type: ignore[attr-defined]
        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]

        return wrapper

    return decorator


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
