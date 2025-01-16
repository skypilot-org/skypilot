"""Nebius cloud adaptor."""

import functools

_nebius_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _nebius_sdk
        if _nebius_sdk is None:
            try:
                import nebius as _nebius  # pylint: disable=import-outside-toplevel
                _nebius_sdk = _nebius
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for nebius.'
                    'Try pip install "skypilot[nebius]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def nebius():
    """Return the nebius package."""
    return _nebius_sdk
