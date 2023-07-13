"""Clouds in Sky."""
from sky.clouds.cloud import Cloud
from sky.clouds.cloud import CLOUD_REGISTRY
from sky.clouds.cloud import CloudImplementationFeatures
from sky.clouds.cloud import Region
from sky.clouds.cloud import Zone
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP
from sky.clouds.lambda_cloud import Lambda
from sky.clouds.local import Local
from sky.clouds.ibm import IBM
from sky.clouds.scp import SCP
from sky.clouds.oci import OCI

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
    'CloudImplementationFeatures',
    'Region',
    'Zone',
    'CLOUD_REGISTRY',
]
