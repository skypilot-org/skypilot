""" Shadeform Cloud. """

import json
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import common as adaptors_common
from sky.catalog import shadeform_catalog
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


@registry.CLOUD_REGISTRY.register
class Shadeform(clouds.Cloud):
    """Shadeform GPU Cloud

    Shadeform is a unified API for deploying and managing cloud GPUs across
    multiple cloud providers.
    """

    # Shadeform doesn't have explicit cluster name limits, but conservative
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    # Features not currently supported by Shadeform
    # yapf: disable
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping instances not supported on Shadeform.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            'Multi-node clusters not supported on Shadeform.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances not supported on Shadeform.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers not supported on Shadeform.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            'Custom network tiers not supported on Shadeform.',
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            'Object storage mounting not supported on Shadeform.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers not supported on Shadeform.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            'High availability controllers not supported.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Disk cloning not supported on Shadeform.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Custom image IDs not supported on Shadeform.',
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            'Docker images not supported on Shadeform yet.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Custom multiple network interfaces not supported.',
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
        assert zone is None, 'Shadeform does not support zones.'
        del zone  # unused
        if use_spot:
            return []  # No spot support

        # IMPORTANT: instance_type here is the specific Shadeform instance type
        # (like 'massedcompute_A6000_base'), NOT the accelerator name
        # We only return regions where this exact instance type exists
        regions = shadeform_catalog.get_region_zones_for_instance_type(
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
        if use_spot:
            return

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
        return None

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Get user identities for Shadeform."""
        # No user identity support needed
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'shadeform')

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        """Get hourly cost for instance type."""
        if use_spot:
            raise ValueError('Spot instances are not supported on Shadeform')
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
        return 0.0

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
        feasible_resources = self._get_feasible_launchable_resources(r)
        instance_type = feasible_resources.resources_list[0].instance_type

        resources_vars = {}
        if instance_type is not None:
            instance_type_split = instance_type.split('_')
            cloud = instance_type_split[0]
            resources_vars.update({
                'instance_type': instance_type,
                'region': region.name,
                'cloud': cloud,
            })

        # Add accelerator resources for Ray
        accelerators = resources.accelerators
        if accelerators is not None:
            resources_vars['custom_resources'] = json.dumps(accelerators,
                                                            separators=(',',
                                                                        ':'))

        return resources_vars

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
            api_key_path = os.path.expanduser('~/.shadeform/api_key')
            if not os.path.exists(api_key_path):
                return False, (f'Shadeform API key not found. '
                               f'Please save your API key to {api_key_path}')

            # Try to read the API key
            with open(api_key_path, 'r', encoding='utf-8') as f:
                api_key = f.read().strip()

            if not api_key:
                return False, f'Shadeform API key is empty in {api_key_path}'

            return True, None

        except (OSError, IOError) as e:
            return False, f'Error checking Shadeform credentials: {str(e)}'

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Get feasible launchable resources."""
        if resources.use_spot:
            return resources_utils.FeasibleResources(
                [], [], 'Spot instances are not supported on Shadeform.')

        if resources.instance_type is not None:
            # Instance type is already specified, validate it
            assert resources.is_launchable(), resources
            fuzzy_candidate_list = [resources.instance_type]
            return resources_utils.FeasibleResources([resources],
                                                     fuzzy_candidate_list, None)

        # Map accelerators to instance types
        def _make_resources(instance_type_list):
            resource_list = []
            for instance_type in instance_type_list:
                r = resources.copy(
                    cloud=Shadeform(),
                    instance_type=instance_type,
                    accelerators=resources.
                    accelerators,  # Keep original accelerators
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Handle accelerator requests
        accelerators = resources.accelerators
        if accelerators is not None:
            # Get the first accelerator type and count
            for accelerator_name, accelerator_count in accelerators.items():
                # Get instance types that provide this accelerator
                func = shadeform_catalog.get_instance_type_for_accelerator
                instance_types, errors = func(accelerator_name,
                                              accelerator_count,
                                              use_spot=resources.use_spot)

                if instance_types:
                    # Create separate resource objects for each instance type
                    # This is crucial: each resource will only be considered
                    # for regions where its specific instance type is available
                    all_resources = []
                    all_candidate_names = []

                    # Create one resource per instance type
                    for instance_type in instance_types:
                        resource = resources.copy(
                            cloud=Shadeform(),
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

            # If accelerator not found in mapping, return error
            return resources_utils.FeasibleResources(
                [], [],
                f'Accelerator {list(accelerators.keys())[0]} not supported.')

        # No accelerators specified, return a default instance type
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Shadeform.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone)
            if default_instance_type is None:
                # TODO: Add hints to all return values in this method to help
                #  users understand why the resources are not launchable.
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make_resources([default_instance_type]), [], None)

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
        # For validation purposes, return empty list (no existing clusters)
        # Actual status querying is handled by the provisioner
        return []
