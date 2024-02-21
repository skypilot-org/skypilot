"""Fluidstack Cloud."""
import json
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

import requests

from sky import clouds
from sky import status_lib
from sky.clouds import service_catalog
from sky.provision.fluidstack import fluidstack_utils
from sky.utils.resources_utils import DiskTier

_CREDENTIAL_FILES = [
    # credential files for FluidStack,
    fluidstack_utils.FLUIDSTACK_API_KEY_PATH,
    fluidstack_utils.FLUIDSTACK_API_TOKEN_PATH,
]
if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib


@clouds.CLOUD_REGISTRY.register
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
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            'Opening ports'
            f'is not supported in {_REPR}.',
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
        regions = service_catalog.get_region_zones_for_instance_type(
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
        return service_catalog.get_hourly_cost(instance_type,
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

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Fluidstack)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[DiskTier] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='fluidstack')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='fluidstack')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='fluidstack')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name_on_cloud: str,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        dryrun: bool = False,
    ) -> Dict[str, Optional[str]]:

        assert zones is None, 'FluidStack does not support zones.'

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        cuda_installation_commands = """
        sudo wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/cuda-keyring_1.1-1_all.deb -O /usr/local/cuda-keyring_1.1-1_all.deb; 
        sudo dpkg -i /usr/local/cuda-keyring_1.1-1_all.deb;
        sudo apt-get update;
        sudo apt-get -y install cuda-toolkit-12-3;
        sudo apt-get install -y cuda-drivers;
        sudo apt-get install -y python3-pip;
        nvidia-smi || sudo reboot;"""
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'fluidstack_username': self.default_username(region.name),
            'cuda_installation_commands': cuda_installation_commands,
        }

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in Fluidstack Cloud
            resources = resources.copy(accelerators=None)
            return ([resources], [])

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
                disk_tier=resources.disk_tier)
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

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
            clouds='fluidstack')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:

        try:
            assert os.path.exists(
                os.path.expanduser(fluidstack_utils.FLUIDSTACK_API_KEY_PATH))
            assert os.path.exists(
                os.path.expanduser(fluidstack_utils.FLUIDSTACK_API_TOKEN_PATH))
        except AssertionError:
            return False, (
                'Failed to access FluidStack Cloud'
                ' with credentials. '
                'To configure credentials, go to:\n    '
                '  https://console.fluidstack.io \n    '
                'to obtain an API key and API Token, '
                'then add save the contents '
                'to ~/.fluidstack/api_key and ~/.fluidstack/api_token \n')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify FluidStack Cloud credentials. '
                           'Check your network connection '
                           'and try again.')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {filename: filename for filename in _CREDENTIAL_FILES}

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # TODO(mjibril): Implement get_current_user_identity for Fluidstack
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'fluidstack')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='fluidstack')

    @classmethod
    def default_username(cls, region: str) -> str:
        return {
            'norway_2_eu': 'fsuser',
            'calgary_1_canada': 'ubuntu',
            'norway_3_eu': 'ubuntu',
            'norway_4_eu': 'ubuntu',
            'india_2': 'root',
            'nevada_1_usa': 'fsuser',
            'generic_1_canada': 'ubuntu',
            'iceland_1_eu': 'ubuntu',
            'new_york_1_usa': 'fsuser',
            'illinois_1_usa': 'fsuser'
        }.get(region, 'ubuntu')

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
