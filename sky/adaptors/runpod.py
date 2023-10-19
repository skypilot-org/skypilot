"""RunPod cloud adaptor."""

import functools

runpod = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global runpod
        if runpod is None:
            try:
                # pylint: disable=import-outside-toplevel
                import runpod as _runpod
                runpod = _runpod
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for runpod.'
                    'Try pip install "skypilot[runpod]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def rp_wrapper():
    """Return the runpod package."""
    return runpod
