"""SimplePod cloud adaptor."""

from sky.adaptors import common

simplepod = common.LazyImport(
    'simplepod',
    import_error_message='Failed to import dependencies for SimplePod. '
    'Try running: pip install "skypilot[simplepod]"')
