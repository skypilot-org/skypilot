"""Prime Intellect cloud adaptor."""

from sky.adaptors import common

primeintellect = common.LazyImport(
    'primeintellect',
    import_error_message='Failed to import dependencies for Prime Intellect. '
    'Try running: pip install "skypilot[primeintellect]"')
