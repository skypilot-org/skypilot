"""PPIO cloud adaptor."""

import functools
from typing import Any, Dict, List, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_ppio_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _ppio_sdk
        if _ppio_sdk is None:
            try:
                import ppio as _ppio  # pylint: disable=import-outside-toplevel
                _ppio_sdk = _ppio
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for PPIO. '
                    'Try pip install "skypilot[ppio]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def ppio():
    """Return the ppio package."""
    return _ppio_sdk


def list_ssh_keys() -> List[Dict[str, Any]]:
    """List all SSH keys in PPIO account.
    
    Note: PPIO doesn't require SSH key management through their API.
    This function is kept for compatibility but returns an empty list.
    """
    # PPIO doesn't require SSH keys to be managed through their API
    # Instances are created with default SSH access
    return []


def add_ssh_key_to_ppio(public_key: str) -> Optional[str]:
    """Add SSH key to PPIO if it doesn't already exist.
    Note: PPIO doesn't require SSH keys to be uploaded to their platform.
    This function is kept for compatibility but returns None.
    Args:
        public_key: The SSH public key string (unused).
    Returns:
        None, as PPIO doesn't require SSH key management.
    """
    # PPIO doesn't require SSH keys to be uploaded
    # Instances are created with default SSH access configured by PPIO
    logger.debug(
        'PPIO does not require SSH key upload - using default instance SSH access'
    )
    return None