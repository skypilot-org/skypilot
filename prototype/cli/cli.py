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
    t1_lines = t1_lines + ['' * len(t1_lines[0])
                          ] * (num_total_lines - len(t1_lines))
    t2_lines = t2_lines + ['' * len(t2_lines[0])
                          ] * (num_total_lines - len(t2_lines))

    # Combine the tables
    combined_lines = []
    for t1_l, t2_l in zip(t1_lines, t2_lines):
        combined_lines.append(t1_l + '\t' + t2_l)

    return '\n'.join(combined_lines)


def _interactive_node(name, resources):
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
    handle = backend.provision(task,
                               task.best_resources,
                               dryrun=False,
                               stream_logs=True)

    # TODO: cd into workdir immediately on the VM
    # TODO: Delete the temporary cluster config yml (or figure out a way to re-use it)
    cloud_vm_ray_backend._run(f'ray attach {handle} --tmux')
    cloud_vm_ray_backend._run(f'ray down -y {handle}')


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

    # Get handle from cluster ID
    handle = None
    if cluster is not None:
        handle = cluster

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun, handle=handle, stream_logs=stream_logs)


@cli.command()
@click.argument('task_id', required=True, type=str)
def cancel(task_id):
    """Cancel a task."""

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.cancel(task_id)


@cli.command()
def status():
    """Show the status of all tasks and clusters."""

    session = Session()
    tasks_status = session.get_tasks()
    clusters_status = session.get_clusters()

    task_table = PrettyTable()
    task_table.field_names = ["ID", "NAME", "DURATION", "STATUS"]
    for task_status in tasks_status:
        launched_at = task_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        task_table.add_row([
            task_status['id'], task_status['name'],
            duration.diff_for_humans(), task_status['status']
        ])

    cluster_table = PrettyTable()
    cluster_table.field_names = ["ID", "CLOUD", "UPTIME"]
    for cluster_status in clusters_status:
        launched_at = cluster_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        cluster_table.add_row([
            cluster_status['id'],
            cluster_status['cloud'],
            duration.diff_for_humans(),
        ])

    output = _combined_pretty_table_str(task_table, cluster_table)
    click.echo(output)


@cli.command()
def provision():
    """Provision a new cluster."""

    raise NotImplementedError()

    session = UserManager()
    session.add_cluster()

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
    handle = backend.provision(task,
                               task.best_resources,
                               dryrun=False,
                               stream_logs=True)

    # TODO: cd into workdir immediately on the VM
    # TODO: Delete the temporary cluster config yml (or figure out a way to re-use it)
    cloud_vm_ray_backend._run(f'ray attach {handle} --tmux')
    cloud_vm_ray_backend._run(f'ray down -y {handle}')


@cli.command()
@click.argument('cluster_id', required=True, type=str)
def teardown():
    """Delete cluster."""
    # TODO: Delete associated tasks as well

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    # STEP 1: Get handle from cluster ID
    # STEP 2: Delete the cluster
    # STEP 3: Remove cluster from session


@cli.command()
def gpunode():
    """Launch an interactive GPU node."""
    # assert False, 'Implement cluster caching and/or Sky provision.'
    # TODO: Sync code files between local and interactive node (watch rsync?)
    _interactive_node('gpunode',
                      {sky.Resources(sky.AWS(), accelerators='V100')})


@cli.command()
def cpunode():
    """Launch an interactive CPU node."""
    _interactive_node('cpunode', {sky.Resources(sky.AWS())})


@cli.command()
def tpunode():
    """Launch an interactive TPU node."""
    raise NotImplementedError()


def main():
    return cli()


if __name__ == '__main__':
    main()
