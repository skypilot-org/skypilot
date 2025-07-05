"""Volume utils."""
import abc
from datetime import datetime
from typing import Any, Dict, List, Optional

import prettytable

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils
from sky.volumes import volume

logger = sky_logging.init_logger(__name__)


def _get_infra_str(cloud: Optional[str], region: Optional[str],
                   zone: Optional[str]) -> str:
    """Get the infrastructure string for the volume."""
    infra = ''
    if cloud:
        infra += cloud
    if region:
        infra += f'/{region}'
    if zone:
        infra += f'/{zone}'
    return infra


class VolumeTable(abc.ABC):
    """The volume table."""

    @abc.abstractmethod
    def format(self) -> str:
        """Format the volume table for display."""
        pass


class PVCVolumeTable(VolumeTable):
    """The PVC volume table."""

    def __init__(self, volumes: List[Dict[str, Any]], show_all: bool = False):
        super().__init__()
        self.table = self._create_table(show_all)
        self._add_rows(volumes, show_all)

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the PVC volume table."""
        #  If show_all is False, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY
        #  If show_all is True, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY, NAME_ON_CLOUD
        #   STORAGE_CLASS, ACCESS_MODE

        if show_all:
            columns = [
                'NAME',
                'TYPE',
                'INFRA',
                'SIZE',
                'USER',
                'WORKSPACE',
                'AGE',
                'STATUS',
                'LAST_USE',
                'USED_BY',
                'NAME_ON_CLOUD',
                'STORAGE_CLASS',
                'ACCESS_MODE',
            ]
        else:
            columns = [
                'NAME',
                'TYPE',
                'INFRA',
                'SIZE',
                'USER',
                'WORKSPACE',
                'AGE',
                'STATUS',
                'LAST_USE',
                'USED_BY',
            ]

        table = log_utils.create_table(columns)
        return table

    def _add_rows(self,
                  volumes: List[Dict[str, Any]],
                  show_all: bool = False) -> None:
        """Add rows to the PVC volume table."""
        for row in volumes:
            # Convert last_attached_at timestamp to human readable string
            last_attached_at = row.get('last_attached_at')
            if last_attached_at is not None:
                last_attached_at_str = datetime.fromtimestamp(
                    last_attached_at).strftime('%Y-%m-%d %H:%M:%S')
            else:
                last_attached_at_str = '-'
            size = row.get('size', '')
            if size:
                size = f'{size}Gi'
            usedby_str = '-'
            usedby_clusters = row.get('usedby_clusters')
            usedby_pods = row.get('usedby_pods')
            if usedby_clusters:
                usedby_str = f'{", ".join(usedby_clusters)}'
            elif usedby_pods:
                usedby_str = f'{", ".join(usedby_pods)}'
            if show_all:
                usedby = usedby_str
            else:
                usedby = common_utils.truncate_long_string(
                    usedby_str, constants.USED_BY_TRUNC_LENGTH)
            infra = _get_infra_str(row.get('cloud'), row.get('region'),
                                   row.get('zone'))
            table_row = [
                row.get('name', ''),
                row.get('type', ''),
                infra,
                size,
                row.get('user_name', '-'),
                row.get('workspace', '-'),
                log_utils.human_duration(row.get('launched_at', 0)),
                row.get('status', ''),
                last_attached_at_str,
                usedby,
            ]
            if show_all:
                table_row.append(row.get('name_on_cloud', ''))
                table_row.append(
                    row.get('config', {}).get('storage_class_name', '-'))
                table_row.append(row.get('config', {}).get('access_mode', ''))

            self.table.add_row(table_row)

    def format(self) -> str:
        """Format the PVC volume table for display."""
        return str(self.table)


def format_volume_table(volumes: List[Dict[str, Any]],
                        show_all: bool = False) -> str:
    """Format the volume table for display.

    Args:
        volume_table (dict): The volume table.

    Returns:
        str: The formatted volume table.
    """
    volumes_per_type: Dict[str, List[Dict[str, Any]]] = {}
    supported_volume_types = [
        volume_type.value for volume_type in volume.VolumeType
    ]
    for row in volumes:
        volume_type = row.get('type', '')
        if volume_type in supported_volume_types:
            if volume_type not in volumes_per_type:
                volumes_per_type[volume_type] = []
            volumes_per_type[volume_type].append(row)
        else:
            logger.warning(f'Unknown volume type: {volume_type}')
            continue
    table_str = ''
    for volume_type, volume_list in volumes_per_type.items():
        if volume_type == volume.VolumeType.PVC.value:
            table = PVCVolumeTable(volume_list, show_all)
            table_str += table.format()
    if table_str:
        return table_str
    else:
        return 'No existing volumes.'
