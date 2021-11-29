import os
from pathlib import Path
import time

import click
import pendulum
from prettytable import PrettyTable

import sky
from sky.backends import cloud_vm_ray_backend
from sky.session import Session


def _combined_pretty_table_str(t1, t2):
    """Generates a combined PrettyTable string given two PrettyTable objects."""

    t1_lines = t1.get_string().split('\n')
    t2_lines = t2.get_string().split('\n')

    # Pad each table correctly
    num_total_lines = max(len(t1_lines), len(t2_lines))
    t1_lines = t1_lines + [' ' * len(t1_lines[0])
                          ] * (num_total_lines - len(t1_lines))
    t2_lines = t2_lines + [' ' * len(t2_lines[0])
                          ] * (num_total_lines - len(t2_lines))

    # Combine the tables
    combined_lines = []
    for t1_l, t2_l in zip(t1_lines, t2_lines):
        combined_lines.append(t1_l + '\t' + t2_l)

    return '\n'.join(combined_lines)


def _interactive_node(name, resources, cluster_handle=None):
    """Creates an interactive session.
    
    Args:
        name: Name of the interactivve session.
        resources: Resources to attach to VM.
        cluster_handle: Cluster YAML file.
    """

    with sky.Dag() as dag:
        # TODO: Add conda environment replication
        # should be setup = 'conda env export | grep -v "^prefix: " > environment.yml'
        # && conda env create -f environment.yml
        task = sky.Task(
            name,
            workdir=os.getcwd(),
            setup=None,
            run='',
        )
        task.set_resources(resources)

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    task = dag.tasks[0]

    handle = cluster_handle
    if handle is None:
        handle = backend.provision(task,
                                   task.best_resources,
                                   dryrun=False,
                                   stream_logs=True)

    session = Session()
    task_id = session.add_task(task)

    # TODO: cd into workdir immediately on the VM
    # TODO: Delete the temporary cluster config yml (or figure out a way to re-use it)
    cloud_vm_ray_backend._run(f'ray attach {handle}')

    if cluster_handle is None:  # if this is a secondary
        cloud_vm_ray_backend._run(f'ray down -y {handle}')
        cluster_name = session.get_cluster_name_from_handle(handle)
        session.remove_cluster(cluster_name)

    session.remove_task(task_id)


@click.group()
def cli():
    pass


@cli.command()
@click.argument('entry_point', required=True, type=str)
@click.option('--cluster', '-c', default=None, type=str)
@click.option('--dryrun',
              '-n',
              default=False,
              type=bool,
              help='If True, do not actually run the job.')
def run(entry_point, cluster, dryrun):
    """Launch a job from a YAML config or Python script."""

    stream_logs = False

    with sky.Dag() as dag:

        entry_point_type = Path(entry_point).suffix
        if entry_point_type == '.py':
            stream_logs = True

            # TODO: Support conda environment cloning (see gpunode comment)
            setup = f"""
            pip3 install -r requirements.txt
            """

            run = f"""
            python3 {entry_point}
            """

            task = sky.Task(
                Path(entry_point).stem,
                workdir=os.getcwd(),
                setup=setup,
                run=run,
            )

            # TODO: Add good way to automatically infer resources?
            # Can also just assume we always attach GPU for `run`
            task.set_resources({sky.Resources(sky.AWS(), accelerators='V100')})

        elif entry_point_type in ['.yaml', '.yml']:
            task = sky.Task.from_yaml(entry_point)

        else:
            raise ValueError(
                f'Unsupported entry point type: {entry_point_type}')

    handle = None
    if cluster is not None:
        session = Session()
        handle = session.get_handle_from_cluster_name(cluster)

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun, handle=handle, stream_logs=stream_logs)


@cli.command()
@click.argument('task_id', required=True, type=str)
def cancel(task_id):
    """Cancel a task."""

    # TODO: Need a way to interrupt `ray exec` mid-flight (maybe grab PID?)
    raise NotImplementedError()


@cli.command()
def status():
    """Show the status of all tasks and clusters."""

    session = Session()
    tasks_status = session.get_tasks()
    clusters_status = session.get_clusters()

    task_table = PrettyTable()
    task_table.field_names = ["ID", "NAME", "DURATION"]
    for task_status in tasks_status:
        launched_at = task_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        task_table.add_row([
            task_status['id'],
            task_status['name'],
            duration.diff_for_humans(),
        ])

    cluster_table = PrettyTable()
    cluster_table.field_names = ["NAME", "UPTIME"]
    for cluster_status in clusters_status:
        launched_at = cluster_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        cluster_table.add_row([
            cluster_status['name'],
            duration.diff_for_humans(),
        ])

    output = _combined_pretty_table_str(task_table, cluster_table)
    click.echo(output)


@cli.command()
@click.argument('cluster_name', required=False, type=str)
def provision(cluster_name=None):
    """Provision a new cluster."""

    with sky.Dag() as dag:
        task = sky.Task(run='')
        # TODO: Make this configurable via command line.
        resources = {sky.Resources(sky.AWS(), accelerators='V100')}
        task.set_resources(resources)

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    task = dag.tasks[0]
    backend.provision(task,
                      task.best_resources,
                      dryrun=False,
                      stream_logs=True,
                      cluster_name=cluster_name)


@cli.command()
@click.argument('cluster_name', required=True, type=str)
def teardown(cluster_name):
    """Delete cluster."""
    # TODO: Delete associated tasks as well

    session = Session()
    handle = session.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        click.echo(f'Cluster {cluster_name} not found.')
        return

    cloud_vm_ray_backend._run(f'ray down -y {handle}')
    session.remove_cluster(cluster_name)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
def gpunode(cluster):
    """Launch an interactive GPU node."""
    # TODO: Sync code files between local and interactive node (watch rsync?)

    handle = None
    if cluster is not None:
        session = Session()
        handle = session.get_handle_from_cluster_name(cluster)

    _interactive_node('gpunode',
                      {sky.Resources(sky.AWS(), accelerators='V100')},
                      cluster_handle=handle)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
def cpunode(cluster):
    """Launch an interactive CPU node."""

    handle = None
    if cluster is not None:
        session = Session()
        handle = session.get_handle_from_cluster_name(cluster)

    _interactive_node('cpunode', {sky.Resources(sky.AWS())},
                      cluster_handle=handle)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
def tpunode(cluster):
    """Launch an interactive TPU node."""
    raise NotImplementedError()


def main():
    return cli()


if __name__ == '__main__':
    main()
