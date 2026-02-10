"""Shadeform cloud adaptor."""

import functools
import socket
from typing import Any, Dict, List, Optional

import requests

from sky import sky_logging
from sky.provision.shadeform import shadeform_utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

_shadeform_sdk = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _shadeform_sdk
        if _shadeform_sdk is None:
            try:
                import shadeform as _shadeform  # pylint: disable=import-outside-toplevel
                _shadeform_sdk = _shadeform
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for Shadeform. '
                    'Try pip install "skypilot[shadeform]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def shadeform():
    """Return the shadeform package."""
    return _shadeform_sdk


def list_ssh_keys() -> List[Dict[str, Any]]:
    """List all SSH keys in Shadeform account."""
    try:
        response = shadeform_utils.get_ssh_keys()
        return response.get('ssh_keys', [])
    except (ValueError, KeyError, requests.exceptions.RequestException) as e:
        logger.warning(f'Failed to list SSH keys from Shadeform: {e}')
        return []


def add_ssh_key_to_shadeform(public_key: str) -> Optional[str]:
    """Add SSH key to Shadeform if it doesn't already exist.

    Args:
        public_key: The SSH public key string.

    Returns:
        The name of the key if added successfully, None otherwise.
    """
    try:
        # Check if key already exists
        existing_keys = list_ssh_keys()
        key_exists = False
        key_id = None
        for key in existing_keys:
            if key.get('public_key') == public_key:
                key_exists = True
                key_id = key.get('id')
                break

        if key_exists:
            logger.info('SSH key already exists in Shadeform account')
            return key_id

        # Generate a unique key name
        hostname = socket.gethostname()
        key_name = f'skypilot-{hostname}-{common_utils.get_user_hash()[:8]}'

        # Add the key
        response = shadeform_utils.add_ssh_key(name=key_name,
                                               public_key=public_key)
        key_id = response['id']
        logger.info(f'Added SSH key to Shadeform: {key_name, key_id}')
        return key_id

    except (ValueError, KeyError, requests.exceptions.RequestException) as e:
        logger.warning(f'Failed to add SSH key to Shadeform: {e}')
        return None
