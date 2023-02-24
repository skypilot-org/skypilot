"""Configuration for service catalog."""

import contextlib
import functools
import threading

_thread_local_config = threading.local()
# Whether the caller requires the catalog to be narrowed down
# to the account-specific catalog (e.g., removing regions not
# enabled for the current account) or just the raw catalog
# fetched from SkyPilot catalog service. The former is used
# for launching clusters, while the latter for commands like
# `show-gpus`.
_thread_local_config.use_default_catalog = False


@contextlib.contextmanager
def _set_use_default_catalog(value: bool):
    old_value = _thread_local_config.use_default_catalog
    _thread_local_config.use_default_catalog = value
    try:
        yield
    finally:
        _thread_local_config.use_default_catalog = old_value


def get_use_default_catalog() -> bool:
    return _thread_local_config.use_default_catalog


def use_default_catalog(func):
    """Decorator: disable fetching account-specific catalog which need credentials."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _set_use_default_catalog(True):
            return func(*args, **kwargs)

    return wrapper
