import click
import sky


@click.group()
def cli():
    pass


@cli.command()
@click.argument("config_path", required=True, type=str)
@click.option('--dryrun', '-d', default=False, type=bool, help="Dry run.")
def run(config_path, dryrun):
    """Launch a job from a YAML config."""

    with sky.Dag() as dag:
        task = sky.Task.from_yaml(config_path)

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun)
    sky.execute(dag)


def main():
    return cli()


if __name__ == "__main__":
    main()
