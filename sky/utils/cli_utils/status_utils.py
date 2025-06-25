"""Utilities for sky status."""
import typing
from typing import Any, Callable, Dict, List, Optional

import click
import colorama

from sky import backends
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky.provision.kubernetes import utils as kubernetes_utils

if typing.TYPE_CHECKING:
    from sky.provision.kubernetes import utils as kubernetes_utils

COMMAND_TRUNC_LENGTH = 25
NUM_COST_REPORT_LINES = 5

# A record in global_user_state's 'clusters' table.
_ClusterRecord = Dict[str, Any]
# A record returned by core.cost_report(); see its docstr for all fields.
_ClusterCostReportRecord = Dict[str, Any]


class StatusColumn:
    """One column of the displayed cluster table"""

    def __init__(self,
                 name: str,
                 calc_func: Callable,
                 truncate: bool = True,
                 show_by_default: bool = True):
        self.name = name
        self.calc_func = calc_func
        self.truncate: bool = truncate
        self.show_by_default = show_by_default

    def calc(self, record):
        val = self.calc_func(record, self.truncate)
        return val


def show_status_table(cluster_records: List[_ClusterRecord],
                      show_all: bool,
                      show_user: bool,
                      query_clusters: Optional[List[str]] = None,
                      show_workspaces: bool = False) -> int:
    """Compute cluster table values and display.

    Returns:
        Number of pending auto{stop,down} clusters that are not already
        STOPPED.
    """
    # TODO(zhwu): Update the information for autostop clusters.
    status_columns = [
        StatusColumn('NAME', _get_name),
    ]
    if show_user:
        status_columns.append(StatusColumn('USER', _get_user_name))
        status_columns.append(
            StatusColumn('USER_ID', _get_user_hash, show_by_default=False))

    status_columns += [
        StatusColumn('WORKSPACE',
                     _get_workspace,
                     show_by_default=show_workspaces),
        StatusColumn('INFRA', _get_infra, truncate=not show_all),
        StatusColumn('RESOURCES', _get_resources, truncate=not show_all),
        StatusColumn('STATUS', _get_status_colored),
        StatusColumn('AUTOSTOP', _get_autostop),
        StatusColumn('LAUNCHED', _get_launched),
    ]
    if show_all:
        status_columns += [
            StatusColumn('HEAD_IP', _get_head_ip, show_by_default=False),
            StatusColumn('COMMAND',
                         _get_command,
                         truncate=not show_all,
                         show_by_default=False),
        ]

    columns = []
    for status_column in status_columns:
        if status_column.show_by_default or show_all:
            columns.append(status_column.name)
    cluster_table = log_utils.create_table(columns)

    num_pending_autostop = 0
    for record in cluster_records:
        row = []
        for status_column in status_columns:
            if status_column.show_by_default or show_all:
                row.append(status_column.calc(record))
        cluster_table.add_row(row)
        num_pending_autostop += _is_pending_autostop(record)

    if cluster_records:
        click.echo(cluster_table)

    if query_clusters:
        cluster_names = {record['name'] for record in cluster_records}
        not_found_clusters = [
            repr(cluster)
            for cluster in query_clusters
            if cluster not in cluster_names
        ]
        if not_found_clusters:
            cluster_str = 'Cluster'
            if len(not_found_clusters) > 1:
                cluster_str += 's'
            cluster_str += ' '
            cluster_str += ', '.join(not_found_clusters)
            click.echo(f'{cluster_str} not found.')
    elif not cluster_records:
        click.echo('No existing clusters.')
    return num_pending_autostop


def get_total_cost_of_displayed_records(
        cluster_records: List[_ClusterCostReportRecord], display_all: bool):
    """Compute total cost of records to be displayed in cost report."""
    cluster_records.sort(
        key=lambda report: -_get_status_value_for_cost_report(report))

    displayed_records = cluster_records[:NUM_COST_REPORT_LINES]
    if display_all:
        displayed_records = cluster_records

    total_cost = sum(record['total_cost'] for record in displayed_records)
    return total_cost


def show_cost_report_table(cluster_records: List[_ClusterCostReportRecord],
                           show_all: bool,
                           controller_name: Optional[str] = None,
                           days: Optional[int] = None):
    """Compute cluster table values and display for cost report.

    For each cluster, this shows: cluster name, resources, launched time,
    duration that cluster was up, and total estimated cost.

    The estimated cost column indicates the price for the cluster based on the
    type of resources being used and the duration of use up until now. This
    means if the cluster is UP, successive calls to cost-report will show
    increasing price.

    The estimated cost is calculated based on the local cache of the cluster
    status, and may not be accurate for:

      - clusters with autostop/use_spot set; or

      - clusters that were terminated/stopped on the cloud console.

    Returns:
        Number of pending auto{stop,down} clusters.
    """
    # TODO(zhwu): Update the information for autostop clusters.

    status_columns = [
        StatusColumn('NAME', _get_name),
        StatusColumn('LAUNCHED', _get_launched),
        StatusColumn('DURATION', _get_duration, truncate=False),
        StatusColumn('RESOURCES',
                     _get_resources_for_cost_report,
                     truncate=False),
        StatusColumn('STATUS',
                     _get_status_for_cost_report,
                     show_by_default=True),
        StatusColumn('COST/hr',
                     _get_price_for_cost_report,
                     show_by_default=True),
        StatusColumn('COST (est.)',
                     _get_estimated_cost_for_cost_report,
                     show_by_default=True),
    ]

    columns = []
    for status_column in status_columns:
        if status_column.show_by_default or show_all:
            columns.append(status_column.name)
    cluster_table = log_utils.create_table(columns)

    num_lines_to_display = NUM_COST_REPORT_LINES
    if show_all:
        num_lines_to_display = len(cluster_records)

    # prioritize showing non-terminated clusters in table
    cluster_records.sort(
        key=lambda report: -_get_status_value_for_cost_report(report))

    for record in cluster_records[:num_lines_to_display]:
        row = []
        for status_column in status_columns:
            if status_column.show_by_default or show_all:
                row.append(status_column.calc(record))
        cluster_table.add_row(row)

    if cluster_records:
        controller_record = cluster_records[0]
        if controller_name is not None:
            autostop = controller_record.get('autostop', None)
            autostop_str = ''
            if autostop is not None:
                autostop_str = (f'{colorama.Style.DIM} (will be autostopped if '
                                f'idle for {autostop}min)'
                                f'{colorama.Style.RESET_ALL}')
            click.echo(f'\n{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
                       f'{controller_name}{colorama.Style.RESET_ALL}'
                       f'{autostop_str}')
        else:
            days_str = '' if days is None else f' (last {days} days)'
            click.echo(f'{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
                       f'Clusters{days_str}'
                       f'{colorama.Style.RESET_ALL}')
        click.echo(cluster_table)


# Some of these lambdas are invoked on both _ClusterRecord and
# _ClusterCostReportRecord, which is okay as we guarantee the queried fields
# exist in those cases.
_get_name = (lambda cluster_record, _: cluster_record['name'])
_get_user_hash = (lambda cluster_record, _: cluster_record['user_hash'])
_get_user_name = (
    lambda cluster_record, _: cluster_record.get('user_name', '-'))
_get_launched = (lambda cluster_record, _: log_utils.readable_time_duration(
    cluster_record['launched_at']))
_get_duration = (lambda cluster_record, _: log_utils.readable_time_duration(
    0, cluster_record['duration'], absolute=True))


def _get_command(cluster_record: _ClusterRecord, truncate: bool = True) -> str:
    command = cluster_record.get('last_use', '-')
    if truncate:
        return common_utils.truncate_long_string(command, COMMAND_TRUNC_LENGTH)
    return command


def _get_status(cluster_record: _ClusterRecord,
                truncate: bool = True) -> status_lib.ClusterStatus:
    del truncate
    return cluster_record['status']


def _get_workspace(cluster_record: _ClusterRecord,
                   truncate: bool = True) -> str:
    del truncate
    return cluster_record['workspace']


def _get_status_colored(cluster_record: _ClusterRecord,
                        truncate: bool = True) -> str:
    del truncate
    return _get_status(cluster_record).colored_str()


def _get_resources(cluster_record: _ClusterRecord,
                   truncate: bool = True) -> str:
    """Get the resources information for a cluster.

    Returns:
        A string in one of the following formats:
        - For cloud VMs: "Nx instance_type" (e.g., "1x m6i.2xlarge")
        - For K8S/SSH: "Nx (...)"
        - "-" if no resource information is available
    """
    handle = cluster_record['handle']
    if isinstance(handle, backends.CloudVmRayResourceHandle):
        launched_resources = handle.launched_resources
        if launched_resources is None:
            return '-'

        # For cloud VMs, show instance type directly
        # For K8S/SSH, show (...) as the resource type
        resources_str = cluster_record.get('resources_str', None)
        if not truncate:
            resources_str_full = cluster_record.get('resources_str_full', None)
            if resources_str_full is not None:
                resources_str = resources_str_full
        if resources_str is None:
            resources_str = resources_utils.get_readable_resources_repr(
                handle, simplify=truncate)

        return resources_str
    return '-'


def _get_autostop(cluster_record: _ClusterRecord, truncate: bool = True) -> str:
    del truncate
    autostop_str = ''
    separation = ''
    if cluster_record['autostop'] >= 0:
        # TODO(zhwu): check the status of the autostop cluster.
        autostop_str = str(cluster_record['autostop']) + 'm'
        separation = ' '

    if cluster_record['to_down']:
        autostop_str += f'{separation}(down)'
    if autostop_str == '':
        autostop_str = '-'
    return autostop_str


def _get_head_ip(cluster_record: _ClusterRecord, truncate: bool = True) -> str:
    del truncate  # Unused
    handle = cluster_record['handle']
    if not isinstance(handle, backends.CloudVmRayResourceHandle):
        return '-'
    if handle.head_ip is None:
        return '-'
    return handle.head_ip


def _is_pending_autostop(cluster_record: _ClusterRecord) -> bool:
    # autostop < 0 means nothing scheduled.
    return cluster_record['autostop'] >= 0 and _get_status(
        cluster_record) != status_lib.ClusterStatus.STOPPED


def _get_infra(cluster_record: _ClusterRecord, truncate: bool = True) -> str:
    """Get the infrastructure information for a cluster.

    Returns:
        A string in one of the following formats:
        - AWS/region (e.g., "AWS/us-east-1")
        - K8S/context (e.g., "K8S/my-ctx")
        - SSH/hostname (e.g., "SSH/my-tobi-box")
        - "-" if no infrastructure information is available
    """
    handle = cluster_record['handle']
    if isinstance(handle, backends.CloudVmRayResourceHandle):
        if handle.launched_resources is None:
            # If launched_resources is None, try to get infra from the record
            return cluster_record.get('infra', '-')
        return handle.launched_resources.infra.formatted_str(truncate)
    return '-'


# ---- 'sky cost-report' helper functions below ----


def _get_status_value_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord,
        truncate: bool = True) -> int:
    del truncate
    status = cluster_cost_report_record['status']
    if status is None:
        return -1
    return 1


def _get_status_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord,
        truncate: bool = True) -> str:
    del truncate
    status = cluster_cost_report_record['status']
    if status is None:
        return f'{colorama.Style.DIM}TERMINATED{colorama.Style.RESET_ALL}'
    return status.colored_str()


def _get_resources_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord,
        truncate: bool = True) -> str:
    del truncate
    launched_nodes = cluster_cost_report_record['num_nodes']
    launched_resources = cluster_cost_report_record['resources']

    launched_resource_str = str(launched_resources)
    resources_str = (f'{launched_nodes}x '
                     f'{launched_resource_str}')

    return resources_str


def _get_price_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord,
        truncate: bool = True) -> str:
    del truncate
    launched_nodes = cluster_cost_report_record['num_nodes']
    launched_resources = cluster_cost_report_record['resources']

    hourly_cost = (launched_resources.get_cost(3600) * launched_nodes)
    price_str = f'$ {hourly_cost:.2f}'
    return price_str


def _get_estimated_cost_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord,
        truncate: bool = True) -> str:
    del truncate
    cost = cluster_cost_report_record['total_cost']

    if not cost:
        return '-'

    return f'$ {cost:.2f}'


def show_kubernetes_cluster_status_table(
        clusters: List['kubernetes_utils.KubernetesSkyPilotClusterInfo'],
        show_all: bool) -> None:
    """Compute cluster table values and display for Kubernetes clusters."""
    status_columns = [
        StatusColumn('USER', lambda c, _: c.user),
        StatusColumn('NAME', lambda c, _: c.cluster_name),
        StatusColumn('RESOURCES', lambda c, _: c.resources_str, truncate=False),
        StatusColumn('STATUS', lambda c, _: c.status.colored_str()),
        StatusColumn(
            'LAUNCHED',
            lambda c, _: log_utils.readable_time_duration(c.launched_at)),
        # TODO(romilb): We should consider adding POD_NAME field here when --all
        #  is passed to help users fetch pod name programmatically.
    ]

    columns = [
        col.name for col in status_columns if col.show_by_default or show_all
    ]
    cluster_table = log_utils.create_table(columns)

    # Sort table by user, then by cluster name
    sorted_clusters = sorted(clusters, key=lambda c: (c.user, c.cluster_name))

    for cluster in sorted_clusters:
        row = []
        for status_column in status_columns:
            if status_column.show_by_default or show_all:
                row.append(status_column.calc(cluster))
        cluster_table.add_row(row)

    if clusters:
        click.echo(f'{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
                   f'SkyPilot clusters'
                   f'{colorama.Style.RESET_ALL}')
        click.echo(cluster_table)
    else:
        click.echo('No SkyPilot resources found in the '
                   'active Kubernetes context.')
