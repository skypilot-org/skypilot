"""Modal cloud."""

import os
import re
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import colorama

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import modal as modal_adaptor
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILE = '~/.modal.toml'
_TOKEN_ID_ENV_VAR = 'MODAL_TOKEN_ID'
_TOKEN_SECRET_ENV_VAR = 'MODAL_TOKEN_SECRET'
_CONFIG_PATH_ENV_VAR = 'MODAL_CONFIG_PATH'
_AUTO_REGION = 'auto'
# Modal's maximum Function timeout: a cluster's call ends after this long.
_MAX_CLUSTER_LIFETIME_HOURS = 24

logger = sky_logging.init_logger(__name__)


@annotations.lru_cache(scope='request')
def _log_cluster_lifetime() -> None:
    """Once per launch: nothing else shows that the cluster will end."""
    logger.info(f'{colorama.Style.DIM}Modal terminates a cluster '
                f'{_MAX_CLUSTER_LIFETIME_HOURS} hours after launch (its '
                f'maximum Function timeout).{colorama.Style.RESET_ALL}')


@registry.CLOUD_REGISTRY.register
class Modal(clouds.Cloud):
    """Modal, with each cluster running as a Modal Function call."""

    _REPR = 'Modal'
    # Modal object names are at most 64 characters. A cluster's App and Dict
    # add a 4-character prefix; a volume adds `-` and a 6-character suffix.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 57
    # yapf: disable
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            'Stopping Modal clusters is not supported; use down instead.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Disk cloning is not supported on Modal.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Custom image IDs are not supported on Modal.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers are not supported on Modal.',
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            'Custom network tiers are not supported on Modal.',
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            'Host controllers are not supported on Modal yet.',
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            'High availability controllers are not supported on Modal.',
        clouds.CloudImplementationFeatures.AUTOSTOP:
            'Autostop without down is not supported on Modal.',
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            'Custom multiple network interfaces are not supported on Modal.',
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            'Local disk requests are not supported on Modal.',
    }
    # yapf: enable
    # Modal multi-node clusters must use every GPU on each node.
    _MULTI_NODE_GPUS_PER_NODE = 8

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del region  # unused
        unsupported = dict(cls._CLOUD_UNSUPPORTED_FEATURES)
        reason = cls._multi_node_unsupported_reason(resources.accelerators)
        if reason is not None:
            unsupported[clouds.CloudImplementationFeatures.MULTI_NODE] = reason
        return unsupported

    @classmethod
    def _multi_node_unsupported_reason(
            cls, accelerators: Optional[Dict[str,
                                             Union[int,
                                                   float]]]) -> Optional[str]:
        """Why these accelerators cannot form a multi-node Modal cluster."""
        per_node = cls._MULTI_NODE_GPUS_PER_NODE
        if not accelerators:
            return (f'Multi-node Modal clusters need {per_node} GPUs per node, '
                    f'e.g. --gpus H100:{per_node}.')
        name, count = next(iter(accelerators.items()))
        if count == per_node:
            return None
        # modal_catalog imports sky.clouds, so a top-level import here would
        # be circular.
        # pylint: disable=import-outside-toplevel
        from sky.catalog import modal_catalog
        max_count = modal_catalog.get_max_gpu_count(name)
        if max_count is not None and max_count < per_node:
            return (f'{name} cannot form multi-node Modal clusters: they need '
                    f'{per_node} GPUs per node, and Modal offers at most '
                    f'{max_count} {name} per node.')
        return (f'Multi-node Modal clusters need all {per_node} GPUs of each '
                f'node: use --gpus {name}:{per_node} instead of '
                f'{name}:{count:g}.')

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
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'modal')
        if region is None:
            region = _AUTO_REGION
        return [r for r in regions if r.name == region]

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
        del cluster_name  # unused
        assert zones is None, 'Modal does not support zones.'
        if not dryrun:
            _log_cluster_lifetime()
        resources = resources.assert_launchable()
        modal_environment_name = self._get_active_environment_name()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        # Both modules import sky.clouds, so top-level imports here would be
        # circular.
        # pylint: disable=import-outside-toplevel
        from sky.catalog import modal_catalog
        from sky.provision.modal import modal_utils
        modal_gpu, modal_cpu, modal_memory = (
            modal_catalog.get_modal_args_from_instance_type(
                resources.instance_type))
        modal_region = None
        if region.name != _AUTO_REGION:
            modal_region = region.name
        modal_volume_mounts = []
        if volume_mounts is not None:
            for volume_mount in volume_mounts:
                if volume_mount.is_ephemeral:
                    continue
                volume_config = volume_mount.volume_config
                if volume_config.type != 'modal-volume':
                    raise ValueError('Modal clusters only support '
                                     'modal-volume volume mounts. Got '
                                     f'{volume_config.type!r} for volume '
                                     f'{volume_mount.volume_name!r}.')
                modal_volume_mounts.append({
                    'Path': modal_utils.resolve_mount_path(volume_mount.path),
                    'VolumeNameOnCloud': volume_config.name_on_cloud,
                    'EnvironmentName':
                        volume_config.config.get('environment_name')
                        or modal_environment_name,
                    'SubPath': volume_mount.sub_path,
                })
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'modal_environment_name': modal_environment_name,
            'modal_region': modal_region,
            'modal_gpu': modal_gpu,
            'modal_cpu': modal_cpu,
            'modal_memory': modal_memory,
            # Spot is Modal's default, preemptible container. GPU containers
            # have no other kind.
            'modal_nonpreemptible': not resources.use_spot,
            'modal_timeout': _MAX_CLUSTER_LIFETIME_HOURS * 60 * 60,
            'modal_docker_image': resources.extract_docker_image(),
            'modal_volume_mounts': modal_volume_mounts,
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
            # modal_catalog imports sky.clouds, so a top-level import here
            # would be circular.
            # pylint: disable=import-outside-toplevel
            from sky.catalog import modal_catalog
            if (not resources.use_spot and modal_catalog.is_gpu_instance_type(
                    resources.instance_type)):
                return resources_utils.FeasibleResources(
                    [], [], modal_catalog.GPU_SPOT_ONLY_HINT)
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
        if not resources.use_spot:
            # pylint: disable=import-outside-toplevel
            from sky.catalog import modal_catalog
            return resources_utils.FeasibleResources(
                [], [], modal_catalog.GPU_SPOT_ONLY_HINT)
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
        env_token_id = os.environ.get(_TOKEN_ID_ENV_VAR)
        env_token_secret = os.environ.get(_TOKEN_SECRET_ENV_VAR)
        if env_token_id or env_token_secret:
            if not (env_token_id and env_token_secret):
                return False, (
                    'Incomplete Modal credentials. Set both MODAL_TOKEN_ID and '
                    'MODAL_TOKEN_SECRET, or run `modal token new`.')
        elif not token_id or not token_secret:
            return False, (
                'Modal credentials were not found. Run `modal token new` '
                'or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.')
        return cls._verify_token()

    @classmethod
    def _verify_token(cls) -> Tuple[bool, Optional[str]]:
        """Asks Modal whether the token still works.

        A token being present says little: Modal tokens can expire (a
        workspace may cap their lifetime) and can be revoked. Without this
        the cloud shows as enabled and the first launch fails instead.
        """
        try:
            # Not get_user_identities(): that caches the last good answer.
            modal_adaptor.modal.Workspace.from_context().hydrate()
        except Exception as e:  # pylint: disable=broad-except
            reason = common_utils.format_exception(e, use_bracket=True)
            # Looked up by name: getting the class from the SDK could itself
            # raise here.
            if type(e).__name__ == 'AuthError':
                return False, (
                    f'Modal rejected the credentials: {reason}\n'
                    '    Create a new token with `modal token new` or set '
                    'MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.')
            return False, f'Failed to verify the Modal credentials: {reason}'
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        if (os.environ.get(_TOKEN_ID_ENV_VAR) and
                os.environ.get(_TOKEN_SECRET_ENV_VAR)):
            return {}
        # For clusters on other clouds, e.g. a jobs controller that launches
        # on Modal. Modal clusters do not get it: see
        # schemas.get_default_remote_identity().
        # modal resolves its config file the same way (MODAL_CONFIG_PATH, else
        # ~/.modal.toml); the cluster reads the default path. Resolved here
        # without importing modal: this runs for every enabled cloud at
        # launch, also where skypilot[modal] is not installed.
        credential_file = os.path.expanduser(
            os.environ.get(_CONFIG_PATH_ENV_VAR) or _CREDENTIAL_FILE)
        if os.path.exists(credential_file):
            return {_CREDENTIAL_FILE: credential_file}
        return {}

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'modal')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='modal')

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return catalog.regions(clouds='modal')

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # Keyed on the config version, so a token for another workspace is
        # picked up without restarting the process.
        return cls._get_user_identities(modal_adaptor.modal.config_version())

    @classmethod
    @annotations.lru_cache(scope='global', maxsize=1)
    def _get_user_identities(cls,
                             config_version: int) -> Optional[List[List[str]]]:
        del config_version  # Only used as the cache key.
        try:
            workspace = modal_adaptor.modal.Workspace.from_context()
            workspace.hydrate()
            workspace_name = workspace.name
            workspace_uuid = getattr(workspace, 'local_uuid', None)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to get Modal workspace identity.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e
        identity = workspace_name or workspace_uuid
        if identity is None:
            return None
        workspace_identity = f'Modal workspace {identity}'
        environment_name = cls._get_active_environment_name()
        # Owner checks compare element-wise, so a cluster launched in
        # another environment of the same workspace still matches on the
        # second element (with a warning), as for AWS IAM users of one
        # account. Each cluster stores its own environment, so it stays
        # manageable after a switch. Another workspace never matches.
        return [[
            f'{workspace_identity}, environment {environment_name}',
            workspace_identity
        ]]

    @classmethod
    def get_active_user_identity_str(cls) -> Optional[str]:
        # The workspace is already part of the first element.
        identity = cls.get_active_user_identity()
        if identity is None:
            return None
        return identity[0]

    @classmethod
    def _get_active_environment_name(cls) -> str:
        """The Modal Environment new clusters and volumes go to.

        Asked from Modal once per request: this is called for every launch
        attempt and again after provisioning, and those calls must agree.
        """
        return cls._active_environment_name(
            modal_adaptor.modal.config_version())

    @classmethod
    @annotations.lru_cache(scope='request', maxsize=1)
    def _active_environment_name(cls, config_version: int) -> str:
        del config_version  # Only used as the cache key.
        try:
            # Set with `modal config set-environment` or MODAL_ENVIRONMENT.
            configured = modal_adaptor.modal.config.config.get('environment')
            if configured:
                return configured
            # Not set: the workspace's default Environment, which only Modal
            # knows.
            environment = modal_adaptor.modal.Environment.from_context()
            environment.hydrate()
            return environment.name
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to get Modal environment identity.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e
