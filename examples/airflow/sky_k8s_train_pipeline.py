import os
import uuid

from airflow import DAG
from airflow.decorators import task
from airflow.models import Variable
from airflow.utils.dates import days_ago
import yaml

default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}

# Unique bucket name for this DAG run
DATA_BUCKET_NAME = str(uuid.uuid4())[:4]


def task_failure_callback(context):
    """Callback to shut down SkyPilot cluster on task failure."""
    cluster_name = context['task_instance'].xcom_pull(
        task_ids=context['task_instance'].task_id, key='cluster_name')
    if cluster_name:
        print(
            f"Task failed or was cancelled. Shutting down SkyPilot cluster: {cluster_name}"
        )
        import sky
        down_request = sky.down(cluster_name)
        sky.stream_and_get(down_request)


@task(on_failure_callback=task_failure_callback)
def run_sky_task(base_path: str,
                 yaml_path: str,
                 envs_override: dict = None,
                 git_branch: str = None,
                 **kwargs):
    """Generic function to run a SkyPilot task.

    This is a blocking call that runs the SkyPilot task and streams the logs.
    In the future, we can use deferrable tasks to avoid blocking the worker
    while waiting for cluster to start.

    Args:
        base_path: Base path (local directory or git repo URL)
        yaml_path: Path to the YAML file (relative to base_path)
        envs_override: Dictionary of environment variables to override in the task config
        git_branch: Optional branch name to checkout (only used if base_path is a git repo)
    """
    import subprocess
    import tempfile

    # Set the SkyPilot API server endpoint from Airflow Variables
    endpoint = Variable.get('SKYPILOT_API_SERVER_ENDPOINT', None)
    if not endpoint:
        raise ValueError('SKYPILOT_API_SERVER_ENDPOINT is not set in airflow.')
    os.environ['SKYPILOT_API_SERVER_ENDPOINT'] = endpoint

    original_cwd = os.getcwd()
    try:
        # Handle git repos vs local paths
        if base_path.startswith(('http://', 'https://', 'git://')):
            with tempfile.TemporaryDirectory() as temp_dir:
                # TODO(romilb): This assumes git credentials are available in the airflow worker
                subprocess.run(['git', 'clone', base_path, temp_dir],
                               check=True)

                # Checkout specific branch if provided
                if git_branch:
                    subprocess.run(['git', 'checkout', git_branch],
                                   cwd=temp_dir,
                                   check=True)

                full_yaml_path = os.path.join(temp_dir, yaml_path)
                # Change to the temp dir to set context
                os.chdir(temp_dir)

                # Run the sky task
                return _run_sky_task(full_yaml_path, envs_override or {},
                                     kwargs)
        else:
            full_yaml_path = os.path.join(base_path, yaml_path)
            os.chdir(base_path)

            # Run the sky task
            return _run_sky_task(full_yaml_path, envs_override or {}, kwargs)
    finally:
        os.chdir(original_cwd)


def _run_sky_task(yaml_path: str, envs_override: dict, kwargs: dict):
    """Internal helper to run the sky task after directory setup."""
    import sky

    with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
        task_config = yaml.safe_load(f)

    # Initialize envs if not present
    if 'envs' not in task_config:
        task_config['envs'] = {}

    # Update the envs with the override values
    # task.update_envs() is not used here, see https://github.com/skypilot-org/skypilot/issues/4363
    task_config['envs'].update(envs_override)

    task = sky.Task.from_yaml_config(task_config)
    cluster_uuid = str(uuid.uuid4())[:4]
    task_name = os.path.splitext(os.path.basename(yaml_path))[0]
    cluster_name = f'{task_name}-{cluster_uuid}'
    kwargs['ti'].xcom_push(key='cluster_name',
                           value=cluster_name)  # For failure callback

    launch_request_id = sky.launch(task, cluster_name=cluster_name, down=True)
    job_id, _ = sky.stream_and_get(launch_request_id)
    # TODO(romilb): In the future, we can use deferrable tasks to avoid blocking
    # the worker while waiting for cluster to start.

    # Stream the logs for airflow logging
    sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)


@task
def generate_bucket_uuid(**context):
    bucket_uuid = str(uuid.uuid4())[:4]
    return bucket_uuid


with DAG(dag_id='sky_k8s_train_pipeline',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    # Path to SkyPilot YAMLs. Can be a git repo or local directory.
    base_path = 'https://github.com/romilbhardwaj/mock_train_workflow.git'

    # Generate bucket UUID as first task
    # See https://stackoverflow.com/questions/55748050/generating-uuid-and-use-it-across-airflow-dag
    bucket_uuid = generate_bucket_uuid()

    # Use the bucket_uuid from previous task
    common_envs = {
        'DATA_BUCKET_NAME': f"sky-data-demo-{{{{ task_instance.xcom_pull(task_ids='generate_bucket_uuid') }}}}",
        'DATA_BUCKET_STORE_TYPE': 's3'
    }

    preprocess = run_sky_task.override(task_id="data_preprocess")(
        base_path,
        'data_preprocessing.yaml',
        envs_override=common_envs,
        git_branch='clientserver_example')
    train_task = run_sky_task.override(task_id="train")(
        base_path,
        'train.yaml',
        envs_override=common_envs,
        git_branch='clientserver_example')
    eval_task = run_sky_task.override(task_id="eval")(
        base_path,
        'eval.yaml',
        envs_override=common_envs,
        git_branch='clientserver_example')

    # Define the workflow
    bucket_uuid >> preprocess >> train_task >> eval_task
