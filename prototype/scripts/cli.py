import click
import sky


@click.group()
def cli():
    pass


@cli.command()
@click.argument("task_id", required=True, type=str)
def log(task_id):
    """View log output for a task."""
    click.echo(task_id)


@cli.command()
def login(task_id):
    """Authenticate with clouds."""
    raise NotImplementedError(task_id)


@cli.command()
@click.argument("task_id", required=True, type=str)
def attach(task_id):
    """Login to the interactive node running a task."""
    raise NotImplementedError(task_id)


@cli.command()
@click.option("--n-gpus",
              "-N",
              default=1,
              type=int,
              help="Select number of GPUs to allocate.")
def gpunode(n_gpus):
    """Create an interactive GPU session."""
    click.echo(n_gpus)


@cli.command()
@click.option("--n-cpus",
              "-N",
              default=4,
              type=int,
              help="Select number of CPUs to allocate.")
def cpunode(n_cpus):
    """Create an interactive CPU session."""
    click.echo(n_cpus)


@cli.command()
@click.argument("config_path", required=True, type=str)
@click.option('--dryrun', '-d', default=False, type=bool, help="Dry run.")
def launch(config_path, dryrun):
    """Launch a job from a YAML config."""

    with sky.Dag() as dag:
        task = sky.Task.from_yaml(config_path)

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun)
    sky.execute(dag)


@cli.command()
@click.argument("task_id", required=True, type=str)
def cancel(task_id):
    """Cancel a task."""
    raise NotImplementedError(task_id)


@cli.command()
def ls():
    """View all jobs and their state."""
    click.echo("nothing")


def main():
    return cli()


if __name__ == "__main__":
    main()
