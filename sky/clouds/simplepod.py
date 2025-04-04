""" SimplePod Cloud. """

import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

import requests

from sky import clouds
from sky.clouds import service_catalog
from sky.provision.simplepod import utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    'simplepod_keys',
]


@registry.CLOUD_REGISTRY.register
class Simplepod(clouds.Cloud):
    """ SimplePod GPU Cloud """

    _REPR = 'Simplepod'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances not supported.',
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on SimplePod.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported yet on SimplePod.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on SimplePod. To read data '
             'from object stores on SimplePod, use `mode: COPY` to copy the data '
             'to local disk.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
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
        assert zone is None, 'SimplePod does not support zones.'
        del accelerators, zone  # unused
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'simplepod')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='simplepod')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # SimplePod includes accelerators in the hourly cost

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None
    ) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='simplepod')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='simplepod')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Validates SimplePod credentials by checking API key and testing API access.

        Returns:
            Tuple[bool, Optional[str]]: (is_valid, error_message)
            - is_valid: True if credentials are valid
            - error_message: None if credentials are valid, otherwise error message
        """
        try:
            # Import check
            from sky.provision.simplepod import utils
        except ImportError:
            return False, ('Failed to import simplepod. '
                         'To install, run: pip install skypilot[simplepod]')

        # Check if API key file exists and can be read
        try:
            utils.SimplePodClient()
        except (utils.SimplePodError, FileNotFoundError) as e:
            return False, (
                'Failed to access SimplePod Cloud with credentials. '
                'To configure credentials, go to:\n'
                '    https://simplepod.ai/ \n'
                'to generate API key and add the line\n'
                '    api_key = [YOUR API KEY]\n'
                'to ~/.simplepod/simplepod_keys')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify SimplePod Cloud credentials. '
                         'Check your network connection '
                         'and try again.')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.simplepod/{filename}': f'~/.simplepod/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='simplepod')

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Check access to compute service.

        Returns same as _check_credentials()
        """
        return cls._check_credentials()

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        status_map = {
            'booting': status_lib.ClusterStatus.INIT,
            'active': status_lib.ClusterStatus.UP,
            'unhealthy': status_lib.ClusterStatus.INIT,
            'terminating': None,
            'terminated': None,
        }
        status_list = []
        vms = utils.SimplePodClient().list_instances()
        possible_names = [f'{name}-head', f'{name}-worker']
        for node in vms:
            if node.get('name') in possible_names:
                node_status = status_map[node['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list

    @classmethod
    def _cloud_unsupported_features(cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return {
            clouds.CloudImplementationFeatures.STOP: 'SimplePod does not support stopping instances',
            clouds.CloudImplementationFeatures.IMAGE_ID: 'SimplePod does not support custom images',
        }

    @classmethod
    def _get_inference_types(cls) -> Optional[Dict[str, List[str]]]:
        return None

    @classmethod
    def instance_type_exists(cls, instance_type: str) -> bool:
        return True  # Since SimplePod uses dynamic instance types

    @classmethod
    def accelerator_in_region_or_zone(cls,
                                    accelerator: str,
                                    acc_count: int,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> bool:
        return True  # Since SimplePod has dynamic availability

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='simplepod')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name: 'resources_utils.ClusterName',
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']],
            num_nodes: int,
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name, dryrun  # Unused.
        assert zones is None, 'SimplePod does not support zones.'

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        resources_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

        if acc_dict is not None:
            # SimplePod's docker runtime information does not contain
            # 'nvidia-container-runtime', causing no GPU option is added to
            # the docker run command. We patch this by adding it here.
            resources_vars['docker_run_options'] = ['--gpus all']

        return resources_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in SimplePod
            resources = resources.copy(accelerators=None)
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Simplepod(),
                    instance_type=instance_type,
                    # Setting this to None as SimplePod doesn't separately bill /
                    # attach the accelerators. Billed as part of the VM type.
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
            default_instance_type = Simplepod.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

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
            clouds='simplepod')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # TODO: Implement get_user_identities for SimplePod
        return None

    @classmethod
    def regions(cls) -> List['clouds.Region']:
        return service_catalog.regions(clouds='simplepod')

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
