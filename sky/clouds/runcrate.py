"""Runcrate Cloud."""
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.provision.runcrate import utils as runcrate_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CLOUD = 'runcrate'


@registry.CLOUD_REGISTRY.register
class Runcrate(clouds.Cloud):
    """Runcrate GPU Cloud (https://www.runcrate.ai).

    Runcrate rents GPU and CPU virtual machines by the hour across many
    regions. Each machine is created through Runcrate's public API and reached
    over SSH as root.
    """

    _REPR = 'Runcrate'

    # Names leave room for the '-head' and '-worker' suffixes.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            (f'Stopping instances is not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            (f'Auto-stop is not supported on {_REPR} yet. Use autodown '
             '(`sky autostop --down` or `-i <minutes> --down`) instead.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            (f'Multi-node clusters are not supported on {_REPR} yet, as its '
             'instances have no private network between them.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Disk cloning is not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            (f'Specifying an image ID is not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            (f'Docker images as the runtime environment are not supported on '
             f'{_REPR} yet.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            (f'Spot instances are not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tiers are not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            (f'Custom network tiers are not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            (f'Host controllers are not supported on {_REPR} yet.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            (f'High availability controllers are not supported on {_REPR} '
             'yet.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            (f'Customized multiple network interfaces are not supported on '
             f'{_REPR} yet.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Specifying local disks is not supported on {_REPR} yet.'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
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
        assert zone is None, 'Runcrate does not support zones.'
        del accelerators, zone, resources  # unused
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, _CLOUD)
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
        del num_nodes  # unused
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds=_CLOUD)

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # The accelerators are part of the instance type's hourly price.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes  # unused
        return 0.0

    def __repr__(self):
        return self._REPR

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
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds=_CLOUD)

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds=_CLOUD)

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds=_CLOUD)

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
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
        del cluster_name, zones, num_nodes, dryrun, volume_mounts  # unused
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.use_spot:
            return resources_utils.FeasibleResources(
                [], [], f'Spot instances are not supported on {self._REPR}.')
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # The accelerators are part of the instance type.
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Runcrate(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Runcrate.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                _make([default_instance_type]), [], None)

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
             max_hourly_cost=resources.max_hourly_cost,
             clouds=_CLOUD)
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to Runcrate."""
        hint = (f'Create an API key at {runcrate_utils.API_KEY_URL} and save '
                'it:\n    '
                f'  mkdir -p ~/.runcrate && echo "<your API key>" > '
                f'{runcrate_utils.CREDENTIALS_PATH}')
        if not os.path.exists(
                os.path.expanduser(runcrate_utils.CREDENTIALS_PATH)):
            return False, (f'Runcrate API key not found at '
                           f'{runcrate_utils.CREDENTIALS_PATH}. {hint}')
        try:
            runcrate_utils.list_ssh_keys()
        except runcrate_utils.RuncrateError as e:
            return False, f'Failed to access Runcrate: {e}. {hint}'
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            runcrate_utils.CREDENTIALS_PATH: runcrate_utils.CREDENTIALS_PATH
        }

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, _CLOUD)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds=_CLOUD)

    @classmethod
    def regions(cls) -> List['clouds.Region']:
        return catalog.regions(clouds=_CLOUD)
