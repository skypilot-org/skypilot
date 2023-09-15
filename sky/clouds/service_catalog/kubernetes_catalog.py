"""Kubernetes Catalog.

Kubernetes does not require a catalog of instances, but we need an image catalog
mapping SkyPilot image tags to corresponding container image tags.
"""

from typing import Optional

from sky.clouds.service_catalog import common

_PULL_FREQUENCY_HOURS = 7

# We keep pull_frequency_hours so we can remotely update the default image paths
_image_df = common.read_catalog('kubernetes/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# TODO(romilb): Refactor implementation of common service catalog functions from
#   clouds/kubernetes.py to kubernetes_catalog.py


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    return common.get_image_id_from_tag_impl(_image_df, tag, region)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    return common.is_image_tag_valid_impl(_image_df, tag, region)
