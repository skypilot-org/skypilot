"""SkyPilot + Prefect integration example for Kubernetes.

This example demonstrates a multi-stage ML pipeline on Kubernetes
using SkyPilot for infrastructure and Prefect for orchestration.

Usage:
    python sky_k8s_example.py
"""
import textwrap
from typing import Any, Dict
import uuid

from prefect import flow
from prefect import get_run_logger
from prefect import task


@task(name='run_sky_task', retries=2, retry_delay_seconds=30)
def run_sky_task(
    task_config: Dict[str, Any],
    cluster_prefix: str = 'prefect',
) -> str:
    """Run a SkyPilot task on Kubernetes.

    Args:
        task_config: Task configuration dictionary.
        cluster_prefix: Prefix for the cluster name.

    Returns:
        The name of the cluster used.
    """
    # pylint: disable=import-outside-toplevel
    import copy

    import sky

    logger = get_run_logger()

    # Deep copy to avoid modifying the original config
    task_config = copy.deepcopy(task_config)

    # Ensure the task targets Kubernetes
    if 'resources' not in task_config:
        task_config['resources'] = {}
    task_config['resources']['infra'] = 'kubernetes'

    # Create task and generate cluster name
    sky_task = sky.Task.from_yaml_config(task_config)
    cluster_uuid = str(uuid.uuid4())[:4]
    task_name = task_config.get('name', 'task')
    cluster_name = f'{cluster_prefix}-{task_name}-{cluster_uuid}'

    logger.info(f'Launching cluster: {cluster_name}')

    # Launch, stream logs, and tear down
    request_id = sky.launch(sky_task, cluster_name=cluster_name, down=True)
    job_id, _ = sky.stream_and_get(request_id)
    sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

    down_id = sky.down(cluster_name)
    sky.stream_and_get(down_id)

    return cluster_name


@flow(name='k8s_ml_pipeline')
def k8s_multi_stage_pipeline():
    """Multi-stage ML pipeline on Kubernetes.

    Stages:
    1. Data preprocessing (CPU)
    2. Model training (GPU)
    3. Model evaluation (GPU)
    """
    logger = get_run_logger()

    # Stage 1: Data Preprocessing (CPU)
    logger.info('Stage 1: Data Preprocessing')
    run_sky_task(
        task_config={
            'name': 'preprocess',
            'resources': {
                'cpus': '2+'
            },
            'setup': 'pip install pandas numpy',
            'run': textwrap.dedent("""\
                echo "Preprocessing data..."
                python -c "
                import numpy as np
                X = np.random.randn(1000, 10)
                print(f'Generated dataset: {X.shape}')
                "
                """),
        })

    # Stage 2: Training (GPU)
    logger.info('Stage 2: Model Training')
    run_sky_task(
        task_config={
            'name': 'train',
            'resources': {
                'accelerators': 'H100:1'
            },
            'setup': 'pip install torch',
            'run': textwrap.dedent("""\
                echo "Training model..."
                python -c "
                import torch
                print(f'CUDA available: {torch.cuda.is_available()}')
                for epoch in range(3):
                    print(f'Epoch {epoch+1}/3 - Loss: {0.5 - 0.1*epoch:.4f}')
                "
                """),
        })

    # Stage 3: Evaluation (GPU)
    logger.info('Stage 3: Model Evaluation')
    run_sky_task(
        task_config={
            'name': 'eval',
            'resources': {
                'accelerators': 'H100:1'
            },
            'setup': 'pip install torch',
            'run': textwrap.dedent("""\
                echo "Evaluating model..."
                python -c "
                import random
                accuracy = random.uniform(0.85, 0.95)
                print(f'Accuracy: {accuracy:.2%}')
                "
                """),
        })

    logger.info('Pipeline complete!')


if __name__ == '__main__':
    k8s_multi_stage_pipeline()
