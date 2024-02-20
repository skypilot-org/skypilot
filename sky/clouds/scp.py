"""SCP: Samsung Cloud Platform.

This module includes the set of functions
to access the SCP catalog and check credentials for the SCP access.
"""

import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.clouds import service_catalog
from sky.clouds.utils import scp_utils
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    'scp_credential',
]

logger = sky_logging.init_logger(__name__)

_SCP_MIN_DISK_SIZE_GB = 100
_SCP_MAX_DISK_SIZE_GB = 300


@clouds.CLOUD_REGISTRY.register
class SCP(clouds.Cloud):
    """SCP Cloud."""

    _REPR = 'SCP'

    # SCP has a 28 char limit for cluster name.
    # Reference: https://cloud.samsungsds.com/openapiguide/#/docs
    #            /v2-en-virtual_server-definitions-VirtualServerCreateV3Request
    _MAX_CLUSTER_NAME_LEN_LIMIT = 28
    # MULTI_NODE: Multi-node is not supported by the implementation yet.
    _MULTI_NODE = 'Multi-node is not supported by the SCP Cloud yet.'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.MULTI_NODE: _MULTI_NODE,
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Migrating disk is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            (f'Specifying image ID is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            (f'Docker image is currently not supported on {_REPR}. '
             'You can try running docker command inside the '
             '`run` section in task.yaml.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            (f'Spot instances are not supported in {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tiers are not supported in {_REPR}.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            (f'Opening ports is currently not supported on {_REPR}.'),
    }

    _INDENT_PREFIX = '    '

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        features = cls._CLOUD_UNSUPPORTED_FEATURES
        if resources.use_spot:
            features[clouds.CloudImplementationFeatures.STOP] = (
                'Stopping spot instances is currently not supported on'
                f' {cls._REPR}.')
        return features

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions(cls) -> List['clouds.Region']:
        return service_catalog.regions(clouds='scp')

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:

        del accelerators, zone  # unused
        if use_spot:
            return []
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'scp')

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
                                               clouds='scp')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # SCP includes accelerators as part of the instance type.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, SCP)

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
                                                         clouds='scp')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='scp')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='scp')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str,
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']],
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name_on_cloud, dryrun  # Unused.
        assert zones is None, 'SCP does not support zones.'

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)

        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        image_id = self._get_image_id(r.image_id, region.name, r.instance_type)
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
        }

    @classmethod
    def _get_image_id(
        cls,
        image_id: Optional[Dict[Optional[str], str]],
        region_name: str,
        instance_type: str,
    ) -> str:

        if image_id is None:
            return cls._get_default_ami(region_name, instance_type)
        if None in image_id:
            image_id_str = image_id[None]
        else:
            assert region_name in image_id, image_id
            image_id_str = image_id[region_name]
        if image_id_str.startswith('skypilot:'):
            image_id_str = service_catalog.get_image_id_from_tag(image_id_str,
                                                                 region_name,
                                                                 clouds='scp')
            if image_id_str is None:
                # Raise ResourcesUnavailableError to make sure the failover
                # in CloudVMRayBackend will be correctly triggered.
                # TODO(zhwu): This is a information leakage to the cloud
                # implementor, we need to find a better way to handle this.
                raise exceptions.ResourcesUnavailableError(
                    f'No image found for region {region_name}')
        return image_id_str

    @classmethod
    def _get_default_ami(cls, region_name: str, instance_type: str) -> str:
        acc = cls.get_accelerators_from_instance_type(instance_type)
        image_id = service_catalog.get_image_id_from_tag('skypilot:ubuntu-2004',
                                                         region_name,
                                                         clouds='scp')
        if acc is not None:
            assert len(acc) == 1, acc
            image_id = service_catalog.get_image_id_from_tag(
                'skypilot:gpu-ubuntu-1804', region_name, clouds='scp')
        if image_id is not None:
            return image_id
        # Raise ResourcesUnavailableError to make sure the failover in
        # CloudVMRayBackend will be correctly triggered.
        # TODO(zhwu): This is a information leakage to the cloud implementor,
        # we need to find a better way to handle this.
        raise exceptions.ResourcesUnavailableError(
            'No image found in catalog for region '
            f'{region_name}. Try setting a valid image_id.')

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
        # Check if the host VM satisfies the min/max disk size limits.
        is_allowed = self._is_disk_size_allowed(resources)
        if not is_allowed:
            return ([], [])
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in SCP Cloud
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=SCP(),
                    instance_type=instance_type,
                    # Setting this to None as SCP doesn't separately bill /
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
            default_instance_type = SCP.get_default_instance_type(
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
            clouds='scp')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        try:
            scp_utils.SCPClient().list_instances()
        except (AssertionError, KeyError, scp_utils.SCPClientError,
                scp_utils.SCPCreationFailError):
            return False, (
                'Failed to access SCP with credentials. '
                'To configure credentials, see: '
                'https://cloud.samsungsds.com/openapiguide\n'
                f'{cls._INDENT_PREFIX}Generate API key and add the '
                'following line to ~/.scp/scp_credential:\n'
                f'{cls._INDENT_PREFIX}  access_key = [YOUR API ACCESS KEY]\n'
                f'{cls._INDENT_PREFIX}  secret_key = [YOUR API SECRET KEY]\n'
                f'{cls._INDENT_PREFIX}  project_id = [YOUR PROJECT ID]')

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.scp/{filename}': f'~/.scp/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # TODO(jgoo1): Implement get_current_user_identity for SCP
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'scp')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region, zone, clouds='scp')

    @staticmethod
    def _is_disk_size_allowed(resources):
        if (resources.disk_size and
            (resources.disk_size < _SCP_MIN_DISK_SIZE_GB or
             resources.disk_size > _SCP_MAX_DISK_SIZE_GB)):
            logger.info(f'In SCP, the disk size must range between'
                        f' {_SCP_MIN_DISK_SIZE_GB} GB '
                        f'and {_SCP_MAX_DISK_SIZE_GB} GB. '
                        f'Input: {resources.disk_size}')
            return False
        return True

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del tag_filters, region, zone, kwargs  # Unused.

        # TODO: Multi-node is not supported yet.

        status_map = {
            'CREATING': status_lib.ClusterStatus.INIT,
            'EDITING': status_lib.ClusterStatus.INIT,
            'RUNNING': status_lib.ClusterStatus.UP,
            'STARTING': status_lib.ClusterStatus.INIT,
            'RESTARTING': status_lib.ClusterStatus.INIT,
            'STOPPING': status_lib.ClusterStatus.STOPPED,
            'STOPPED': status_lib.ClusterStatus.STOPPED,
            'TERMINATING': None,
            'TERMINATED': None,
        }
        status_list = []
        vms = scp_utils.SCPClient().list_instances()
        for node in vms:
            if node['virtualServerName'] == name:
                node_status = status_map[node['virtualServerState']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list
