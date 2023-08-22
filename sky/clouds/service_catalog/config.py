"""Configuration for service catalog."""

import contextlib
import functools
import threading

_thread_local_config = threading.local()


@contextlib.contextmanager
def _set_use_default_catalog_if_failed(value: bool):
    old_value = get_use_default_catalog_if_failed()
    _thread_local_config.use_default_catalog = value
    try:
        yield
    finally:
        _thread_local_config.use_default_catalog = old_value


def get_use_default_catalog_if_failed() -> bool:
    """Whether to use default catalog if failed to fetch account-specific one.

    Whether the caller requires the catalog to be narrowed down to the account-
    specific catalog (e.g., removing regions not enabled for the current account
    or use zone name assigned to the AWS account).

    When set to True, the caller allows to use the default service catalog,
    which may have inaccurate information (e.g., AWS's zone names are account-
    specific), but it is ok for the read-only operators, such as `show-gpus` or
    `sky status`.
    """
    if not hasattr(_thread_local_config, 'use_default_catalog'):
        # Should not set it globally, as the global assignment
        # will be executed only once if the module is imported
        # in the main thread, and will not be executed in other
        # threads.
        _thread_local_config.use_default_catalog = False
    return _thread_local_config.use_default_catalog


def fallback_to_default_catalog(func):
    """Decorator: allow failure for fetching account-specific catalog.

    The account-specific catalog requires the credentials of the
    cloud to be set.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _set_use_default_catalog_if_failed(True):
            return func(*args, **kwargs)

    return wrapper
