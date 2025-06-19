"""Volume utils."""
from datetime import datetime
from typing import Any, Dict, List

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils
from sky.volumes import volume

logger = sky_logging.init_logger(__name__)


def format_volume_table(volumes: List[Dict[str, Any]],
                        show_all: bool = False) -> str:
    """Format the volume table for display.

    Args:
        volume_table (dict): The volume table.

    Returns:
        str: The formatted volume table.
    """
    # Show different tables for different volume types.
    # For PVC,
    #  If show_all is True, show the table with the columns:
    #   NAME, TYPE, CONTEXT, NAMESPACE, SIZE, USER_HASH, WORKSPACE,
    #   LAUNCHED, LAST_ATTACHED, LAST_USE(Truncated), STATUS
    #  If show_all is False, show the table with the columns:
    #   NAME, TYPE, CONTEXT, NAMESPACE, SIZE, USER_HASH, WORKSPACE,
    #   LAUNCHED, LAST_ATTACHED, LAST_USE, STATUS, NAME_ON_CLOUD,
    #   STORAGE_CLASS, ACCESS_MODE

    if show_all:
        columns = [
            'NAME',
            'TYPE',
            'CONTEXT',
            'NAMESPACE',
            'SIZE',
            'USER_HASH',
            'WORKSPACE',
            'LAUNCHED',
            'LAST_ATTACHED',
            'STATUS',
            'LAST_USE',
            'NAME_ON_CLOUD',
            'STORAGE_CLASS',
            'ACCESS_MODE',
        ]
    else:
        columns = [
            'NAME',
            'TYPE',
            'CONTEXT',
            'NAMESPACE',
            'SIZE',
            'USER_HASH',
            'WORKSPACE',
            'LAUNCHED',
            'LAST_ATTACHED',
            'STATUS',
            'LAST_USE',
        ]

    pvc_table = log_utils.create_table(columns)

    for row in volumes:
        volume_type = row.get('type', '')
        if volume_type == volume.VolumeType.PVC.value:
            table = pvc_table
        else:
            logger.warning(f'Unknown volume type: {volume_type}')
            continue

        # Convert last_attached_at timestamp to human readable string
        last_attached_at = row.get('last_attached_at')
        if last_attached_at is not None:
            last_attached_at_str = datetime.fromtimestamp(
                last_attached_at).strftime('%Y-%m-%d %H:%M:%S')
        else:
            last_attached_at_str = ''

        table_row = [
            row.get('name', ''),
            row.get('type', ''),
            row.get('region', ''),
            row.get('spec', {}).get('namespace', ''),
            row.get('spec', {}).get('size', ''),
            row.get('user_hash', ''),
            row.get('workspace', ''),
            log_utils.readable_time_duration(row.get('launched_at', 0)),
            last_attached_at_str,
            row.get('status', ''),
        ]
        if show_all:
            table_row.append(row.get('last_use', ''))
            table_row.append(row.get('name_on_cloud', ''))
            table_row.append(row.get('spec', {}).get('storage_class_name', ''))
            table_row.append(row.get('spec', {}).get('access_mode', ''))
        else:
            table_row.append(
                common_utils.truncate_long_string(
                    row.get('last_use', ''), constants.LAST_USE_TRUNC_LENGTH))
        table.add_row(table_row)
    if volumes:
        return str(pvc_table)
    else:
        return 'No volumes.'
