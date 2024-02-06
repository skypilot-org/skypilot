"""RunPod cloud adaptor."""

import functools

_runpod_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _runpod_sdk
        if _runpod_sdk is None:
            try:
                import runpod as _runpod  # pylint: disable=import-outside-toplevel
                _runpod_sdk = _runpod
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for runpod.'
                    'Try pip install "skypilot[runpod]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def runpod():
    """Return the runpod package."""
    return _runpod_sdk
