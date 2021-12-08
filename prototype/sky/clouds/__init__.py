"""Clouds in Sky."""
import importlib
import os

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
]

__CLOUD_DICT__ = {
    'aws': AWS,
    'azure': Azure,
    'gcp': GCP,
}


def cloud_factory(name):
    return __CLOUD_DICT__[name]
