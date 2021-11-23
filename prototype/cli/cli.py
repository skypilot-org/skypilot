import click

import sky


@click.group()
def cli():
    pass


@cli.command()
@click.argument('config_path', required=True, type=str)
@click.option('--dryrun', '-n', default=False, type=bool, help='Dry run.')
def run(config_path, dryrun):
    """Launch a job from a YAML config."""

    with sky.Dag() as dag:
        sky.Task.from_yaml(config_path)

    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=dryrun)


def main():
    return cli()


if __name__ == '__main__':
    main()
