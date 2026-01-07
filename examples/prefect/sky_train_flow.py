"""SkyPilot integration for Prefect.

This module demonstrates how to run SkyPilot tasks as part of a Prefect flow.
It provides a reusable `run_sky_task` function that can be used to execute
any SkyPilot YAML task as a Prefect task.

Example usage:
    from prefect import flow
    from sky_train_flow import run_sky_task

    @flow
    def my_workflow():
        # Run a SkyPilot task from a Git repository
        run_sky_task(
            base_path='https://github.com/skypilot-org/mock-train-workflow.git',
            yaml_path='train.yaml',
            envs_override={'EPOCHS': '10'}
        )

    if __name__ == '__main__':
        my_workflow()
"""
from typing import Any, Dict, Optional
import uuid

from prefect import flow
from prefect import get_run_logger
from prefect import task
from prefect.blocks.system import Secret


@task(
    name='run_sky_task',
    description='Run a SkyPilot task on cloud infrastructure',
    retries=2,
    retry_delay_seconds=30,
)
def run_sky_task(
    base_path: str,
    yaml_path: str,
    envs_override: Optional[Dict[str, Any]] = None,
    git_branch: Optional[str] = None,
    cluster_prefix: Optional[str] = None,
    down_on_completion: bool = True,
    api_server_endpoint: Optional[str] = None,
) -> str:
    """Run a SkyPilot task from a YAML configuration file.

    This task launches a SkyPilot cluster, runs the specified task, streams
    the logs, and optionally tears down the cluster upon completion.

    Args:
        base_path: Base path to the task YAML files. Can be either:
            - A local directory path (e.g., '/path/to/tasks')
            - A Git repository URL (e.g., 'https://github.com/org/repo.git')
        yaml_path: Path to the YAML file relative to base_path.
        envs_override: Dictionary of environment variables to override in the
            task configuration. These will be merged with any existing envs
            in the YAML file.
        git_branch: Optional branch name to checkout if base_path is a Git
            repository URL.
        cluster_prefix: Optional prefix for the cluster name. If not provided,
            the task name from the YAML file is used.
        down_on_completion: Whether to tear down the cluster after the task
            completes. Defaults to True. Set to False if you want to keep the
            cluster for debugging or subsequent tasks.
        api_server_endpoint: Optional SkyPilot API server endpoint. If not
            provided, uses the SKYPILOT_API_SERVER_ENDPOINT environment
            variable or defaults to local execution.

    Returns:
        The name of the cluster that was used to run the task.

    Raises:
        Exception: If the SkyPilot task fails to launch or execute.
    """
    # pylint: disable=import-outside-toplevel,redefined-outer-name
    import os
    import subprocess
    import tempfile

    import yaml

    logger = get_run_logger()

    # Set the API server endpoint if provided
    if api_server_endpoint:
        os.environ['SKYPILOT_API_SERVER_ENDPOINT'] = api_server_endpoint

    # Import sky after setting environment variables
    import sky

    def _run_sky_task_internal(
        full_yaml_path: str,
        envs: Dict[str, Any],
        working_dir: str,
    ) -> str:
        """Internal helper to run the sky task."""
        original_cwd = os.getcwd()
        try:
            os.chdir(working_dir)

            with open(os.path.expanduser(full_yaml_path), 'r',
                      encoding='utf-8') as f:
                task_config = yaml.safe_load(f)

            # Initialize envs if not present
            if 'envs' not in task_config:
                task_config['envs'] = {}

            # Update the envs with the override values
            task_config['envs'].update(envs)

            # Create the SkyPilot task
            sky_task = sky.Task.from_yaml_config(task_config)

            # Generate a unique cluster name
            cluster_uuid = str(uuid.uuid4())[:4]
            task_name = os.path.splitext(os.path.basename(full_yaml_path))[0]
            prefix = cluster_prefix or task_name
            cluster_name = f'{prefix}-{cluster_uuid}'

            logger.info(f'Starting SkyPilot task on cluster: {cluster_name}')
            logger.info(f'Task YAML: {full_yaml_path}')
            logger.info(f'Environment overrides: {envs}')

            # Launch the cluster and run the task
            launch_request_id = sky.launch(
                sky_task,
                cluster_name=cluster_name,
                down=down_on_completion,
            )

            # Stream logs and wait for completion
            job_id, _ = sky.stream_and_get(launch_request_id)
            logger.info(f'Task launched with job_id: {job_id}')

            # Stream the task logs
            sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

            # Tear down the cluster if requested
            if down_on_completion:
                logger.info(f'Tearing down cluster: {cluster_name}')
                down_request_id = sky.down(cluster_name)
                sky.stream_and_get(down_request_id)
                logger.info(f'Cluster {cluster_name} terminated successfully')

            return cluster_name

        finally:
            os.chdir(original_cwd)

    # Handle git repos vs local paths
    if base_path.startswith(('http://', 'https://', 'git://')):
        logger.info(f'Cloning repository: {base_path}')
        with tempfile.TemporaryDirectory() as temp_dir:
            # Clone the repository
            clone_cmd = ['git', 'clone', base_path, temp_dir]
            subprocess.run(clone_cmd, check=True, capture_output=True)

            # Checkout specific branch if provided
            if git_branch:
                logger.info(f'Checking out branch: {git_branch}')
                subprocess.run(
                    ['git', 'checkout', git_branch],
                    cwd=temp_dir,
                    check=True,
                    capture_output=True,
                )

            full_yaml_path = os.path.join(temp_dir, yaml_path)
            return _run_sky_task_internal(
                full_yaml_path,
                envs_override or {},
                temp_dir,
            )
    else:
        full_yaml_path = os.path.join(base_path, yaml_path)
        return _run_sky_task_internal(
            full_yaml_path,
            envs_override or {},
            base_path,
        )


@task(name='generate_bucket_uuid', description='Generate a unique bucket UUID')
def generate_bucket_uuid() -> str:
    """Generate a unique bucket UUID for the workflow run."""
    return str(uuid.uuid4())[:4]


@task(name='get_secret', description='Fetch a secret from Prefect')
def get_secret(secret_name: str) -> Optional[str]:
    """Fetch a secret from Prefect's Secret block.

    Args:
        secret_name: Name of the secret block in Prefect.

    Returns:
        The secret value, or None if the secret doesn't exist.
    """
    logger = get_run_logger()
    try:
        secret_block = Secret.load(secret_name)
        return secret_block.get()
    except ValueError:
        logger.warning(f'Secret {secret_name} not found, skipping')
        return None


@flow(
    name='sky_train_workflow',
    description='ML training workflow using SkyPilot for cloud infrastructure',
)
def sky_train_workflow(
    base_path: str = 'https://github.com/skypilot-org/mock-train-workflow.git',
    data_bucket_store_type: str = 's3',
    api_server_endpoint: Optional[str] = None,
):
    """End-to-end ML training workflow using SkyPilot.

    This flow demonstrates a typical ML workflow with three stages:
    1. Data preprocessing - Generates/processes data and writes to cloud storage
    2. Training - Trains a model using the processed data
    3. Evaluation - Evaluates the trained model

    Each stage runs as a separate SkyPilot task on cloud infrastructure.

    Args:
        base_path: Path to the task YAML files (local dir or Git URL).
        data_bucket_store_type: Cloud storage type ('s3', 'gcs', 'azure').
        api_server_endpoint: Optional SkyPilot API server endpoint.
    """
    logger = get_run_logger()
    logger.info('Starting SkyPilot training workflow')

    # Generate a unique bucket name for this run
    bucket_uuid = generate_bucket_uuid()
    bucket_name = f'sky-data-demo-{bucket_uuid}'

    logger.info(f'Using bucket: {bucket_name}')

    # Common environment variables for all tasks
    common_envs = {
        'DATA_BUCKET_NAME': bucket_name,
        'DATA_BUCKET_STORE_TYPE': data_bucket_store_type,
    }

    # Stage 1: Data Preprocessing
    logger.info('Stage 1: Running data preprocessing')
    run_sky_task(
        base_path=base_path,
        yaml_path='data_preprocessing.yaml',
        envs_override=common_envs,
        cluster_prefix='preprocess',
        api_server_endpoint=api_server_endpoint,
    )

    # Stage 2: Training
    logger.info('Stage 2: Running training')
    run_sky_task(
        base_path=base_path,
        yaml_path='train.yaml',
        envs_override=common_envs,
        cluster_prefix='train',
        api_server_endpoint=api_server_endpoint,
    )

    # Stage 3: Evaluation
    logger.info('Stage 3: Running evaluation')
    run_sky_task(
        base_path=base_path,
        yaml_path='eval.yaml',
        envs_override=common_envs,
        cluster_prefix='eval',
        api_server_endpoint=api_server_endpoint,
    )

    logger.info('Workflow completed successfully!')
    return bucket_name


@flow(
    name='sky_gpu_workflow',
    description='GPU-accelerated workflow using SkyPilot',
)
def sky_gpu_workflow(
    task_yaml: str,
    num_gpus: int = 1,
    gpu_type: Optional[str] = None,
    envs: Optional[Dict[str, Any]] = None,
    api_server_endpoint: Optional[str] = None,
):
    """Run a GPU-accelerated task on cloud infrastructure.

    This flow is useful for running single GPU tasks like:
    - Fine-tuning LLMs
    - Running inference
    - Training computer vision models

    Args:
        task_yaml: Path to the task YAML file or inline YAML string.
        num_gpus: Number of GPUs to request.
        gpu_type: Optional GPU type (e.g., 'A100', 'V100', 'H100').
        envs: Optional environment variables to pass to the task.
        api_server_endpoint: Optional SkyPilot API server endpoint.

    Returns:
        The name of the cluster used.
    """
    # pylint: disable=import-outside-toplevel,redefined-outer-name
    import os
    import tempfile

    import yaml

    logger = get_run_logger()

    # Set the API server endpoint if provided
    if api_server_endpoint:
        os.environ['SKYPILOT_API_SERVER_ENDPOINT'] = api_server_endpoint

    import sky

    # Handle inline YAML or file path
    if task_yaml.strip().startswith(('name:', 'resources:', 'setup:', 'run:')):
        # Inline YAML string
        task_config = yaml.safe_load(task_yaml)
    else:
        # File path
        with open(os.path.expanduser(task_yaml), 'r', encoding='utf-8') as f:
            task_config = yaml.safe_load(f)

    # Update resources with GPU requirements
    if 'resources' not in task_config:
        task_config['resources'] = {}

    if num_gpus > 0:
        task_config['resources']['accelerators'] = (
            f'{gpu_type}:{num_gpus}' if gpu_type else f'A10G:{num_gpus}')

    # Update environment variables
    if envs:
        if 'envs' not in task_config:
            task_config['envs'] = {}
        task_config['envs'].update(envs)

    # Create a temporary file for the modified YAML
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(task_config, f)
        temp_yaml_path = f.name

    try:
        sky_task = sky.Task.from_yaml_config(task_config)

        cluster_uuid = str(uuid.uuid4())[:4]
        task_name = task_config.get('name', 'gpu-task')
        cluster_name = f'{task_name}-{cluster_uuid}'

        logger.info(f'Starting GPU task on cluster: {cluster_name}')
        logger.info(f'GPU configuration: {task_config["resources"]}')

        # Launch the cluster
        launch_request_id = sky.launch(
            sky_task,
            cluster_name=cluster_name,
            down=True,
        )

        job_id, _ = sky.stream_and_get(launch_request_id)
        logger.info(f'Task launched with job_id: {job_id}')

        # Stream logs
        sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

        # Tear down
        logger.info(f'Tearing down cluster: {cluster_name}')
        down_request_id = sky.down(cluster_name)
        sky.stream_and_get(down_request_id)

        return cluster_name

    finally:
        os.unlink(temp_yaml_path)


@flow(
    name='sky_parallel_workflow',
    description='Run multiple SkyPilot tasks in parallel',
)
def sky_parallel_workflow(
    tasks: list,
    api_server_endpoint: Optional[str] = None,
):
    """Run multiple SkyPilot tasks in parallel.

    This flow demonstrates how to leverage Prefect's parallel execution
    capabilities to run multiple SkyPilot tasks concurrently on different
    clusters.

    Args:
        tasks: List of task configurations. Each item should be a dict with:
            - base_path: Path to task YAMLs
            - yaml_path: YAML file name
            - envs_override: Optional environment overrides
            - cluster_prefix: Optional cluster name prefix
        api_server_endpoint: Optional SkyPilot API server endpoint.

    Returns:
        List of cluster names that were created.

    Example:
        tasks = [
            {'base_path': '/path/to/tasks', 'yaml_path': 'task1.yaml'},
            {'base_path': '/path/to/tasks', 'yaml_path': 'task2.yaml'},
        ]
        sky_parallel_workflow(tasks)
    """
    logger = get_run_logger()
    logger.info(f'Starting parallel workflow with {len(tasks)} tasks')

    # Submit all tasks concurrently
    futures = []
    for task_config in tasks:
        future = run_sky_task.submit(
            base_path=task_config['base_path'],
            yaml_path=task_config['yaml_path'],
            envs_override=task_config.get('envs_override'),
            git_branch=task_config.get('git_branch'),
            cluster_prefix=task_config.get('cluster_prefix'),
            api_server_endpoint=api_server_endpoint,
        )
        futures.append(future)

    # Wait for all tasks to complete and collect results
    results = [future.result() for future in futures]

    logger.info(f'All {len(results)} tasks completed successfully')
    return results


if __name__ == '__main__':
    # Example: Run the training workflow
    # You can customize these parameters or use environment variables

    import os

    # Get API server endpoint from environment or use default
    api_endpoint = os.environ.get('SKYPILOT_API_SERVER_ENDPOINT')

    # Run the workflow
    result = sky_train_workflow(
        base_path='https://github.com/skypilot-org/mock-train-workflow.git',
        data_bucket_store_type='s3',
        api_server_endpoint=api_endpoint,
    )
    print(f'Workflow completed. Bucket: {result}')
