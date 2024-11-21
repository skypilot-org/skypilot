import os
import uuid

from airflow import DAG
from airflow.decorators import task
from airflow.utils.dates import days_ago
import yaml

import sky

default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}

# Update this path to the root of the mock workflow
MOCK_WORKFLOW_ROOT = '/Users/romilb/Romil/Berkeley/Research/sky-experiments/examples/airflow/workflow_clientserver/'


def task_failure_callback(context):
    """Callback to shut down SkyPilot cluster on task failure."""
    cluster_name = context['task_instance'].xcom_pull(
        task_ids=context['task_instance'].task_id, key='cluster_name')
    if cluster_name:
        print(
            f"Task failed or was cancelled. Shutting down SkyPilot cluster: {cluster_name}"
        )
        sky.down(cluster_name)


@task(on_failure_callback=task_failure_callback)
def run_sky_task(task_name: str, bucket_name: str, bucket_store_type: str,
                 **kwargs):
    """Generic function to run a SkyPilot task.
    
    Args:
        task_name: Name of the task (data_preprocessing/train/eval)
        bucket_url: URL of the data bucket to be passed in DATA_BUCKET_URL env var
    """
    yaml_path = MOCK_WORKFLOW_ROOT + f"{task_name}.yaml"

    with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
        task_config = yaml.safe_load(f)

    # Update the envs with the bucket name and store type
    # task.update_envs() is not used here, see https://github.com/skypilot-org/skypilot/issues/4363
    task_config['envs']['DATA_BUCKET_NAME'] = bucket_name
    task_config['envs']['DATA_BUCKET_STORE_TYPE'] = bucket_store_type

    task = sky.Task.from_yaml_config(task_config)
    cluster_uuid = str(uuid.uuid4())[:4]  # To uniquely identify the cluster
    cluster_name = f'{task_name}-{cluster_uuid}'
    kwargs['ti'].xcom_push(key='cluster_name',
                           value=cluster_name)  # For failure callback

    launch_request_id = sky.launch(task, cluster_name=cluster_name, down=True)
    job_id, _ = sky.stream_and_get(launch_request_id)
    # TODO(romilb): In the future, we can use deferrable tasks to avoid blocking
    # the worker while waiting for cluster to start.

    # Stream the logs for airflow logging
    job_logs = sky.tail_logs(cluster_name=cluster_name,
                             job_id=job_id,
                             follow=True)
    sky.stream_and_get(job_logs)


with DAG(dag_id='sky_k8s_train_pipeline',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    bucket_uuid = str(uuid.uuid4())[:4]
    bucket_name = f"sky-data-demo-{bucket_uuid}"
    bucket_store_type = "s3"

    preprocess = run_sky_task.override(task_id="data_preprocess")(
        "data_preprocessing", bucket_name, bucket_store_type)
    train_task = run_sky_task.override(task_id="train")("train", bucket_name,
                                                        bucket_store_type)
    eval_task = run_sky_task.override(task_id="eval")("eval", bucket_name,
                                                      bucket_store_type)

    # Define the workflow
    preprocess >> train_task >> eval_task
