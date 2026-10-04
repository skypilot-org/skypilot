"""Utilities for formatting tables for CLI output."""
import abc
from datetime import datetime
from typing import Any, Dict, List, Optional

import prettytable

from sky import sky_logging
from sky.jobs import utils as managed_jobs
from sky.schemas.api import responses
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import volume

logger = sky_logging.init_logger(__name__)


def format_job_queue(jobs: List[responses.ClusterJobRecord]):
    """Format the job queue for display.

    Usage:
        jobs = get_job_queue()
        print(format_job_queue(jobs))
    """
    job_table = log_utils.create_table([
        'ID', 'NAME', 'USER', 'SUBMITTED', 'STARTED', 'DURATION', 'RESOURCES',
        'STATUS', 'LOG', 'GIT COMMIT'
    ])
    for job in jobs:
        job_table.add_row([
            job.job_id,
            job.job_name,
            job.username,
            log_utils.readable_time_duration(job.submitted_at),
            log_utils.readable_time_duration(job.start_at),
            log_utils.readable_time_duration(job.start_at,
                                             job.end_at,
                                             absolute=True),
            job.resources,
            job.status.colored_str(),
            job.log_path,
            job.metadata.get('git_commit', '-'),
        ])
    return job_table


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


def format_job_table(
    jobs: List[responses.ManagedJobRecord],
    show_all: bool,
    show_user: bool,
    pool_status: Optional[List[Dict[str, Any]]] = None,
    max_jobs: Optional[int] = None,
    status_counts: Optional[Dict[str, int]] = None,
):
    jobs = [job.model_dump() for job in jobs]
    return managed_jobs.format_job_table(
        jobs,
        pool_status=pool_status,
        show_all=show_all,
        show_user=show_user,
        max_jobs=max_jobs,
        job_status_counts=status_counts,
    )


_BASIC_COLUMNS = [
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

    def __init__(self,
                 volumes: List[responses.VolumeRecord],
                 show_all: bool = False):
        super().__init__()
        self.table = self._create_table(show_all)
        self._add_rows(volumes, show_all)

    def _get_row_base_columns(self,
                              row: responses.VolumeRecord,
                              show_all: bool = False) -> List[str]:
        """Get the base columns for a row."""
        # Convert last_attached_at timestamp to human readable string
        last_attached_at = row.get('last_attached_at')
        if last_attached_at is not None:
            last_attached_at_str = datetime.fromtimestamp(
                last_attached_at).strftime('%Y-%m-%d %H:%M:%S')
        else:
            last_attached_at_str = '-'
        size = row.get('size')
        if size is not None:
            size = f'{size}Gi'
        else:
            size = '-'
        # A volume reports the capacity it has now, so a resize that has not
        # landed yet is invisible in the size alone. The message column says
        # what it is waiting for, but only this says how big it is getting.
        target = row.get('resize_target_size')
        if row.get('resize_status') and _is_larger(target, row.get('size')):
            size = f'{size} -> {target}Gi'
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
        return [
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

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the volume table."""
        raise NotImplementedError

    def _add_rows(self,
                  volumes: List[responses.VolumeRecord],
                  show_all: bool = False) -> None:
        """Add rows to the volume table."""
        raise NotImplementedError

    @abc.abstractmethod
    def format(self) -> str:
        """Format the volume table for display."""
        raise NotImplementedError


def _is_larger(target: Optional[str], size: Optional[str]) -> bool:
    """Whether a resize is heading somewhere the recorded size does not show.

    Both are whole gibibytes, so a resize smaller than that -- or one whose new
    space has landed while the state has not cleared -- reads as no resize.
    """
    try:
        return (target is not None and size is not None and
                int(target) > int(size))
    except ValueError:
        return False


def _volume_message(row: responses.VolumeRecord) -> str:
    """What the MESSAGE column says about a volume.

    One cell, so a volume with both an error and a resize in flight shows the
    error: it is the one that says the volume is unusable. The dashboard's
    details column picks the same way.
    """
    return row.get('error_message') or row.get('resize_message') or ''


class PVCVolumeTable(VolumeTable):
    """The PVC volume table."""

    def __init__(self,
                 volumes: List[responses.VolumeRecord],
                 show_all: bool = False):
        # Check if any volume has something to say before creating the table
        self._has_messages = any(_volume_message(row) for row in volumes)
        super().__init__(volumes, show_all)

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the PVC volume table."""
        #  If show_all is False, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY, IS_EPHEMERAL
        #   (+ MESSAGE if any volume has something to say)
        #  If show_all is True, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY, IS_EPHEMERAL, NAME_ON_CLOUD
        #   STORAGE_CLASS, ACCESS_MODE, MESSAGE

        columns = _BASIC_COLUMNS + [
            'IS_EPHEMERAL',
        ]
        if show_all:
            columns = columns + [
                'NAME_ON_CLOUD',
                'STORAGE_CLASS',
                'ACCESS_MODE',
                'MESSAGE',
            ]
        elif self._has_messages:
            # Show MESSAGE column even without show_all if there is one
            columns = columns + ['MESSAGE']

        table = log_utils.create_table(columns)
        return table

    def _add_rows(self,
                  volumes: List[responses.VolumeRecord],
                  show_all: bool = False) -> None:
        """Add rows to the PVC volume table."""
        for row in volumes:
            table_row = self._get_row_base_columns(row, show_all)
            table_row.append(row.get('is_ephemeral', False))
            if show_all:
                table_row.append(row.get('name_on_cloud', ''))
                table_row.append(
                    row.get('config', {}).get('storage_class_name', '-'))
                table_row.append(row.get('config', {}).get('access_mode', ''))
                # Add the message
                message = _volume_message(row)
                table_row.append(message if message else '-')
            elif self._has_messages:
                # Show the message even without show_all if there is one
                message = _volume_message(row)
                # Truncate the message for display
                if message:
                    message = common_utils.truncate_long_string(
                        message, constants.ERROR_MESSAGE_TRUNC_LENGTH)
                table_row.append(message if message else '-')

            self.table.add_row(table_row)

    def format(self) -> str:
        """Format the PVC volume table for display."""
        return 'Kubernetes PVCs:\n' + str(self.table)


class HostPathVolumeTable(VolumeTable):
    """The Kubernetes hostPath volume table."""

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the hostPath volume table."""
        columns = _BASIC_COLUMNS + ['HOST_PATH']
        table = log_utils.create_table(columns)
        return table

    def _add_rows(self,
                  volumes: List[responses.VolumeRecord],
                  show_all: bool = False) -> None:
        """Add rows to the hostPath volume table."""
        for row in volumes:
            table_row = self._get_row_base_columns(row, show_all)
            table_row.append(row.get('config', {}).get('host_path', ''))
            self.table.add_row(table_row)

    def format(self) -> str:
        """Format the hostPath volume table for display."""
        return 'Kubernetes HostPath Volumes:\n' + str(self.table)


class RunPodVolumeTable(VolumeTable):
    """The RunPod volume table."""

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the RunPod volume table."""
        #  If show_all is False, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY
        #  If show_all is True, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY, NAME_ON_CLOUD

        if show_all:
            columns = _BASIC_COLUMNS + ['NAME_ON_CLOUD']
        else:
            columns = _BASIC_COLUMNS

        table = log_utils.create_table(columns)
        return table

    def _add_rows(self,
                  volumes: List[responses.VolumeRecord],
                  show_all: bool = False) -> None:
        """Add rows to the RunPod volume table."""
        for row in volumes:
            table_row = self._get_row_base_columns(row, show_all)
            if show_all:
                table_row.append(row.get('name_on_cloud', ''))

            self.table.add_row(table_row)

    def format(self) -> str:
        """Format the RunPod volume table for display."""
        return 'RunPod Network Volumes:\n' + str(self.table)


def format_volume_table(volumes: List[responses.VolumeRecord],
                        show_all: bool = False) -> str:
    """Format the volume table for display.

    Args:
        volume_table (dict): The volume table.

    Returns:
        str: The formatted volume table.
    """
    volumes_per_type: Dict[str, List[responses.VolumeRecord]] = {}
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
        if table_str:
            table_str += '\n\n'
        if volume_type == volume.VolumeType.PVC.value:
            pvc_table = PVCVolumeTable(volume_list, show_all)
            table_str += pvc_table.format()
        elif volume_type == volume.VolumeType.HOSTPATH.value:
            hostpath_table = HostPathVolumeTable(volume_list, show_all)
            table_str += hostpath_table.format()
        elif volume_type == volume.VolumeType.RUNPOD_NETWORK_VOLUME.value:
            runpod_table = RunPodVolumeTable(volume_list, show_all)
            table_str += runpod_table.format()
    if table_str:
        return table_str
    else:
        return 'No existing volumes.'
