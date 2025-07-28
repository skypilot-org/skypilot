"""Lustre provisioning. (TODO: Add Persistent Disks and Local SSDs)"""
from typing import List, Tuple

from sky import models
from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def apply_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Creates or registers a Lustre volume."""
    return config


def delete_volume(config: models.VolumeConfig) -> models.VolumeConfig:
    """Deletes a Lustre volume."""
    return config


def get_volume_usedby(_: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Gets the usedby resources of a volume."""
    return [], []
