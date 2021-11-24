import time

import click
import pendulum
from prettytable import PrettyTable

import sky
from sky.user_manager import UserManager


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


@click.group()
def cli():
    pass


@cli.command()
@click.argument('config_path', required=True, type=str)
@click.option('--dryrun',
              '-n',
              default=False,
              type=bool,
              help='If True, do not actually run the job.')
def run(config_path, dryrun):
    """Launch a job from a YAML config."""

    session = UserManager()

    with sky.Dag() as dag:
        task = sky.Task.from_yaml(config_path)

        # TODO: Factor this within Sky kernel
        # Reasoning here would be that if a user wants to write a custom script then
        # their launched tasks/clusters would still be tracked by the user manager.
        import pdb
        pdb.set_trace()
        session.add_task(task)

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun)


@cli.command()
def status():
    """Show the status of all tasks and clusters."""

    session = UserManager()
    tasks_status = session.get_tasks()
    clusters_status = session.get_clusters()

    task_table = PrettyTable()
    task_table.field_names = ["ID", "NAME", "DURATION", "STATUS"]
    for task_status in tasks_status:
        launched_at = task_status['launched_at']
        duration = pendulum.now().subtract(seconds=time.time() - launched_at)
        task_table.add_row([
            task_status['id'], 
            task_status['name'], 
            duration.diff_for_humans(),
            task_status['status']
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
def gpunode():
    """Launch an interactive GPU node."""
    pass
    # with sky.Dag() as dag:
    # sky.Task


@cli.command()
def cpunode():
    """Launch an interactive CPU node."""
    pass
    # with sky.Dag() as dag:
    # sky.Task


@cli.command()
def tpunode():
    """Launch an interactive TPU node."""
    pass


def main():
    return cli()


if __name__ == '__main__':
    main()
