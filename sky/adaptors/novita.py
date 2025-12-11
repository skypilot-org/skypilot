"""Novita cloud adaptor."""

import functools
from typing import Any, Dict, List, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_novita_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _novita_sdk
        if _novita_sdk is None:
            try:
                import novita as _novita  # pylint: disable=import-outside-toplevel
                _novita_sdk = _novita
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for Novita. '
                    'Try pip install "skypilot[novita]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def novita():
    """Return the novita package."""
    return _novita_sdk


def list_ssh_keys() -> List[Dict[str, Any]]:
    """List all SSH keys in Novita account.
    
    Note: Novita doesn't require SSH key management through their API.
    This function is kept for compatibility but returns an empty list.
    """
    # Novita doesn't require SSH keys to be managed through their API
    # Instances are created with default SSH access
    return []


def add_ssh_key_to_novita(public_key: str) -> Optional[str]:
    """Add SSH key to Novita if it doesn't already exist.

    Note: Novita doesn't require SSH keys to be uploaded to their platform.
    This function is kept for compatibility but returns None.

    Args:
        public_key: The SSH public key string (unused).

    Returns:
        None, as Novita doesn't require SSH key management.
    """
    # Novita doesn't require SSH keys to be uploaded
    # Instances are created with default SSH access configured by Novita
    logger.debug('Novita does not require SSH key upload - using default instance SSH access')
    return None
