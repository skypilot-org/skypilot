"""Lambda Cloud."""
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import common as adaptors_common
from sky.provision.lambda_cloud import lambda_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    import requests

    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib
else:
    requests = adaptors_common.LazyImport('requests')

# Minimum set of files under ~/.lambda_cloud that grant Lambda Cloud access.
_CREDENTIAL_FILES = [
    'lambda_keys',
]


@registry.CLOUD_REGISTRY.register
class Lambda(clouds.Cloud):
    """Lambda Labs GPU Cloud."""

    _REPR = 'Lambda'

    # Lamdba has a 64 char limit for cluster name.
    # Reference: https://cloud.lambdalabs.com/api/v1/docs#operation/launchInstance # pylint: disable=line-too-long
    # However, we need to account for the suffixes '-head' and '-worker'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 57
    # Currently, none of clouds.CloudImplementationFeatures are implemented
    # for Lambda Cloud.
    # STOP/AUTOSTOP: The Lambda cloud provider does not support stopping VMs.
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Lambda cloud does not support stopping VMs.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER: f'Migrating disk is currently not supported on {_REPR}.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: f'Spot instances are not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.IMAGE_ID: f'Specifying image ID is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: f'Custom disk tiers are not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS: f'Host controllers are not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS: f'High availability controllers are not supported on {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported in '
             f'{_REPR}.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources  # unused
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
        assert zone is None, 'Lambda does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'lambda')

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
                                       clouds='lambda')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # Lambda includes accelerators as part of the instance type.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return 'Lambda'

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional['resources_utils.DiskTier'] = None,
            local_disk: Optional[str] = None,
            region: Optional[str] = None,
            zone: Optional[str] = None) -> Optional[str]:
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='lambda')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='lambda')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='lambda')

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
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        del cluster_name, dryrun  # Unused.
        assert zones is None, 'Lambda does not support zones.'
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        resources_vars: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

        if acc_dict is not None:
            # Lambda cloud's docker runtime information does not contain
            # 'nvidia-container-runtime', causing no GPU option is added to
            # the docker run command. We patch this by adding it here.
            resources_vars['docker_run_options'] = ['--gpus all']

        return resources_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in Lambda Cloud
            resources = resources.copy(accelerators=None)
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Lambda(),
                    instance_type=instance_type,
                    # Setting this to None as Lambda doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
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
            default_instance_type = Lambda.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
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
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             clouds='lambda')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        Lambda's compute service."""
        try:
            lambda_utils.LambdaCloudClient().list_instances()
        except (AssertionError, KeyError, lambda_utils.LambdaCloudError):
            return False, ('Failed to access Lambda Cloud with credentials. '
                           'To configure credentials, go to:\n    '
                           '  https://cloud.lambdalabs.com/api-keys\n    '
                           'to generate API key and add the line\n    '
                           '  api_key = [YOUR API KEY]\n    '
                           'to ~/.lambda_cloud/lambda_keys')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify Lambda Cloud credentials. '
                           'Check your network connection '
                           'and try again.')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.lambda_cloud/{filename}': f'~/.lambda_cloud/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # TODO(ewzeng): Implement get_user_identities for Lambda
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'lambda')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='lambda')

    @classmethod
    def regions(cls) -> List['clouds.Region']:
        return catalog.regions(clouds='lambda')

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
        # TODO(ewzeng): filter by hash_filter_string to be safe
        status_list = []
        vms = lambda_utils.LambdaCloudClient().list_instances()
        possible_names = [f'{name}-head', f'{name}-worker']
        for node in vms:
            if node.get('name') in possible_names:
                node_status = status_map[node['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list
