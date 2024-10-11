"""Utilities for sky status."""
from typing import Any, Callable, Dict, List, Optional, Tuple

import click
import colorama

from sky import backends
from sky import clouds as sky_clouds
from sky import resources as resources_lib
from sky import status_lib
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import resources_utils

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
                 trunc_length: int = 0,
                 show_by_default: bool = True):
        self.name = name
        self.calc_func = calc_func
        self.trunc_length = trunc_length
        self.show_by_default = show_by_default

    def calc(self, record):
        val = self.calc_func(record)
        if self.trunc_length != 0:
            val = common_utils.truncate_long_string(str(val), self.trunc_length)
        return val


def show_status_table(cluster_records: List[_ClusterRecord],
                      show_all: bool) -> int:
    """Compute cluster table values and display.

    Returns:
        Number of pending auto{stop,down} clusters that are not already
        STOPPED.
    """
    # TODO(zhwu): Update the information for autostop clusters.

    status_columns = [
        StatusColumn('NAME', _get_name),
        StatusColumn('LAUNCHED', _get_launched),
        StatusColumn('RESOURCES',
                     _get_resources,
                     trunc_length=70 if not show_all else 0),
        StatusColumn('REGION', _get_region, show_by_default=False),
        StatusColumn('ZONE', _get_zone, show_by_default=False),
        StatusColumn('STATUS', _get_status_colored),
        StatusColumn('AUTOSTOP', _get_autostop),
        StatusColumn('HEAD_IP', _get_head_ip, show_by_default=False),
        StatusColumn('COMMAND',
                     _get_command,
                     trunc_length=COMMAND_TRUNC_LENGTH if not show_all else 0),
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
    else:
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
                           controller_name: Optional[str] = None):
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
        StatusColumn('DURATION', _get_duration, trunc_length=20),
        StatusColumn('RESOURCES',
                     _get_resources_for_cost_report,
                     trunc_length=70 if not show_all else 0),
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
        if controller_name is not None:
            autostop_minutes = constants.CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP
            click.echo(f'\n{colorama.Fore.CYAN}{colorama.Style.BRIGHT}'
                       f'{controller_name}{colorama.Style.RESET_ALL}'
                       f'{colorama.Style.DIM} (will be autostopped if idle for '
                       f'{autostop_minutes}min)'
                       f'{colorama.Style.RESET_ALL}')
        else:
            click.echo(f'{colorama.Fore.CYAN}{colorama.Style.BRIGHT}Clusters'
                       f'{colorama.Style.RESET_ALL}')
        click.echo(cluster_table)


# Some of these lambdas are invoked on both _ClusterRecord and
# _ClusterCostReportRecord, which is okay as we guarantee the queried fields
# exist in those cases.
_get_name = (lambda cluster_record: cluster_record['name'])
_get_launched = (lambda cluster_record: log_utils.readable_time_duration(
    cluster_record['launched_at']))
_get_region = (
    lambda clusters_status: clusters_status['handle'].launched_resources.region)
_get_command = (lambda cluster_record: cluster_record['last_use'])
_get_duration = (lambda cluster_record: log_utils.readable_time_duration(
    0, cluster_record['duration'], absolute=True))


def _get_status(cluster_record: _ClusterRecord) -> status_lib.ClusterStatus:
    return cluster_record['status']


def _get_status_colored(cluster_record: _ClusterRecord) -> str:
    return _get_status(cluster_record).colored_str()


def _get_resources(cluster_record: _ClusterRecord) -> str:
    handle = cluster_record['handle']
    if isinstance(handle, backends.LocalDockerResourceHandle):
        resources_str = 'docker'
    elif isinstance(handle, backends.CloudVmRayResourceHandle):
        resources_str = resources_utils.get_readable_resources_repr(handle)
    else:
        raise ValueError(f'Unknown handle type {type(handle)} encountered.')
    return resources_str


def _get_zone(cluster_record: _ClusterRecord) -> str:
    zone_str = cluster_record['handle'].launched_resources.zone
    if zone_str is None:
        zone_str = '-'
    return zone_str


def _get_autostop(cluster_record: _ClusterRecord) -> str:
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


def _get_head_ip(cluster_record: _ClusterRecord) -> str:
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


# ---- 'sky cost-report' helper functions below ----


def _get_status_value_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord) -> int:
    status = cluster_cost_report_record['status']
    if status is None:
        return -1
    return 1


def _get_status_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord) -> str:
    status = cluster_cost_report_record['status']
    if status is None:
        return f'{colorama.Style.DIM}TERMINATED{colorama.Style.RESET_ALL}'
    return status.colored_str()


def _get_resources_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord) -> str:
    launched_nodes = cluster_cost_report_record['num_nodes']
    launched_resources = cluster_cost_report_record['resources']

    launched_resource_str = str(launched_resources)
    resources_str = (f'{launched_nodes}x '
                     f'{launched_resource_str}')

    return resources_str


def _get_price_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord) -> str:
    launched_nodes = cluster_cost_report_record['num_nodes']
    launched_resources = cluster_cost_report_record['resources']

    hourly_cost = (launched_resources.get_cost(3600) * launched_nodes)
    price_str = f'$ {hourly_cost:.2f}'
    return price_str


def _get_estimated_cost_for_cost_report(
        cluster_cost_report_record: _ClusterCostReportRecord) -> str:
    cost = cluster_cost_report_record['total_cost']

    if not cost:
        return '-'

    return f'$ {cost:.2f}'


def show_kubernetes_cluster_status_table(clusters: List[Any],
                                         show_all: bool) -> None:
    """Compute cluster table values and display for Kubernetes clusters."""
    status_columns = [
        StatusColumn('USER', lambda c: c['user']),
        StatusColumn('NAME', lambda c: c['cluster_name']),
        StatusColumn(
            'LAUNCHED',
            lambda c: log_utils.readable_time_duration(c['launched_at'])),
        StatusColumn('RESOURCES',
                     lambda c: c['resources_str'],
                     trunc_length=70 if not show_all else 0),
        StatusColumn('STATUS', lambda c: c['status'].colored_str()),
        # TODO(romilb): We should consider adding POD_NAME field here when --all
        #  is passed to help users fetch pod name programmatically.
    ]

    columns = [
        col.name for col in status_columns if col.show_by_default or show_all
    ]
    cluster_table = log_utils.create_table(columns)

    # Sort table by user, then by cluster name
    sorted_clusters = sorted(clusters,
                             key=lambda c: (c['user'], c['cluster_name']))

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


def process_skypilot_pods(
    pods: List[Any],
    context: Optional[str] = None
) -> Tuple[List[Dict[Any, Any]], Dict[str, Any], Dict[str, Any]]:
    """Process SkyPilot pods on k8s to extract cluster and controller info.

    Args:
        pods: List of Kubernetes pod objects.
        context: Kubernetes context name, used to detect GPU label formatter.

    Returns:
        A tuple containing:
        - List of dictionaries with cluster information.
        - Dictionary of job controller information.
        - Dictionary of serve controller information.

        Each dictionary contains the following keys:
            'cluster_name_on_cloud': The cluster_name_on_cloud used by SkyPilot
            'cluster_name': The cluster name without the user hash
            'user': The user who created the cluster. Fetched from pod label
            'status': The cluster status (assumed UP if pod exists)
            'pods': List of pod objects in the cluster
            'launched_at': Timestamp of when the cluster was launched
            'resources': sky.Resources object for the cluster
    """
    clusters: Dict[str, Dict] = {}
    jobs_controllers: Dict[str, Dict] = {}
    serve_controllers: Dict[str, Dict] = {}

    for pod in pods:
        cluster_name_on_cloud = pod.metadata.labels.get('skypilot-cluster')
        cluster_name = cluster_name_on_cloud.rsplit(
            '-', 1
        )[0]  # Remove the user hash to get cluster name (e.g., mycluster-2ea4)

        # Check if cluster name is name of a controller
        # Can't use controller_utils.Controllers.from_name(cluster_name)
        # because hash is different across users
        if 'controller' in cluster_name_on_cloud:
            start_time = pod.status.start_time.timestamp()
            controller_info = {
                'cluster_name_on_cloud': cluster_name_on_cloud,
                'cluster_name': cluster_name,
                'user': pod.metadata.labels.get('skypilot-user'),
                'status': status_lib.ClusterStatus.UP,
                # Assuming UP if pod exists
                'pods': [pod],
                'launched_at': start_time
            }
            if 'sky-jobs-controller' in cluster_name_on_cloud:
                jobs_controllers[cluster_name_on_cloud] = controller_info
            elif 'sky-serve-controller' in cluster_name_on_cloud:
                serve_controllers[cluster_name_on_cloud] = controller_info

        if cluster_name_on_cloud not in clusters:
            # Parse the start time for the cluster
            start_time = pod.status.start_time
            if start_time is not None:
                start_time = pod.status.start_time.timestamp()

            # Parse resources
            cpu_request = kubernetes_utils.parse_cpu_or_gpu_resource(
                pod.spec.containers[0].resources.requests.get('cpu', '0'))
            memory_request = kubernetes_utils.parse_memory_resource(
                pod.spec.containers[0].resources.requests.get('memory', '0'),
                unit='G')
            gpu_count = kubernetes_utils.parse_cpu_or_gpu_resource(
                pod.spec.containers[0].resources.requests.get(
                    'nvidia.com/gpu', '0'))
            if gpu_count > 0:
                label_formatter, _ = (
                    kubernetes_utils.detect_gpu_label_formatter(context))
                assert label_formatter is not None, (
                    'GPU label formatter cannot be None if there are pods '
                    f'requesting GPUs: {pod.metadata.name}')
                gpu_label = label_formatter.get_label_key()
                # Get GPU name from pod node selector
                if pod.spec.node_selector is not None:
                    gpu_name = label_formatter.get_accelerator_from_label_value(
                        pod.spec.node_selector.get(gpu_label))

            resources = resources_lib.Resources(
                cloud=sky_clouds.Kubernetes(),
                cpus=int(cpu_request),
                memory=int(memory_request),
                accelerators=(f'{gpu_name}:{gpu_count}'
                              if gpu_count > 0 else None))
            if pod.status.phase == 'Pending':
                # If pod is pending, do not show it in the status
                continue

            clusters[cluster_name_on_cloud] = {
                'cluster_name_on_cloud': cluster_name_on_cloud,
                'cluster_name': cluster_name,
                'user': pod.metadata.labels.get('skypilot-user'),
                'status': status_lib.ClusterStatus.UP,
                'pods': [],
                'launched_at': start_time,
                'resources': resources,
            }
        else:
            # Update start_time if this pod started earlier
            pod_start_time = pod.status.start_time
            if pod_start_time is not None:
                pod_start_time = pod_start_time.timestamp()
                if pod_start_time < clusters[cluster_name_on_cloud][
                        'launched_at']:
                    clusters[cluster_name_on_cloud][
                        'launched_at'] = pod_start_time
        clusters[cluster_name_on_cloud]['pods'].append(pod)
    # Update resources_str in clusters:
    for cluster_name, cluster in clusters.items():
        resources = cluster['resources']
        num_pods = len(cluster['pods'])
        resources_str = f'{num_pods}x {resources}'
        cluster['resources_str'] = resources_str
    return list(clusters.values()), jobs_controllers, serve_controllers
