"""Cudo Compute cloud adaptor."""

from sky.adaptors import common

cudo = common.LazyImport(
    'cudo_compute',
    import_error_message='Failed to import dependencies for Cudo Compute. '
    'Try running: pip install "skypilot[cudo]"')
