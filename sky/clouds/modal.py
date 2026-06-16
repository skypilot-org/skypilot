"""Modal cloud."""

import os
import re
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import modal as modal_adaptor
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILE = '~/.modal.toml'
_AUTO_REGION = 'auto'


@registry.CLOUD_REGISTRY.register
class Modal(clouds.Cloud):
    """Modal Sandbox cloud."""

    _REPR = 'Modal'
    _MAX_CLUSTER_NAME_LEN_LIMIT = 60
    # yapf: disable
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping Modal Sandboxes is not supported; use down instead.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            'Multi-node Modal clusters are not supported yet.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Disk cloning is not supported on Modal.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Custom image IDs are not supported on Modal.',
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            'Docker images are not supported on Modal yet.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances are not supported on Modal.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers are not supported on Modal.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            'Custom network tiers are not supported on Modal.',
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            'Opening arbitrary ports is not supported on Modal yet.',
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            'Storage mounting is not supported on Modal yet.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers are not supported on Modal yet.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            'High availability controllers are not supported on Modal.',
        clouds.CloudImplementationFeatures.AUTO_TERMINATE:
            'Auto-termination is not supported on Modal yet.',
        clouds.CloudImplementationFeatures.AUTOSTOP:
            'Autostop is not supported on Modal yet.',
        clouds.CloudImplementationFeatures.AUTODOWN:
            'Autodown is not supported on Modal yet.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Custom multiple network interfaces are not supported on Modal.',
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            'Local disk requests are not supported on Modal.',
    }
    # yapf: enable

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

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
        assert zone is None, 'Modal does not support zones.'
        del accelerators, zone, resources  # unused
        if use_spot:
            return []
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'modal')
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
                                       clouds='modal')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    @classmethod
    def is_label_valid(cls, label_key: str,
                       label_value: str) -> Tuple[bool, Optional[str]]:
        key_regex = re.compile(r'^[a-z]([a-z0-9_-]{0,62})?$')
        value_regex = re.compile(r'^[a-z0-9_-]{0,63}$')
        key_valid = bool(key_regex.match(label_key))
        value_valid = bool(value_regex.match(label_value))
        error_msg = None
        condition_msg = ('can include lowercase alphanumeric characters, '
                         'dashes, and underscores, with a total length of 63 '
                         'characters or less.')
        if not key_valid:
            error_msg = (f'Invalid label key {label_key} for Modal. '
                         'Key must start with a lowercase letter '
                         f'and {condition_msg}')
        if not value_valid:
            error_msg = (f'Invalid label value {label_value} for Modal. '
                         f'Value {condition_msg}')
        if not key_valid or not value_valid:
            return False, error_msg
        return True, None

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
            clouds='modal')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='modal')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='modal')

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
    ) -> Dict[str, Any]:
        del cluster_name, dryrun, volume_mounts  # unused
        if num_nodes != 1:
            raise ValueError('Modal only supports single-node clusters.')
        assert zones is None, 'Modal does not support zones.'
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        # pylint: disable=import-outside-toplevel
        from sky.catalog import modal_catalog
        modal_gpu, modal_cpu, modal_memory = (
            modal_catalog.get_modal_args_from_instance_type(
                resources.instance_type))
        modal_region = None
        if region.name != _AUTO_REGION:
            modal_region = region.name
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'modal_region': modal_region,
            'modal_gpu': modal_gpu,
            'modal_cpu': modal_cpu,
            'modal_memory': modal_memory,
            'modal_timeout': 24 * 60 * 60,
            'modal_idle_timeout': None,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            if not catalog.instance_type_exists(resources.instance_type,
                                                'modal'):
                raise ValueError(
                    f'Invalid instance type: {resources.instance_type}')
            resources = resources.copy(accelerators=None,
                                       cpus=None,
                                       memory=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(cloud=Modal(),
                                   instance_type=instance_type,
                                   accelerators=None,
                                   cpus=None,
                                   memory=None)
                resource_list.append(r)
            return resource_list

        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = Modal.get_default_instance_type(
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
             clouds='modal')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        try:
            modal_config = modal_adaptor.modal.config.config
            token_id = modal_config.get('token_id')
            token_secret = modal_config.get('token_secret')
        except Exception as e:  # pylint: disable=broad-except
            return False, (
                'Failed to access Modal credentials. Install Modal with '
                '`pip install "skypilot[modal]"` and run `modal token new` '
                f'or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET. {e}')
        if not token_id or not token_secret:
            return False, (
                'Modal credentials were not found. Run `modal token new` '
                'or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_file = os.path.expanduser(_CREDENTIAL_FILE)
        if os.path.exists(credential_file):
            return {_CREDENTIAL_FILE: _CREDENTIAL_FILE}
        return {}

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'modal')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='modal')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return catalog.regions(clouds='modal')

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del tag_filters, region, zone, kwargs  # unused
        # pylint: disable=import-outside-toplevel
        from sky.provision.modal import instance as modal_instance
        statuses = modal_instance.query_instances('Modal', name, None)
        return [status for status, _ in statuses.values() if status is not None]
