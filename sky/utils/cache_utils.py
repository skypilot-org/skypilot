"""Thread-safe TTL cache utilities."""
import functools
import threading
import time
from typing import Any, Callable, Dict, Tuple


def ttl_cache(ttl_seconds: int = 60) -> Callable:
    """A thread-safe TTL cache decorator for methods and functions."""

    def decorator(func: Callable) -> Callable:
        cache: Dict[Tuple[Any, ...], Tuple[Any, float]] = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            key: Tuple[Any, ...] = (args, tuple(sorted(kwargs.items())))
            now = time.time()

            with lock:
                if key in cache:
                    result, expiry = cache[key]
                    if now < expiry:
                        return result

            result = func(*args, **kwargs)

            with lock:
                cache[key] = (result, now + ttl_seconds)

            return result

        wrapper.cache_clear = cache.clear  # type: ignore
        return wrapper

    return decorator
