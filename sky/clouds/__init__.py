"""Clouds in Sky."""

from sky.clouds.cloud import Cloud
from sky.clouds.cloud import cloud_in_iterable
from sky.clouds.cloud import CloudImplementationFeatures
from sky.clouds.cloud import ProvisionerVersion
from sky.clouds.cloud import Region
from sky.clouds.cloud import StatusVersion
from sky.clouds.cloud import Zone
from sky.clouds.cloud_registry import CLOUD_REGISTRY

# NOTE: import the above first to avoid circular imports.
# isort: split
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.cudo import Cudo
from sky.clouds.fluidstack import Fluidstack
from sky.clouds.gcp import GCP
from sky.clouds.ibm import IBM
from sky.clouds.kubernetes import Kubernetes
from sky.clouds.lambda_cloud import Lambda
from sky.clouds.oci import OCI
from sky.clouds.paperspace import Paperspace
from sky.clouds.runpod import RunPod
from sky.clouds.scp import SCP
from sky.clouds.vsphere import Vsphere

__all__ = [
    'IBM',
    'AWS',
    'Azure',
    'Cloud',
    'Cudo',
    'GCP',
    'Lambda',
    'Paperspace',
    'SCP',
    'RunPod',
    'OCI',
    'Vsphere',
    'Kubernetes',
    'CloudImplementationFeatures',
    'Region',
    'Zone',
    'CLOUD_REGISTRY',
    'ProvisionerVersion',
    'StatusVersion',
    'Fluidstack',
    # Utility functions
    'cloud_in_iterable',
]
