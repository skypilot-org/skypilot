"""Spheron cloud adaptor."""

import socket
from typing import Any, Dict, List

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.provision.spheron import spheron_utils
from sky.utils import common_utils

# Lazy import to avoid adding requests to the import cost of `import sky`.
requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)


def list_ssh_keys() -> List[Dict[str, Any]]:
    """List all SSH keys in Spheron account."""
    try:
        response = spheron_utils.get_ssh_keys()
        # Spheron API returns a list directly
        if isinstance(response, list):
            return response
        return response.get('sshKeys', response.get('ssh_keys', []))
    except (ValueError, KeyError, requests.exceptions.RequestException) as e:
        logger.warning(f'Failed to list SSH keys from Spheron: {e}')
        return []


def add_ssh_key_to_spheron(public_key: str) -> str:
    """Add SSH key to Spheron if it doesn't already exist.

    Args:
        public_key: The SSH public key string.

    Returns:
        The key ID.

    Raises:
        Exception: If the SSH key cannot be added or retrieved.
    """
    # Check if key already exists
    existing_keys = list_ssh_keys()
    for key in existing_keys:
        if key.get('publicKey') == public_key:
            key_id = key.get('id')
            if key_id is None:
                raise ValueError(
                    'SSH key found in Spheron account but has no id field.')
            logger.info('SSH key already exists in Spheron account')
            return key_id

    # Generate a unique key name
    hostname = socket.gethostname()
    key_name = f'skypilot-{hostname}-{common_utils.get_user_hash()[:8]}'

    # Add the key
    response = spheron_utils.add_ssh_key(name=key_name, public_key=public_key)
    key_id = response['id']
    logger.info(f'Added SSH key to Spheron: {key_name}, id={key_id}')
    return key_id
