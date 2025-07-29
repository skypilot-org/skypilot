from typing import Optional
import uuid

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowNotFoundException
from airflow.providers.google.common.hooks.base_google import GoogleBaseHook
import pendulum

default_args = {
    'owner': 'airflow',
    'start_date': pendulum.today('UTC').add(days=-1,),
}


@task.virtualenv(
    python_version='3.11',
    requirements=['skypilot-nightly[gcp,aws,kubernetes]'],
    system_site_packages=False,
    templates_dict={
        'SKYPILOT_API_SERVER_ENDPOINT':
            ('{{ var.value.SKYPILOT_API_SERVER_ENDPOINT }}'),
    },
)
def run_sky_task(
    base_path: str,  # pylint: disable=redefined-outer-name
    yaml_path: str,
    gcp_service_account_json: Optional[str] = None,  # pylint: disable=redefined-outer-name
    envs_override: dict = None,
    git_branch: str = None,
    **kwargs,
):
    """Generic function to run a SkyPilot task.

    This is a blocking call that runs the SkyPilot task and streams the logs.
    In the future, we can use deferrable tasks to avoid blocking the worker
    while waiting for cluster to start.

    Args:
        base_path: Base path (local directory or git repo URL)
        yaml_path: Path to the YAML file (relative to base_path)
        gcp_service_account_json: GCP service account JSON-encoded string
        envs_override: Dictionary of environment variables to override in the
            task config
        git_branch: Optional branch name to checkout (only used if base_path
            is a git repo)
    """
    # pylint: disable=import-outside-toplevel
    import os
    import subprocess
    import tempfile
    # pylint: disable=import-outside-toplevel, reimported, redefined-outer-name
    import uuid

    import yaml

    def _run_sky_task(yaml_path: str, envs_override: dict):
        """Internal helper to run the sky task after directory setup."""
        # pylint: disable=import-outside-toplevel
        import sky
        with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
            task_config = yaml.safe_load(f)

        # Initialize envs if not present
        if 'envs' not in task_config:
            task_config['envs'] = {}

        # Update the envs with the override values
        # task.update_envs() is not used here, see
        # https://github.com/skypilot-org/skypilot/issues/4363
        task_config['envs'].update(envs_override)

        # pylint: disable=redefined-outer-name
        task = sky.Task.from_yaml_config(task_config)
        cluster_uuid = str(uuid.uuid4())[:4]
        task_name = os.path.splitext(os.path.basename(yaml_path))[0]
        cluster_name = f'{task_name}-{cluster_uuid}'

        print(f'Starting SkyPilot task with cluster: {cluster_name}')

        launch_request_id = sky.launch(task,
                                       cluster_name=cluster_name,
                                       down=True)
        job_id, _ = sky.stream_and_get(launch_request_id)
        # TODO(romilb): In the future, we can use deferrable tasks to avoid
        # blocking the worker while waiting for cluster to start.

        # Stream the logs for airflow logging
        sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

        # Terminate the cluster after the task is done
        down_id = sky.down(cluster_name)
        sky.stream_and_get(down_id)

        return cluster_name

    # Set the SkyPilot API server endpoint
    if kwargs['templates_dict']:
        os.environ['SKYPILOT_API_SERVER_ENDPOINT'] = (
            kwargs['templates_dict']['SKYPILOT_API_SERVER_ENDPOINT'])

    original_cwd = os.getcwd()

    # Write GCP service account JSON to a temporary file,
    # which will be mounted to the SkyPilot cluster.
    if gcp_service_account_json:
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix='.json') as temp_file:
            temp_file.write(gcp_service_account_json.encode('utf-8'))
            envs_override['GCP_SERVICE_ACCOUNT_JSON_PATH'] = temp_file.name

    try:
        # Handle git repos vs local paths
        if base_path.startswith(('http://', 'https://', 'git://')):
            with tempfile.TemporaryDirectory() as temp_dir:
                # TODO(romilb): This assumes git credentials are available
                # in the airflow worker
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
                return _run_sky_task(full_yaml_path, envs_override or {})
        else:
            full_yaml_path = os.path.join(base_path, yaml_path)
            os.chdir(base_path)

            # Run the sky task
            return _run_sky_task(full_yaml_path, envs_override or {})
    finally:
        os.chdir(original_cwd)


@task
def generate_bucket_uuid():
    """Generate a unique bucket UUID for this DAG run."""
    bucket_uuid = str(uuid.uuid4())[:4]  # pylint: disable=redefined-outer-name
    return bucket_uuid


@task
def get_gcp_service_account_json() -> Optional[str]:
    """Fetch GCP credentials from Airflow connection."""
    try:
        hook = GoogleBaseHook(gcp_conn_id='skypilot_gcp_task')
        status, message = hook.test_connection()
        print(f'GCP connection status: {status}, message: {message}')
    except AirflowNotFoundException:
        print('GCP connection not found, skipping')
        return None
    conn = hook.get_connection(hook.gcp_conn_id)
    service_account_json = conn.extra_dejson.get('keyfile_dict')
    return service_account_json


with DAG(dag_id='sky_train_dag', default_args=default_args,
         catchup=False) as dag:
    # Path to SkyPilot YAMLs. Can be a git repo or local directory.
    base_path = 'https://github.com/skypilot-org/mock-train-workflow.git'

    # Generate bucket UUID as first task
    bucket_uuid = generate_bucket_uuid()

    # Get GCP credentials (if available)
    gcp_service_account_json = get_gcp_service_account_json()

    # Use the bucket_uuid from previous task
    common_envs = {
        'DATA_BUCKET_NAME': f'sky-data-demo-{bucket_uuid}',
        'DATA_BUCKET_STORE_TYPE': 's3',
    }

    preprocess_task = run_sky_task.override(task_id='data_preprocess')(
        base_path,
        # Or data_preprocessing_gcp_sa.yaml if you want
        # to use a custom GCP service account
        'data_preprocessing.yaml',
        gcp_service_account_json=gcp_service_account_json,
        envs_override=common_envs,
    )
    train_task = run_sky_task.override(task_id='train')(
        base_path, 'train.yaml', envs_override=common_envs)
    eval_task = run_sky_task.override(task_id='eval')(base_path,
                                                      'eval.yaml',
                                                      envs_override=common_envs)

    # Define the workflow
    # pylint: disable=pointless-statement
    (bucket_uuid >> gcp_service_account_json >> preprocess_task >> train_task >>
     eval_task)
