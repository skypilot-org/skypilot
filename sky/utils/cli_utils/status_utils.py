"""Utilities for sky status."""
from typing import Callable
import click

from sky import backends
from sky.backends import backend_utils
from sky.utils.cli_utils import cli_utils
from sky.skylet.utils import log_utils


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

    def calc(self, cluster_status):
        val = self.calc_func(cluster_status)
        if self.trunc_length != 0:
            val = cli_utils.truncate_long_string(str(val), self.trunc_length)
        return val


def show_status_table(show_all: bool, refresh: bool):
    """Compute cluster table values and display."""
    # TODO(zhwu): Update the information for auto-stop clusters.
    clusters_status = backend_utils.get_clusters(refresh)

    status_columns = [
        StatusColumn('NAME', _get_name),
        StatusColumn('LAUNCHED', _get_launched),
        StatusColumn('RESOURCES', _get_resources),
        StatusColumn('REGION', _get_region, show_by_default=False),
        StatusColumn('STATUS', _get_status),
        StatusColumn('DURATION', _get_duration, show_by_default=False),
        StatusColumn('AUTOSTOP', _get_autostop),
        StatusColumn('COMMAND',
                     _get_command,
                     trunc_length=35 if not show_all else 0),
        StatusColumn('HOURLY_PRICE', _get_price, show_by_default=False)
    ]

    columns = []
    for status_column in status_columns:
        if status_column.show_by_default or show_all:
            columns.append(status_column.name)
    cluster_table = log_utils.create_table(columns)

    pending_autostop = 0
    for cluster_status in clusters_status:
        row = []
        for status_column in status_columns:
            if status_column.show_by_default or show_all:
                row.append(status_column.calc(cluster_status))
        cluster_table.add_row(row)
        pending_autostop += _is_pending_autostop(cluster_status)

    if clusters_status:
        click.echo(cluster_table)
        if pending_autostop:
            click.echo(
                '\n'
                f'You have {pending_autostop} clusters with autostop scheduled.'
                ' Refresh statuses with: `sky status --refresh`.')
    else:
        click.echo('No existing clusters.')


_get_name = (lambda cluster_status: cluster_status['name'])
_get_launched = (lambda cluster_status: log_utils.readable_time_duration(
    cluster_status['launched_at']))
_get_region = (
    lambda clusters_status: clusters_status['handle'].launched_resources.region)
_get_status = (lambda cluster_status: cluster_status['status'].value)
_get_duration = (lambda cluster_status: log_utils.readable_time_duration(
    cluster_status['launched_at']))
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
