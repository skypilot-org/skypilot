"""Cudo Compute cloud adaptor."""

import functools

_cudo_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _cudo_sdk
        if _cudo_sdk is None:
            try:
                import cudo_compute as _cudo  # pylint: disable=import-outside-toplevel
                _cudo_sdk = _cudo
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for Cudo Compute.'
                    'Try pip install "skypilot[cudo]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def cudo():
    """Return the Cudo Compute package."""
    return _cudo_sdk
