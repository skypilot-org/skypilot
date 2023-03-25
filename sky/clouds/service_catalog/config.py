"""Configuration for service catalog."""

import contextlib
import functools
import threading

_thread_local_config = threading.local()


@contextlib.contextmanager
def _set_use_default_catalog(value: bool):
    old_value = get_use_default_catalog()
    _thread_local_config.use_default_catalog = value
    try:
        yield
    finally:
        _thread_local_config.use_default_catalog = old_value


# Whether the caller requires the catalog to be narrowed down
# to the account-specific catalog (e.g., removing regions not
# enabled for the current account) or just the raw catalog
# fetched from SkyPilot catalog service. The former is used
# for launching clusters, while the latter for commands like
# `show-gpus`.
def get_use_default_catalog() -> bool:
    if not hasattr(_thread_local_config, 'use_default_catalog'):
        # Should not set it globally, as the global assignment
        # will be executed only once if the module is imported
        # in the main thread, and will not be executed in other
        # threads.
        _thread_local_config.use_default_catalog = False
    return _thread_local_config.use_default_catalog


def use_default_catalog(func):
    """Decorator: disable fetching account-specific catalog.

    The account-specific catalog requires the credentials of the
    cloud to be set.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _set_use_default_catalog(True):
            return func(*args, **kwargs)

    return wrapper
