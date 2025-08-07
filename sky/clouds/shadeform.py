""" Shadeform Cloud. """

import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import common as adaptors_common
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib
else:
    requests = adaptors_common.LazyImport('requests')

# Minimum set of files under ~/.shadeform that grant Shadeform access.
_CREDENTIAL_FILES = [
    'api_key',
]

# Default cloud to use for Shadeform API
_DEFAULT_CLOUD = 'datacrunch'

# Supported regions for datacrunch
_DATACRUNCH_REGIONS = [
    'helsinki-finland-1',
    'helsinki-finland-2',
    'helsinki-finland-5',
    'reykjanesbaer-iceland-1',
]


@registry.CLOUD_REGISTRY.register
class Shadeform(clouds.Cloud):
    """Shadeform GPU Cloud

    Shadeform is a unified API for deploying and managing cloud GPUs across
    multiple cloud providers. This implementation defaults to using datacrunch
    as the underlying cloud provider.
    """

    _REPR = 'Shadeform'

    # Shadeform doesn't have explicit cluster name limits, but let's be conservative
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    # Features not currently supported by Shadeform
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping instances is not supported on Shadeform yet.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node clusters are not supported yet on Shadeform, as the '
             'interconnection among nodes requires special networking setup.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Custom disk tiers are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER: 'Custom network tiers are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Object storage mounting is not supported on Shadeform. '
             'Use `mode: COPY` to copy data to local disk instead.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS: 'Host controllers are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS: 'High availability controllers are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER: 'Disk cloning is not supported on Shadeform.',
        clouds.CloudImplementationFeatures.IMAGE_ID: 'Custom image IDs are not supported on Shadeform.',
        clouds.CloudImplementationFeatures.DOCKER_IMAGE: 'Docker images are not supported on Shadeform yet.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK: 'Custom multiple network interfaces are not supported on Shadeform.',
    }

    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided."""
        del resources  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        """Get regions that offer the requested instance type."""
        del accelerators  # unused
        if use_spot:
            return []  # No spot support

        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'shadeform')

        if region is not None:
            regions = [r for r in regions if r.name == region]

        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[None]:
        """Iterate over zones for provisioning."""
        del num_nodes  # unused
        if use_spot:
            return

        regions = cls.regions_with_offering(instance_type, accelerators,
                                            use_spot, region, None)
        for r in regions:
            assert r.zones is not None, r
            for zone in r.zones:
                if zone.name == region:
                    yield None

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Get vCPUs and memory from instance type."""
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='shadeform')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        """Get accelerator information from instance type."""
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='shadeform')

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> Optional[str]:
        """Get default instance type."""
        del disk_tier  # Not supported
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=None,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='shadeform')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        """Return shell command to get the zone of the instance."""
        # Since we're using datacrunch, the zone is the same as region
        return None

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Get user identities for Shadeform."""
        # No user identity support needed
        return None

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        """Get hourly cost for instance type."""
        if use_spot:
            raise ValueError("Spot instances are not supported on Shadeform")
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='shadeform')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Get hourly cost for accelerators."""
        if use_spot:
            raise ValueError("Spot instances are not supported on Shadeform")

        # Find instance type that matches the accelerator requirements
        assert len(accelerators) == 1, accelerators
        acc_name, acc_count = list(accelerators.items())[0]

        instance_types, _ = catalog.get_instance_type_for_accelerator(
            acc_name,
            acc_count,
            use_spot=use_spot,
            region=region,
            zone=zone,
            clouds='shadeform')

        if not instance_types:
            raise ValueError(f"No instance type found for {accelerators}")

        # Return cost of the cheapest instance type
        costs = []
        for instance_type in instance_types:
            try:
                cost = catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='shadeform')
                costs.append(cost)
            except ValueError:
                continue

        if not costs:
            raise ValueError(f"No pricing found for {accelerators}")

        return min(costs)

    def get_egress_cost(self, num_gigabytes: float) -> float:
        """Get egress cost."""
        # No explicit egress pricing from Shadeform API
        return 0.0

    def __repr__(self):
        return 'Shadeform'

    @classmethod
    def get_current_user_identity(cls) -> Optional[str]:
        """Get current user identity."""
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        """Make variables for deployment template."""
        del zones, num_nodes, dryrun, volume_mounts  # unused for Shadeform

        # Get instance type
        r = resources.copy(accelerators=None)
        feasible_resources = self.get_feasible_launchable_resources(r,
                                                                    num_nodes=1)
        instance_type = feasible_resources.resources_list[0].instance_type

        return {
            'instance_type': instance_type,
            'region': region.name,
            'cloud': _DEFAULT_CLOUD,  # Always use datacrunch for now
        }

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Get credential files that need to be mounted."""
        return {
            f'~/.shadeform/{f}': f'~/.shadeform/{f}' for f in _CREDENTIAL_FILES
        }

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        """Get current user identity string."""
        return None

    @classmethod
    def check_credentials(
        cls, cloud_capability: clouds.CloudCapability
    ) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Check if Shadeform credentials are properly configured."""
        del cloud_capability  # unused for Shadeform
        try:
            import os
            api_key_path = os.path.expanduser('~/.shadeform/api_key')
            if not os.path.exists(api_key_path):
                return False, (
                    f'Shadeform API key not found. Please save your API key to {api_key_path}'
                )

            # Try to read the API key
            with open(api_key_path, 'r') as f:
                api_key = f.read().strip()

            if not api_key:
                return False, f'Shadeform API key is empty in {api_key_path}'

            return True, None

        except Exception as e:
            return False, f'Error checking Shadeform credentials: {str(e)}'

    def get_feasible_launchable_resources(
            self,
            resources: 'resources_lib.Resources',
            num_nodes: int = 1) -> 'resources_utils.FeasibleResources':
        """Get feasible launchable resources."""
        del num_nodes  # unused for now
        if resources.use_spot:
            return resources_utils.FeasibleResources(
                [], [], 'Spot instances are not supported on Shadeform.')

        # For simplicity, return the resources as-is if valid
        # In a full implementation, this would validate against available instance types
        fuzzy_candidate_list: List[str] = []
        if resources.instance_type is not None:
            fuzzy_candidate_list = [resources.instance_type]

        return resources_utils.FeasibleResources([resources],
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Check compute credentials."""
        success, msg = cls.check_credentials(clouds.CloudCapability.COMPUTE)
        # Convert return type to match expected signature
        if isinstance(msg, dict):
            msg = str(msg)
        return success, msg

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        """Query cluster status."""
        # This will be implemented in the provisioner
        raise NotImplementedError(
            "Status querying will be handled by the provisioner")
