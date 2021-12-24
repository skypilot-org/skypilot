"""The 'sky' command line tool.

Example usage:

  # See available commands.
  >> sky

  # Run a task, described in a yaml file.
  # Provisioning, setup, file syncing are handled.
  >> sky run task.yaml
  >> sky run [-c cluster_name] task.yaml

  # Show the list of running clusters.
  >> sky status

  # Tear down a specific cluster.
  >> sky down -c cluster_name

  # Tear down all existing clusters.
  >> sky down -a

TODO:
- Add support for local Docker backend.  Currently this module is very coupled
  with CloudVmRayBackend, as seen by the many use of ray commands.

NOTE: the order of command definitions in this file corresponds to how they are
listed in "sky --help".  Take care to put logically connected commands close to
each other.
"""
import getpass
import os
import time
from typing import List, Optional

import click
import pendulum
import prettytable

import sky
from sky import backends
from sky import global_user_state
from sky.backends import backend as backend_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend

_CLUSTER_FLAG_HELP = """
A cluster name. If provided, either reuse an existing cluster with that name or
provision a new cluster with that name. Otherwise provision a new cluster with
an autogenerated name.
""".strip()

Path = str
Backend = backends.Backend


def _truncate_long_string(s: str, max_length: int = 50) -> str:
    if len(s) <= max_length:
        return s
    splits = s.split(' ')
    if len(splits[0]) > max_length:
        return splits[0][:max_length] + '...'
    # Truncate on word boundary.
    i = 0
    total = 0
    for i, part in enumerate(splits):
        total += len(part)
        if total >= max_length:
            break
    return ' '.join(splits[:i]) + ' ...'


def _default_interactive_node_name(node_type: str):
    """Returns a deterministic name to refer to the same node."""
    # FIXME: this technically can collide in Azure/GCP with another
    # same-username user.  E.g., sky-gpunode-ubuntu.  Not a problem on AWS
    # which is the current cloud for interactive nodes.
    assert node_type in ('cpunode', 'gpunode', 'tpunode'), node_type
    return f'sky-{node_type}-{getpass.getuser()}'


# TODO: add support for --tmux.
# TODO: skip installing ray to speed up provisioning.
def _create_and_ssh_into_node(
        node_type: str,
        resources: sky.Resources,
        cluster_name: str,
        backend: Optional[backend_lib.Backend] = None,
        port_forward: Optional[List[int]] = None,
        use_screen: bool = False,
):
    """Creates and attaches to an interactive node.

    Args:
        node_type: Type of the interactive node: { 'cpunode', 'gpunode' }.
        resources: Resources to attach to VM.
        cluster_name: a cluster name to identify the interactive node.
        backend: the Backend to use (currently only CloudVmRayBackend).
        port_forward: List of ports to forward.
    """
    assert node_type in ('cpunode', 'gpunode', 'tpunode'), node_type
    with sky.Dag() as dag:
        # TODO: Add conda environment replication
        # should be setup =
        # 'conda env export | grep -v "^prefix: " > environment.yml'
        # && conda env create -f environment.yml
        task = sky.Task(
            node_type,
            workdir=os.getcwd(),
            setup=None,
            run='',
        )
        task.set_resources(resources)

    backend = backend if backend is not None else backends.CloudVmRayBackend()
    backend.register_info(dag=dag)

    dag = sky.optimize(dag)
    task = dag.tasks[0]

    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None or handle.head_ip is None:
        # head_ip would be None if previous provisioning failed.
        handle = backend.provision(task,
                                   task.best_resources,
                                   dryrun=False,
                                   stream_logs=True,
                                   cluster_name=cluster_name)

    # TODO: cd into workdir immediately on the VM
    # TODO: Delete the temporary cluster config yml (or figure out a way to
    # re-use it)
    # Use ssh rather than 'ray attach' to suppress ray messages, speed up
    # connection, and for allowing adding 'cd workdir' in the future.
    # Disable check, since the returncode could be non-zero if the user Ctrl-D.
    commands = backend.ssh_head_command(handle, port_forward=port_forward)
    if use_screen:
        commands += ['screen', '-D', '-R']
    backend_utils.run(commands, shell=False, check=False)
    cluster_name = global_user_state.get_cluster_name_from_handle(handle)

    click.echo('To attach it again:  ', nl=False)
    if cluster_name == _default_interactive_node_name(node_type):
        option = ''
    else:
        option = f' -c {cluster_name}'
    click.secho(f'sky {node_type}{option}', bold=True)
    click.echo('To tear down the node:  ', nl=False)
    click.secho(f'sky down {cluster_name}', bold=True)
    click.echo('To stop the node:  ', nl=False)
    click.secho(f'sky stop {cluster_name}', bold=True)


class _NaturalOrderGroup(click.Group):
    """Lists commands in the order they are defined in this script.

    Reference: https://github.com/pallets/click/issues/513
    """

    def list_commands(self, ctx):
        return self.commands.keys()


@click.group(cls=_NaturalOrderGroup)
def cli():
    pass


@cli.command()
@click.argument('yaml_path', required=True, type=str)
@click.option('--cluster',
              '-c',
              default=None,
              type=str,
              help=_CLUSTER_FLAG_HELP)
@click.option('--dryrun',
              '-n',
              default=False,
              is_flag=True,
              help='If True, do not actually run the job.')
def run(yaml_path: Path, cluster: str, dryrun: bool):
    """Launch a task from a YAML spec (rerun setup if a cluster exists)."""
    with sky.Dag() as dag:
        sky.Task.from_yaml(yaml_path)
    # FIXME: --cluster flag semantics has the following bug.  'sky run -c name
    # x.yml' requiring GCP.  Then change x.yml to requiring AWS.  'sky run -c
    # name x.yml' again.  The GCP cluster is not down'd but should be.  The
    # root cause is due to 'ray up' not dealing with this cross-cloud case (but
    # does correctly deal with in-cloud config changes).
    #
    # This bug also means that the old GCP cluster with the same name is
    # orphaned.  `sky down` would not have an entry pointing to that handle, so
    # would only down the NEW cluster.
    #
    # To fix all of the above, fix/circumvent the bug that 'ray up' not downing
    # old cloud's cluster with the same name.
    sky.execute(dag, dryrun=dryrun, stream_logs=True, cluster_name=cluster)


@cli.command()
@click.argument('yaml_path', required=True, type=str)
@click.option('--cluster',
              '-c',
              required=True,
              type=str,
              help='Name of the existing cluster to execute a task on.')
def exec(yaml_path: Path, cluster: str):  # pylint: disable=redefined-builtin
    """Execute a task from a YAML spec on a cluster (skip setup).

    \b
    Actions performed by this command only include:
      - workdir syncing
      - executing the task's run command
    `sky exec` is thus typically faster than `sky run`, provided a cluster
    already exists.

    All setup steps (provisioning, setup commands, file mounts syncing) are
    skipped.  If any of those specifications changed, this command will not
    reflect those changes.  To ensure a cluster's setup is up to date, use `sky
    run` instead.

    Typical workflow:

      # First command: set up the cluster once.

      >> sky run -c name app.yaml

    \b
      # Starting iterative development...
      # For example, modify local workdir code.
      # Future commands: simply execute the task on the launched cluster.

      >> sky exec -c name app.yaml

      # Simply do "sky run" again if anything other than Task.run is modified:

      >> sky run -c name app.yaml

    """
    handle = global_user_state.get_handle_from_cluster_name(cluster)
    if handle is None:
        raise click.BadParameter(f'Cluster \'{cluster}\' not found.  '
                                 'Use `sky run` to provision first.')
    with sky.Dag() as dag:
        sky.Task.from_yaml(yaml_path)
    sky.execute(dag,
                handle=handle,
                stages=[
                    sky.execution.Stage.SYNC_WORKDIR,
                    sky.execution.Stage.EXEC,
                ])


@cli.command()
@click.option('--all',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Show all information in full.')
def status(all):  # pylint: disable=redefined-builtin
    """Show launched clusters."""
    show_all = all
    clusters_status = global_user_state.get_clusters()
    cluster_table = prettytable.PrettyTable()
    cluster_table.field_names = [
        'NAME',
        'LAUNCHED',
        'RESOURCES',
        'COMMAND',
        'STATUS',
    ]
    cluster_table.align['COMMAND'] = 'l'

    def shorten_duration_diff_string(diff):
        diff = diff.replace('second', 'sec')
        diff = diff.replace('minute', 'min')
        diff = diff.replace('hour', 'hr')
        return diff

    for cluster_status in clusters_status:
        launched_at = cluster_status['launched_at']
        handle = cluster_status['handle']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        resources_str = '<initializing>'
        if (handle.requested_nodes is not None and
                handle.launched_resources is not None):
            resources_str = (f'{handle.requested_nodes}x '
                             f'{handle.launched_resources}')
        cluster_table.add_row([
            # NAME
            cluster_status['name'],
            # LAUNCHED
            shorten_duration_diff_string(duration.diff_for_humans()),
            # RESOURCES
            resources_str,
            # COMMAND
            cluster_status['last_use']
            if show_all else _truncate_long_string(cluster_status['last_use']),
            # STATUS
            cluster_status['status'],
        ])
    click.echo(f'Clusters\n{cluster_table}')


@cli.command()
@click.argument('cluster', required=False)
@click.option('--port-forward',
              '-p',
              multiple=True,
              default=[],
              type=int,
              required=False,
              help=('Port to be forwarded. To forward multiple ports, '
                    'use this option multiple times.'))
def ssh(cluster: str, port_forward: Optional[List[int]]):
    """SSH into an existing cluster.

    CLUSTER is the name of the cluster to attach to.  If CLUSTER is not
    supplied, the cluster launched last will be used.

    Examples:

      \b
      # ssh into a specific cluster.
      sky ssh cluster_name

      \b
      # Port forward.
      sky ssh --port-forward 8080 --port-forward 4650 cluster_name
      sky ssh -p 8080 -p 4650 cluster_name
    """
    name = cluster
    if name is None:
        launched_clusters = global_user_state.get_clusters()
        if len(launched_clusters) == 0:
            raise click.UsageError(
                'No launched clusters found (see `sky status`).')
        name = sorted(launched_clusters,
                      key=lambda x: x['launched_at'])[-1]['name']
    assert isinstance(name, str) and name, name
    handle = global_user_state.get_handle_from_cluster_name(name)
    if handle is None:
        raise click.UsageError(
            f'Cluster {name} is not found (see `sky status`).')
    command = backends.CloudVmRayBackend().ssh_head_command(
        handle, port_forward=port_forward)
    # Disable check, since the returncode could be non-zero if the user Ctrl-D.
    backend_utils.run(command, shell=False, check=False)


@cli.command()
@click.argument('cluster', required=False)
@click.option('--all',
              '-a',
              default=None,
              is_flag=True,
              help='Tear down all existing clusters.')
def down(
        cluster: str,
        all: Optional[bool],  # pylint: disable=redefined-builtin
):
    """Tear down cluster(s).

    CLUSTER is the name of the cluster to tear down.  If both CLUSTER and --all
    are supplied, the latter takes precedence.

    Accelerators (e.g., TPU) that are part of the cluster will be deleted too.

    Examples:

      \b
      # Tear down a specific cluster.
      sky down cluster_name

      \b
      # Tear down all existing clusters.
      sky down -a
    """
    _terminate_or_stop(cluster, apply_to_all=all, terminate=True)


@cli.command()
@click.argument('cluster', required=False)
@click.option('--all',
              '-a',
              default=None,
              is_flag=True,
              help='Tear down all existing clusters.')
def stop(
        cluster: str,
        all: Optional[bool],  # pylint: disable=redefined-builtin
):
    """Stop cluster(s).

    CLUSTER is the name of the cluster to stop.  If both CLUSTER and --all are
    supplied, the latter takes precedence.

    Limitation: this currently only works for AWS clusters.

    Examples:

      \b
      # Stop a specific cluster.
      sky stop cluster_name

      \b
      # Stop all existing clusters.
      sky stop -a
    """
    _terminate_or_stop(cluster, apply_to_all=all, terminate=False)


def _terminate_or_stop(name: Optional[str], apply_to_all: Optional[bool],
                       terminate: bool) -> None:
    """Terminates or stops a cluster (or all clusters)."""
    command = 'down' if terminate else 'stop'
    if name is None and apply_to_all is None:
        raise click.UsageError(
            f'sky {command} requires either a cluster name (see `sky status`) '
            'or --all.')

    to_down = []
    if name is not None:
        handle = global_user_state.get_handle_from_cluster_name(name)
        if handle is not None:
            to_down = [{'name': name, 'handle': handle}]
    if apply_to_all:
        to_down = global_user_state.get_clusters()
        if name is not None:
            print(f'Both --all and --cluster specified for sky {command}. '
                  'Letting --all take effect.')
            name = None
    if not to_down:
        if name is not None:
            print(f'Cluster {name} is not found (see `sky status`).')
        else:
            print('No existing clusters found (see `sky status`).')

    # FIXME: Assumes a specific backend.
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    for record in to_down:  # TODO: parallelize.
        name = record['name']
        handle = record['handle']
        backend.teardown(handle, terminate=terminate)
        if terminate:
            click.secho(f'Terminating cluster {name}...done.', fg='green')
        else:
            click.secho(f'Stopping cluster {name}...done.', fg='green')
            click.echo(
                f'  Tip: to resume the cluster, use "sky run -c {name} <yaml>" '
                'or "sky cpunode/gpunode".')


@cli.command()
@click.option('--cluster',
              '-c',
              default=None,
              type=str,
              help=_CLUSTER_FLAG_HELP)
@click.option('--port-forward',
              '-p',
              multiple=True,
              default=[],
              type=int,
              required=False,
              help=('Port to be forwarded. To forward multiple ports, '
                    'use this option multiple times.'))
@click.option('--screen',
              default=False,
              is_flag=True,
              help='If true, attach using screen.')
def gpunode(cluster: str, port_forward: Optional[List[int]], screen):
    """Launch or attach to an interactive GPU node.

    Automatically syncs the current working directory.

    Example:

      \b
      # Launch a default gpunode.
      $ sky gpunode

      \b
      # Do work, then log out.  The node is kept running.

      \b
      # Attach back to the same node and do more work.
      $ sky gpunode

      \b
      # Alternatively, create multiple interactive nodes by specifying names
      # via --cluster (-c).
      $ sky gpunode -c node0
      $ sky gpunode -c node1

      \b
      # Port forward.
      sky gpunode --port-forward 8080 --port-forward 4650 -c cluster_name
      sky gpunode -p 8080 -p 4650 -c cluster_name
    """
    name = cluster
    if name is None:
        name = _default_interactive_node_name('gpunode')
    _create_and_ssh_into_node(
        'gpunode',
        sky.Resources(sky.AWS(), accelerators='V100'),
        cluster_name=name,
        port_forward=port_forward,
        use_screen=screen,
    )


@cli.command()
@click.option('--cluster',
              '-c',
              default=None,
              type=str,
              help=_CLUSTER_FLAG_HELP)
@click.option('--port-forward',
              '-p',
              multiple=True,
              default=[],
              type=int,
              required=False,
              help=('Port to be forwarded. To forward multiple ports, '
                    'use this option multiple times.'))
@click.option('--screen',
              default=False,
              is_flag=True,
              help='If true, attach using screen.')
def cpunode(cluster: str, port_forward: Optional[List[int]], screen):
    """Launch or attach to an interactive CPU node.

    Automatically syncs the current working directory.

    Example:

      \b
      # Launch a default cpunode.
      $ sky cpunode

      \b
      # Do work, then log out.  The node is kept running.

      \b
      # Attach back to the same node and do more work.
      $ sky cpunode

      \b
      # Alternatively, create multiple interactive nodes by specifying names
      # via --cluster (-c).
      $ sky cpunode -c node0
      $ sky cpunode -c node1

      \b
      # Port forward.
      sky cpunode --port-forward 8080 --port-forward 4650 -c cluster_name
      sky cpunode -p 8080 -p 4650 -c cluster_name
    """
    name = cluster
    if name is None:
        name = _default_interactive_node_name('cpunode')
    _create_and_ssh_into_node(
        'cpunode',
        sky.Resources(sky.AWS()),
        cluster_name=name,
        port_forward=port_forward,
        use_screen=screen,
    )


@cli.command()
@click.option('--cluster',
              '-c',
              default=None,
              type=str,
              help=_CLUSTER_FLAG_HELP)
@click.option('--port-forward',
              '-p',
              multiple=True,
              default=[],
              type=int,
              required=False,
              help=('Port to be forwarded. To forward multiple ports, '
                    'use this option multiple times.'))
@click.option('--screen',
              default=False,
              is_flag=True,
              help='If true, attach using screen.')
def tpunode(cluster: str, port_forward: Optional[List[int]], screen):
    """Launch or attach to an interactive TPU node.

    Automatically syncs the current working directory.

    Example:

      \b
      # Launch a default tpunode.
      $ sky tpunode

      \b
      # Do work, then log out.  The node is kept running.

      \b
      # Attach back to the same node and do more work.
      $ sky tpunode

      \b
      # Alternatively, create multiple interactive nodes by specifying names
      # via --cluster (-c).
      $ sky tpunode -c node0
      $ sky tpunode -c node1

      \b
      # Port forward.
      sky tpunode --port-forward 8080 --port-forward 4650 -c cluster_name
      sky tpunode -p 8080 -p 4650 -c cluster_name
    """
    name = cluster
    if name is None:
        name = _default_interactive_node_name('tpunode')
    _create_and_ssh_into_node(
        'tpunode',
        sky.Resources(sky.GCP(), accelerators='tpu-v3-8'),
        cluster_name=name,
        port_forward=port_forward,
        use_screen=screen,
    )


@cli.command()
def init():
    """Determines a set of clouds that Sky will use.

    It checks access credentials for AWS, Azure and GCP. Sky jobs will only
    run in clouds that you have access to. After configuring access for a
    cloud, rerun `sky init` to reflect the changes.
    """
    click.echo('Sky will use the following clouds to run jobs. '
               'To change this, configure\ncloud access credentials,'
               ' and rerun ' + click.style('sky init', bold=True) + '.\n')

    enabled_clouds = []
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP()]:
        click.echo(f'  Checking {cloud}...', nl=False)
        ok, reason = cloud.check_credentials()
        click.echo('\r', nl=False)
        status_msg = 'enabled' if ok else 'disabled'
        status_color = 'green' if ok else 'red'
        click.echo(
            '  ' +
            click.style(f'{cloud}: {status_msg}', fg=status_color, bold=True) +
            ' ' * 10)
        if ok:
            enabled_clouds.append(str(cloud))
        else:
            click.echo(f'    Reason: {reason}')
    click.echo()

    global_user_state.set_enabled_clouds(enabled_clouds)


def main():
    return cli()


if __name__ == '__main__':
    main()
