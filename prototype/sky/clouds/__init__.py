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

__CLOUD_DICT__ = {
    'AWS': AWS,
    'Azure': Azure,
    'GCP': GCP,
}

ALL_CLOUDS = [c() for c in __CLOUD_DICT__.values()]


def from_str(name: str) -> 'Cloud':
    return __CLOUD_DICT__[name]
