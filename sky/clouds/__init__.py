"""Clouds in Sky."""
from sky.clouds.cloud import Cloud
from sky.clouds.cloud import CLOUD_REGISTRY
from sky.clouds.cloud import Region
from sky.clouds.cloud import Zone
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP
from sky.clouds.local import Local

__all__ = [
    'AWS',
    'Azure',
    'Cloud',
    'GCP',
    'Local',
    'Region',
    'Zone',
    'CLOUD_REGISTRY',
]
