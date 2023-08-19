"""Clouds in Sky."""
from sky.clouds.cloud import Cloud
from sky.clouds.cloud import CloudImplementationFeatures
from sky.clouds.cloud import Region
from sky.clouds.cloud import Zone
from sky.clouds.cloud_registry import CLOUD_REGISTRY

# NOTE: import the above first to avoid circular imports.
# isort: split
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP
from sky.clouds.ibm import IBM
from sky.clouds.kubernetes import Kubernetes
from sky.clouds.lambda_cloud import Lambda
from sky.clouds.local import Local
from sky.clouds.oci import OCI
from sky.clouds.scp import SCP

__all__ = [
    'IBM',
    'AWS',
    'Azure',
    'Cloud',
    'GCP',
    'Lambda',
    'Local',
    'SCP',
    'OCI',
    'Kubernetes',
    'CloudImplementationFeatures',
    'Region',
    'Zone',
    'CLOUD_REGISTRY',
]
