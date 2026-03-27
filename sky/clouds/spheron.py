"""Spheron Cloud."""

import json
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.catalog import spheron_catalog
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

# Minimum set of files under ~/.spheron that grant Spheron access.
_CREDENTIAL_FILES = [
    'api_key',
]


@registry.CLOUD_REGISTRY.register
class Spheron(clouds.Cloud):
    """Spheron GPU Cloud

    Spheron is a GPU marketplace providing access to GPU instances across
    multiple cloud providers through a unified API.
    """

    _REPR = 'Spheron'
    name = 'spheron'

    # Spheron doesn't have explicit cluster name limits, but conservative
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    # Features not currently supported by Spheron
    # yapf: disable
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping instances not supported on Spheron.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            'Multi-node clusters not supported on Spheron.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers not supported on Spheron.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            'Custom network tiers not supported on Spheron.',
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            'Object storage mounting not supported on Spheron.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers not supported on Spheron.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            'High availability controllers not supported on Spheron.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Disk cloning not supported on Spheron.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Custom image IDs not supported on Spheron.',
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            'Docker images not supported on Spheron yet.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Custom multiple network interfaces not supported on Spheron.',
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            'Local disk is not supported on Spheron.',
    }
    # yapf: enable

    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided."""
        del resources  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        """Get regions that offer the requested instance type."""
        assert zone is None, 'Spheron does not support zones.'
        del zone  # unused

        regions = spheron_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot)

        if region is not None:
            regions = [r for r in regions if r.name == region]
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

        regions = cls.regions_with_offering(instance_type, accelerators,
                                            use_spot, region, None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Get vCPUs and memory from instance type."""
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='spheron')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        """Get accelerator information from instance type."""
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='spheron')

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None,
    ) -> Optional[str]:
        """Get default instance type."""
        del disk_tier, local_disk  # Not supported
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=None,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='spheron')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        """Return shell command to get the zone of the instance."""
        return None

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Get user identities for Spheron."""
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'spheron')

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        """Get hourly cost for instance type."""
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='spheron')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Get hourly cost for accelerators."""
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        """Get egress cost."""
        return 0.0

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
        del cluster_name, zones, num_nodes, dryrun, volume_mounts  # unused

        resources.assert_launchable()
        instance_type = resources.instance_type
        assert instance_type is not None, resources

        # For Spheron, instance_type IS the offerId.
        # Look up provider-specific metadata from the catalog.
        offer_info = spheron_catalog.get_instance_info(instance_type,
                                                       region.name,
                                                       resources.use_spot)
        resources_vars: Dict[str, Any] = {
            'instance_type': instance_type,
            'region': region.name,
            'offer_id': instance_type,
            'spheron_provider': offer_info.get('Provider', ''),
            'gpu_type': offer_info.get('AcceleratorName', ''),
            'gpu_count': offer_info.get('AcceleratorCount', 1),
            'operating_system': offer_info.get('OperatingSystem',
                                               'ubuntu-22.04'),
            'spheron_instance_type': offer_info.get('SpheronInstanceType',
                                                    'DEDICATED'),
        }

        # Add accelerator resources for Ray
        accelerators = resources.accelerators
        if accelerators is not None:
            resources_vars['custom_resources'] = json.dumps(accelerators,
                                                            separators=(',',
                                                                        ':'))

        return resources_vars

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Get credential files that need to be mounted."""
        return {f'~/.spheron/{f}': f'~/.spheron/{f}' for f in _CREDENTIAL_FILES}

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        """Get current user identity string."""
        return None

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Check if Spheron credentials are properly configured."""
        try:
            api_key_path = os.path.expanduser('~/.spheron/api_key')
            if not os.path.exists(api_key_path):
                return False, (f'Spheron API key not found. '
                               f'Please save your API key to {api_key_path}')

            with open(api_key_path, 'r', encoding='utf-8') as f:
                api_key = f.read().strip()

            if not api_key:
                return False, f'Spheron API key is empty in {api_key_path}'

            return True, None

        except (OSError, IOError) as e:
            return False, f'Error checking Spheron credentials: {str(e)}'

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Get feasible launchable resources."""
        if resources.instance_type is not None:
            # Instance type is already specified, validate it
            assert resources.is_launchable(), resources
            fuzzy_candidate_list = [resources.instance_type]
            return resources_utils.FeasibleResources([resources],
                                                     fuzzy_candidate_list, None)

        def _make_resources(instance_type_list):
            resource_list = []
            for instance_type in instance_type_list:
                r = resources.copy(
                    cloud=Spheron(),
                    instance_type=instance_type,
                    accelerators=resources.accelerators,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Handle accelerator requests
        accelerators = resources.accelerators
        if accelerators is not None:
            for accelerator_name, accelerator_count in accelerators.items():
                func = spheron_catalog.get_instance_type_for_accelerator
                instance_types, errors = func(
                    accelerator_name,
                    accelerator_count,
                    use_spot=resources.use_spot,
                    max_hourly_cost=resources.max_hourly_cost)

                if instance_types:
                    all_resources = []
                    all_candidate_names = []
                    for instance_type in instance_types:
                        resource = resources.copy(
                            cloud=Spheron(),
                            instance_type=instance_type,
                            accelerators=resources.accelerators,
                            cpus=None,
                            memory=None,
                        )
                        all_resources.append(resource)
                        all_candidate_names.append(instance_type)
                    return resources_utils.FeasibleResources(
                        all_resources, all_candidate_names, None)
                else:
                    error_msg = (f'No instances available for accelerator '
                                 f'{accelerator_name}')
                    if errors:
                        error_msg += f': {"; ".join(errors)}'
                    return resources_utils.FeasibleResources([], [], error_msg)

        # No accelerators specified, return a default instance type
        default_instance_type = Spheron.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier,
            region=resources.region,
            zone=resources.zone,
            use_spot=resources.use_spot,
            max_hourly_cost=resources.max_hourly_cost)
        if default_instance_type is None:
            return resources_utils.FeasibleResources([], [], None)
        else:
            return resources_utils.FeasibleResources(
                _make_resources([default_instance_type]), [], None)

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        """Query cluster status."""
        # Actual status querying is handled by the provisioner
        return []
