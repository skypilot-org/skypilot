"""Primeintellect cloud adaptor."""

from sky.adaptors import common

primeintellect = common.LazyImport(
    'primeintellect',
    import_error_message='Failed to import dependencies for Primeintellect. '
    'Try running: pip install "skypilot[primeintellect]"')
