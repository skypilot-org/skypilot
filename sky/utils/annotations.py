"""Annotations for public APIs."""

import functools

# Whether the current process is a SkyPilot server process.
is_server = True


def public_api(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global is_server
        is_server = False
        return func(*args, **kwargs)

    return wrapper
