"""Utility functions for the storage module."""
from typing import Any, Dict, List

from sky import sky_logging
from sky.utils import log_utils

logger = sky_logging.init_logger(__name__)


def format_storage_table(storages: List[Dict[str, Any]]) -> str:
    """Format the storage table for display.

    Args:
        storage_table (dict): The storage table.

    Returns:
        str: The formatted storage table.
    """
    storage_table = log_utils.create_table([
        'NAME',
        'CREATED',
        'STORE',
        'COMMAND',
        'STATUS',
    ])

    for row in storages:
        launched_at = row['launched_at']
        storage_table.add_row([
            # NAME
            row['name'],
            # LAUNCHED
            log_utils.readable_time_duration(launched_at),
            # CLOUDS
            ', '.join([s.value for s in row['store']]),
            # COMMAND
            row['last_use'],
            # STATUS
            row['status'].value,
        ])
    if storages:
        return storage_table
    else:
        return 'No existing storage.'
