"""SkyPilot + Prefect integration example for Kubernetes.

This example demonstrates how to run SkyPilot tasks on a Kubernetes cluster
using Prefect for workflow orchestration.

Prerequisites:
    1. kubectl configured with access to your Kubernetes cluster
    2. SkyPilot configured for Kubernetes: `sky check kubernetes`
    3. Prefect installed: `pip install prefect>=2.0`
    4. SkyPilot installed: `pip install "skypilot[kubernetes]"`

Usage:
    # Run directly
    python sky_k8s_example.py

    # Or with custom configuration
    python sky_k8s_example.py --context my-k8s-context --gpu-type A100
"""
import argparse
import os
from typing import Any, Dict, Optional
import uuid

from prefect import flow
from prefect import get_run_logger
from prefect import task


@task(
    name='run_k8s_sky_task',
    description='Run a SkyPilot task on Kubernetes',
    retries=2,
    retry_delay_seconds=30,
)
def run_k8s_sky_task(
    task_config: Dict[str, Any],
    cluster_prefix: str = 'k8s',
    kubernetes_context: Optional[str] = None,
    api_server_endpoint: Optional[str] = None,
) -> str:
    """Run a SkyPilot task on Kubernetes.

    Args:
        task_config: Task configuration dictionary (will be converted to YAML).
        cluster_prefix: Prefix for the cluster name.
        kubernetes_context: Optional Kubernetes context to use.
        api_server_endpoint: Optional SkyPilot API server endpoint.

    Returns:
        The name of the cluster used.
    """
    # pylint: disable=import-outside-toplevel
    import tempfile

    import yaml

    logger = get_run_logger()

    # Set environment variables
    if api_server_endpoint:
        os.environ['SKYPILOT_API_SERVER_ENDPOINT'] = api_server_endpoint

    import sky

    # Ensure the task targets Kubernetes
    if 'resources' not in task_config:
        task_config['resources'] = {}
    task_config['resources']['cloud'] = 'kubernetes'

    # Set Kubernetes context if provided
    if kubernetes_context:
        # SkyPilot will use this context
        os.environ['KUBECONTEXT'] = kubernetes_context
        logger.info(f'Using Kubernetes context: {kubernetes_context}')

    # Create a temporary YAML file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(task_config, f)
        temp_yaml_path = f.name

    try:
        sky_task = sky.Task.from_yaml_config(task_config)

        cluster_uuid = str(uuid.uuid4())[:4]
        task_name = task_config.get('name', 'task')
        cluster_name = f'{cluster_prefix}-{task_name}-{cluster_uuid}'

        logger.info(f'Starting Kubernetes task on cluster: {cluster_name}')

        # Launch the task
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
    name='k8s_training_workflow',
    description='ML training workflow on Kubernetes using SkyPilot',
)
def k8s_training_workflow(
    model_name: str = 'bert-base-uncased',
    num_epochs: int = 3,
    batch_size: int = 32,
    num_gpus: int = 1,
    gpu_type: str = 'A10G',
    kubernetes_context: Optional[str] = None,
    api_server_endpoint: Optional[str] = None,
):
    """Run ML training on Kubernetes using SkyPilot.

    This workflow demonstrates running a GPU training job on Kubernetes
    with configurable hyperparameters.

    Args:
        model_name: Name of the model to train.
        num_epochs: Number of training epochs.
        batch_size: Training batch size.
        num_gpus: Number of GPUs to use.
        gpu_type: Type of GPU (e.g., 'A10G', 'A100', 'V100').
        kubernetes_context: Optional Kubernetes context.
        api_server_endpoint: Optional SkyPilot API server endpoint.

    Returns:
        The cluster name used for training.
    """
    logger = get_run_logger()
    logger.info('Starting Kubernetes training workflow')
    logger.info(
        f'Model: {model_name}, Epochs: {num_epochs}, Batch: {batch_size}')
    logger.info(f'GPUs: {num_gpus}x {gpu_type}')

    # Define the training task
    training_task = {
        'name': 'k8s-training',
        'resources': {
            'cloud': 'kubernetes',
            'accelerators': f'{gpu_type}:{num_gpus}',
            'cpus': '4+',
            'memory': '16+',
        },
        'envs': {
            'MODEL_NAME': model_name,
            'NUM_EPOCHS': str(num_epochs),
            'BATCH_SIZE': str(batch_size),
        },
        'setup': """
            pip install torch transformers datasets accelerate
            """,
        'run': """
            echo "Starting training on Kubernetes"
            echo "Model: $MODEL_NAME"
            echo "Epochs: $NUM_EPOCHS"
            echo "Batch size: $BATCH_SIZE"
            
            # GPU info
            nvidia-smi || echo "No NVIDIA GPU available"
            
            # Mock training - replace with actual training script
            python -c "
import torch
print(f'PyTorch version: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'GPU memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')

# Simulate training
import time
for epoch in range($NUM_EPOCHS):
    print(f'Epoch {epoch+1}/$NUM_EPOCHS')
    time.sleep(2)  # Simulate training time
    print(f'  Loss: {0.5 - 0.1*epoch:.4f}')
print('Training complete!')
"
            echo "Training finished successfully"
            """,
    }

    cluster = run_k8s_sky_task(
        task_config=training_task,
        cluster_prefix='prefect-k8s',
        kubernetes_context=kubernetes_context,
        api_server_endpoint=api_server_endpoint,
    )

    logger.info(f'Training completed on cluster: {cluster}')
    return cluster


@flow(
    name='k8s_batch_inference',
    description='Batch inference workflow on Kubernetes',
)
def k8s_batch_inference_workflow(
    model_path: str,
    data_path: str,
    output_path: str,
    num_gpus: int = 1,
    kubernetes_context: Optional[str] = None,
    api_server_endpoint: Optional[str] = None,
):
    """Run batch inference on Kubernetes.

    Args:
        model_path: Path to the model (S3/GCS/local).
        data_path: Path to input data.
        output_path: Path for inference outputs.
        num_gpus: Number of GPUs to use.
        kubernetes_context: Optional Kubernetes context.
        api_server_endpoint: Optional SkyPilot API server endpoint.

    Returns:
        The cluster name used for inference.
    """
    logger = get_run_logger()
    logger.info('Starting batch inference workflow')

    inference_task = {
        'name': 'k8s-inference',
        'resources': {
            'cloud': 'kubernetes',
            'accelerators': f'A10G:{num_gpus}',
        },
        'envs': {
            'MODEL_PATH': model_path,
            'DATA_PATH': data_path,
            'OUTPUT_PATH': output_path,
        },
        'setup': """
            pip install torch transformers
            """,
        'run': """
            echo "Running batch inference"
            echo "Model: $MODEL_PATH"
            echo "Data: $DATA_PATH"
            echo "Output: $OUTPUT_PATH"
            
            # Mock inference - replace with actual inference code
            python -c "
import torch
print('Running inference...')
# Simulate inference
import time
for i in range(10):
    print(f'Processing batch {i+1}/10')
    time.sleep(1)
print('Inference complete!')
"
            """,
    }

    cluster = run_k8s_sky_task(
        task_config=inference_task,
        cluster_prefix='prefect-inference',
        kubernetes_context=kubernetes_context,
        api_server_endpoint=api_server_endpoint,
    )

    logger.info(f'Inference completed on cluster: {cluster}')
    return cluster


@flow(
    name='k8s_multi_stage_pipeline',
    description='Multi-stage ML pipeline on Kubernetes',
)
def k8s_multi_stage_pipeline(
    kubernetes_context: Optional[str] = None,
    api_server_endpoint: Optional[str] = None,
):
    """Run a multi-stage ML pipeline on Kubernetes.

    Stages:
    1. Data preprocessing (CPU)
    2. Model training (GPU)
    3. Model evaluation (GPU)

    All stages run on Kubernetes pods managed by SkyPilot.
    """
    logger = get_run_logger()
    logger.info('Starting multi-stage Kubernetes pipeline')

    # Stage 1: Data Preprocessing (CPU only)
    preprocess_task = {
        'name': 'data-preprocess',
        'resources': {
            'cloud': 'kubernetes',
            'cpus': '2+',
            'memory': '4+',
        },
        'setup': """
            pip install pandas numpy scikit-learn
            """,
        'run': """
            echo "Running data preprocessing..."
            python -c "
import numpy as np
print('Generating synthetic dataset...')
X = np.random.randn(1000, 10)
y = np.random.randint(0, 2, 1000)
print(f'Dataset shape: {X.shape}')
print('Preprocessing complete!')
"
            """,
    }

    logger.info('Stage 1: Data Preprocessing')
    run_k8s_sky_task(
        task_config=preprocess_task,
        cluster_prefix='stage1',
        kubernetes_context=kubernetes_context,
        api_server_endpoint=api_server_endpoint,
    )

    # Stage 2: Training (GPU)
    train_task = {
        'name': 'model-train',
        'resources': {
            'cloud': 'kubernetes',
            'accelerators': 'A10G:1',
        },
        'setup': """
            pip install torch
            """,
        'run': """
            echo "Running model training..."
            python -c "
import torch
print(f'CUDA available: {torch.cuda.is_available()}')
# Mock training
print('Training model...')
for epoch in range(3):
    print(f'Epoch {epoch+1}/3 - Loss: {0.5 - 0.1*epoch:.4f}')
print('Training complete!')
"
            """,
    }

    logger.info('Stage 2: Model Training')
    run_k8s_sky_task(
        task_config=train_task,
        cluster_prefix='stage2',
        kubernetes_context=kubernetes_context,
        api_server_endpoint=api_server_endpoint,
    )

    # Stage 3: Evaluation (GPU)
    eval_task = {
        'name': 'model-eval',
        'resources': {
            'cloud': 'kubernetes',
            'accelerators': 'A10G:1',
        },
        'setup': """
            pip install torch
            """,
        'run': """
            echo "Running model evaluation..."
            python -c "
import torch
import random
print(f'CUDA available: {torch.cuda.is_available()}')
# Mock evaluation
accuracy = random.uniform(0.85, 0.95)
print(f'Model accuracy: {accuracy:.2%}')
print('Evaluation complete!')
"
            """,
    }

    logger.info('Stage 3: Model Evaluation')
    run_k8s_sky_task(
        task_config=eval_task,
        cluster_prefix='stage3',
        kubernetes_context=kubernetes_context,
        api_server_endpoint=api_server_endpoint,
    )

    logger.info('Pipeline completed successfully!')


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Run SkyPilot tasks on Kubernetes with Prefect')
    parser.add_argument(
        '--workflow',
        type=str,
        default='training',
        choices=['training', 'inference', 'pipeline'],
        help='Workflow to run',
    )
    parser.add_argument(
        '--context',
        type=str,
        default=None,
        help='Kubernetes context to use',
    )
    parser.add_argument(
        '--gpu-type',
        type=str,
        default='A10G',
        help='GPU type (e.g., A10G, A100, V100)',
    )
    parser.add_argument(
        '--num-gpus',
        type=int,
        default=1,
        help='Number of GPUs',
    )
    parser.add_argument(
        '--api-server',
        type=str,
        default=None,
        help='SkyPilot API server endpoint',
    )

    args = parser.parse_args()

    # Get API server from env if not provided
    api_endpoint = args.api_server or os.environ.get(
        'SKYPILOT_API_SERVER_ENDPOINT')

    if args.workflow == 'training':
        k8s_training_workflow(
            gpu_type=args.gpu_type,
            num_gpus=args.num_gpus,
            kubernetes_context=args.context,
            api_server_endpoint=api_endpoint,
        )
    elif args.workflow == 'inference':
        k8s_batch_inference_workflow(
            model_path='s3://my-bucket/model.pt',
            data_path='s3://my-bucket/data/',
            output_path='s3://my-bucket/outputs/',
            num_gpus=args.num_gpus,
            kubernetes_context=args.context,
            api_server_endpoint=api_endpoint,
        )
    elif args.workflow == 'pipeline':
        k8s_multi_stage_pipeline(
            kubernetes_context=args.context,
            api_server_endpoint=api_endpoint,
        )


if __name__ == '__main__':
    main()
