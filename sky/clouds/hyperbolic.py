"""Hyperbolic Cloud provider implementation
for SkyPilot.
"""
import os
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky import clouds
from sky.clouds import service_catalog
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils.resources_utils import DiskTier

if typing.TYPE_CHECKING:
    from sky.resources import Resources


@registry.CLOUD_REGISTRY.register
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
            ('Hyperbolic does not support custom disk tiers.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on Hyperbolic. '
             'To read data from object stores on Hyperbolic, use `mode: COPY` '
             'to copy the data to local disk.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Hyperbolic.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Hyperbolic does not support spot instances.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Hyperbolic does not support cloning disks from existing '
             'clusters.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Hyperbolic does not support Docker images.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('Hyperbolic does not support opening ports.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Hyperbolic does not support custom image IDs.'),
    }
    # Note: Region and zone selection are not supported on Hyperbolic.
    # All resources are provisioned in a single region
    # without zones.
    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        assert zone is None, 'Hyperbolic does not support zones.'
        del accelerators, zone  # unused
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'hyperbolic')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='hyperbolic')

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='hyperbolic')

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[DiskTier] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='hyperbolic')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='hyperbolic')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        api_key_path = os.path.expanduser('~/.hyperbolic/api_key')
        if os.path.exists(api_key_path):
            return True, None
        return False, f'API key not found at {api_key_path}'

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        return cls._check_credentials()

    @classmethod
    def get_credential_file_mounts(cls) -> Dict[str, str]:
        api_key_path = os.path.expanduser('~/.hyperbolic/api_key')
        if os.path.exists(api_key_path):
            return {api_key_path: '~/.hyperbolic/api_key'}
        return {}

    def __repr__(self):
        return self._REPR

    def _get_feasible_launchable_resources(
            self,
            resources: 'Resources') -> 'resources_utils.FeasibleResources':
        # Check if the instance type exists in the catalog
        if resources.instance_type is not None:
            if service_catalog.instance_type_exists(resources.instance_type,
                                                    'hyperbolic'):
                # Remove accelerators for launchable resources
                resources_launch = resources.copy(accelerators=None)
                return resources_utils.FeasibleResources([resources_launch], [],
                                                         None)
            else:
                return resources_utils.FeasibleResources(
                    [], [], 'No matching instance type in Hyperbolic catalog.')

        # If accelerators are specified
        accelerators = resources.accelerators
        if accelerators is not None:
            assert len(accelerators) == 1, resources
            acc, acc_count = list(accelerators.items())[0]
            (instance_list, fuzzy_candidate_list
            ) = service_catalog.get_instance_type_for_accelerator(
                acc,
                acc_count,
                use_spot=resources.use_spot,
                cpus=resources.cpus,
                memory=resources.memory,
                region=resources.region,
                zone=resources.zone,
                clouds='hyperbolic')
            if instance_list is None:
                return resources_utils.FeasibleResources([],
                                                         fuzzy_candidate_list,
                                                         None)

            def _make(instance_list):
                resource_list = []
                for instance_type in instance_list:
                    r = resources.copy(
                        cloud=self,
                        instance_type=instance_type,
                        accelerators=None,
                        cpus=None,
                        memory=None,
                    )
                    resource_list.append(r)
                return resource_list

            return resources_utils.FeasibleResources(_make(instance_list),
                                                     fuzzy_candidate_list, None)

        # If nothing is specified, return a default instance type
        default_instance_type = self.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier)
        if default_instance_type is None:
            return resources_utils.FeasibleResources([], [], None)
        else:
            r = resources.copy(
                cloud=self,
                instance_type=default_instance_type,
                accelerators=None,
                cpus=None,
                memory=None,
            )
            return resources_utils.FeasibleResources([r], [], None)

    def instance_type_exists(self, instance_type: str) -> bool:
        return True

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if zone is not None:
            raise ValueError('Hyperbolic does not support zones.')
        return region, None

    @classmethod
    def regions(cls) -> List['clouds.Region']:
        return [clouds.Region('default')]

    @classmethod
    def zones_provision_loop(cls,
                             *,
                             region: str,
                             num_nodes: int,
                             instance_type: str,
                             accelerators: Optional[Dict[str, int]] = None,
                             use_spot: bool = False):
        yield None

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def get_egress_cost(self, num_gigabytes: float):
        return 0.0

    def accelerators_to_hourly_cost(self, accelerators: Dict[str, int],
                                    use_spot: bool, region: Optional[str],
                                    zone: Optional[str]) -> float:
        return 0.0

    def make_deploy_resources_variables(self,
                                        resources,
                                        cluster_name,
                                        region,
                                        zones,
                                        num_nodes,
                                        dryrun=False):
        """Converts planned sky.Resources to cloud-specific
        resource variables."""
        print(f'DEBUG: r.instance_type = {resources.instance_type}')
        del cluster_name, dryrun  # unused
        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'use_spot': r.use_spot,
        }

    def cluster_name_in_hint(self, cluster_name_on_cloud: Optional[str],
                             cluster_name: str) -> bool:
        """Check if a node's name matches the cluster name pattern."""
        if cluster_name_on_cloud is None:
            return False
        return cluster_name_on_cloud.startswith(cluster_name)
