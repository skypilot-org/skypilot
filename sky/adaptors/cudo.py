"""Cudo Compute cloud adaptor."""

from sky.adaptors import common

cudo_sdk = common.LazyImport(
    'cudo_compute',
    import_error_message='Fail to import dependencies for Cudo Compute.'
    'Try pip install "skypilot[cudo]"')


def cudo():
    """Return the Cudo Compute package."""
    return cudo_sdk.load_module()
