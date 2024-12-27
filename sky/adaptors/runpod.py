"""RunPod cloud adaptor."""

from sky.adaptors import common

runpod = common.LazyImport(
    'runpod',
    import_error_message='Failed to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')

_LAZY_MODULES = (runpod,)


@common.load_lazy_modules(_LAZY_MODULES)
def query_exception():
    """Query exception."""
    return runpod.error.QueryError
