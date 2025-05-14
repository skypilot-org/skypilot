import os
from typing import Dict, List, Optional, Tuple, Union

from sky import clouds
from sky import resources
from sky.clouds import service_catalog
from sky.utils import registry
from sky.utils.resources_utils import DiskTier


@registry.CLOUD_REGISTRY.register(aliases=['Hyperbolic'])
class Hyperbolic(clouds.Cloud):
    """Hyperbolic Cloud Provider."""

    _REPR = 'Hyperbolic'
    name = 'hyperbolic'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on Hyperbolic.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported yet on Hyperbolic.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on Hyperbolic. To read data '
             'from object stores on Hyperbolic, use `mode: COPY` to copy the data '
             'to local disk.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Hyperbolic.'),
    }
    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    def __init__(self):
        super().__init__()

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(
        cls, instance_type: str, accelerators: Optional[Dict[str, int]],
        use_spot: bool, region: Optional[str], zone: Optional[str]
    ) -> List[clouds.Region]:
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'hyperbolic')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls, instance_type: str
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='hyperbolic')

    def instance_type_to_hourly_cost(
        self, instance_type: str, use_spot: bool,
        region: Optional[str] = None, zone: Optional[str] = None
    ) -> float:
        return service_catalog.get_hourly_cost(
            instance_type, use_spot=use_spot, region=region, zone=zone, clouds='hyperbolic')

    @classmethod
    def get_default_instance_type(
        cls, cpus: Optional[str] = None, memory: Optional[str] = None,
        disk_tier: Optional[DiskTier] = None
    ) -> Optional[str]:
        return service_catalog.get_default_instance_type(
            cpus=cpus, memory=memory, disk_tier=disk_tier, clouds='hyperbolic')

    @classmethod
    def get_accelerators_from_instance_type(
        cls, instance_type: str
    ) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='hyperbolic')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        api_key_path = os.path.expanduser('~/.hyperbolic/api_key')
        if os.path.exists(api_key_path):
            return True, None
        return False, f'API key not found at {api_key_path}'

    @classmethod
    def _check_compute_credentials(cls) -> tuple[bool, str | None]:
        return cls._check_credentials()

    def __repr__(self):
        return self._REPR

    # Add more methods as needed for your cloud's features.
