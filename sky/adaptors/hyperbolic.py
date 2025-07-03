"""Hyperbolic cloud adaptor."""

from sky.adaptors import common

hyperbolic = common.LazyImport(
    'hyperbolic',
    import_error_message='Failed to import dependencies for Hyperbolic. '
    'Try running: pip install "skypilot[hyperbolic]"')
