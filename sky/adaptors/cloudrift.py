"""CloudRift adaptor."""

import typing

from sky.utils import log_utils

logger = log_utils.get_logger(__name__)

if typing.TYPE_CHECKING:
    from sky.adaptors import common

def check_exceptions_dependencies_installed() -> typing.Tuple[bool, str]:
    """Checks that dependencies for CloudRift are installed.

    Returns:
        A tuple (success, msg). If success is False, msg contains the error message.
        Otherwise, msg is an empty string.
    """
    # TODO: Add actual dependency checks when CloudRift client is implemented
    return True, ''

def exceptions() -> 'common.Exceptions':
    """Returns CloudRift exceptions.

    Raises:
        ImportError: If CloudRift dependencies are not installed.
    """
    # TODO: Implement proper exception handling for CloudRift
    class DummyExceptions:
        """Dummy exceptions for CloudRift."""
        
        class HttpResponseError(Exception):
            """Http response error."""
            
            def __init__(self, message=None, error=None):
                self.message = message
                self.error = error
                super().__init__(message)
    
    return DummyExceptions
