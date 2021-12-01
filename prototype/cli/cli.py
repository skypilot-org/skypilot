import os
import pathlib
import time
import yaml

import click
import pendulum
from prettytable import PrettyTable

import sky
from sky import clouds
from sky import global_user_state
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.backends import local_docker_backend


def _get_region_zones_from_handle(handle):
    """Gets region and zones from a Ray YAML file."""

    with open(handle, 'r') as f:
        yaml_dict = yaml.safe_load(f)

    provider_config = yaml_dict['provider']
    region = provider_config['region']
    zones = provider_config['availability_zone']

    region = clouds.Region(name=region)
    if zones is not None:
        zones = [clouds.Zone(name=zone) for zone in zones.split(',')]
        region.set_zones(zones)

    return region, zones


def _create_interactive_node(name,
                             resources,
                             cluster_handle=None,
                             provider=cloud_vm_ray_backend.CloudVmRayBackend):
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

    backend = provider()
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    task = dag.tasks[0]

    handle = cluster_handle
    if handle is None:
        handle = backend.provision(task,
                                   task.best_resources,
                                   dryrun=False,
                                   stream_logs=True)

    task_id = global_user_state.add_task(task)

    # TODO: cd into workdir immediately on the VM
    # TODO: Delete the temporary cluster config yml (or figure out a way to re-use it)
    cloud_vm_ray_backend._run(f'ray attach {handle}')

    if cluster_handle is None:  # if this is a secondary
        cloud_vm_ray_backend._run(f'ray down -y {handle}')
        cluster_name = global_user_state.get_cluster_name_from_handle(handle)
        global_user_state.remove_cluster(cluster_name)

    global_user_state.remove_task(task_id)


def _reuse_cluster(task, cluster_name):
    """Reuses a cluster. Update cluster if required."""

    handle = global_user_state.get_handle_from_cluster_name(cluster_name)

    # Ensure changes to workdir, setup, etc. are reflected in the cluster
    region, zones = _get_region_zones_from_handle(handle)

    with open(handle, 'r') as f:
        existing_cluster_handle_content = f.read()

    config_dict = backend_utils.write_cluster_config(
        None,
        task,
        cloud_vm_ray_backend._get_cluster_config_template(task),
        region=region,
        zones=zones,
        dryrun=False,
        cluster_name=cluster_name)

    new_handle = str(config_dict['ray'])
    assert new_handle == handle, 'Cluster handle changed'

    with open(new_handle, 'r') as f:
        new_cluster_handle_content = f.read()
        cluster_config_changed = existing_cluster_handle_content != new_cluster_handle_content

    # If cluster configs are identical, then no need to re-run this step.
    if cluster_config_changed:
        cloud_vm_ray_backend._run(f'ray up -y {handle} --no-config-cache')

    return new_handle


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
        task = sky.Task.from_yaml(entry_point)

    # TODO: This is sketchy. What if we're reusing a cluster and the optimized plan is different?
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)

    handle = None
    if cluster is not None:
        new_task = dag.tasks[0]
        handle = _reuse_cluster(new_task, cluster)

    sky.execute(dag, dryrun=dryrun, handle=handle, stream_logs=stream_logs)


@cli.command()
def sweep():
    """Sweep over a set of parameters."""

    # TODO: integrate into run and design with YAML in mind
    # per_trial_resources = ...
    # total_resources = ...

    # par_task = sky.ParTask([
    #     sky.Task(
    #         run=f'python app.py -s={i}').set_resources(per_trial_resources)
    #     for i in range(10)
    # ])

    # # Provision and share a total of this many resources.  Inner Tasks will
    # # be bin-packed and scheduled according to their demands.
    # par_task.set_resources(total_resources)
    raise NotImplementedError()


@cli.command()
@click.argument('task_id', required=True, type=str)
def cancel(task_id):
    """Cancel a task."""
    # TODO: Current implementation is blocking and will wait for the task to complete.
    # If this is changed to non-blocking, then we will need a way to kill async tasks with ray exec.
    
    global_user_state.remove_task(task_id)


@cli.command()
def status():
    """Show the status of all tasks and clusters."""

    tasks_status = global_user_state.get_tasks()
    clusters_status = global_user_state.get_clusters()

    task_table = PrettyTable()
    task_table.field_names = ["TASK ID", "TASK NAME", "LAUNCHED"]
    for task_status in tasks_status:
        launched_at = task_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        task_table.add_row([
            task_status['id'],
            task_status['name'],
            duration.diff_for_humans(),
        ])

    cluster_table = PrettyTable()
    cluster_table.field_names = ["CLUSTER NAME", "LAUNCHED"]
    for cluster_status in clusters_status:
        launched_at = cluster_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        cluster_table.add_row([
            cluster_status['name'],
            duration.diff_for_humans(),
        ])

    click.echo(task_table)
    click.echo(cluster_table)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
def gpunode(cluster):
    """Launch an interactive GPU node. Automatically syncs current working directory."""
    # TODO: Sync code files between local and interactive node (watch rsync?)
    # TODO: Add port forwarding to allow access to localhost:PORT for jupyter

    handle = None
    if cluster is not None:
        handle = global_user_state.get_handle_from_cluster_name(cluster)

    _create_interactive_node('gpunode',
                             {sky.Resources(sky.AWS(), accelerators='V100')},
                             cluster_handle=handle)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
@click.option('--docker', '-i', default=False, type=bool)
def cpunode(cluster):
    """Launch an interactive CPU node. Automatically syncs current working directory."""

    handle = None
    if cluster is not None:
        handle = global_user_state.get_handle_from_cluster_name(cluster)

    _create_interactive_node('cpunode', {sky.Resources(sky.AWS())},
                             cluster_handle=handle)


@cli.command()
@click.option('--cluster', '-c', default=None, type=str)
def tpunode(cluster):
    """Launch an interactive TPU node. Automatically syncs current working directory."""
    raise NotImplementedError()


def main():
    return cli()


if __name__ == '__main__':
    main()
