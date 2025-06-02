import click

import sky
from sky.client import sdk
from sky.jobs.client import sdk as jobs_sdk
from sky.utils import registry

# Rewrite the resource parameter to avoid import since this file will be
# executed by different Python environments
LOW_RESOURCE_PARAM = {
    'cpus': '2+',
    'memory': '4+',
}


@click.group()
def cli():
    pass


@cli.command()
@click.option("--cluster-name",
              type=str,
              help="The name of the cluster to launch")
@click.option("--cloud", type=str, help="The cloud to launch the cluster on")
@click.option("--command", type=str, help="The command to run on the cluster")
def launch_cluster(cluster_name: str, cloud: str, command: str) -> None:
    task = sky.Task(run=command)
    cloud = registry.CLOUD_REGISTRY.from_str(cloud)
    resource = sky.Resources(cloud=cloud, **LOW_RESOURCE_PARAM)
    task.set_resources(resource)
    request_id = sdk.launch(cluster_name=cluster_name, task=task)
    sdk.stream_and_get(request_id)


@cli.command()
def api_info() -> None:
    print(sdk.api_info())


@cli.command()
@click.option("--cluster-name", type=str, help="The name of the cluster")
def get_cluster_status(cluster_name: str) -> None:
    request_id = sdk.status([cluster_name])
    cluster_records = sdk.stream_and_get(request_id)
    print(cluster_records)


@cli.command()
@click.option("--cluster-name", type=str, help="The name of the cluster")
def queue(cluster_name: str) -> None:
    request_id = sdk.queue(cluster_name)
    records = sdk.stream_and_get(request_id)
    print(records)


@cli.command()
@click.option("--cluster-name", type=str, help="The name of the cluster")
@click.option("--job-id", type=int, help="The id of the job")
def cluster_logs(cluster_name: str, job_id: int) -> None:
    request_id = sdk.tail_logs(cluster_name, job_id, follow=False)
    sdk.stream_and_get(request_id)


@cli.command()
@click.option("--job-name", type=str, help="The name of the job")
@click.option("--cloud", type=str, help="The cloud to launch the job on")
@click.option("--command", type=str, help="The command to run on the job")
def launch_managed_job(job_name: str, cloud: str, command: str) -> None:
    task = sky.Task(run=command)
    task.name = job_name
    cloud = registry.CLOUD_REGISTRY.from_str(cloud)
    resource = sky.Resources(cloud=cloud, **LOW_RESOURCE_PARAM)
    task.set_resources(resource)
    request_id = jobs_sdk.launch(
        name=job_name,
        task=task,
    )
    sdk.stream_and_get(request_id)


@cli.command()
@click.option("--job-name", type=str, help="The name of the job")
def managed_job_logs(job_name: str) -> None:
    jobs_sdk.tail_logs(name=job_name, follow=False)


@cli.command()
def managed_job_queue() -> None:
    request_id = jobs_sdk.queue(refresh=True)
    records = sdk.stream_and_get(request_id)
    print(records)


@cli.command()
@click.option("--cluster-name", type=str, help="The name of the cluster")
def down(cluster_name: str) -> None:
    request_id = sdk.down(cluster_name)
    sdk.stream_and_get(request_id)


if __name__ == "__main__":
    cli()
