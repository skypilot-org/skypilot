"""Utilities for formatting tables for CLI output."""

from typing import List

from sky.schemas.api import responses
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils


def format_storage_table(storages: List[responses.StorageRecord],
                         show_all: bool = False) -> str:
    """Format the storage table for display.

    Args:
        storage_table (dict): The storage table.

    Returns:
        str: The formatted storage table.
    """
    storage_table = log_utils.create_table([
        'NAME',
        'UPDATED',
        'STORE',
        'COMMAND',
        'STATUS',
    ])

    for row in storages:
        launched_at = row.launched_at
        if show_all:
            command = row.last_use
        else:
            command = common_utils.truncate_long_string(
                row.last_use, constants.LAST_USE_TRUNC_LENGTH)
        storage_table.add_row([
            # NAME
            row.name,
            # LAUNCHED
            log_utils.readable_time_duration(launched_at),
            # CLOUDS
            ', '.join([s.value for s in row.store]),
            # COMMAND,
            command,
            # STATUS
            row.status.value,
        ])
    if storages:
        return str(storage_table)
    else:
        return 'No existing storage.'
