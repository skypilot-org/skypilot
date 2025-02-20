"""Digital Ocean cloud adaptors"""

# pylint: disable=import-outside-toplevel

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for DO. '
                         'Try pip install "skypilot[do]"')
pydo = common.LazyImport('pydo', import_error_message=_IMPORT_ERROR_MESSAGE)
azure = common.LazyImport('azure', import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (pydo, azure)


# `pydo`` inherits Azure exceptions. See:
# https://github.com/digitalocean/pydo/blob/7b01498d99eb0d3a772366b642e5fab3d6fc6aa2/examples/poc_droplets_volumes_sshkeys.py#L6
@common.load_lazy_modules(modules=_LAZY_MODULES)
def exceptions():
    """Azure exceptions."""
    from azure.core import exceptions as azure_exceptions
    return azure_exceptions
