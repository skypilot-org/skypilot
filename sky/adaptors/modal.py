"""Modal cloud adaptor."""

from sky.adaptors import common

modal = common.LazyImport(
    'modal',
    import_error_message='Failed to import dependencies for Modal. '
    'Try running: pip install "skypilot[modal]"')
