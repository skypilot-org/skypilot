"""Clouds in Sky."""
from typing import List

from sky.clouds.cloud import Cloud
from sky.clouds.cloud import Region
from sky.clouds.cloud import Zone
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP

__all__ = [
    'AWS',
    'Azure',
    'Cloud',
    'GCP',
    'Region',
    'Zone',
    'from_str',
    'cloud_in_list',
]

__CLOUD_DICT__ = {
    'AWS': AWS,
    'Azure': Azure,
    'GCP': GCP,
}


def from_str(name: str) -> 'Cloud':
    return __CLOUD_DICT__[name]


def cloud_in_list(cloud: 'Cloud', clouds: List['Cloud']) -> bool:
    return any(cloud.is_same_cloud(c) for c in clouds)
