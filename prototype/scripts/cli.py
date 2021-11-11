import os
import subprocess

import click

import sky
from sky import clouds


@click.group()
def cli():
    pass


@cli.command()
@click.argument("task_id", required=True, type=str)
def logs(task_id):
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

    # Export current conda environment
    subprocess.run(f'mkdir -p .sky && conda env export --no-builds | grep -v "^prefix: " > .sky/environment.yml',
                    shell=True,
                    check=True,
                    capture_output=True)
    
    env_name = os.environ['CONDA_DEFAULT_ENV']

    run = f"""
    source /home/ubuntu/anaconda3/etc/profile.d/conda.sh
    conda env create -f .sky/environment.yml
    conda activate {env_name}
    """

    with sky.Dag() as dag:

        task = sky.Task(
            'gpunode',
            workdir=os.getcwd(),
            setup='',
            run=run,
        )

        task.set_resources({
            ##### Fully specified
            # sky.Resources(clouds.AWS(), 'p3.2xlarge'),
            # sky.Resources(clouds.GCP(), 'n1-standard-16'),
            # sky.Resources(
            #     clouds.GCP(),
            #     'n1-standard-8',
            #     # Options: 'V100', {'V100': <num>}.
            #     'V100',
            # ),
            ##### Partially specified
            # sky.Resources(accelerators='V100'),
            # sky.Resources(accelerators='tpu-v3-8'),
            sky.Resources(clouds.AWS(), accelerators={'V100': n_gpus}),
            # sky.Resources(clouds.AWS(), accelerators='V100'),
        })
    
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag)

    # TODO(gmittal): Handle graceful attach + detach and automatic activation of conda env


@cli.command()
@click.option("--n-cpus",
              "-N",
              default=4,
              type=int,
              help="Select number of CPUs to allocate.")
def cpunode(n_cpus):
    """Create an interactive CPU session."""

    # TODO: Currently no way to ask for number of CPUs as resources.
    # These kinds of nodes are very useful for data preprocessing/downloading.
    raise NotImplementedError


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
    """View all jobs and their state.
    
    TODO: Current framework only supports one job at a time. Need to fix
    https://github.com/concretevitamin/sky-experiments/issues/18 before this.
    """
    raise NotImplementedError()


def main():
    return cli()


if __name__ == "__main__":
    main()
