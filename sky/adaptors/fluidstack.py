"""fluidstack cloud adaptor."""

import functools

_fluidstack_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _fluidstack_sdk
        if _fluidstack_sdk is None:
            try:
                import fluidstack as _fluidstack  # pylint: disable=import-outside-toplevel
                _fluidstack_sdk = _fluidstack
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for fluidstack.'
                    'Try pip install "skypilot[fluidstack]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def fluidstack():
    """Return the fluidstack package."""
    return _fluidstack_sdk
