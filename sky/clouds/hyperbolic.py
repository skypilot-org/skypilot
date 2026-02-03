"""Hyperbolic Cloud provider implementation
for SkyPilot.
"""
import os
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils.resources_utils import DiskTier

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib


@registry.CLOUD_REGISTRY.register
class Hyperbolic(clouds.Cloud):
    """Hyperbolic Cloud Provider."""

    _REPR = 'Hyperbolic'
    name = 'hyperbolic'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    API_KEY_PATH = os.path.expanduser('~/.hyperbolic/api_key')

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: ('Stopping not supported.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Custom disk tiers not supported.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Storage mounting not supported.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers not supported.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot instances not supported.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Disk cloning not supported.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Docker images not supported.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('Opening ports not supported.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Custom image IDs not supported.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tiers not supported.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Host controllers not supported.'),
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            ('Auto-termination not supported.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Auto-stop not supported.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Auto-down not supported.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces not supported.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'hyperbolic')

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
        assert zone is None, 'Hyperbolic does not support zones.'
        del accelerators, zone  # unused

        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'hyperbolic')
        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='hyperbolic')

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='hyperbolic')

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[DiskTier] = None,
                                  local_disk: Optional[str] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='hyperbolic')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='hyperbolic')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        if os.path.exists(cls.API_KEY_PATH):
            return True, None
        return False, f'API key not found at {cls.API_KEY_PATH}'

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        return cls._check_credentials()

    @classmethod
    def get_credential_file_mounts(cls) -> Dict[str, str]:
        if os.path.exists(cls.API_KEY_PATH):
            return {cls.API_KEY_PATH: '~/.hyperbolic/api_key'}
        return {}

    def __repr__(self):
        return self._REPR

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        # Check if the instance type exists in the catalog
        if resources.instance_type is not None:
            if catalog.instance_type_exists(resources.instance_type,
                                            'hyperbolic'):
                # Remove accelerators for launchable resources
                resources_launch = resources.copy(accelerators=None)
                return resources_utils.FeasibleResources([resources_launch], [],
                                                         None)
            else:
                raise ValueError(
                    f'Invalid instance type: {resources.instance_type}')

        # If accelerators are specified
        accelerators = resources.accelerators
        if accelerators is not None:
            assert len(accelerators) == 1, resources
            acc, acc_count = list(accelerators.items())[0]
            (instance_list,
             fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
                 acc,
                 acc_count,
                 use_spot=resources.use_spot,
                 cpus=resources.cpus,
                 memory=resources.memory,
                 local_disk=resources.local_disk,
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
            disk_tier=resources.disk_tier,
            local_disk=resources.local_disk,
            region=resources.region,
            zone=resources.zone)
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

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if zone is not None:
            raise ValueError('Hyperbolic does not support zones.')
        return catalog.validate_region_zone(region, zone, 'hyperbolic')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        """Returns the list of regions in Hyperbolic's catalog."""
        return catalog.regions('hyperbolic')

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
        """Returns a dict of variables for the deployment template."""
        del dryrun, region, cluster_name  # unused
        assert zones is None, ('Hyperbolic does not support zones', zones)

        resources = resources.assert_launchable()
        # resources.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'num_nodes': 1,  # Hyperbolic only supports single-node clusters
        }

    def cluster_name_in_hint(self, cluster_name_on_cloud: Optional[str],
                             cluster_name: str) -> bool:
        """Check if a node's name matches the cluster name pattern."""
        if cluster_name_on_cloud is None:
            return False
        return cluster_name_on_cloud.startswith(cluster_name)
