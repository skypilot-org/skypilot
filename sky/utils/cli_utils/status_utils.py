"""Utilities for sky status."""
from typing import Any, Callable, Dict, List
import click
import colorama

from sky import backends
from sky.backends import backend_utils
from sky.utils import common_utils
from sky.utils.cli_utils import cli_utils
from sky.utils import log_utils

_COMMAND_TRUNC_LENGTH = 25


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
            val = cli_utils.truncate_long_string(str(val), self.trunc_length)
        return val


def show_status_table(cluster_records: List[Dict[str, Any]], show_all: bool):
    """Compute cluster table values and display."""
    # TODO(zhwu): Update the information for auto-stop clusters.

    status_columns = [
        StatusColumn('NAME', _get_name),
        StatusColumn('LAUNCHED', _get_launched),
        StatusColumn('RESOURCES',
                     _get_resources,
                     trunc_length=70 if not show_all else 0),
        StatusColumn('REGION', _get_region, show_by_default=False),
        StatusColumn('ZONE', _get_zone, show_by_default=False),
        StatusColumn('HOURLY_PRICE', _get_price, show_by_default=False),
        StatusColumn('STATUS', _get_status),
        StatusColumn('AUTOSTOP', _get_autostop),
        StatusColumn('COMMAND',
                     _get_command,
                     trunc_length=_COMMAND_TRUNC_LENGTH if not show_all else 0),
    ]

    columns = []
    for status_column in status_columns:
        if status_column.show_by_default or show_all:
            columns.append(status_column.name)
    cluster_table = log_utils.create_table(columns)

    pending_autostop = 0
    for record in cluster_records:
        row = []
        for status_column in status_columns:
            if status_column.show_by_default or show_all:
                row.append(status_column.calc(record))
        cluster_table.add_row(row)
        pending_autostop += _is_pending_autostop(record)

    if cluster_records:
        click.echo(cluster_table)
        if pending_autostop:
            click.echo(
                '\n'
                f'You have {pending_autostop} clusters with autostop scheduled.'
                ' Refresh statuses with: `sky status --refresh`.')
    else:
        click.echo('No existing clusters.')


def show_local_status_table(local_clusters: List[str]):
    """Lists all local clusters.

    Lists both uninitialized and initialized local clusters. Uninitialized
    local clusters are clusters that have their cluster configs in
    ~/.sky/local. Sky does not know if the cluster is valid yet and does not
    know what resources the cluster has. Hence, this func will return blank
    values for such clusters. Initialized local clusters are created using
    `sky launch`. Sky understands what types of resources are on the nodes and
    has ran at least one job on the cluster.
    """
    clusters_status = backend_utils.get_clusters(
        include_reserved=False,
        refresh=False,
        cloud_filter=backend_utils.CloudFilter.LOCAL)
    columns = [
        'NAME',
        'USER',
        'HEAD_IP',
        'RESOURCES',
        'COMMAND',
    ]

    cluster_table = log_utils.create_table(columns)
    names = []
    # Handling for initialized clusters.
    for cluster_status in clusters_status:
        handle = cluster_status['handle']
        config_path = handle.cluster_yaml
        config = common_utils.read_yaml(config_path)
        username = config['auth']['ssh_user']

        if not isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
            raise ValueError(f'Unknown handle type {type(handle)} encountered.')

        if (handle.launched_nodes is not None and
                handle.launched_resources is not None):
            if hasattr(handle,
                       'local_handle') and handle.local_handle is not None:
                local_cluster_resources = [
                    r.accelerators
                    for r in handle.local_handle['cluster_resources']
                ]
                # Replace with (no GPUs)
                local_cluster_resources = [
                    r if r is not None else '(no GPUs)'
                    for r in local_cluster_resources
                ]
                head_ip = handle.local_handle['ips'][0]
            else:
                local_cluster_resources = []
                head_ip = ''
            for idx, resource in enumerate(local_cluster_resources):
                if not bool(resource):
                    local_cluster_resources[idx] = None
            resources_str = '[{}]'.format(', '.join(
                map(str, local_cluster_resources)))
        command_str = cluster_status['last_use']
        cluster_name = handle.cluster_name
        row = [
            # NAME
            cluster_name,
            # USER
            username,
            # HEAD_IP
            head_ip,
            # RESOURCES
            resources_str,
            # COMMAND
            cli_utils.truncate_long_string(command_str, _COMMAND_TRUNC_LENGTH),
        ]
        names.append(cluster_name)
        cluster_table.add_row(row)

    # Handling for uninitialized clusters.
    for clus in local_clusters:
        if clus not in names:
            row = [
                # NAME
                clus,
                # USER
                '-',
                # HEAD_IP
                '-',
                # RESOURCES
                '-',
                # COMMAND
                '-',
            ]
            cluster_table.add_row(row)

    if clusters_status or local_clusters:
        click.echo(f'\n{colorama.Fore.CYAN}{colorama.Style.BRIGHT}Local '
                   f'clusters:{colorama.Style.RESET_ALL}')
        click.echo(cluster_table)


_get_name = (lambda cluster_status: cluster_status['name'])
_get_launched = (lambda cluster_status: log_utils.readable_time_duration(
    cluster_status['launched_at']))
_get_region = (
    lambda clusters_status: clusters_status['handle'].launched_resources.region)
_get_status = (lambda cluster_status: cluster_status['status'].value)
_get_command = (lambda cluster_status: cluster_status['last_use'])


def _get_resources(cluster_status):
    handle = cluster_status['handle']
    resources_str = '<initializing>'
    if isinstance(handle, backends.LocalDockerBackend.ResourceHandle):
        resources_str = 'docker'
    elif isinstance(handle, backends.CloudVmRayBackend.ResourceHandle):
        if (handle.launched_nodes is not None and
                handle.launched_resources is not None):
            launched_resource_str = str(handle.launched_resources)
            resources_str = (f'{handle.launched_nodes}x '
                             f'{launched_resource_str}')
    else:
        raise ValueError(f'Unknown handle type {type(handle)} encountered.')
    return resources_str


def _get_zone(cluster_status):
    zone_str = cluster_status['handle'].launched_resources.zone
    if zone_str is None:
        zone_str = '-'
    return zone_str


def _get_autostop(cluster_status):
    autostop_str = '-'
    if cluster_status['autostop'] >= 0:
        # TODO(zhwu): check the status of the autostop cluster.
        autostop_str = str(cluster_status['autostop']) + ' min'
    return autostop_str


def _get_price(cluster_status):
    handle = cluster_status['handle']
    hourly_cost = (handle.launched_resources.get_cost(3600) *
                   handle.launched_nodes)
    price_str = f'$ {hourly_cost:.3f}'
    return price_str


def _is_pending_autostop(cluster_status):
    return _get_autostop(cluster_status) != '-' and _get_status(
        cluster_status) != 'STOPPED'
