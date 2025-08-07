"""Shadeform cloud adaptor."""

import functools

_shadeform_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _shadeform_sdk
        if _shadeform_sdk is None:
            try:
                import shadeform as _shadeform  # pylint: disable=import-outside-toplevel
                _shadeform_sdk = _shadeform
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for Shadeform. '
                    'Try pip install "skypilot[shadeform]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def shadeform():
    """Return the shadeform package."""
    return _shadeform_sdk
