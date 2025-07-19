"""AWS SageMaker adaptor using lazy imports."""
from sky.adaptors import common

boto3 = common.LazyImport(
    'boto3',
    import_error_message='Failed to import dependencies for SageMaker. '
    'Try running: pip install "skypilot[aws]"')
