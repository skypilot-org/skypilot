"""Fluidstack Cloud."""
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import common as adaptors_common
from sky.provision.fluidstack import fluidstack_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils.resources_utils import DiskTier

_CREDENTIAL_FILES = [
    # credential files for FluidStack,
    fluidstack_utils.FLUIDSTACK_API_KEY_PATH
]
if typing.TYPE_CHECKING:
    import requests

    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib
else:
    requests = adaptors_common.LazyImport('requests')


@registry.CLOUD_REGISTRY.register
class Fluidstack(clouds.Cloud):
    """FluidStack GPU Cloud."""

    _REPR = 'Fluidstack'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 57
    # Currently, none of clouds.CloudImplementationFeatures
    # are implemented for Fluidstack Cloud.
    # STOP/AUTOSTOP: The Fluidstack cloud
    # provider does not support stopping VMs.

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping clusters in FluidStack'
            ' is not supported in SkyPilot',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Migrating '
            f'disk is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances are'
            f' not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Specifying image ID '
            f'is not supported for {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers'
            f' is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers'
            f' are not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported in '
             f'{_REPR}.'),
    }
    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
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
        assert zone is None, 'FluidStack does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'fluidstack')

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
                                       clouds='fluidstack')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # Fluidstack includes accelerators as part of the instance type.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return 'Fluidstack'

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[DiskTier] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='fluidstack')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='fluidstack')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='fluidstack')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Optional[str]]:

        assert zones is None, 'FluidStack does not support zones.'
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'fluidstack_username': 'ubuntu',
        }

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in Fluidstack Cloud
            resources = resources.copy(accelerators=None)
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Fluidstack(),
                    instance_type=instance_type,
                    # Setting this to None as
                    # Fluidstack doesn't separately bill /
                    # attach the accelerators.
                    #  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = Fluidstack.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                region=resources.region,
                zone=resources.zone)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
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
             region=resources.region,
             zone=resources.zone,
             clouds='fluidstack')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        FluidStack's compute service."""
        try:
            assert os.path.exists(
                os.path.expanduser(fluidstack_utils.FLUIDSTACK_API_KEY_PATH))

            with open(os.path.expanduser(
                    fluidstack_utils.FLUIDSTACK_API_KEY_PATH),
                      encoding='UTF-8') as f:
                api_key = f.read().strip()
                if not api_key.startswith('api_key'):
                    return False, ('Invalid FluidStack API key format. '
                                   'To configure credentials, go to:\n    '
                                   '  https://dashboard.fluidstack.io \n    '
                                   'to obtain an API key, '
                                   'then add save the contents '
                                   'to ~/.fluidstack/api_key \n')
        except AssertionError:
            return False, ('Failed to access FluidStack Cloud'
                           ' with credentials. '
                           'To configure credentials, go to:\n    '
                           '  https://dashboard.fluidstack.io \n    '
                           'to obtain an API key, '
                           'then add save the contents '
                           'to ~/.fluidstack/api_key \n')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify FluidStack Cloud credentials. '
                           'Check your network connection '
                           'and try again.')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {filename: filename for filename in _CREDENTIAL_FILES}

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # TODO(mjibril): Implement get_active_user_identity for Fluidstack
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'fluidstack')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='fluidstack')

    @classmethod
    def query_status(
        cls,
        name: str,
        tag_filters: Dict[str, str],
        region: Optional[str],
        zone: Optional[str],
        **kwargs,
    ) -> List[status_lib.ClusterStatus]:
        return []
