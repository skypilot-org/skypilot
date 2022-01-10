"""The 'sky' command line tool.

Example usage:

  # See available commands.
  >> sky

  # Run a task, described in a yaml file.
  # Provisioning, setup, file syncing are handled.
  >> sky launch task.yaml
  >> sky launch [-c cluster_name] task.yaml

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
import functools
import getpass
import os
import time
from typing import Dict, List, Optional, Tuple

import click
import pandas as pd
import pendulum
import prettytable
import tabulate

import sky
from sky import backends
from sky import logging
from sky import global_user_state
from sky import task as task_lib
from sky.backends import backend as backend_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.clouds import service_catalog

logger = logging.init_logger(__name__)

_CLUSTER_FLAG_HELP = """
A cluster name. If provided, either reuse an existing cluster with that name or
provision a new cluster with that name. Otherwise provision a new cluster with
an autogenerated name.
""".strip()
_INTERACTIVE_NODE_TYPES = ('cpunode', 'gpunode', 'tpunode')

Path = str
Backend = backends.Backend

SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR


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


def _parse_accelerator_options(accelerator_options: str) -> Dict[str, int]:
    """Parses accelerator options. e.g. V100:8 into {'V100': 8}."""
    accelerators = {}
    accelerator_options = accelerator_options.split(':')
    if len(accelerator_options) == 1:
        accelerator_type = accelerator_options[0]
        accelerators[accelerator_type] = 1
    elif len(accelerator_options) == 2:
        accelerator_type = accelerator_options[0]
        accelerator_count = int(accelerator_options[1])
        accelerators[accelerator_type] = accelerator_count
    else:
        raise ValueError(f'Invalid accelerator option: {accelerator_options}')
    return accelerators


def _interactive_node_cli_command(cli_func):
    """Click command decorator for interactive node commands."""
    assert cli_func.__name__ in _INTERACTIVE_NODE_TYPES, cli_func.__name__

    cluster_option = click.option('--cluster',
                                  '-c',
                                  default=None,
                                  type=str,
                                  help=_CLUSTER_FLAG_HELP)
    port_forward_option = click.option(
        '--port-forward',
        '-p',
        multiple=True,
        default=[],
        type=int,
        required=False,
        help=('Port to be forwarded. To forward multiple ports, '
              'use this option multiple times.'))
    screen_option = click.option('--screen',
                                 default=False,
                                 is_flag=True,
                                 help='If true, attach using screen.')
    tmux_option = click.option('--tmux',
                               default=False,
                               is_flag=True,
                               help='If true, attach using tmux.')
    cloud_option = click.option('--cloud',
                                default=None,
                                type=str,
                                help='Cloud provider to use.')
    instance_type_option = click.option('--instance-type',
                                        '-t',
                                        default=None,
                                        type=str,
                                        help='Instance type to use.')
    gpus = click.option(
        '--gpus',
        default=None,
        type=str,
        help='Type and number of GPUs to use (e.g. V100:8 or V100).')
    tpus = click.option(
        '--tpus',
        default=None,
        type=str,
        help='Type and number of TPUs to use (e.g. tpu-v3-8:4 or tpu-v3-8).')

    spot_option = click.option('--spot',
                               default=False,
                               is_flag=True,
                               help='If true, use spot instances.')

    click_decorators = [
        cli.command(),
        cluster_option,
        port_forward_option,

        # Resource options
        *([cloud_option] if cli_func.__name__ != 'tpunode' else []),
        instance_type_option,
        *([gpus] if cli_func.__name__ == 'gpunode' else []),
        *([tpus] if cli_func.__name__ == 'tpunode' else []),
        spot_option,

        # Attach options
        screen_option,
        tmux_option,
    ]
    decorator = functools.reduce(lambda res, f: f(res),
                                 reversed(click_decorators), cli_func)

    return decorator


def _default_interactive_node_name(node_type: str):
    """Returns a deterministic name to refer to the same node."""
    # FIXME: this technically can collide in Azure/GCP with another
    # same-username user.  E.g., sky-gpunode-ubuntu.  Not a problem on AWS
    # which is the current cloud for interactive nodes.
    assert node_type in _INTERACTIVE_NODE_TYPES, node_type
    return f'sky-{node_type}-{getpass.getuser()}'


# TODO: skip installing ray to speed up provisioning.
def _create_and_ssh_into_node(
        node_type: str,
        resources: sky.Resources,
        cluster_name: str,
        backend: Optional[backend_lib.Backend] = None,
        port_forward: Optional[List[int]] = None,
        session_manager: Optional[str] = None,
):
    """Creates and attaches to an interactive node.

    Args:
        node_type: Type of the interactive node: { 'cpunode', 'gpunode' }.
        resources: Resources to attach to VM.
        cluster_name: a cluster name to identify the interactive node.
        backend: the Backend to use (currently only CloudVmRayBackend).
        port_forward: List of ports to forward.
        session_manager: Attach session manager: { 'screen', 'tmux' }.
    """
    assert node_type in _INTERACTIVE_NODE_TYPES, node_type
    assert session_manager in (None, 'screen', 'tmux'), session_manager
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
    dag = sky.optimize(dag)
    task = dag.tasks[0]
    backend.register_info(dag=dag)

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
    if session_manager == 'screen':
        commands += ['screen', '-D', '-R']
    elif session_manager == 'tmux':
        commands += ['tmux', 'attach', '||', 'tmux', 'new']
    backend_utils.run(commands, shell=False, check=False)
    cluster_name = global_user_state.get_cluster_name_from_handle(handle)

    click.echo('To attach to it again:  ', nl=False)
    if cluster_name == _default_interactive_node_name(node_type):
        option = ''
    else:
        option = f' -c {cluster_name}'
    click.secho(f'sky {node_type}{option}', bold=True)
    click.echo('To tear down the node:\t', nl=False)
    click.secho(f'sky down {cluster_name}', bold=True)
    click.echo('To stop the node:\t', nl=False)
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
@click.option('--detach_run',
              '-d',
              default=False,
              is_flag=True,
              help='If True, run setup first (blocking), '
              'then detach from the job\'s execution.')
def launch(yaml_path: Path, cluster: str, dryrun: bool, detach_run: bool):
    """Launch task from a YAML spec (re-setup if a cluster exists)."""
    with sky.Dag() as dag:
        sky.Task.from_yaml(yaml_path)

    click.secho(f'Running task on cluster {cluster} ...', fg='yellow')
    sky.launch(dag,
               dryrun=dryrun,
               stream_logs=True,
               cluster_name=cluster,
               detach_run=detach_run)


@cli.command()
@click.argument('yaml_path', required=True, type=str)
@click.option('--cluster',
              '-c',
              required=True,
              type=str,
              help='Name of the existing cluster to execute a task on.')
@click.option('--detach_run',
              '-d',
              default=False,
              is_flag=True,
              help='If True, run setup first (blocking), '
              'then detach from the job\'s execution.')
# pylint: disable=redefined-builtin
def exec(yaml_path: Path, cluster: str, detach_run: bool):
    """Execute task from a YAML spec on a cluster (skip setup).

    \b
    Actions performed by this command only include:
      - workdir syncing
      - executing the task's run command
    `sky exec` is thus typically faster than `sky launch`, provided a cluster
    already exists.

    All setup steps (provisioning, setup commands, file mounts syncing) are
    skipped.  If any of those specifications changed, this command will not
    reflect those changes.  To ensure a cluster's setup is up to date, use `sky
    run` instead.

    Typical workflow:

      # First command: set up the cluster once.

      >> sky launch -c name app.yaml

    \b
      # Starting iterative development...
      # For example, modify local workdir code.
      # Future commands: simply execute the task on the launched cluster.

      >> sky exec -c name app.yaml

      # Do "sky launch" again if anything other than Task.run is modified:

      >> sky launch -c name app.yaml

    """
    with sky.Dag() as dag:
        sky.Task.from_yaml(yaml_path)

    click.secho(f'Executing task on cluster {cluster} ...', fg='yellow')
    sky.exec(dag, cluster_name=cluster, detach_run=detach_run)


def _readable_time_duration(start_time: int):
    duration = pendulum.now().subtract(seconds=time.time() - start_time)
    diff = duration.diff_for_humans()
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff


@cli.command()
@click.option('--all',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Show all information in full.')
def status(all: bool):  # pylint: disable=redefined-builtin
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

    for cluster_status in clusters_status:
        launched_at = cluster_status['launched_at']
        handle = cluster_status['handle']
        resources_str = '<initializing>'
        if (handle.launched_nodes is not None and
                handle.launched_resources is not None):
            launched_resource_str = str(handle.launched_resources)
            if not show_all:
                launched_resource_str = _truncate_long_string(
                    launched_resource_str)
            resources_str = (f'{handle.launched_nodes}x '
                             f'{launched_resource_str}')
        cluster_table.add_row([
            # NAME
            cluster_status['name'],
            # LAUNCHED
            _readable_time_duration(launched_at),
            # RESOURCES
            resources_str,
            # COMMAND
            cluster_status['last_use']
            if show_all else _truncate_long_string(cluster_status['last_use']),
            # STATUS
            cluster_status['status'].value,
        ])
    click.echo(f'Sky Clusters\n{cluster_table}')


@cli.command()
@click.option('--all-users',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Show all users\' information in full.')
@click.option('--skip-finished',
              '-s',
              default=False,
              is_flag=True,
              required=False,
              help='Show only pending/running jobs\' information.')
@click.argument('cluster', required=False)
def queue(cluster: Optional[str], skip_finished: bool, all_users: bool):
    """Show the job queue for a cluster."""
    click.secho('Fetching and parsing job queue...', fg='yellow')
    all_jobs = not skip_finished
    backend = backends.CloudVmRayBackend()

    codegen = backend_utils.JobLibCodeGen()
    username = getpass.getuser()
    if all_users:
        username = None
    codegen.show_jobs(username, all_jobs)
    code = codegen.build()

    if cluster is not None:
        handle = global_user_state.get_handle_from_cluster_name(cluster)
        if handle is None:
            raise click.BadParameter(
                f'Cluster {cluster} is not found (see `sky status`).')

        job_table = backend.run_on_head(handle, code)
        click.echo(f'Sky Job Queue of Cluster {cluster}\n{job_table}')
        return

    clusters_status = global_user_state.get_clusters()
    for cluster_status in clusters_status:
        handle = cluster_status['handle']
        job_table = backend.run_on_head(handle, code)
        click.echo(
            f'Sky Job Queue of Cluster {handle.cluster_name}\n{job_table}')


@cli.command()
@click.option('--cluster',
              '-c',
              required=True,
              type=str,
              help='Name of the existing cluster to find the job.')
@click.argument('job_id', required=True, type=str)
def logs(cluster: str, job_id: str):
    """Tailing the log of a job."""
    # TODO: Add an option for downloading logs.
    cluster_name = cluster
    backend = backends.CloudVmRayBackend()

    codegen = backend_utils.JobLibCodeGen()
    codegen.tail_logs(job_id)
    code = codegen.build()

    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise click.BadParameter(f'Cluster \'{cluster_name}\' not found'
                                 ' (see `sky status`).')
    click.secho('Start streaming logs...', fg='yellow')
    backend.run_on_head(handle, code, stream_logs=True)


@cli.command()
@click.option('--cluster',
              '-c',
              required=True,
              type=str,
              help='Name of the existing cluster to cancel the task.')
@click.option('--all',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Cancel all jobs.')
@click.argument('jobs', required=False, type=int, nargs=-1)
def cancel(cluster: str, all: bool, jobs: List[int]):  # pylint: disable=redefined-builtin
    """Cancel the jobs on a cluster."""
    if len(jobs) == 0 and not all:
        raise click.UsageError(
            'sky cancel requires either a job id '
            f'(see `sky queue {cluster} -aj`) or the --all flag.')

    handle = global_user_state.get_handle_from_cluster_name(cluster)
    if handle is None:
        raise click.BadParameter(f'Cluster \'{cluster}\' not found'
                                 ' (see `sky status`).')

    if all:
        click.secho(f'Cancelling all jobs on cluster {cluster} ...',
                    fg='yellow')
        jobs = None
    else:
        click.secho(f'Cancelling jobs {jobs} on cluster {cluster} ...',
                    fg='yellow')

    codegen = backend_utils.JobLibCodeGen()
    codegen.cancel_jobs(jobs)
    code = codegen.build()

    # FIXME: Assumes a specific backend.
    backend = backends.CloudVmRayBackend()
    backend.run_on_head(handle, code, stream_logs=False)


@cli.command()
@click.argument('clusters', nargs=-1, required=False)
@click.option('--all',
              '-a',
              default=None,
              is_flag=True,
              help='Tear down all existing clusters.')
def down(
        clusters: Tuple[str],
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
      # Tear down multiple clusters.
      sky down cluster1 cluster2

      \b
      # Tear down all existing clusters.
      sky down -a
    """
    _terminate_or_stop_clusters(clusters, apply_to_all=all, terminate=True)


@cli.command()
@click.argument('clusters', nargs=-1, required=False)
@click.option('--all',
              '-a',
              default=None,
              is_flag=True,
              help='Tear down all existing clusters.')
def stop(
        clusters: Tuple[str],
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
      # Stop multiple clusters.
      sky stop cluster1 cluster2

      \b
      # Stop all existing clusters.
      sky stop -a
    """
    _terminate_or_stop_clusters(clusters, apply_to_all=all, terminate=False)


@cli.command()
@click.argument('clusters', nargs=-1, required=False)
def start(clusters: Tuple[str]):
    """Restart cluster(s).

    If a cluster is previously stopped (status == STOPPED) or failed in
    provisioning/a task's setup (status == INIT), this command will attempt to
    start the cluster.  (In the second case, any failed setup steps are not
    performed and only a request to start the machines is attempted.)

    If a cluster is already in an UP status, this command has no effect on it.

    Examples:

      \b
      # Restart a specific cluster.
      sky start cluster_name

      \b
      # Restart multiple clusters.
      sky start cluster1 cluster2
    """
    to_start = []
    if clusters:

        def _filter(name, all_clusters):
            for cluster_record in all_clusters:
                if name == cluster_record['name']:
                    return cluster_record
            return None

        all_clusters = global_user_state.get_clusters()
        for name in clusters:
            record = _filter(name, all_clusters)
            if record is None:
                print(f'Cluster {name} was not found.')
                continue
            # A cluster may have one of the following states:
            #
            #  STOPPED - ok to restart
            #    (currently, only AWS clusters can be in this state)
            #
            #  UP - skipped, see below
            #
            #  INIT - ok to restart:
            #    1. It can be a failed-to-provision cluster, so it isn't up
            #      (Ex: gpunode --gpus=A100:8).  Running `sky start` enables
            #      retrying the provisioning - without setup steps being
            #      completed. (Arguably the original command that failed should
            #      be used instead; but using start isn't harmful - after it
            #      gets provisioned successfully the user can use the original
            #      command).
            #
            #    2. It can be an up cluster that failed one of the setup steps.
            #      This way 'sky start' can change its status to UP, enabling
            #      'sky ssh' to debug things (otherwise `sky ssh` will fail an
            #      INIT state cluster due to head_ip not being cached).
            #
            #      This can be replicated by adding `exit 1` to Task.setup.
            if record['status'] == global_user_state.ClusterStatus.UP:
                # An UP cluster; skipping 'sky start' because:
                #  1. For a really up cluster, this has no effects (ray up -y
                #    --no-restart) anyway.
                #  2. A cluster may show as UP but is manually stopped in the
                #    UI.  If Azure/GCP: ray autoscaler doesn't support reusing,
                #    so 'sky start -c existing' will actually launch a new
                #    cluster with this name, leaving the original cluster
                #    zombied (remains as stopped in the cloud's UI).
                #
                #    This is dangerous and unwanted behavior!
                print(f'Cluster {name} already has status UP.')
                continue
            assert record['status'] in (
                global_user_state.ClusterStatus.INIT,
                global_user_state.ClusterStatus.STOPPED), record
            to_start.append({'name': name, 'handle': record['handle']})
    if not to_start:
        return
    # FIXME: Assumes a specific backend.
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    for record in to_start:
        name = record['name']
        handle = record['handle']
        with sky.Dag():
            dummy_task = sky.Task().set_resources(handle.requested_resources)
            dummy_task.num_nodes = handle.launched_nodes
        click.secho(f'Starting cluster {name}...', bold=True)
        backend.provision(dummy_task,
                          to_provision=None,
                          dryrun=False,
                          stream_logs=True,
                          cluster_name=name)
        click.secho(f'Cluster {name} started.', fg='green')


def _terminate_or_stop_clusters(names: Tuple[str], apply_to_all: Optional[bool],
                                terminate: bool) -> None:
    """Terminates or stops a cluster (or all clusters)."""
    command = 'down' if terminate else 'stop'
    if not names and apply_to_all is None:
        raise click.UsageError(
            f'sky {command} requires either a cluster name (see `sky status`) '
            'or --all.')

    to_down = []
    if len(names) > 0:
        for name in names:
            handle = global_user_state.get_handle_from_cluster_name(name)
            if handle is not None:
                to_down.append({'name': name, 'handle': handle})
            else:
                print(f'Cluster {name} was not found. Skipping.')
    if apply_to_all:
        to_down = global_user_state.get_clusters()
        if len(names) > 0:
            print(f'Both --all and cluster(s) specified for sky {command}. '
                  'Letting --all take effect.')
            names = []
    if not to_down:
        if len(names) > 0:
            cluster_list = ', '.join(names)
            print(f'Clusters ({cluster_list}) not found (see `sky status`).')
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
            click.echo('  To restart the cluster, run: ', nl=False)
            click.secho(f'sky start {name}', bold=True)


@_interactive_node_cli_command
def gpunode(cluster: str, port_forward: Optional[List[int]],
            cloud: Optional[str], instance_type: Optional[str],
            gpus: Optional[str], spot: Optional[bool], screen: Optional[bool],
            tmux: Optional[bool]):
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
    if screen and tmux:
        raise click.UsageError('Cannot use both screen and tmux.')

    session_manager = None
    if screen or tmux:
        session_manager = 'tmux' if tmux else 'screen'
    name = cluster
    if name is None:
        name = _default_interactive_node_name('gpunode')

    cloud_provider = task_lib.CLOUD_REGISTRY.get(cloud, None)
    if cloud is not None and cloud not in task_lib.CLOUD_REGISTRY:
        raise click.UsageError(
            f'Cloud \'{cloud}\' is not supported. ' + \
            f'Supported clouds: {list(task_lib.CLOUD_REGISTRY.keys())}'
        )
    if gpus is None:
        gpus = {'K80': 1}
    else:
        gpus = _parse_accelerator_options(gpus)
    resources = sky.Resources(cloud=cloud_provider,
                              instance_type=instance_type,
                              accelerators=gpus,
                              use_spot=spot)

    _create_and_ssh_into_node(
        'gpunode',
        resources,
        cluster_name=name,
        port_forward=port_forward,
        session_manager=session_manager,
    )


@_interactive_node_cli_command
def cpunode(cluster: str, port_forward: Optional[List[int]],
            cloud: Optional[str], instance_type: Optional[str],
            spot: Optional[bool], screen: Optional[bool], tmux: Optional[bool]):
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
    if screen and tmux:
        raise click.UsageError('Cannot use both screen and tmux.')

    session_manager = None
    if screen or tmux:
        session_manager = 'tmux' if tmux else 'screen'
    name = cluster
    if name is None:
        name = _default_interactive_node_name('cpunode')

    cloud_provider = task_lib.CLOUD_REGISTRY.get(cloud, None)
    if cloud is not None and cloud not in task_lib.CLOUD_REGISTRY:
        raise click.UsageError(
            f'Cloud \'{cloud}\' is not supported. ' + \
            f'Supported clouds: {list(task_lib.CLOUD_REGISTRY.keys())}'
        )
    resources = sky.Resources(cloud=cloud_provider,
                              instance_type=instance_type,
                              use_spot=spot)

    _create_and_ssh_into_node(
        'cpunode',
        resources,
        cluster_name=name,
        port_forward=port_forward,
        session_manager=session_manager,
    )


@_interactive_node_cli_command
def tpunode(cluster: str, port_forward: Optional[List[int]],
            instance_type: Optional[str], tpus: Optional[str],
            spot: Optional[bool], screen: Optional[bool], tmux: Optional[bool]):
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
    if screen and tmux:
        raise click.UsageError('Cannot use both screen and tmux.')

    session_manager = None
    if screen or tmux:
        session_manager = 'tmux' if tmux else 'screen'
    name = cluster
    if name is None:
        name = _default_interactive_node_name('tpunode')

    if tpus is None:
        tpus = {'tpu-v3-8': 1}
    else:
        tpus = _parse_accelerator_options(tpus)
    resources = sky.Resources(cloud=sky.GCP(),
                              instance_type=instance_type,
                              accelerators=tpus,
                              use_spot=spot)

    _create_and_ssh_into_node(
        'tpunode',
        resources,
        cluster_name=name,
        port_forward=port_forward,
        session_manager=session_manager,
    )


@cli.command()
@click.argument('gpu_name', required=False)
@click.option('--all',
              '-a',
              is_flag=True,
              default=False,
              help='Show details of all GPU/TPU/accelerator offerings.')
def show_gpus(gpu_name: Optional[str], all: bool):  # pylint: disable=redefined-builtin
    """Show all GPU/TPU/accelerator offerings that Sky supports."""
    show_all = all
    if show_all and gpu_name is not None:
        raise click.UsageError('--all is only allowed without a GPU name.')

    def _output():
        if gpu_name is None:
            result = service_catalog.list_accelerator_counts(gpus_only=True)
            ordered = [('Common Nvidia GPUs\n------------------', [])]
            for gpu in service_catalog.get_common_gpus():
                ordered.append((gpu, result.pop(gpu)))
            ordered.append(('-----------\nGoogle TPUs\n-----------', []))
            for tpu in service_catalog.get_tpus():
                ordered.append((tpu, result.pop(tpu)))
            if show_all:
                ordered.append(('----------\nOther GPUs\n----------', []))
                ordered.extend(sorted(result.items()))

            data = [(gpu, str(counts)[1:-1]) for gpu, counts in ordered]
            yield tabulate.tabulate(data, ['GPU', 'Available Quantities'])
            yield '\n' + '-' * 42 + '\n'
            if not show_all:
                yield ('To specify a GPU/TPU in your task YAML, '
                       'use `<gpu>: <qty>`. Example:\n'
                       '    resources:\n'
                       '      accelerators:\n'
                       '        V100: 8\n\n'
                       'To show what cloud offers a GPU/TPU type, use '
                       '`sky show-gpus <gpu>`. To show all GPUs, including '
                       'less common ones, and their detailed information, '
                       'use `sky show-gpus --all`.')
                return
            yield ('Each table below shows the detailed offerings '
                   'of a GPU/TPU.\n\n')

        result = service_catalog.list_accelerators(gpus_only=True,
                                                   name_filter=gpu_name)
        show_gcp_msg = False
        for gpu, items in result.items():
            headers = ['GPU', 'Qty', 'Cloud', 'Instance Type', 'Host Memory']
            data = []
            for item in items:
                if item.cloud == 'GCP':
                    show_gcp_msg = True
                instance_type_str = item.instance_type if not pd.isna(
                    item.instance_type) else '(*)'
                mem_str = f'{item.memory:.0f}GB' if item.memory > 0 else '(*)'
                data.append([
                    item.accelerator_name, item.accelerator_count, item.cloud,
                    instance_type_str, mem_str
                ])
            yield tabulate.tabulate(data, headers)
            yield '\n\n'

        if show_gcp_msg:
            yield (
                '(*) By default Sky uses GCP n1-highmem-8 (52GB memory) and '
                'attaches GPU/TPUs to it. If you need more memory, specify an '
                'instance type according to '
                'https://cloud.google.com/compute/docs/'
                'general-purpose-machines#n1_machines.\n\n')

        yield ('To specify a GPU/TPU in your task YAML, use `<gpu>: <qty>`. '
               'Example:\n'
               '    resources:\n'
               '      accelerators:\n'
               '        V100: 8\n\n'
               'Alternatively, specify a cloud and instance type. Example:\n'
               '    resources:\n'
               '      cloud: aws\n'
               '      instance_type: p3.16xlarge\n')

    if show_all:
        click.echo_via_pager(_output())
    else:
        for out in _output():
            click.echo(out, nl=False)
        click.echo()


def main():
    return cli()


if __name__ == '__main__':
    main()
