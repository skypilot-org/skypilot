"""Annotations for public APIs."""

import functools

# Whether the current process is a SkyPilot API server process.
is_server = True
FUNCTIONS_NEED_RELOAD_CACHE = []


def public_api(func):
    """Mark a function as a public API.

    Code invoked by server-side functions will find annotations.is_server to be
    True, so they can have some server-side handling.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global is_server
        is_server = False
        return func(*args, **kwargs)

    return wrapper


def cache_needs_reload(func):
    """Mark a function that needs to reload the cache for a new request."""
    FUNCTIONS_NEED_RELOAD_CACHE.append(func)
    return func
