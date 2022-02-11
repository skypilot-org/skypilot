"""Clouds in Sky."""
from sky.clouds.cloud import Cloud
from sky.clouds.cloud import Region
from sky.clouds.cloud import Zone
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP

__all__ = [
    'ALL_CLOUDS',
    'AWS',
    'Azure',
    'Cloud',
    'GCP',
    'Region',
    'Zone',
    'from_str',
]

CLOUD_REGISTRY = {
    'aws': AWS(),
    'gcp': GCP(),
    'azure': Azure(),
}

ALL_CLOUDS = list(CLOUD_REGISTRY.values())


def from_str(name: str) -> 'Cloud':
    return CLOUD_REGISTRY[name]
