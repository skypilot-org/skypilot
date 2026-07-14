"""OpenStack cloud implementation."""

import math
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.adaptors import openstack as openstack_adaptor
from sky.catalog import openstack_catalog
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib


@registry.CLOUD_REGISTRY.register
class OpenStack(clouds.Cloud):
    """OpenStack cloud."""

    _REPR = 'OpenStack'

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        # Leave room for resource-name suffixes and Neutron descriptions,
        # whose common upper bound is 255 characters.
        return 200

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node clusters are not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Cloning disks is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Docker images are not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot instances are not supported on OpenStack.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Custom disk tiers are not supported on OpenStack.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tiers are not supported on OpenStack.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('Opening ports is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Cloud storage mounting is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Managed jobs and services are not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on OpenStack.'),
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            ('Automatic termination is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('Autostop is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Autodown is not supported on OpenStack yet.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Multiple network interfaces are not supported on OpenStack.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            ('Local disks are not supported on OpenStack yet.'),
    }

    @classmethod
    def _unsupported_features_for_resources(cls, resources, region=None):
        del resources, region
        return cls._CLOUD_UNSUPPORTED_FEATURES.copy()

    @classmethod
    def _get_cloud_profile(cls) -> str:
        profile = skypilot_config.get_nested(keys=('openstack', 'cloud'),
                                             default_value=None)
        if not profile:
            raise ValueError('OpenStack config requires a named clouds.yaml '
                             'profile in openstack.cloud.')
        return profile

    @staticmethod
    def _get_project_id(connection: Any) -> Optional[str]:
        project_id = getattr(connection, 'current_project_id', None)
        if not project_id:
            session = getattr(connection, 'session', None)
            get_project_id = getattr(session, 'get_project_id', None)
            if callable(get_project_id):
                project_id = get_project_id()
        return str(project_id) if project_id else None

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        if not adaptors_common.can_import_modules(['openstack']):
            return False, ('OpenStack dependencies are not installed. Run: '
                           'pip install "skypilot[openstack]"')
        try:
            profile = cls._get_cloud_profile()
            connection = openstack_adaptor.get_connection(profile)
            connection.authorize()
            project_id = cls._get_project_id(connection)
            config = getattr(connection, 'config', None)
            region = getattr(config, 'region_name', None)
            if region is None and isinstance(config, dict):
                region = config.get('region_name')
            if project_id is None or not region:
                raise ValueError('OpenStack project ID or region could not be '
                                 'determined.')
            openstack_catalog.refresh_catalog(profile,
                                              project_id=project_id,
                                              region=region,
                                              connection=connection)
        except Exception as e:  # pylint: disable=broad-except
            return False, (
                'Failed to verify OpenStack credentials. Check the '
                'selected clouds.yaml profile and API access. '
                f'{common_utils.format_exception(e, use_bracket=True)}')
        return True, None

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        try:
            connection = openstack_adaptor.get_connection(
                cls._get_cloud_profile())
            connection.authorize()
            project_id = cls._get_project_id(connection)
            session = getattr(connection, 'session', None)
            get_user_id = getattr(session, 'get_user_id', None)
            user_id = get_user_id() if callable(get_user_id) else None
        except Exception as e:  # pylint: disable=broad-except
            raise exceptions.CloudUserIdentityError(
                'Failed to get the OpenStack user identity.') from e
        if user_id:
            identity = str(user_id)
            if project_id:
                identity += f' [project_id={project_id}]'
            return [[identity]]
        if project_id:
            return [[f'project_id={project_id}']]
        return None

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del instance_type, use_spot, region, zone
        return 0.0

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del accelerators, use_spot, region, zone
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes
        return 0.0

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return catalog.regions(clouds='openstack')

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
        del resources
        if accelerators is not None or use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(instance_type,
                                                             use_spot=False,
                                                             clouds='openstack')
        if region is not None:
            regions = [item for item in regions if item.name == region]
        if zone is not None:
            filtered = []
            for item in regions:
                zones = [] if item.zones is None else [
                    candidate for candidate in item.zones
                    if candidate.name == zone
                ]
                if zones:
                    filtered.append(clouds.Region(item.name).set_zones(zones))
            regions = filtered
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
    ) -> Iterator[Optional[List[clouds.Zone]]]:
        del num_nodes
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region,
                                            zone=None)
        for item in regions:
            if item.zones is None:
                yield None
            else:
                for zone in item.zones:
                    yield [zone]

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # Provisioning runs on the API server. The MVP does not support hosted
        # controllers, so uploading clouds.yaml to workload VMs would expose
        # server-side credentials without a runtime need.
        return {}

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        connection = openstack_adaptor.get_connection(cls._get_cloud_profile(),
                                                      region)
        image = connection.image.find_image(image_id, ignore_missing=True)
        if image is None:
            raise ValueError(f'OpenStack image {image_id!r} was not found in '
                             f'region {region!r}.')
        size_bytes = getattr(image, 'size', None)
        min_disk_gb = getattr(image, 'min_disk', None)
        if size_bytes is None and min_disk_gb is None:
            raise ValueError(f'OpenStack image {image_id!r} does not report a '
                             'size.')
        image_size_gb = (math.ceil(float(size_bytes) /
                                   1024**3) if size_bytes is not None else 0)
        min_disk_gb = float(min_disk_gb) if min_disk_gb is not None else 0.0
        return float(max(image_size_gb, min_disk_gb))

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        del cluster_name, dryrun, volume_mounts
        if num_nodes != 1:
            raise ValueError('OpenStack currently supports single-node '
                             'clusters only.')
        if zones is not None and len(zones) > 1:
            raise ValueError('OpenStack currently supports one availability '
                             'zone per cluster.')

        resources = resources.assert_launchable()
        image_ids = resources.image_id
        if image_ids is None:
            raise ValueError('OpenStack requires an image_id (a Glance image '
                             'ID or name).')
        if None in image_ids:
            image_id = image_ids[None]
        elif region.name in image_ids:
            image_id = image_ids[region.name]
        else:
            raise ValueError(f'No image_id was specified for OpenStack region '
                             f'{region.name!r}.')

        def _config(key: str, default: Any = None) -> Any:
            return skypilot_config.get_nested(keys=('openstack', key),
                                              default_value=default)

        cloud_profile = _config('cloud')
        network = _config('network')
        ssh_user = _config('ssh_user')
        for key, value in [('cloud', cloud_profile), ('network', network),
                           ('ssh_user', ssh_user)]:
            if not value:
                raise ValueError(f'OpenStack config requires {key!r}.')

        use_internal_ips = _config('use_internal_ips', False)
        external_network = _config('external_network')
        if not use_internal_ips and not external_network:
            raise ValueError('OpenStack config requires external_network when '
                             'use_internal_ips is false.')

        assert resources.instance_type is not None
        openstack_catalog.check_disk_size(resources.instance_type,
                                          resources.disk_size)

        availability_zone = zones[0].name if zones else resources.zone
        return {
            'availability_zone': availability_zone,
            'cloud': cloud_profile,
            'custom_resources':
                resources_utils.make_ray_custom_resources_str(None),
            'disk_size': resources.disk_size,
            'external_network': external_network,
            'image_id': image_id,
            'instance_type': resources.instance_type,
            'network': network,
            'region': region.name,
            'security_group_name': _config('security_group_name'),
            'ssh_user': ssh_user,
            'use_internal_ips': use_internal_ips,
        }

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> None:
        del instance_type
        return None

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='openstack')

    @classmethod
    def get_arch_from_instance_type(cls, instance_type: str) -> Optional[str]:
        return catalog.get_arch_from_instance_type(instance_type,
                                                   clouds='openstack')

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, clouds='openstack')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='openstack')

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        # OpenStack has no standard pricing API. Restrict it to explicitly
        # requested resources so zero-priced flavors do not win multi-cloud
        # optimization.
        if resources.cloud is None:
            return resources_utils.FeasibleResources([], [], None)
        if resources.use_spot or resources.accelerators is not None:
            return resources_utils.FeasibleResources(
                [], [], 'OpenStack currently supports CPU instances only.')
        if resources.max_hourly_cost is not None:
            return resources_utils.FeasibleResources(
                [], [], 'OpenStack pricing is unknown; max_hourly_cost cannot '
                'be evaluated.')
        if resources.instance_type is not None:
            openstack_catalog.check_disk_size(resources.instance_type,
                                              resources.disk_size)
            return resources_utils.FeasibleResources(
                [resources.copy(accelerators=None)], [], None)

        instance_type = openstack_catalog.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier,
            local_disk=resources.local_disk,
            region=resources.region,
            zone=resources.zone,
            use_spot=resources.use_spot,
            max_hourly_cost=resources.max_hourly_cost,
            min_disk_size=resources.disk_size)
        if instance_type is None:
            return resources_utils.FeasibleResources([], [], None)
        openstack_catalog.check_disk_size(instance_type, resources.disk_size)
        launchable = resources.copy(cloud=OpenStack(),
                                    instance_type=instance_type,
                                    accelerators=None,
                                    cpus=None,
                                    memory=None)
        return resources_utils.FeasibleResources([launchable], [], None)

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
            clouds='openstack')
