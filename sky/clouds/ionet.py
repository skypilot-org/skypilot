"""IONet cloud implementation for SkyPilot."""
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.provision.ionet import utils as ionet_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib
    from sky.catalog import ionet_catalog
    from sky.utils import volume as volume_lib
else:
    # Loading the catalog module reads the catalog, which looks up this cloud
    # in the registry, so it must not be imported before IONet is registered.
    ionet_catalog = adaptors_common.LazyImport('sky.catalog.ionet_catalog')

_SETUP_HINT = ('Create an API key for the IO Cloud project (see '
               'https://io.net/docs/reference/vmaas/get-started-with-vmaas-api'
               ') and save it to ~/.ionet/api_key or set IONET_API_KEY.')


@registry.CLOUD_REGISTRY.register
class IONet(clouds.Cloud):
    """IONet GPU Cloud Provider."""

    _REPR = 'IONet'

    # io.net does not document a length limit for `resource_private_name`;
    # 50 is a conservative bound.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 50

    # IONet capabilities and limitations
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('io.net does not support stopping VMs.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('io.net does not support disk migration.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('io.net does not support spot instances.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('io.net does not support custom image IDs.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('io.net does not support docker images.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('io.net does not support custom disk tiers.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('io.net does not support custom network tiers.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('io.net does not support hosting controllers.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('io.net does not support highly available controllers.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('io.net does not support multiple network interfaces.'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('io.net currently supports only single-node clusters.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            ('io.net does not support custom public ports.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('io.net does not support storage mounts.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            ('io.net does not support custom local disks.'),
        clouds.CloudImplementationFeatures.AUTOSTOP:
            ('io.net does not support autostop.'),
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('io.net does not support autodown.'),
    }

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Check if IONet credentials are properly configured."""
        try:
            ionet_utils.get_api_key()
        except ionet_utils.IONetError as exc:
            return False, (f'{exc} {_SETUP_HINT}')
        try:
            # A cheap authenticated read; the first page of deployments is
            # also what the provisioner reads to find existing clusters.
            ionet_utils.IONetClient().list_deployments(1)
            return True, None
        except ionet_utils.IONetError as exc:
            if exc.status_code in (401, 403):
                return False, (f'io.net rejected the API key ({exc}). '
                               f'{_SETUP_HINT}')
            return False, f'IONet credential check failed: {exc}'

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Mounts the io.net API key file when it exists locally.

        Mounting lets managed-jobs and serve controllers launch on io.net.
        """
        if os.path.exists(os.path.expanduser(ionet_utils.API_KEY_PATH)):
            return {ionet_utils.API_KEY_PATH: ionet_utils.API_KEY_PATH}
        return {}

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'ionet')

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None
    ) -> List[clouds.Region]:
        assert zone is None, 'IONet does not support zones.'
        del accelerators, zone, resources
        if use_spot:
            return []

        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'ionet')

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
                                       clouds='ionet')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # IONet includes accelerators as part of the instance type.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # IONet doesn't charge for egress
        return 0.0

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional['resources_utils.DiskTier'] = None,
            local_disk: Optional[str] = None,
            region: Optional[str] = None,
            zone: Optional[str] = None,
            use_spot: bool = False,
            max_hourly_cost: Optional[float] = None) -> Optional[str]:
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='ionet')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='ionet')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='ionet')

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
        del cluster_name, dryrun, volume_mounts
        assert zones is None, 'IONet does not support zones.'
        if num_nodes != 1:
            raise ValueError('io.net VMaaS supports only one-node clusters.')
        resources = resources.assert_launchable()

        # Get accelerator information
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # Map instance type to IONet hardware specifications
        hardware_id, gpus_per_vm = ionet_catalog.get_hardware_config(
            resources.instance_type, region.name)
        storage_gib = ionet_catalog.get_storage_gib(resources.instance_type,
                                                    region.name)
        if resources.disk_size_specified and storage_gib is None:
            raise ValueError('io.net did not report storage capacity for this '
                             'offer; an explicit disk_size cannot be verified.')
        if (resources.disk_size_specified and storage_gib is not None and
                resources.disk_size > storage_gib):
            raise ValueError(
                f'io.net offer {resources.instance_type!r} provides '
                f'{storage_gib} GiB storage, below the requested '
                f'{resources.disk_size} GiB.')
        # io.net bills a VM for a fixed lifetime, so the user must choose it.
        duration_hours = skypilot_config.get_effective_region_config(
            cloud='ionet',
            region=None,
            keys=('duration_hours',),
            default_value=None)
        if (not isinstance(duration_hours, int) or
                isinstance(duration_hours, bool) or duration_hours < 1):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'io.net bills each VM for a fixed number of hours. Set '
                    '`ionet.duration_hours` to a positive integer in '
                    '~/.sky/config.yaml.')

        return {
            'instance_type': resources.instance_type,
            'hardware_id': hardware_id,
            'gpus_per_vm': gpus_per_vm,
            'custom_resources': custom_resources,
            'region': region.name,
            'duration_hours': duration_hours,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Filter and return feasible resources for IONet."""
        if resources.use_spot:
            return resources_utils.FeasibleResources([], [], None)

        if resources.instance_type is not None:
            # The optimizer has already validated the user's instance type.
            assert resources.cloud is not None and self.is_same_cloud(
                resources.cloud), resources
            return resources_utils.FeasibleResources(
                [resources.copy(cloud=IONet(), accelerators=None)], [], None)

        # Auto-select instance type based on requirements
        def _make(instance_type: str):
            r = resources.copy(
                cloud=IONet(),
                instance_type=instance_type,
                # IONet determines accelerators based on instance type
                accelerators=None,
                cpus=None,
                memory=None,
            )
            return r

        # Get feasible instance types from catalog
        if resources.accelerators is None:
            instance_type = self.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost)
            instance_types: Optional[List[str]] = ([] if instance_type is None
                                                   else [instance_type])
            fuzzy_candidates: List[str] = []
        else:
            if len(resources.accelerators) != 1:
                return resources_utils.FeasibleResources([], [], None)
            accelerator, count = next(iter(resources.accelerators.items()))
            instance_types, fuzzy_candidates = (
                catalog.get_instance_type_for_accelerator(
                    accelerator,
                    count,
                    cpus=resources.cpus,
                    memory=resources.memory,
                    local_disk=resources.local_disk,
                    region=resources.region,
                    zone=resources.zone,
                    max_hourly_cost=resources.max_hourly_cost,
                    clouds='ionet'))
        return resources_utils.FeasibleResources(
            [_make(t) for t in (instance_types or [])], fuzzy_candidates, None)

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # IONet doesn't support multiple identities
        return None
