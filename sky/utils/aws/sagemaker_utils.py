"""Utility functions for AWS SageMaker jobs."""
from typing import Dict, Optional

from sky.adaptors import sagemaker as sagemaker_adaptor


def launch_training_job(job_name: str,
                        image_uri: str,
                        role: str,
                        *,
                        instance_type: str = 'ml.m5.large',
                        instance_count: int = 1,
                        volume_size_gb: int = 50,
                        max_runtime: int = 3600,
                        hyperparameters: Optional[Dict[str, str]] = None,
                        output_path: Optional[str] = None) -> None:
    """Launch a SageMaker training job.

    This is a thin wrapper around ``boto3.client('sagemaker').create_training_job``.
    """
    sm_client = sagemaker_adaptor.boto3.client('sagemaker')

    config = {
        'TrainingJobName': job_name,
        'AlgorithmSpecification': {
            'TrainingImage': image_uri,
            'TrainingInputMode': 'File',
        },
        'RoleArn': role,
        'ResourceConfig': {
            'InstanceType': instance_type,
            'InstanceCount': instance_count,
            'VolumeSizeInGB': volume_size_gb,
        },
        'StoppingCondition': {'MaxRuntimeInSeconds': max_runtime},
    }
    if output_path is not None:
        config['OutputDataConfig'] = {'S3OutputPath': output_path}
    if hyperparameters:
        # Convert hyperparameter values to strings as required by SageMaker
        config['HyperParameters'] = {
            key: str(value) for key, value in hyperparameters.items()
        }

    sm_client.create_training_job(**config)
