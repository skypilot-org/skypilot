"""RunPod cloud adaptor."""

from sky.adaptors import common

runpod = common.LazyImport(
    'runpod',
    import_error_message='Fail to import dependencies for RunPod. '
    'Try running: pip install "skypilot[runpod]"')
