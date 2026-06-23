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


def format_managed_job_details(records: List[responses.ManagedJobRecord],
                               user_yaml: Optional[str] = None) -> str:
    """Format a single managed job's details for display.

    Renders job-level metadata (status, resources, entrypoint, git commit,
    etc.) followed by the original task YAML, similar to ``kubectl describe``.
    Mirrors the dashboard's job detail view (two resource lines — requested vs.
    used — plus external links). ``records`` is the queue result filtered to
    one job; a pipeline has one record per task that all share the same user
    YAML.

    Args:
        records: the per-task records for a single job.
        user_yaml: the task YAML to show. Falls back to the raw stored YAML on
            the record; callers should pass a display-formatted version.
    """
    primary = records[0]
    if user_yaml is None:
        user_yaml = primary.user_yaml

    git_commit = (primary.metadata or {}).get('git_commit')

    status_str = primary.status.colored_str() if primary.status else '-'
    duration = log_utils.readable_time_duration(primary.start_at,
                                                primary.end_at,
                                                absolute=True)
    recoveries = ('-'
                  if primary.recovery_count is None else primary.recovery_count)
    # Two resource lines, matching the dashboard: what the user asked for, and
    # what the cluster actually ran (empty once a terminal job's handle is
    # gone).
    requested = primary.resources or '-'
    used = (primary.cluster_resources_full or primary.cluster_resources or '-')

    rows = [
        ('Name', primary.job_name or '-'),
        ('Status', status_str),
        ('User', primary.user_name or '-'),
        ('Workspace', primary.workspace or '-'),
        ('Pool', primary.pool or '-'),
        ('Submitted', log_utils.readable_time_duration(primary.submitted_at)),
        ('Duration', duration or '-'),
        ('Recoveries', recoveries),
        ('Requested', requested),
        ('Resources', used),
        ('Infra', primary.infra or '-'),
        ('Entrypoint', primary.entrypoint or '-'),
    ]
    # Only show git commit / failure / links when present (like the dashboard),
    # rather than padding the view with empty '-' rows.
    if git_commit:
        rows.append(('Git commit', git_commit))
    if primary.failure_reason:
        rows.append(('Failure', primary.failure_reason))
    if primary.links:
        links_str = ', '.join(
            f'{label}: {url}' for label, url in primary.links.items())
        rows.append(('Links', links_str))

    width = max(len(key) for key, _ in rows)
    lines = [f'Managed job {primary.job_id}', '']
    lines += [f'  {key.ljust(width)}   {value}' for key, value in rows]

    if user_yaml:
        lines += ['', 'Task YAML', '-' * 50, user_yaml.rstrip('\n')]
    return '\n'.join(lines)


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


class PVCVolumeTable(VolumeTable):
    """The PVC volume table."""

    def __init__(self,
                 volumes: List[responses.VolumeRecord],
                 show_all: bool = False):
        # Check if any volume has an error before creating the table
        self._has_errors = any(row.get('error_message') for row in volumes)
        super().__init__(volumes, show_all)

    def _create_table(self, show_all: bool = False) -> prettytable.PrettyTable:
        """Create the PVC volume table."""
        #  If show_all is False, show the table with the columns:
        #   NAME, TYPE, INFRA, SIZE, USER, WORKSPACE,
        #   AGE, STATUS, LAST_USE, USED_BY, IS_EPHEMERAL
        #   (+ MESSAGE if any volume is not ready)
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
        elif self._has_errors:
            # Show MESSAGE column even without show_all if there are issues
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
                # Add error message
                error_msg = row.get('error_message', '')
                table_row.append(error_msg if error_msg else '-')
            elif self._has_errors:
                # Show error message even without show_all if there are errors
                error_msg = row.get('error_message', '')
                # Truncate error message for display
                if error_msg:
                    error_msg = common_utils.truncate_long_string(
                        error_msg, constants.ERROR_MESSAGE_TRUNC_LENGTH)
                table_row.append(error_msg if error_msg else '-')

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
