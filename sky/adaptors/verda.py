"""Verda Cloud adaptor."""

import functools

_sdk = None

def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _vast_sdk

        if _vast_sdk is None:
            try:
                import datacrunch as _verda  # pylint: disable=import-outside-toplevel
                _sdk = _verda.DataCrunchClient()
            except ImportError as e:
                raise ImportError(f'Fail to import dependencies for Verda Cloud: {e}\n'
                                  'Try pip install "skypilot[verda]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def verda():
    """Return the verda package."""
    return _sdk
