"""Scaleway
"""
import typing
from typing import List, Dict, Optional

from sky import clouds

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    pass


@clouds.CLOUD_REGISTRY.register
class Scaleway(clouds.Cloud):
    """Scaleway GPU instances"""

    _REPR = 'Scaleway'
    # pylint: disable=line-too-long
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Scaleway does not support spot VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Scaleway does not support multi nodes.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Scaleway does not support custom disk tiers',
    }

    _MAX_CLUSTER_NAME_LEN_LIMIT = 50

    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            cls._regions = [
                clouds.Region(...),
            ]
        return cls._regions

    def __repr__(self):
        return 'Scaleway'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Scaleway)
