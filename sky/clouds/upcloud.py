"""UpCloud Cloud provider."""

import re
import time
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import provision as provision_lib
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import upcloud as upcloud_adaptor
from sky.provision.upcloud import utils as upcloud_utils
from sky.utils import annotations
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_INDENT_PREFIX = '    '
_VOLUME_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$')

logger = sky_logging.init_logger(__name__)


@registry.CLOUD_REGISTRY.register
class UpCloud(clouds.Cloud):
    """UpCloud IaaS GPU/CPU VMs."""

    _REPR = 'UpCloud'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 64
    _MAX_VOLUME_NAME_LEN_LIMIT = 64
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on UpCloud.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tiers are not supported on UpCloud.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Custom multi-network is not supported on UpCloud.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}'),
    }

    # UpCloud storage tiers: hdd (LOW), standard (MEDIUM), maxiops (HIGH).
    # There is no tier faster than MaxIOPS, so ULTRA is not supported.
    _SUPPORTED_DISK_TIERS = (set(resources_utils.DiskTier) -
                             {resources_utils.DiskTier.ULTRA})
    _BEST_DISK_TIER = resources_utils.DiskTier.HIGH
    _DEFAULT_DISK_TIER = resources_utils.DiskTier.MEDIUM

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.UPDATABLE

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del region
        unsupported = dict(cls._CLOUD_UNSUPPORTED_FEATURES)
        if resources.use_spot:
            unsupported[clouds.CloudImplementationFeatures.STOP] = (
                'Stopping spot instances is not supported on UpCloud: UpCloud '
                'stops spot servers when it reclaims capacity, so a stopped '
                'spot cluster cannot be told apart from a preempted one.')
        return unsupported

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def is_volume_name_valid(cls,
                             volume_name: str) -> Tuple[bool, Optional[str]]:
        if len(volume_name) > cls._MAX_VOLUME_NAME_LEN_LIMIT:
            return (False, f'Volume name exceeds the maximum length of '
                    f'{cls._MAX_VOLUME_NAME_LEN_LIMIT} characters.')
        if not _VOLUME_NAME_PATTERN.match(volume_name):
            return (False, 'Volume name must start with a letter or digit '
                    'and contain only letters, digits, "-" and "_".')
        return True, None

    @classmethod
    def is_label_valid(cls, label_key: str,
                       label_value: str) -> Tuple[bool, Optional[str]]:
        if not re.match(r'^(?!_)[\x20-\x7e]{2,32}$', label_key):
            return (False, f'Invalid label key {label_key} for UpCloud. Key '
                    'must be 2-32 printable ASCII characters and must not '
                    'start with "_".')
        if len(label_value) > 255:
            return (False, f'Invalid label value {label_value} for UpCloud. '
                    'Value must be 255 characters or fewer in length.')
        return True, None

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
        assert zone is None, 'UpCloud does not support zones.'
        del accelerators, zone, resources
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'upcloud')
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
        del num_nodes
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='upcloud')

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del accelerators, use_spot, region, zone
        return 0.0

    def get_egress_cost(self, num_gigabytes: float):
        del num_gigabytes
        return 0.0

    @classmethod
    def _volume_vars(
        cls,
        region: str,
        volume_mounts: Optional[List['volume_lib.VolumeMount']],
    ) -> Tuple[List[Dict[str, str]], Optional[str]]:
        """Returns the file storage mounts and their private network."""
        file_volumes: List[Dict[str, str]] = []
        networks = set()
        for vol in volume_mounts or []:
            volume_config = vol.volume_config
            if volume_config.region not in (None, region):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Volume {vol.volume_name!r} is in '
                        f'{volume_config.region}, but the cluster is launched '
                        f'in {region}.')
            file_volumes.append({
                'address': volume_config.config['nfs_address'],
                'share_path': volume_config.config['nfs_path'],
                'path': vol.path,
            })
            networks.add(volume_config.config['network'])
        if len(networks) > 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'UpCloud clusters can mount file storage volumes of one '
                    f'private network only, got networks {sorted(networks)}.')
        return file_volumes, next(iter(networks), None)

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
        del cluster_name, dryrun, num_nodes
        assert zones is None, ('UpCloud does not support zones', zones)
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        docker_image = resources.extract_docker_image()
        file_volumes, volume_network = self._volume_vars(
            region.name, volume_mounts)
        docker_run_options: List[str] = []
        if docker_image is not None:
            if file_volumes:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NotSupportedError(
                        'UpCloud volumes cannot be mounted into docker '
                        'containers yet.')
            if acc_dict:
                docker_run_options.append('--gpus all')
            logger.info(
                f'Launching UpCloud cluster with docker image {docker_image}. '
                'Ensure the image is Debian-based and allows passwordless '
                'sudo.')

        network_name = skypilot_config.get_effective_region_config(
            cloud='upcloud',
            region=region.name,
            keys=('network_name',),
            default_value=None)

        result: Dict[str, Any] = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'use_spot': resources.use_spot,
            'disk_tier': upcloud_utils.disk_tier_to_upcloud(resources.disk_tier
                                                           ),
            'image_id': None,
            # GPU plans default to the template with NVIDIA drivers, CUDA,
            # Docker and the NVIDIA Container Toolkit.
            'default_image': 'gpu' if acc_dict else 'cpu',
            'install_docker': docker_image is not None,
            'upcloud_network': volume_network,
            'upcloud_network_name': network_name,
            'upcloud_file_volumes': file_volumes,
        }
        cloud_image_id = resources.get_cloud_image_id()
        if cloud_image_id is not None:
            if None in cloud_image_id:
                result['image_id'] = cloud_image_id[None]
            else:
                assert region.name in cloud_image_id, cloud_image_id
                result['image_id'] = cloud_image_id[region.name]
        if docker_run_options:
            result['docker_run_options'] = docker_run_options
        return result

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='upcloud')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='upcloud')

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
            clouds='upcloud')

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            if catalog.instance_type_exists(resources.instance_type, 'upcloud'):
                resources_launch = resources.copy(accelerators=None)
                return resources_utils.FeasibleResources([resources_launch], [],
                                                         None)
            return resources_utils.FeasibleResources(
                [], [],
                f'Instance type {resources.instance_type} not available '
                'on UpCloud')

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
                 max_hourly_cost=resources.max_hourly_cost,
                 clouds='upcloud')
            if instance_list is None:
                return resources_utils.FeasibleResources([],
                                                         fuzzy_candidate_list,
                                                         None)

            resource_list = []
            for instance_type in instance_list:
                resource_list.append(
                    resources.copy(
                        cloud=self,
                        instance_type=instance_type,
                        accelerators=None,
                        cpus=None,
                        memory=None,
                    ))
            return resources_utils.FeasibleResources(resource_list,
                                                     fuzzy_candidate_list, None)

        default_instance_type = self.get_default_instance_type(
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
        return resources_utils.FeasibleResources([
            resources.copy(
                cloud=self,
                instance_type=default_instance_type,
                accelerators=None,
                cpus=None,
                memory=None,
            )
        ], [], None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        try:
            upcloud_adaptor.check_compute_credentials()
            return True, None
        except upcloud_adaptor.UpCloudCredentialsNotFound as e:
            hints = (f'{e}'
                     f'\n{_INDENT_PREFIX}Create an API token in the UpCloud '
                     'Hub and save it in '
                     f'{upcloud_adaptor.UPCTL_CONFIG_PATH} as '
                     '"token: <api-token>".'
                     f'\n{_INDENT_PREFIX}Restrict file permissions: '
                     f'chmod 600 {upcloud_adaptor.UPCTL_CONFIG_PATH}'
                     f'\n{_INDENT_PREFIX}For more info: '
                     'https://docs.skypilot.co/en/latest/getting-started/'
                     'installation.html#upcloud')
            return False, hints
        except Exception as e:  # pylint: disable=broad-except
            return False, str(e)

    @classmethod
    @annotations.lru_cache(scope='request')
    def _check_storage_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks UpCloud Managed Object Storage (S3-compatible) credentials."""
        if not upcloud_adaptor.upcloud_profile_in_aws_cred_and_config():
            hints = (f'[{upcloud_adaptor.UPCLOUD_PROFILE_NAME}] profile '
                     'is not set in ~/.aws/credentials and ~/.aws/config.')
            hints += ' Run the following commands:'
            hints += (f'\n{_INDENT_PREFIX}  $ pip install boto3'
                      f'\n{_INDENT_PREFIX}  $ aws configure --profile upcloud')
            hints += (
                f'\n{_INDENT_PREFIX}Set endpoint_url to your MOS endpoint '
                f'(e.g. https://xxxxxx.upbucket.com).'
                f'\n{_INDENT_PREFIX}For more info: '
                'https://docs.skypilot.co/en/latest/getting-started/'
                'installation.html#upcloud')
            return False, hints
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        mounts: Dict[str, str] = {}
        if upcloud_adaptor.credential_file_exists():
            mounts[upcloud_adaptor.UPCTL_CONFIG_PATH] = (
                upcloud_adaptor.UPCTL_CONFIG_PATH)
        if upcloud_adaptor.upcloud_profile_in_aws_cred_and_config():
            mounts['~/.aws/credentials'] = '~/.aws/credentials'
            mounts['~/.aws/config'] = '~/.aws/config'
        return mounts

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """Returns the username of the account owning the API token."""
        return cls._get_user_identities()

    @classmethod
    @annotations.lru_cache(scope='request', maxsize=1)
    def _get_user_identities(cls) -> Optional[List[List[str]]]:
        try:
            account = upcloud_adaptor.get_account()
        except upcloud_adaptor.UpCloudCredentialsNotFound as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'UpCloud credentials not found: {e}') from None
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get UpCloud user identity: {e}') from None
        account = account if isinstance(account, dict) else account.to_dict()
        username = account.get('account', {}).get('username')
        if not username:
            return None
        return [[str(username)]]

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'upcloud')

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if zone is not None:
            raise ValueError('UpCloud does not support zones.')
        return catalog.validate_region_zone(region, zone, clouds='upcloud')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return catalog.regions(clouds='upcloud')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        del region
        client = upcloud_utils.UpCloudClient()
        try:
            storage = client.get_storage(image_id)
        except upcloud_utils.UpCloudError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'UpCloud image {image_id!r} not found: {e}') from e
        storage_type = str(storage.get('type', '')).lower()
        if storage_type not in ('template', 'normal', ''):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'UpCloud image {image_id!r} has type {storage_type!r}; '
                    'expected a template or normal disk.')
        try:
            return float(storage.get('size') or 0)
        except (TypeError, ValueError) as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'UpCloud image {image_id!r} has no size.') from e

    @classmethod
    def create_image_from_cluster(cls,
                                  cluster_name: resources_utils.ClusterName,
                                  region: Optional[str],
                                  zone: Optional[str]) -> str:
        del zone
        status = provision_lib.query_instances(
            'UpCloud',
            cluster_name.display_name,
            cluster_name.name_on_cloud,
            {'region': region} if region else None,
        )
        instance_ids = list(status.keys())
        if not instance_ids:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Failed to find source cluster '
                                   f'{cluster_name.display_name!r} on UpCloud.')
        if len(instance_ids) != 1:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    'Only support creating image from single '
                    f'instance, but got: {instance_ids}')
        client = upcloud_utils.UpCloudClient()
        server = client.get_server(instance_ids[0])
        disk_uuid = upcloud_utils.get_boot_storage_uuid(server)
        image_title = (
            f'skypilot-{cluster_name.display_name}-{int(time.time())}'[:255])
        template = client.templatize_storage(disk_uuid, image_title)
        template_uuid = str(template.get('uuid') or '')
        if not template_uuid:
            raise RuntimeError(
                f'UpCloud templatize returned no UUID: {template}')
        upcloud_utils.wait_for_storage_state(client, template_uuid, ['online'])
        return template_uuid

    @classmethod
    def maybe_move_image(cls, image_id: str, source_region: str,
                         target_region: str, source_zone: Optional[str],
                         target_zone: Optional[str]) -> str:
        del source_zone, target_zone
        if source_region == target_region:
            return image_id
        client = upcloud_utils.UpCloudClient()
        storage = client.get_storage(image_id)
        current_zone = str(storage.get('zone') or source_region)
        if current_zone == target_region:
            return image_id
        title = str(storage.get('title') or 'skypilot-clone')[:240]
        cloned = client.clone_storage(
            image_id,
            zone=target_region,
            title=f'{title}-{target_region}',
            tier=storage.get('tier'),
        )
        cloned_uuid = str(cloned.get('uuid') or '')
        if not cloned_uuid:
            raise RuntimeError(f'UpCloud clone returned no UUID: {cloned}')
        upcloud_utils.wait_for_storage_state(client, cloned_uuid, ['online'])
        # The source image was created only to be moved here.
        cls.delete_image(image_id, source_region)
        return cloned_uuid

    @classmethod
    def delete_image(cls, image_id: str, region: Optional[str]) -> None:
        del region
        client = upcloud_utils.UpCloudClient()
        try:
            client.delete_storage(image_id)
        except upcloud_utils.UpCloudError as e:
            logger.warning(f'Failed to delete UpCloud image {image_id}: {e}')
