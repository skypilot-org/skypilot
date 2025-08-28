"""Resources: compute requirements of Tasks."""
import collections
import dataclasses
import re
import textwrap
import typing
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

import colorama

from sky import catalog
from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.clouds import cloud as sky_cloud
from sky.provision import docker_utils
from sky.provision.gcp import constants as gcp_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.nebius import constants as nebius_constants
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.utils import accelerator_registry
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import infra_utils
from sky.utils import log_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.utils import volume as volume_lib

logger = sky_logging.init_logger(__name__)

DEFAULT_DISK_SIZE_GB = 256

RESOURCE_CONFIG_ALIASES = {
    'gpus': 'accelerators',
}

MEMORY_SIZE_UNITS = {
    'b': 1,
    'k': 2**10,
    'kb': 2**10,
    'm': 2**20,
    'mb': 2**20,
    'g': 2**30,
    'gb': 2**30,
    't': 2**40,
    'tb': 2**40,
    'p': 2**50,
    'pb': 2**50,
}


@dataclasses.dataclass
class AutostopConfig:
    """Configuration for autostop."""
    # enabled isn't present in the yaml config, but it's needed for this class
    # to be complete.
    enabled: bool
    # If enabled is False, these values are ignored.
    # Keep the default value to 0 to make the behavior consistent with the CLI
    # flags.
    idle_minutes: int = 0
    down: bool = False
    wait_for: Optional[autostop_lib.AutostopWaitFor] = None

    def to_yaml_config(self) -> Union[Literal[False], Dict[str, Any]]:
        if not self.enabled:
            return False
        config: Dict[str, Any] = {
            'idle_minutes': self.idle_minutes,
            'down': self.down,
        }
        if self.wait_for is not None:
            config['wait_for'] = self.wait_for.value
        return config

    @classmethod
    def from_yaml_config(
        cls, config: Union[bool, int, str, Dict[str, Any], None]
    ) -> Optional['AutostopConfig']:
        if isinstance(config, bool):
            if config:
                return cls(enabled=True)
            else:
                return cls(enabled=False)

        if isinstance(config, int):
            return cls(idle_minutes=config, down=False, enabled=True)

        if isinstance(config, str):
            return cls(idle_minutes=resources_utils.parse_time_minutes(config),
                       down=False,
                       enabled=True)

        if isinstance(config, dict):
            # If we have a dict, autostop is enabled. (Only way to disable is
            # with `false`, a bool.)
            autostop_config = cls(enabled=True)
            if 'idle_minutes' in config:
                autostop_config.idle_minutes = config['idle_minutes']
            if 'down' in config:
                autostop_config.down = config['down']
            if 'wait_for' in config:
                autostop_config.wait_for = (
                    autostop_lib.AutostopWaitFor.from_str(config['wait_for']))
            return autostop_config

        return None


class Resources:
    """Resources: compute requirements of Tasks.

    This class is immutable once created (to ensure some validations are done
    whenever properties change). To update the property of an instance of
    Resources, use ``resources.copy(**new_properties)``.

    Used:

    * for representing resource requests for tasks/apps
    * as a "filter" to get concrete launchable instances
    * for calculating billing
    * for provisioning on a cloud

    """
    # If any fields changed, increment the version. For backward compatibility,
    # modify the __setstate__ method to handle the old version.
    _VERSION = 28

    def __init__(
        self,
        cloud: Optional[clouds.Cloud] = None,
        instance_type: Optional[str] = None,
        cpus: Union[None, int, float, str] = None,
        memory: Union[None, int, float, str] = None,
        accelerators: Union[None, str, Dict[str, Union[int, float]]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        infra: Optional[str] = None,
        use_spot: Optional[bool] = None,
        job_recovery: Optional[Union[Dict[str, Optional[Union[str, int]]],
                                     str]] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        image_id: Union[Dict[Optional[str], str], str, None] = None,
        disk_size: Optional[Union[str, int]] = None,
        disk_tier: Optional[Union[str, resources_utils.DiskTier]] = None,
        network_tier: Optional[Union[str, resources_utils.NetworkTier]] = None,
        ports: Optional[Union[int, str, List[str], Tuple[str]]] = None,
        labels: Optional[Dict[str, str]] = None,
        autostop: Union[bool, int, str, Dict[str, Any], None] = None,
        priority: Optional[int] = None,
        volumes: Optional[List[Dict[str, Any]]] = None,
        # Internal use only.
        # pylint: disable=invalid-name
        _docker_login_config: Optional[docker_utils.DockerLoginConfig] = None,
        _docker_username_for_runpod: Optional[str] = None,
        _is_image_managed: Optional[bool] = None,
        _requires_fuse: Optional[bool] = None,
        _cluster_config_overrides: Optional[Dict[str, Any]] = None,
        _no_missing_accel_warnings: Optional[bool] = None,
    ):
        """Initialize a Resources object.

        All fields are optional.  ``Resources.is_launchable`` decides whether
        the Resources is fully specified to launch an instance.

        Examples:
          .. code-block:: python

            # Fully specified cloud and instance type (is_launchable() is True).
            sky.Resources(infra='aws', instance_type='p3.2xlarge')
            sky.Resources(infra='k8s/my-cluster-ctx', accelerators='V100')
            sky.Resources(infra='gcp/us-central1', accelerators='V100')

            # Specifying required resources; the system decides the
            # cloud/instance type. The below are equivalent:
            sky.Resources(accelerators='V100')
            sky.Resources(accelerators='V100:1')
            sky.Resources(accelerators={'V100': 1})
            sky.Resources(cpus='2+', memory='16+', accelerators='V100')


        Args:
          cloud: the cloud to use. Deprecated. Use `infra` instead.
          instance_type: the instance type to use.
          cpus: the number of CPUs required for the task.
            If a str, must be a string of the form ``'2'`` or ``'2+'``, where
            the ``+`` indicates that the task requires at least 2 CPUs.
          memory: the amount of memory in GiB required. If a
            str, must be a string of the form ``'16'`` or ``'16+'``, where
            the ``+`` indicates that the task requires at least 16 GB of memory.
          accelerators: the accelerators required. If a str, must be
            a string of the form ``'V100'`` or ``'V100:2'``, where the ``:2``
            indicates that the task requires 2 V100 GPUs. If a dict, must be a
            dict of the form ``{'V100': 2}`` or ``{'tpu-v2-8': 1}``.
          accelerator_args: accelerator-specific arguments. For example,
            ``{'tpu_vm': True, 'runtime_version': 'tpu-vm-base'}`` for TPUs.
          infra: a string specifying the infrastructure to use, in the format
            of "cloud/region" or "cloud/region/zone". For example,
            `aws/us-east-1` or `k8s/my-cluster-ctx`. This is an alternative to
            specifying cloud, region, and zone separately. If provided, it
            takes precedence over cloud, region, and zone parameters.
          use_spot: whether to use spot instances. If None, defaults to
            False.
          job_recovery: the job recovery strategy to use for the managed
            job to recover the cluster from preemption. Refer to
            `recovery_strategy module <https://github.com/skypilot-org/skypilot/blob/master/sky/jobs/recovery_strategy.py>`__ # pylint: disable=line-too-long
            for more details.
            When a dict is provided, it can have the following fields:

            - strategy: the recovery strategy to use.
            - max_restarts_on_errors: the max number of restarts on user code
              errors.

          region: the region to use. Deprecated. Use `infra` instead.
          zone: the zone to use. Deprecated. Use `infra` instead.
          image_id: the image ID to use. If a str, must be a string
            of the image id from the cloud, such as AWS:
            ``'ami-1234567890abcdef0'``, GCP:
            ``'projects/my-project-id/global/images/my-image-name'``;
            Or, a image tag provided by SkyPilot, such as AWS:
            ``'skypilot:gpu-ubuntu-2004'``. If a dict, must be a dict mapping
            from region to image ID, such as:

            .. code-block:: python

              {
                'us-west1': 'ami-1234567890abcdef0',
                'us-east1': 'ami-1234567890abcdef0'
              }

          disk_size: the size of the OS disk in GiB.
          disk_tier: the disk performance tier to use. If None, defaults to
            ``'medium'``.
          network_tier: the network performance tier to use. If None, defaults to
            ``'standard'``.
          ports: the ports to open on the instance.
          labels: the labels to apply to the instance. These are useful for
            assigning metadata that may be used by external tools.
            Implementation depends on the chosen cloud - On AWS, labels map to
            instance tags. On GCP, labels map to instance labels. On
            Kubernetes, labels map to pod labels. On other clouds, labels are
            not supported and will be ignored.
          autostop: the autostop configuration to use. For launched resources,
            may or may not correspond to the actual current autostop config.
          priority: the priority for this resource configuration. Must be an
            integer from -1000 to 1000, where higher values indicate higher priority.
            If None, no priority is set.
          volumes: the volumes to mount on the instance.
          _docker_login_config: the docker configuration to use. This includes
            the docker username, password, and registry server. If None, skip
            docker login.
          _docker_username_for_runpod: the login username for the docker
            containers. This is used by RunPod to set the ssh user for the
            docker containers.
          _requires_fuse: whether the task requires FUSE mounting support. This
            is used internally by certain cloud implementations to do additional
            setup for FUSE mounting. This flag also safeguards against using
            FUSE mounting on existing clusters that do not support it. If None,
            defaults to False.

        Raises:
            ValueError: if some attributes are invalid.
            exceptions.NoCloudAccessError: if no public cloud is enabled.
        """
        self._version = self._VERSION

        if infra is not None and (cloud is not None or region is not None or
                                  zone is not None):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Cannot specify both `infra` and `cloud`, '
                                 '`region`, or `zone` parameters. '
                                 f'Got: infra={infra}, cloud={cloud}, '
                                 f'region={region}, zone={zone}')

        # Infra is user facing, and cloud, region, zone in parameters are for
        # backward compatibility. Internally, we keep using cloud, region, zone
        # for simplicity.
        if infra is not None:
            infra_info = infra_utils.InfraInfo.from_str(infra)
            # Infra takes precedence over individually specified parameters
            cloud = registry.CLOUD_REGISTRY.from_str(infra_info.cloud)
            region = infra_info.region
            zone = infra_info.zone

        self._cloud = cloud
        self._region: Optional[str] = region
        self._zone: Optional[str] = zone

        self._instance_type = instance_type

        self._use_spot_specified = use_spot is not None
        self._use_spot = use_spot if use_spot is not None else False
        self._job_recovery: Optional[Dict[str, Optional[Union[str,
                                                              int]]]] = None
        if job_recovery is not None:
            if isinstance(job_recovery, str):
                job_recovery = {'strategy': job_recovery}
            if 'strategy' not in job_recovery:
                job_recovery['strategy'] = None

            strategy_name = job_recovery['strategy']
            if strategy_name == 'none':
                self._job_recovery = None
            else:
                if isinstance(strategy_name, str):
                    job_recovery['strategy'] = strategy_name.upper()
                self._job_recovery = job_recovery

        if disk_size is not None:
            self._disk_size = int(
                resources_utils.parse_memory_resource(disk_size, 'disk_size'))
        else:
            self._disk_size = DEFAULT_DISK_SIZE_GB

        self._image_id: Optional[Dict[Optional[str], str]] = None
        if isinstance(image_id, str):
            self._image_id = {self._region: image_id.strip()}
        elif isinstance(image_id, dict):
            if None in image_id:
                self._image_id = {self._region: image_id[None].strip()}
            else:
                self._image_id = {
                    typing.cast(str, k).strip(): v.strip()
                    for k, v in image_id.items()
                }
        else:
            self._image_id = image_id
        if isinstance(self._cloud, clouds.Kubernetes):
            _maybe_add_docker_prefix_to_image_id(self._image_id)
        self._is_image_managed = _is_image_managed

        if isinstance(disk_tier, str):
            disk_tier_str = str(disk_tier).lower()
            supported_tiers = [tier.value for tier in resources_utils.DiskTier]
            if disk_tier_str not in supported_tiers:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Invalid disk_tier {disk_tier_str!r}. '
                                     f'Disk tier must be one of '
                                     f'{", ".join(supported_tiers)}.')
            disk_tier = resources_utils.DiskTier(disk_tier_str)
        self._disk_tier = disk_tier

        if isinstance(network_tier, str):
            network_tier_str = str(network_tier).lower()
            supported_tiers = [
                tier.value for tier in resources_utils.NetworkTier
            ]
            if network_tier_str not in supported_tiers:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Invalid network_tier {network_tier_str!r}. '
                        f'Network tier must be one of '
                        f'{", ".join(supported_tiers)}.')
            network_tier = resources_utils.NetworkTier(network_tier_str)
        self._network_tier = network_tier

        if ports is not None:
            if isinstance(ports, tuple):
                ports = list(ports)
            if not isinstance(ports, list):
                ports = [str(ports)]
            ports = resources_utils.simplify_ports(
                [str(port) for port in ports])
            if not ports:
                # Set to None if empty. This is mainly for resources from
                # cli, which will comes in as an empty tuple.
                ports = None
        self._ports = ports

        self._labels = labels

        self._docker_login_config = _docker_login_config

        # TODO(andyl): This ctor param seems to be unused.
        # We always use `Task.set_resources` and `Resources.copy` to set the
        # `docker_username_for_runpod`. But to keep the consistency with
        # `_docker_login_config`, we keep it here.
        self._docker_username_for_runpod = _docker_username_for_runpod

        self._requires_fuse = _requires_fuse

        self._cluster_config_overrides = _cluster_config_overrides
        self._cached_repr: Optional[str] = None
        self._no_missing_accel_warnings = _no_missing_accel_warnings

        # Initialize _priority before calling the setter
        self._priority: Optional[int] = None

        self._set_cpus(cpus)
        self._set_memory(memory)
        self._set_accelerators(accelerators, accelerator_args)
        self._set_autostop_config(autostop)
        self._set_priority(priority)
        self._set_volumes(volumes)

    def validate(self):
        """Validate the resources and infer the missing fields if possible."""
        self._try_canonicalize_accelerators()
        self._try_validate_and_set_region_zone()
        self._try_validate_instance_type()
        self._try_validate_cpus_mem()
        self._try_validate_managed_job_attributes()
        self._try_validate_image_id()
        self._try_validate_disk_tier()
        self._try_validate_volumes()
        self._try_validate_ports()
        self._try_validate_labels()

    # When querying the accelerators inside this func (we call self.accelerators
    # which is a @property), we will check the cloud's catalog, which can error
    # if it fails to fetch some account specific catalog information (e.g., AWS
    # zone mapping). It is fine to use the default catalog as this function is
    # only for display purposes.
    @catalog.fallback_to_default_catalog
    def __repr__(self) -> str:
        """Returns a string representation for display.

        Examples:

            >>> sky.Resources(accelerators='V100')
            <Cloud>({'V100': 1})

            >>> sky.Resources(accelerators='V100', use_spot=True)
            <Cloud>([Spot], {'V100': 1})

            >>> sky.Resources(accelerators='V100',
            ...     use_spot=True, instance_type='p3.2xlarge')
            AWS(p3.2xlarge[Spot], {'V100': 1})

            >>> sky.Resources(accelerators='V100', instance_type='p3.2xlarge')
            AWS(p3.2xlarge, {'V100': 1})

            >>> sky.Resources(instance_type='p3.2xlarge')
            AWS(p3.2xlarge, {'V100': 1})

            >>> sky.Resources(disk_size=100)
            <Cloud>(disk_size=100)
        """
        if self._cached_repr is not None:
            return self._cached_repr
        accelerators = ''
        accelerator_args = ''
        if self.accelerators is not None:
            accelerators = f', {self.accelerators}'
            if self.accelerator_args is not None:
                accelerator_args = f', accelerator_args={self.accelerator_args}'

        cpus = ''
        if self._cpus is not None:
            cpus = f', cpus={self._cpus}'

        memory = ''
        if self.memory is not None:
            memory = f', mem={self.memory}'

        use_spot = ''
        if self.use_spot:
            use_spot = '[Spot]'

        image_id = ''
        if self.image_id is not None:
            if None in self.image_id:
                image_id = f', image_id={self.image_id[None]}'
            else:
                image_id = f', image_id={self.image_id}'

        disk_tier = ''
        if self.disk_tier is not None:
            disk_tier = f', disk_tier={self.disk_tier.value}'

        network_tier = ''
        if self.network_tier is not None:
            network_tier = f', network_tier={self.network_tier.value}'

        disk_size = ''
        if self.disk_size != DEFAULT_DISK_SIZE_GB:
            disk_size = f', disk_size={self.disk_size}'

        ports = ''
        if self.ports is not None:
            ports = f', ports={self.ports}'

        if self._instance_type is not None:
            instance_type = f'{self._instance_type}'
        else:
            instance_type = ''

        # Do not show region/zone here as `sky status -a` would show them as
        # separate columns. Also, Resources repr will be printed during
        # failover, and the region may be dynamically determined.
        hardware_str = (
            f'{instance_type}{use_spot}'
            f'{cpus}{memory}{accelerators}{accelerator_args}{image_id}'
            f'{disk_tier}{network_tier}{disk_size}{ports}')
        # It may have leading ',' (for example, instance_type not set) or empty
        # spaces.  Remove them.
        while hardware_str and hardware_str[0] in (',', ' '):
            hardware_str = hardware_str[1:]

        cloud_str = '<Cloud>'
        if self.cloud is not None:
            cloud_str = f'{self.cloud}'

        self._cached_repr = f'{cloud_str}({hardware_str})'
        return self._cached_repr

    @property
    def repr_with_region_zone(self) -> str:
        region_str = ''
        if self.region is not None:
            region_name = self.region
            if self.region.startswith('ssh-'):
                region_name = common_utils.removeprefix(self.region, 'ssh-')
            region_str = f', region={region_name}'
        zone_str = ''
        if self.zone is not None:
            zone_str = f', zone={self.zone}'
        repr_str = str(self)
        if repr_str.endswith(')'):
            repr_str = repr_str[:-1] + f'{region_str}{zone_str})'
        else:
            repr_str += f'{region_str}{zone_str}'
        return repr_str

    @property
    def infra(self) -> infra_utils.InfraInfo:
        cloud = str(self.cloud) if self.cloud is not None else None
        return infra_utils.InfraInfo(cloud, self.region, self.zone)

    @property
    def cloud(self) -> Optional[clouds.Cloud]:
        return self._cloud

    @property
    def region(self) -> Optional[str]:
        return self._region

    @property
    def zone(self) -> Optional[str]:
        return self._zone

    @property
    def instance_type(self) -> Optional[str]:
        return self._instance_type

    @property
    @annotations.lru_cache(scope='global', maxsize=1)
    def cpus(self) -> Optional[str]:
        """Returns the number of vCPUs that each instance must have.

        For example, cpus='4' means each instance must have exactly 4 vCPUs,
        and cpus='4+' means each instance must have at least 4 vCPUs.

        (Developer note: The cpus field is only used to select the instance type
        at launch time. Thus, Resources in the backend's ResourceHandle will
        always have the cpus field set to None.)
        """
        if self._cpus is not None:
            return self._cpus
        if self.cloud is not None and self._instance_type is not None:
            vcpus, _ = self.cloud.get_vcpus_mem_from_instance_type(
                self._instance_type)
            return str(vcpus)
        return None

    @property
    def memory(self) -> Optional[str]:
        """Returns the memory that each instance must have in GB.

        For example, memory='16' means each instance must have exactly 16GB
        memory; memory='16+' means each instance must have at least 16GB
        memory.

        (Developer note: The memory field is only used to select the instance
        type at launch time. Thus, Resources in the backend's ResourceHandle
        will always have the memory field set to None.)
        """
        return self._memory

    @property
    @annotations.lru_cache(scope='global', maxsize=1)
    def accelerators(self) -> Optional[Dict[str, Union[int, float]]]:
        """Returns the accelerators field directly or by inferring.

        For example, Resources(infra='aws', instance_type='p3.2xlarge') has its
        accelerators field set to None, but this function will infer {'V100': 1}
        from the instance type.
        """
        if self._accelerators is not None:
            return self._accelerators
        if self.cloud is not None and self._instance_type is not None:
            return self.cloud.get_accelerators_from_instance_type(
                self._instance_type)
        return None

    @property
    def accelerator_args(self) -> Optional[Dict[str, Any]]:
        return self._accelerator_args

    @property
    def use_spot(self) -> bool:
        return self._use_spot

    @property
    def use_spot_specified(self) -> bool:
        return self._use_spot_specified

    @property
    def job_recovery(self) -> Optional[Dict[str, Optional[Union[str, int]]]]:
        return self._job_recovery

    @property
    def disk_size(self) -> int:
        return self._disk_size

    @property
    def image_id(self) -> Optional[Dict[Optional[str], str]]:
        return self._image_id

    @property
    def disk_tier(self) -> Optional[resources_utils.DiskTier]:
        return self._disk_tier

    @property
    def network_tier(self) -> Optional[resources_utils.NetworkTier]:
        return self._network_tier

    @property
    def ports(self) -> Optional[List[str]]:
        return self._ports

    @property
    def labels(self) -> Optional[Dict[str, str]]:
        return self._labels

    @property
    def volumes(self) -> Optional[List[Dict[str, Any]]]:
        return self._volumes

    @property
    def autostop_config(self) -> Optional[AutostopConfig]:
        """The requested autostop config.

        Warning: This is the autostop config that was originally used to
        launch the resources. It may not correspond to the actual current
        autostop config.
        """
        return self._autostop_config

    @property
    def priority(self) -> Optional[int]:
        """The priority for this resource configuration.

        Higher values indicate higher priority. Valid range is -1000 to 1000.
        """
        return self._priority

    @property
    def is_image_managed(self) -> Optional[bool]:
        return self._is_image_managed

    @property
    def requires_fuse(self) -> bool:
        if self._requires_fuse is None:
            return False
        return self._requires_fuse

    @property
    def no_missing_accel_warnings(self) -> bool:
        """Returns whether to force quiet mode for this resource."""
        if self._no_missing_accel_warnings is None:
            return False
        return self._no_missing_accel_warnings

    def set_requires_fuse(self, value: bool) -> None:
        """Sets whether this resource requires FUSE mounting support.

        Args:
            value: Whether the resource requires FUSE mounting support.
        """
        # TODO(zeping): This violates the immutability of Resources.
        #  Refactor to use Resources.copy instead.
        self._requires_fuse = value

    @property
    def cluster_config_overrides(self) -> Dict[str, Any]:
        if self._cluster_config_overrides is None:
            return {}
        return self._cluster_config_overrides

    @property
    def docker_login_config(self) -> Optional[docker_utils.DockerLoginConfig]:
        return self._docker_login_config

    @property
    def docker_username_for_runpod(self) -> Optional[str]:
        return self._docker_username_for_runpod

    def _set_cpus(
        self,
        cpus: Union[None, int, float, str],
    ) -> None:
        if cpus is None:
            self._cpus = None
            return

        self._cpus = str(cpus)
        if isinstance(cpus, str):
            if cpus.endswith('+'):
                num_cpus_str = cpus[:-1]
            else:
                num_cpus_str = cpus

            try:
                num_cpus = float(num_cpus_str)
            except ValueError:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'The "cpus" field should be either a number or '
                        f'a string "<number>+". Found: {cpus!r}') from None
        else:
            num_cpus = float(cpus)

        if num_cpus <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'The "cpus" field should be positive. Found: {cpus!r}')

    def _set_memory(
        self,
        memory: Union[None, int, float, str],
    ) -> None:
        if memory is None:
            self._memory = None
            return

        memory = resources_utils.parse_memory_resource(str(memory),
                                                       'memory',
                                                       ret_type=float,
                                                       allow_plus=True,
                                                       allow_x=True)
        self._memory = memory
        if memory.endswith(('+', 'x')):
            # 'x' is used internally for make sure our resources used by
            # jobs controller (memory: 3x) to have enough memory based on
            # the vCPUs.
            num_memory_gb = memory[:-1]
        else:
            num_memory_gb = memory

        try:
            memory_gb = float(num_memory_gb)
        except ValueError:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'The "memory" field should be either a number or '
                    f'a string "<number>+". Found: {memory!r}') from None

        if memory_gb <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'The "memory" field should be positive. Found: {memory!r}')

    def _set_accelerators(
        self,
        accelerators: Union[None, str, Dict[str, Union[int, float]]],
        accelerator_args: Optional[Dict[str, Any]],
    ) -> None:
        """Sets accelerators.

        Args:
            accelerators: A string or a dict of accelerator types to counts.
            accelerator_args: A dict of accelerator types to args.
        """
        if accelerators is not None:
            if isinstance(accelerators, str):  # Convert to Dict[str, int].
                if ':' not in accelerators:
                    accelerators = {accelerators: 1}
                else:
                    assert isinstance(accelerators,
                                      str), (type(accelerators), accelerators)
                    splits = accelerators.split(':')
                    parse_error = ('The "accelerators" field as a str '
                                   'should be <name> or <name>:<cnt>. '
                                   f'Found: {accelerators!r}')
                    if len(splits) != 2:
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(parse_error)
                    try:
                        num = float(splits[1])
                        num = int(num) if num.is_integer() else num
                        accelerators = {splits[0]: num}
                    except ValueError:
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(parse_error) from None

            acc, _ = list(accelerators.items())[0]
            if 'tpu' in acc.lower():
                # TODO(syang): GCP TPU names are supported on both GCP and
                # kubernetes (GKE), but this logic automatically assumes
                # GCP TPUs can only be used on GCP.
                # Fix the logic such that GCP TPU names can failover between
                # GCP and kubernetes.
                if self.cloud is None:
                    if kubernetes_utils.is_tpu_on_gke(acc, normalize=False):
                        self._cloud = clouds.Kubernetes()
                    else:
                        self._cloud = clouds.GCP()
                assert self.cloud is not None and (
                    self.cloud.is_same_cloud(clouds.GCP()) or
                    self.cloud.is_same_cloud(clouds.Kubernetes())), (
                        'Cloud must be GCP or Kubernetes for TPU '
                        'accelerators.')

                if accelerator_args is None:
                    accelerator_args = {}

                use_tpu_vm = accelerator_args.get('tpu_vm', True)
                if (self.cloud.is_same_cloud(clouds.GCP()) and
                        not kubernetes_utils.is_tpu_on_gke(acc,
                                                           normalize=False)):
                    if 'runtime_version' not in accelerator_args:

                        def _get_default_runtime_version() -> str:
                            if not use_tpu_vm:
                                return '2.12.0'
                            # TPU V5 requires a newer runtime version.
                            if acc.startswith('tpu-v5'):
                                return 'v2-alpha-tpuv5'
                            # TPU V6e requires a newer runtime version.
                            elif acc.startswith('tpu-v6e'):
                                return 'v2-alpha-tpuv6e'
                            return 'tpu-vm-base'

                        accelerator_args['runtime_version'] = (
                            _get_default_runtime_version())
                        logger.info(
                            'Missing runtime_version in accelerator_args, using'
                            f' default ({accelerator_args["runtime_version"]})')

                    if self.instance_type is not None and use_tpu_vm:
                        if self.instance_type != 'TPU-VM':
                            with ux_utils.print_exception_no_traceback():
                                raise ValueError(
                                    'Cannot specify instance type (got '
                                    f'{self.instance_type!r}) for TPU VM.')

        self._accelerators: Optional[Dict[str, Union[int,
                                                     float]]] = accelerators
        self._accelerator_args: Optional[Dict[str, Any]] = accelerator_args

    def _set_autostop_config(
        self,
        autostop: Union[bool, int, str, Dict[str, Any], None],
    ) -> None:
        self._autostop_config = AutostopConfig.from_yaml_config(autostop)

    def _set_priority(self, priority: Optional[int]) -> None:
        """Sets the priority for this resource configuration.

        Args:
            priority: Priority value from -1000 to 1000, where higher values
                indicate higher priority. If None, no priority is set.
        """
        if priority is not None:
            if not constants.MIN_PRIORITY <= priority <= constants.MAX_PRIORITY:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Priority must be between {constants.MIN_PRIORITY} and'
                        f' {constants.MAX_PRIORITY}. Found: {priority}')
        self._priority = priority

    def _set_volumes(
        self,
        volumes: Optional[List[Dict[str, Any]]],
    ) -> None:
        if not volumes:
            self._volumes = None
            return
        valid_volumes = []
        supported_tiers = [tier.value for tier in resources_utils.DiskTier]
        supported_storage_types = [
            storage_type.value for storage_type in resources_utils.StorageType
        ]
        supported_attach_modes = [
            attach_mode.value for attach_mode in resources_utils.DiskAttachMode
        ]
        network_type = resources_utils.StorageType.NETWORK
        read_write_mode = resources_utils.DiskAttachMode.READ_WRITE
        for volume in volumes:
            if 'path' not in volume:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Invalid volume {volume!r}. '
                                     f'Volume must have a "path" field.')
            if 'storage_type' not in volume:
                volume['storage_type'] = network_type
            else:
                if isinstance(volume['storage_type'], str):
                    storage_type_str = str(volume['storage_type']).lower()
                    if storage_type_str not in supported_storage_types:
                        logger.warning(
                            f'Invalid storage_type {storage_type_str!r}. '
                            f'Set it to '
                            f'{network_type.value}.')
                        volume['storage_type'] = network_type
                    else:
                        volume['storage_type'] = resources_utils.StorageType(
                            storage_type_str)
            if 'auto_delete' not in volume:
                volume['auto_delete'] = False
            if 'attach_mode' in volume:
                if isinstance(volume['attach_mode'], str):
                    attach_mode_str = str(volume['attach_mode']).lower()
                    if attach_mode_str not in supported_attach_modes:
                        logger.warning(
                            f'Invalid attach_mode {attach_mode_str!r}. '
                            f'Set it to {read_write_mode.value}.')
                        volume['attach_mode'] = read_write_mode
                    else:
                        volume['attach_mode'] = resources_utils.DiskAttachMode(
                            attach_mode_str)
            else:
                volume['attach_mode'] = read_write_mode
            if volume['storage_type'] == network_type:
                # TODO(luca): add units to this disk_size as well
                if ('disk_size' in volume and
                        round(volume['disk_size']) != volume['disk_size']):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Volume size must be an integer. '
                                         f'Got: {volume["size"]}.')
                if 'name' not in volume:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Network volume {volume["path"]} '
                                         f'must have "name" field.')
            elif 'name' in volume:
                logger.info(f'Volume {volume["path"]} is a local disk. '
                            f'The "name" field will be ignored.')
                del volume['name']
            if 'disk_tier' in volume:
                if isinstance(volume['disk_tier'], str):
                    disk_tier_str = str(volume['disk_tier']).lower()
                    if disk_tier_str not in supported_tiers:
                        logger.warning(
                            f'Invalid disk_tier {disk_tier_str!r}. '
                            f'Set it to {resources_utils.DiskTier.BEST.value}.')
                        volume['disk_tier'] = resources_utils.DiskTier.BEST
                    else:
                        volume['disk_tier'] = resources_utils.DiskTier(
                            disk_tier_str)
            elif volume['storage_type'] == network_type:
                logger.debug(
                    f'No disk_tier specified for volume {volume["path"]}. '
                    f'Set it to {resources_utils.DiskTier.BEST.value}.')
                volume['disk_tier'] = resources_utils.DiskTier.BEST

            valid_volumes.append(volume)
        self._volumes = valid_volumes

    def override_autostop_config(
            self,
            down: bool = False,
            idle_minutes: Optional[int] = None,
            wait_for: Optional[autostop_lib.AutostopWaitFor] = None) -> None:
        """Override autostop config to the resource.

        Args:
            down: If true, override the autostop config to use autodown.
            idle_minutes: If not None, override the idle minutes to autostop or
                autodown.
            wait_for: If not None, override the wait mode.
        """
        if not down and idle_minutes is None:
            return
        if self._autostop_config is None:
            self._autostop_config = AutostopConfig(enabled=True,)
        if down:
            self._autostop_config.down = down
        if idle_minutes is not None:
            self._autostop_config.idle_minutes = idle_minutes
        if wait_for is not None:
            self._autostop_config.wait_for = wait_for

    def is_launchable(self) -> bool:
        """Returns whether the resource is launchable."""
        return self.cloud is not None and self._instance_type is not None

    def assert_launchable(self) -> 'LaunchableResources':
        """A workaround to make mypy understand that is_launchable() is true.

        Note: The `cast` to `LaunchableResources` is only for static type
        checking with MyPy. At runtime, the Python interpreter does not enforce
        types, and the returned object will still be an instance of `Resources`.
        """
        assert self.is_launchable(), self
        return typing.cast(LaunchableResources, self)

    def need_cleanup_after_preemption_or_failure(self) -> bool:
        """Whether a resource needs cleanup after preemption or failure."""
        assert self.is_launchable(), self
        assert self.cloud is not None, 'Cloud must be specified'
        return self.cloud.need_cleanup_after_preemption_or_failure(self)

    def _try_canonicalize_accelerators(self) -> None:
        """Try to canonicalize the accelerators attribute.

        We don't canonicalize accelerators during creation of Resources object
        because it may check Kubernetes accelerators online. It requires
        Kubernetes credentias which may not be available locally when a remote
        API server is used.
        """
        if self._accelerators is None:
            return
        self._accelerators = {
            accelerator_registry.canonicalize_accelerator_name(
                acc, self._cloud): acc_count
            for acc, acc_count in self._accelerators.items()
        }

    def _try_validate_and_set_region_zone(self) -> None:
        """Try to validate and set the region and zone attribute.

        Raises:
            ValueError: if the attributes are invalid.
            exceptions.NoCloudAccessError: if no public cloud is enabled.
        """
        if self._region is None and self._zone is None:
            return

        if self._cloud is None:
            # Try to infer the cloud from region/zone, if unique. If 0 or >1
            # cloud corresponds to region/zone, errors out.
            valid_clouds = []
            enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
                sky_cloud.CloudCapability.COMPUTE,
                raise_if_no_cloud_access=True)
            cloud_to_errors = {}
            for cloud in enabled_clouds:
                try:
                    cloud.validate_region_zone(self._region, self._zone)
                except ValueError as e:
                    cloud_to_errors[repr(cloud)] = e
                    continue
                valid_clouds.append(cloud)

            if not valid_clouds:
                if len(enabled_clouds) == 1:
                    cloud_str = f'for cloud {enabled_clouds[0]}'
                else:
                    cloud_str = f'for any cloud among {enabled_clouds}'
                with ux_utils.print_exception_no_traceback():
                    if len(cloud_to_errors) == 1:
                        # UX: if 1 cloud, don't print a table.
                        hint = list(cloud_to_errors.items())[0][-1]
                    else:
                        table = log_utils.create_table(['Cloud', 'Hint'])
                        table.add_row(['-----', '----'])
                        for cloud_msg, error in cloud_to_errors.items():
                            reason_str = '\n'.join(textwrap.wrap(
                                str(error), 80))
                            table.add_row([cloud_msg, reason_str])
                        hint = table.get_string()
                    raise ValueError(
                        f'Invalid (region {self._region!r}, zone '
                        f'{self._zone!r}) {cloud_str}. Details:\n{hint}')
            elif len(valid_clouds) > 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Cannot infer cloud from (region {self._region!r}, '
                        f'zone {self._zone!r}). Multiple enabled clouds '
                        f'have region/zone of the same names: {valid_clouds}. '
                        f'To fix: explicitly specify `cloud`.')
            logger.debug(f'Cloud is not specified, using {valid_clouds[0]} '
                         f'inferred from region {self._region!r} and zone '
                         f'{self._zone!r}')
            self._cloud = valid_clouds[0]

        # Validate if region and zone exist in the catalog, and set the region
        # if zone is specified.
        self._region, self._zone = self._cloud.validate_region_zone(
            self._region, self._zone)

    def get_valid_regions_for_launchable(self) -> List[clouds.Region]:
        """Returns a set of `Region`s that can provision this Resources.

        Each `Region` has a list of `Zone`s that can provision this Resources.

        (Internal) This function respects any config in skypilot_config that
        may have restricted the regions to be considered (e.g., a
        ssh_proxy_command dict with region names as keys).
        """
        assert self.is_launchable(), self
        assert self.cloud is not None, 'Cloud must be specified'
        assert self._instance_type is not None, (
            'Instance type must be specified')
        regions = self.cloud.regions_with_offering(self._instance_type,
                                                   self.accelerators,
                                                   self._use_spot, self._region,
                                                   self._zone)
        if self._image_id is not None and None not in self._image_id:
            regions = [r for r in regions if r.name in self._image_id]

        # Filter the regions by the skypilot_config
        ssh_proxy_command_config = skypilot_config.get_effective_region_config(
            cloud=str(self._cloud).lower(),
            region=None,
            keys=('ssh_proxy_command',),
            default_value=None)
        if (isinstance(ssh_proxy_command_config, str) or
                ssh_proxy_command_config is None):
            # All regions are valid as the regions are not specified for the
            # ssh_proxy_command config.
            return regions

        # ssh_proxy_command_config: Dict[str, str], region_name -> command
        # This type check is done by skypilot_config at config load time.
        filtered_regions = []
        for region in regions:
            region_name = region.name
            if region_name not in ssh_proxy_command_config:
                continue
            # TODO: filter out the zones not available in the vpc_name
            filtered_regions.append(region)

        # Friendlier UX. Otherwise users only get a generic
        # ResourcesUnavailableError message without mentioning
        # ssh_proxy_command.
        if not filtered_regions:
            yellow = colorama.Fore.YELLOW
            reset = colorama.Style.RESET_ALL
            logger.warning(
                f'{yellow}Request {self} cannot be satisfied by any feasible '
                'region. To fix, check that ssh_proxy_command\'s region keys '
                f'include the regions to use.{reset}')

        return filtered_regions

    def _try_validate_instance_type(self) -> None:
        """Try to validate the instance type attribute.

        Raises:
            ValueError: if the attribute is invalid.
            exceptions.NoCloudAccessError: if no public cloud is enabled.
        """
        if self.instance_type is None:
            return

        # Validate instance type
        if self.cloud is not None:
            valid = self.cloud.instance_type_exists(self._instance_type)
            if not valid:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Invalid instance type {self._instance_type!r} '
                        f'for cloud {self.cloud}.')
        else:
            # If cloud not specified
            valid_clouds = []
            enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
                sky_cloud.CloudCapability.COMPUTE,
                raise_if_no_cloud_access=True)
            for cloud in enabled_clouds:
                if cloud.instance_type_exists(self._instance_type):
                    valid_clouds.append(cloud)
            if not valid_clouds:
                if len(enabled_clouds) == 1:
                    cloud_str = f'for cloud {enabled_clouds[0]}'
                else:
                    cloud_str = f'for any cloud among {enabled_clouds}'
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Invalid instance type {self._instance_type!r} '
                        f'{cloud_str}.')
            if len(valid_clouds) > 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Ambiguous instance type {self._instance_type!r}. '
                        f'Please specify cloud explicitly among {valid_clouds}.'
                    )
            logger.debug(
                f'Cloud is not specified, using {valid_clouds[0]} '
                f'inferred from the instance_type {self.instance_type!r}.')
            self._cloud = valid_clouds[0]

    def _try_validate_cpus_mem(self) -> None:
        """Try to validate the cpus and memory attributes.

        Raises:
            ValueError: if the attributes are invalid.
        """
        if self._cpus is None and self._memory is None:
            return
        if self._instance_type is not None:
            # The assertion should be true because we have already executed
            # _try_validate_instance_type() before this method.
            # The _try_validate_instance_type() method infers and sets
            # self.cloud if self.instance_type is not None.
            assert self.cloud is not None
            cpus, mem = self.cloud.get_vcpus_mem_from_instance_type(
                self._instance_type)
            if self._cpus is not None:
                assert cpus is not None, (
                    f'Can\'t get vCPUs from instance type: '
                    f'{self._instance_type}, check catalog or '
                    f'specify cpus directly.')
                if self._cpus.endswith('+'):
                    if cpus < float(self._cpus[:-1]):
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                f'{self.instance_type} does not have enough '
                                f'vCPUs. {self.instance_type} has {cpus} '
                                f'vCPUs, but {self._cpus} is requested.')
                elif cpus != float(self._cpus):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'{self.instance_type} does not have the requested '
                            f'number of vCPUs. {self.instance_type} has {cpus} '
                            f'vCPUs, but {self._cpus} is requested.')
            if self.memory is not None:
                assert mem is not None, (
                    f'Can\'t get memory from instance type: '
                    f'{self._instance_type}, check catalog or '
                    f'specify memory directly.')
                if self.memory.endswith(('+', 'x')):
                    if mem < float(self.memory[:-1]):
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                f'{self.instance_type} does not have enough '
                                f'memory. {self.instance_type} has {mem} GB '
                                f'memory, but {self.memory} is requested.')
                elif mem != float(self.memory):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'{self.instance_type} does not have the requested '
                            f'memory. {self.instance_type} has {mem} GB '
                            f'memory, but {self.memory} is requested.')

    def _try_validate_managed_job_attributes(self) -> None:
        """Try to validate managed job related attributes.

        Raises:
            ValueError: if the attributes are invalid.
        """
        if self._job_recovery is None or self._job_recovery['strategy'] is None:
            return
        # Validate the job recovery strategy
        assert isinstance(self._job_recovery['strategy'],
                          str), 'Job recovery strategy must be a string'
        registry.JOBS_RECOVERY_STRATEGY_REGISTRY.from_str(
            self._job_recovery['strategy'])

    def extract_docker_image(self) -> Optional[str]:
        if self.image_id is None:
            return None
        # Handle dict image_id
        if len(self.image_id) == 1:
            # Check if the single key matches the region or is None (any region)
            image_key = list(self.image_id.keys())[0]
            if image_key == self.region or image_key is None:
                image_id = self.image_id[image_key]
                if image_id.startswith('docker:'):
                    return image_id[len('docker:'):]
        return None

    def _try_validate_image_id(self) -> None:
        """Try to validate the image_id attribute.

        Raises:
            ValueError: if the attribute is invalid.
        """

        if self._network_tier == resources_utils.NetworkTier.BEST:
            if isinstance(self._cloud, clouds.GCP):
                # Handle GPU Direct TCPX requirement for docker images
                if self._image_id is None:
                    self._image_id = {
                        self._region: gcp_constants.GCP_GPU_DIRECT_IMAGE_ID
                    }
            elif isinstance(self._cloud, clouds.Nebius):
                if self._image_id is None:
                    self._image_id = {
                        self._region: nebius_constants.INFINIBAND_IMAGE_ID
                    }
            elif self._image_id:
                # Custom image specified - validate it's a docker image
                # Check if any of the specified images are not docker images
                non_docker_images = []
                for region, image_id in self._image_id.items():
                    if not image_id.startswith('docker:'):
                        non_docker_images.append(
                            f'{image_id} (region: {region})')

                if non_docker_images:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'When using network_tier=BEST, image_id '
                            f'must be a docker image. '
                            f'Found non-docker images: '
                            f'{", ".join(non_docker_images)}. '
                            f'Please either: (1) use a docker image '
                            f'(prefix with "docker:"), or '
                            f'(2) leave image_id empty to use the default')

        if self._image_id is None:
            return

        if self.extract_docker_image() is not None:
            # TODO(tian): validate the docker image exists / of reasonable size
            if self.cloud is not None:
                self.cloud.check_features_are_supported(
                    self, {clouds.CloudImplementationFeatures.DOCKER_IMAGE})
            return

        if self.cloud is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cloud must be specified when image_id is provided.')

        try:
            self.cloud.check_features_are_supported(
                self,
                requested_features={
                    clouds.CloudImplementationFeatures.IMAGE_ID
                })
        except exceptions.NotSupportedError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'image_id is only supported for AWS/GCP/Azure/IBM/OCI/'
                    'Kubernetes, please explicitly specify the cloud.') from e

        if self._region is not None:
            # If the image_id has None as key (region-agnostic),
            # use it for any region
            if None in self._image_id:
                # Replace None key with the actual region
                self._image_id = {self._region: self._image_id[None]}
            elif self._region not in self._image_id:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'image_id {self._image_id} should contain the image '
                        f'for the specified region {self._region}.')
            else:
                # Narrow down the image_id to the specified region.
                self._image_id = {self._region: self._image_id[self._region]}

        # Check the image_id's are valid.
        for region, image_id in self._image_id.items():
            if (image_id.startswith('skypilot:') and
                    not self.cloud.is_image_tag_valid(image_id, region)):
                region_str = f' ({region})' if region else ''
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Image tag {image_id!r} is not valid, please make sure'
                        f' the tag exists in {self._cloud}{region_str}.')

            if (self.cloud.is_same_cloud(clouds.AWS()) and
                    not image_id.startswith('skypilot:') and region is None):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'image_id is only supported for AWS in a specific '
                        'region, please explicitly specify the region.')

        # Validate the image exists and the size is smaller than the disk size.
        for region, image_id in self._image_id.items():
            # Check the image exists and get the image size.
            # It will raise ValueError if the image does not exist.
            image_size = self.cloud.get_image_size(image_id, region)
            if image_size > self.disk_size:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Image {image_id!r} is {image_size}GB, which is '
                        f'larger than the specified disk_size: {self.disk_size}'
                        ' GB. Please specify a larger disk_size to use this '
                        'image.')

    def _try_validate_disk_tier(self) -> None:
        """Try to validate the disk_tier attribute.

        Raises:
            ValueError: if the attribute is invalid.
        """
        if self.disk_tier is None:
            return
        if self.cloud is not None:
            try:
                self.cloud.check_disk_tier_enabled(self.instance_type,
                                                   self.disk_tier)
            except exceptions.NotSupportedError:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Disk tier {self.disk_tier.value} is not supported '
                        f'for instance type {self.instance_type}.') from None

    def _try_validate_volumes(self) -> None:
        """Try to validate the volumes attribute.
        Raises:
            ValueError: if the attribute is invalid.
        """
        if self.volumes is None:
            return
        if self.cloud is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Cloud must be specified when '
                                 'volumes are provided.')
        if not self.cloud.is_same_cloud(clouds.GCP()):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Volumes are only supported for GCP'
                                 f' not for {self.cloud}.')

        need_region_or_zone = False
        try:
            for volume in self.volumes:
                if ('name' in volume and volume['storage_type']
                        == resources_utils.StorageType.NETWORK):
                    need_region_or_zone = True
                if 'disk_tier' not in volume:
                    continue
                # TODO(hailong): check instance local SSD
                # support for instance_type.
                # Refer to https://cloud.google.com/compute/docs/disks/local-ssd#machine-series-lssd # pylint: disable=line-too-long
                self.cloud.check_disk_tier_enabled(self.instance_type,
                                                   volume['disk_tier'])
            if (need_region_or_zone and self._region is None and
                    self._zone is None):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('When specifying the volume name, please'
                                     ' also specify the region or zone.')
        except exceptions.NotSupportedError:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Disk tier {volume["disk_tier"].value} is not '
                    f'supported for instance type {self.instance_type}.'
                ) from None

    def _try_validate_ports(self) -> None:
        """Try to validate the ports attribute.

        Raises:
            ValueError: if the attribute is invalid.
            exceptions.NoCloudAccessError: if no public cloud is enabled.
        """
        if self.ports is None:
            return
        if self.cloud is not None:
            self.cloud.check_features_are_supported(
                self, {clouds.CloudImplementationFeatures.OPEN_PORTS})
        else:
            at_least_one_cloud_supports_ports = False
            for cloud in sky_check.get_cached_enabled_clouds_or_refresh(
                    sky_cloud.CloudCapability.COMPUTE,
                    raise_if_no_cloud_access=True):
                try:
                    cloud.check_features_are_supported(
                        self, {clouds.CloudImplementationFeatures.OPEN_PORTS})
                    at_least_one_cloud_supports_ports = True
                except exceptions.NotSupportedError:
                    pass
            if not at_least_one_cloud_supports_ports:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'No enabled clouds support opening ports. To fix: '
                        'do not specify resources.ports, or enable a cloud '
                        'that does support this feature.')
        # We don't need to check the ports format since we already done it
        # in resources_utils.simplify_ports

    def _try_validate_labels(self) -> None:
        """Try to validate the labels attribute.

        Raises:
            ValueError: if the attribute is invalid.
        """
        if not self._labels:
            return
        if self.cloud is not None:
            validated_clouds = [self.cloud]
        else:
            # If no specific cloud is set, validate label against ALL clouds.
            # The label will be dropped if invalid for any one of the cloud
            validated_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
                sky_cloud.CloudCapability.COMPUTE)
        invalid_table = log_utils.create_table(['Label', 'Reason'])
        for key, value in self._labels.items():
            for cloud in validated_clouds:
                valid, err_msg = cloud.is_label_valid(key, value)
                if not valid:
                    invalid_table.add_row([
                        f'{key}: {value}',
                        f'Label rejected due to {cloud}: {err_msg}'
                    ])
                    break
        if invalid_table.rows:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'The following labels are invalid:'
                    '\n\t' + invalid_table.get_string().replace('\n', '\n\t'))

    def get_cost(self, seconds: float) -> float:
        """Returns cost in USD for the runtime in seconds."""
        hours = seconds / 3600
        # Instance.
        assert self.cloud is not None, 'Cloud must be specified'
        assert self._instance_type is not None, (
            'Instance type must be specified')
        hourly_cost = self.cloud.instance_type_to_hourly_cost(
            self._instance_type, self.use_spot, self._region, self._zone)
        # Accelerators (if any).
        if self.accelerators is not None:
            hourly_cost += self.cloud.accelerators_to_hourly_cost(
                self.accelerators, self.use_spot, self._region, self._zone)
        return hourly_cost * hours

    def get_accelerators_str(self) -> str:
        accelerators = self.accelerators
        if accelerators is None:
            accelerators = '-'
        elif isinstance(accelerators, dict) and len(accelerators) == 1:
            accelerators, count = list(accelerators.items())[0]
            accelerators = f'{accelerators}:{count}'
        return accelerators

    def get_spot_str(self) -> str:
        return '[Spot]' if self.use_spot else ''

    def make_deploy_variables(
        self,
        cluster_name: resources_utils.ClusterName,
        region: clouds.Region,
        zones: Optional[List[clouds.Zone]],
        num_nodes: int,
        dryrun: bool,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Optional[str]]:
        """Converts planned sky.Resources to resource variables.

        These variables are divided into two categories: cloud-specific and
        cloud-agnostic. The cloud-specific variables are generated by the
        cloud.make_deploy_resources_variables() method, and the cloud-agnostic
        variables are generated by this method.
        """
        # Initial setup commands
        initial_setup_commands = []
        if (skypilot_config.get_nested(
            ('nvidia_gpus', 'disable_ecc'),
                False,
                override_configs=self.cluster_config_overrides) and
                self.accelerators is not None):
            initial_setup_commands = [constants.DISABLE_GPU_ECC_COMMAND]

        docker_image = self.extract_docker_image()

        # Cloud specific variables
        assert self.cloud is not None, 'Cloud must be specified'
        cloud_specific_variables = self.cloud.make_deploy_resources_variables(
            self, cluster_name, region, zones, num_nodes, dryrun, volume_mounts)

        # TODO(andyl): Should we print some warnings if users' envs share
        # same names with the cloud specific variables, but not enabled
        # since it's not on the particular cloud?

        # Docker run options
        docker_run_options = skypilot_config.get_nested(
            ('docker', 'run_options'),
            default_value=[],
            override_configs=self.cluster_config_overrides)
        if isinstance(docker_run_options, str):
            docker_run_options = [docker_run_options]
        # Special accelerator runtime might require additional docker run
        # options. e.g., for TPU, we need --privileged.
        if 'docker_run_options' in cloud_specific_variables:
            docker_run_options.extend(
                cloud_specific_variables['docker_run_options'])
        if docker_run_options and isinstance(self.cloud, clouds.Kubernetes):
            logger.warning(
                f'{colorama.Style.DIM}Docker run options are specified, '
                'but ignored for Kubernetes: '
                f'{" ".join(docker_run_options)}'
                f'{colorama.Style.RESET_ALL}')
        return dict(
            cloud_specific_variables,
            **{
                # Docker config
                'docker_run_options': docker_run_options,
                # Docker image. The image name used to pull the image, e.g.
                # ubuntu:latest.
                'docker_image': docker_image,
                # Docker container name. The name of the container. Default to
                # `sky_container`.
                'docker_container_name':
                    constants.DEFAULT_DOCKER_CONTAINER_NAME,
                # Docker login config (if any). This helps pull the image from
                # private registries.
                'docker_login_config': self._docker_login_config,
                # Initial setup commands.
                'initial_setup_commands': initial_setup_commands,
            })

    def get_reservations_available_resources(self) -> Dict[str, int]:
        """Returns the number of available reservation resources."""
        if self.use_spot:
            # GCP's & AWS's reservations do not support spot instances. We
            # assume other clouds behave the same. We can move this check down
            # to each cloud if any cloud supports reservations for spot.
            return {}
        specific_reservations = set(
            skypilot_config.get_effective_region_config(
                cloud=str(self.cloud).lower(),
                region=self.region,
                keys=('specific_reservations',),
                default_value=set()))

        if isinstance(self.cloud, clouds.DummyCloud):
            return self.cloud.get_reservations_available_resources(
                instance_type='',
                region='',
                zone=None,
                specific_reservations=specific_reservations)

        assert (self.cloud is not None and self.instance_type is not None and
                self.region is not None), (
                    f'Cloud, instance type, region must be specified. '
                    f'Resources={self}, cloud={self.cloud}, '
                    f'instance_type={self.instance_type}, region={self.region}')
        return self.cloud.get_reservations_available_resources(
            self.instance_type, self.region, self.zone, specific_reservations)

    def less_demanding_than(
        self,
        other: Union[List['Resources'], 'Resources'],
        requested_num_nodes: int = 1,
        check_ports: bool = False,
    ) -> bool:
        """Returns whether this resources is less demanding than the other.

        Args:
            other: Resources of the launched cluster. If the cluster is
              heterogeneous, it is represented as a list of Resource objects.
            requested_num_nodes: Number of nodes that the current task
              requests from the cluster.
            check_ports: Whether to check the ports field.
        """
        if isinstance(other, list):
            resources_list = [self.less_demanding_than(o) for o in other]
            return requested_num_nodes <= sum(resources_list)

        assert other.cloud is not None, 'Other cloud must be specified'

        if self.cloud is not None and not self.cloud.is_same_cloud(other.cloud):
            return False
        # self.cloud <= other.cloud

        if self.region is not None and self.region != other.region:
            return False
        # self.region <= other.region

        if self.zone is not None and self.zone != other.zone:
            return False
        # self.zone <= other.zone

        if self.image_id is not None:
            if other.image_id is None:
                return False
            if other.region is None:
                # Current image_id should be a subset of other.image_id
                if not self.image_id.items() <= other.image_id.items():
                    return False
            else:
                this_image = (self.image_id.get(other.region) or
                              self.image_id.get(None))
                other_image = (other.image_id.get(other.region) or
                               other.image_id.get(None))
                if this_image != other_image:
                    return False

        if (self._instance_type is not None and
                self._instance_type != other.instance_type):
            return False
        # self._instance_type <= other.instance_type

        other_accelerators = other.accelerators
        if self.accelerators is not None:
            if other_accelerators is None:
                return False
            for acc in self.accelerators:
                if acc not in other_accelerators:
                    return False
                if self.accelerators[acc] > other_accelerators[acc]:
                    return False
        # self.accelerators <= other.accelerators

        if (self.accelerator_args is not None and
                self.accelerator_args != other.accelerator_args):
            return False
        # self.accelerator_args == other.accelerator_args

        if self.use_spot_specified and self.use_spot != other.use_spot:
            return False

        if self.disk_tier is not None:
            if other.disk_tier is None:
                return False
            # Here, BEST tier means the best we can get; for a launched
            # cluster, the best (and only) tier we can get is the launched
            # cluster's tier. Therefore, we don't need to check the tier
            # if it is BEST.
            if self.disk_tier != resources_utils.DiskTier.BEST:
                # Add parenthesis for better readability.
                if not (self.disk_tier <= other.disk_tier):  # pylint: disable=superfluous-parens
                    return False

        if self.network_tier is not None:
            if other.network_tier is None:
                return False
            if not self.network_tier <= other.network_tier:
                return False

        if check_ports:
            if self.ports is not None:
                if other.ports is None:
                    return False
                self_ports = resources_utils.port_ranges_to_set(self.ports)
                other_ports = resources_utils.port_ranges_to_set(other.ports)
                if not self_ports <= other_ports:
                    return False

        if self.requires_fuse and not other.requires_fuse:
            # On Kubernetes, we can't launch a task that requires FUSE on a pod
            # that wasn't initialized with FUSE support at the start.
            # Other clouds don't have this limitation.
            if other.cloud.is_same_cloud(clouds.Kubernetes()):
                return False

        # self <= other
        return True

    def should_be_blocked_by(self, blocked: 'Resources') -> bool:
        """Whether this Resources matches the blocked Resources.

        If a field in `blocked` is None, it should be considered as a wildcard
        for that field.
        """
        assert self.cloud is not None, 'Cloud must be specified'
        is_matched = True
        if (blocked.cloud is not None and
                not self.cloud.is_same_cloud(blocked.cloud)):
            is_matched = False
        if (blocked.instance_type is not None and
                self.instance_type != blocked.instance_type):
            is_matched = False
        if blocked.region is not None and self._region != blocked.region:
            is_matched = False
        if blocked.zone is not None and self._zone != blocked.zone:
            is_matched = False
        if (blocked.accelerators is not None and
                self.accelerators != blocked.accelerators):
            is_matched = False
        if blocked.use_spot is not None and self.use_spot != blocked.use_spot:
            is_matched = False
        return is_matched

    def is_empty(self) -> bool:
        """Is this Resources an empty request (all fields None)?"""
        return all([
            self._cloud is None,
            self._instance_type is None,
            self._cpus is None,
            self._memory is None,
            self._accelerators is None,
            self._accelerator_args is None,
            not self._use_spot_specified,
            self._disk_size == DEFAULT_DISK_SIZE_GB,
            self._disk_tier is None,
            self._network_tier is None,
            self._image_id is None,
            self._ports is None,
            self._docker_login_config is None,
        ])

    def copy(self, **override) -> 'Resources':
        """Returns a copy of the given Resources."""
        use_spot = self.use_spot if self._use_spot_specified else None

        current_override_configs = self._cluster_config_overrides
        if current_override_configs is None:
            current_override_configs = {}
        new_override_configs = override.pop('_cluster_config_overrides', {})
        overlaid_configs = skypilot_config.overlay_skypilot_config(
            original_config=config_utils.Config(current_override_configs),
            override_configs=new_override_configs,
        )
        override_configs = config_utils.Config()
        for key in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK:
            elem = overlaid_configs.get_nested(key, None)
            if elem is not None:
                override_configs.set_nested(key, elem)

        current_autostop_config = None
        if self.autostop_config is not None:
            current_autostop_config = self.autostop_config.to_yaml_config()

        override_configs = dict(override_configs) if override_configs else None
        resources = Resources(
            cloud=override.pop('cloud', self.cloud),
            instance_type=override.pop('instance_type', self.instance_type),
            cpus=override.pop('cpus', self._cpus),
            memory=override.pop('memory', self.memory),
            accelerators=override.pop('accelerators', self.accelerators),
            accelerator_args=override.pop('accelerator_args',
                                          self.accelerator_args),
            use_spot=override.pop('use_spot', use_spot),
            job_recovery=override.pop('job_recovery', self.job_recovery),
            disk_size=override.pop('disk_size', self.disk_size),
            region=override.pop('region', self.region),
            zone=override.pop('zone', self.zone),
            image_id=override.pop('image_id', self.image_id),
            disk_tier=override.pop('disk_tier', self.disk_tier),
            network_tier=override.pop('network_tier', self.network_tier),
            ports=override.pop('ports', self.ports),
            labels=override.pop('labels', self.labels),
            autostop=override.pop('autostop', current_autostop_config),
            priority=override.pop('priority', self.priority),
            volumes=override.pop('volumes', self.volumes),
            infra=override.pop('infra', None),
            _docker_login_config=override.pop('_docker_login_config',
                                              self._docker_login_config),
            _docker_username_for_runpod=override.pop(
                '_docker_username_for_runpod',
                self._docker_username_for_runpod),
            _is_image_managed=override.pop('_is_image_managed',
                                           self._is_image_managed),
            _requires_fuse=override.pop('_requires_fuse', self._requires_fuse),
            _cluster_config_overrides=override_configs,
            _no_missing_accel_warnings=override.pop(
                'no_missing_accel_warnings', self._no_missing_accel_warnings),
        )
        assert not override
        return resources

    def valid_on_region_zones(self, region: str, zones: List[str]) -> bool:
        """Returns whether this Resources is valid on given region and zones"""
        if self.region is not None and self.region != region:
            return False
        if self.zone is not None and self.zone not in zones:
            return False
        if self.image_id is not None:
            if None not in self.image_id and region not in self.image_id:
                return False
        return True

    def get_required_cloud_features(
            self) -> Set[clouds.CloudImplementationFeatures]:
        """Returns the set of cloud features required by this Resources."""
        features = set()
        if self.use_spot:
            features.add(clouds.CloudImplementationFeatures.SPOT_INSTANCE)
        if (self.disk_tier is not None and
                self.disk_tier != resources_utils.DiskTier.BEST):
            features.add(clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER)
        if (self.network_tier is not None and
                self.network_tier == resources_utils.NetworkTier.BEST):
            features.add(clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER)
        if self.extract_docker_image() is not None:
            features.add(clouds.CloudImplementationFeatures.DOCKER_IMAGE)
        elif self.image_id is not None:
            features.add(clouds.CloudImplementationFeatures.IMAGE_ID)
        if self.ports is not None:
            features.add(clouds.CloudImplementationFeatures.OPEN_PORTS)
        if self.volumes is not None:
            for volume in self.volumes:
                if 'disk_tier' in volume and volume[
                        'disk_tier'] != resources_utils.DiskTier.BEST:
                    features.add(
                        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER)
        return features

    @staticmethod
    def _apply_resource_config_aliases(
            config: Optional[Dict[str, Any]]) -> None:
        """Mutatively applies overriding aliases to the passed in config.

        Note: Nested aliases are not supported.
        The preferred way to support nested aliases would be to cast
        the parsed resource config dictionary to a config_utils.Config object
        and use the get_, set_, and pop_ nested methods accordingly.
        However, this approach comes at a significant memory cost as get_
        and pop_nested create deep copies of the config.
        """
        if not config:
            return

        for alias, canonical in RESOURCE_CONFIG_ALIASES.items():
            if alias in config:
                if canonical in config:
                    raise exceptions.InvalidSkyPilotConfigError(
                        f'Cannot specify both {alias} '
                        f'and {canonical} in config.')
                config[canonical] = config[alias]
                del config[alias]

    @classmethod
    def _parse_accelerators_from_str(
            cls, accelerators: str) -> List[Tuple[str, bool]]:
        """Parse accelerators string into a list of possible accelerators.

        Returns:
            A list of possible accelerators. Each element is a tuple of
            (accelerator_name, was_user_specified). was_user_specified is True
            if the accelerator was directly named by the user (for example
            "H100:2" would be True, but "80GB+" would be False since it doesn't
            mention the name of the accelerator).
        """
        # sanity check
        assert isinstance(accelerators, str), accelerators

        manufacturer = None
        memory = None
        count = 1

        split = accelerators.split(':')
        if len(split) == 3:
            manufacturer, memory, count_str = split
            count = int(count_str)
            assert re.match(r'^[0-9]+[GgMmTt][Bb]\+?$', memory), \
                'If specifying a GPU manufacturer, you must also' \
                'specify the memory size'
        elif len(split) == 2 and re.match(r'^[0-9]+[GgMmTt][Bb]\+?$', split[0]):
            memory = split[0]
            count = int(split[1])
        elif len(split) == 2 and re.match(r'^[0-9]+[GgMmTt][Bb]\+?$', split[1]):
            manufacturer, memory = split
        elif len(split) == 1 and re.match(r'^[0-9]+[GgMmTt][Bb]\+?$', split[0]):
            memory = split[0]
        else:
            # it is just an accelerator name, not a memory size
            return [(accelerators, True)]

        # we know we have some case of manufacturer, memory, count, now we
        # need to convert that to a list of possible accelerators
        memory_parsed = resources_utils.parse_memory_resource(memory,
                                                              'accelerators',
                                                              allow_plus=True)
        plus = memory_parsed[-1] == '+'
        if plus:
            memory_parsed = memory_parsed[:-1]
        memory_gb = int(memory_parsed)

        accelerators = [
            (f'{device}:{count}', False)
            for device in accelerator_registry.get_devices_by_memory(
                memory_gb, plus, manufacturer=manufacturer)
        ]

        return accelerators

    @classmethod
    def from_yaml_config(
        cls, config: Optional[Dict[str, Any]]
    ) -> Union[Set['Resources'], List['Resources']]:
        """Creates Resources objects from a YAML config.

        Args:
            config: A dict of resource config.

        Returns:
            A set of Resources objects if any_of is specified, otherwise a list
            of Resources objects if ordered is specified, otherwise a set with
            a single Resources object.
        """
        if config is None:
            return {Resources()}

        Resources._apply_resource_config_aliases(config)
        anyof = config.get('any_of')
        if anyof is not None and isinstance(anyof, list):
            for anyof_config in anyof:
                Resources._apply_resource_config_aliases(anyof_config)
        ordered = config.get('ordered')
        if ordered is not None and isinstance(ordered, list):
            for ordered_config in ordered:
                Resources._apply_resource_config_aliases(ordered_config)
        common_utils.validate_schema(config, schemas.get_resources_schema(),
                                     'Invalid resources YAML: ')

        def _override_resources(
                base_resource_config: Dict[str, Any],
                override_configs: List[Dict[str, Any]]) -> List[Resources]:
            resources_list = []
            for override_config in override_configs:
                new_resource_config = base_resource_config.copy()
                # Labels are handled separately.
                override_labels = override_config.pop('labels', None)
                new_resource_config.update(override_config)

                # Update the labels with the override labels.
                labels = new_resource_config.get('labels', None)
                if labels is not None and override_labels is not None:
                    labels.update(override_labels)
                elif override_labels is not None:
                    labels = override_labels
                new_resource_config['labels'] = labels

                # Call from_yaml_config again instead of
                # _from_yaml_config_single to handle the case, where both
                # multiple accelerators and `any_of` is specified.
                # This will not cause infinite recursion because we have made
                # sure that `any_of` and `ordered` cannot be specified in the
                # resource candidates in `any_of` or `ordered`, by the schema
                # validation above.
                resources_list.extend(
                    list(Resources.from_yaml_config(new_resource_config)))
            return resources_list

        config = config.copy()
        any_of_configs = config.pop('any_of', None)
        ordered_configs = config.pop('ordered', None)
        if any_of_configs is not None and ordered_configs is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify both "any_of" and "ordered" in resources.')

        # Parse resources.accelerators field.
        accelerators = config.get('accelerators')
        if config and accelerators is not None:
            if isinstance(accelerators, str):
                accelerators_list = cls._parse_accelerators_from_str(
                    accelerators)
            elif isinstance(accelerators, dict):
                accelerator_names = [
                    f'{k}:{v}' if v is not None else f'{k}'
                    for k, v in accelerators.items()
                ]
                accelerators_list = []
                for accel_name in accelerator_names:
                    parsed_accels = cls._parse_accelerators_from_str(accel_name)
                    accelerators_list.extend(parsed_accels)
            elif isinstance(accelerators, list) or isinstance(
                    accelerators, set):
                accelerators_list = []
                for accel_name in accelerators:
                    parsed_accels = cls._parse_accelerators_from_str(accel_name)
                    accelerators_list.extend(parsed_accels)
            else:
                assert False, ('Invalid accelerators type:'
                               f'{type(accelerators)}')
            # now that accelerators is a list, we need to decide which to
            # include in the final set, however, there may be multiple copies
            # of the same accelerator, some given by name by the user and the
            # other copy being given by memory size. In this case, we only care
            # about the user specified ones (so we can give a warning if it
            # doesn't exist).
            accel_to_user_specified: Dict[str, bool] = collections.OrderedDict()
            for accel, user_specified in accelerators_list:
                # If this accelerator is not in dict yet, or if current one is
                # user specified and existing one is not, update the entry
                accel_to_user_specified[accel] = (user_specified or
                                                  accel_to_user_specified.get(
                                                      accel, False))

            # only time we care about ordered is when we are given a list,
            # otherwise we default to a set
            accelerators_type = list if isinstance(accelerators, list) else set
            accelerators = accelerators_type([
                (accel, user_specified)
                for accel, user_specified in accel_to_user_specified.items()
            ])

            if len(accelerators) > 1 and ordered_configs:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Cannot specify multiple "accelerators" with "ordered" '
                        'in resources.')
            if (len(accelerators) > 1 and any_of_configs and
                    not isinstance(accelerators, set)):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Cannot specify multiple "accelerators" with preferred '
                        'order (i.e., list of accelerators) with "any_of" '
                        'in resources.')

        if any_of_configs:
            resources_list = _override_resources(config, any_of_configs)
            return set(resources_list)
        if ordered_configs:
            resources_list = _override_resources(config, ordered_configs)
            return resources_list
        # Translate accelerators field to potential multiple resources.
        if accelerators:
            # In yaml file, we store accelerators as a list.
            # In Task, we store a list of resources, each with 1 accelerator.
            # This for loop is for format conversion.
            tmp_resources_list = []
            for acc, user_specified in accelerators:
                tmp_resource = config.copy()
                tmp_resource['accelerators'] = acc
                if not user_specified:
                    tmp_resource['_no_missing_accel_warnings'] = True
                tmp_resources_list.append(
                    Resources._from_yaml_config_single(tmp_resource))

            assert isinstance(accelerators, (list, set)), accelerators
            return type(accelerators)(tmp_resources_list)
        return {Resources._from_yaml_config_single(config)}

    @classmethod
    def _from_yaml_config_single(cls, config: Dict[str, str]) -> 'Resources':
        resources_fields: Dict[str, Any] = {}

        # Extract infra field if present
        infra = config.pop('infra', None)
        resources_fields['infra'] = infra

        # Keep backward compatibility with cloud, region, zone
        # Note: if both `infra` and any of `cloud`, `region`, `zone` are
        # specified, it will raise an error during the Resources.__init__
        # validation.
        resources_fields['cloud'] = registry.CLOUD_REGISTRY.from_str(
            config.pop('cloud', None))
        resources_fields['region'] = config.pop('region', None)
        resources_fields['zone'] = config.pop('zone', None)

        resources_fields['instance_type'] = config.pop('instance_type', None)
        resources_fields['cpus'] = config.pop('cpus', None)
        resources_fields['memory'] = config.pop('memory', None)
        resources_fields['accelerators'] = config.pop('accelerators', None)
        resources_fields['accelerator_args'] = config.pop(
            'accelerator_args', None)
        resources_fields['use_spot'] = config.pop('use_spot', None)
        if config.get('spot_recovery') is not None:
            logger.warning('spot_recovery is deprecated. Use job_recovery '
                           'instead (the system is defaulting to that for '
                           'you).')
            resources_fields['job_recovery'] = config.pop('spot_recovery', None)
        else:
            # spot_recovery and job_recovery are guaranteed to be mutually
            # exclusive by the schema validation.
            resources_fields['job_recovery'] = config.pop('job_recovery', None)
        resources_fields['disk_size'] = config.pop('disk_size', None)
        resources_fields['image_id'] = config.pop('image_id', None)
        resources_fields['disk_tier'] = config.pop('disk_tier', None)
        resources_fields['network_tier'] = config.pop('network_tier', None)
        resources_fields['ports'] = config.pop('ports', None)
        resources_fields['labels'] = config.pop('labels', None)
        resources_fields['autostop'] = config.pop('autostop', None)
        resources_fields['priority'] = config.pop('priority', None)
        resources_fields['volumes'] = config.pop('volumes', None)
        resources_fields['_docker_login_config'] = config.pop(
            '_docker_login_config', None)
        resources_fields['_docker_username_for_runpod'] = config.pop(
            '_docker_username_for_runpod', None)
        resources_fields['_is_image_managed'] = config.pop(
            '_is_image_managed', None)
        resources_fields['_requires_fuse'] = config.pop('_requires_fuse', None)
        resources_fields['_cluster_config_overrides'] = config.pop(
            '_cluster_config_overrides', None)

        if resources_fields['cpus'] is not None:
            resources_fields['cpus'] = str(resources_fields['cpus'])
        if resources_fields['memory'] is not None:
            resources_fields['memory'] = str(resources_fields['memory'])
        if resources_fields['accelerator_args'] is not None:
            resources_fields['accelerator_args'] = dict(
                resources_fields['accelerator_args'])
        if resources_fields['disk_size'] is not None:
            # although it will end up being an int, we don't know at this point
            # if it has units or not, so we store it as a string
            resources_fields['disk_size'] = str(resources_fields['disk_size'])
        resources_fields['_no_missing_accel_warnings'] = config.pop(
            '_no_missing_accel_warnings', None)

        assert not config, f'Invalid resource args: {config.keys()}'
        return Resources(**resources_fields)

    def to_yaml_config(self) -> Dict[str, Union[str, int]]:
        """Returns a yaml-style dict of config for this resource bundle."""
        config = {}

        def add_if_not_none(key, value):
            if value is not None and value != 'None':
                config[key] = value

        # Construct infra field if cloud is set
        infra = self.infra.to_str()
        add_if_not_none('infra', infra)

        add_if_not_none('instance_type', self.instance_type)
        add_if_not_none('cpus', self._cpus)
        add_if_not_none('memory', self.memory)
        add_if_not_none('accelerators', self._accelerators)
        add_if_not_none('accelerator_args', self.accelerator_args)

        if self._use_spot_specified:
            add_if_not_none('use_spot', self.use_spot)
        add_if_not_none('job_recovery', self.job_recovery)
        add_if_not_none('disk_size', self.disk_size)
        add_if_not_none('image_id', self.image_id)
        if self.disk_tier is not None:
            config['disk_tier'] = self.disk_tier.value
        if self.network_tier is not None:
            config['network_tier'] = self.network_tier.value
        add_if_not_none('ports', self.ports)
        add_if_not_none('labels', self.labels)
        if self.volumes is not None:
            # Convert DiskTier/StorageType enum to string value for each volume
            volumes = []
            for volume in self.volumes:
                volume_copy = volume.copy()
                if 'disk_tier' in volume_copy:
                    volume_copy['disk_tier'] = volume_copy['disk_tier'].value
                if 'storage_type' in volume_copy:
                    volume_copy['storage_type'] = volume_copy[
                        'storage_type'].value
                if 'attach_mode' in volume_copy:
                    volume_copy['attach_mode'] = volume_copy[
                        'attach_mode'].value
                volumes.append(volume_copy)
            config['volumes'] = volumes
        if self._autostop_config is not None:
            config['autostop'] = self._autostop_config.to_yaml_config()

        add_if_not_none('_no_missing_accel_warnings',
                        self._no_missing_accel_warnings)
        add_if_not_none('priority', self.priority)
        if self._docker_login_config is not None:
            config['_docker_login_config'] = dataclasses.asdict(
                self._docker_login_config)
        if self._docker_username_for_runpod is not None:
            config['_docker_username_for_runpod'] = (
                self._docker_username_for_runpod)
        add_if_not_none('_cluster_config_overrides',
                        self._cluster_config_overrides)
        if self._is_image_managed is not None:
            config['_is_image_managed'] = self._is_image_managed
        if self._requires_fuse is not None:
            config['_requires_fuse'] = self._requires_fuse
        return config

    def __setstate__(self, state):
        """Set state from pickled state, for backward compatibility."""
        self._version = self._VERSION

        # TODO (zhwu): Design our persistent state format with `__getstate__`,
        # so that to get rid of the version tracking.
        version = state.pop('_version', None)
        # Handle old version(s) here.
        if version is None:
            version = -1
        if version < 0:
            cloud = state.pop('cloud', None)
            state['_cloud'] = cloud

            instance_type = state.pop('instance_type', None)
            state['_instance_type'] = instance_type

            use_spot = state.pop('use_spot', False)
            state['_use_spot'] = use_spot

            accelerator_args = state.pop('accelerator_args', None)
            state['_accelerator_args'] = accelerator_args

            disk_size = state.pop('disk_size', DEFAULT_DISK_SIZE_GB)
            state['_disk_size'] = disk_size

        if version < 2:
            self._region = None

        # spot_recovery is deprecated. We keep the history just for readability,
        # it should be removed by chunk in the future.
        if version < 3:
            self._spot_recovery = None

        if version < 4:
            self._image_id = None

        if version < 5:
            self._zone = None

        if version < 6:
            accelerators = state.pop('_accelerators', None)
            if accelerators is not None:
                accelerators = {
                    accelerator_registry.canonicalize_accelerator_name(
                        acc, cloud=None): acc_count
                    for acc, acc_count in accelerators.items()
                }
            state['_accelerators'] = accelerators

        if version < 7:
            self._cpus = None

        if version < 8:
            self._memory = None

        image_id = state.get('_image_id', None)
        if isinstance(image_id, str):
            state['_image_id'] = {state.get('_region', None): image_id}

        if version < 9:
            self._disk_tier = None

        if version < 10:
            self._is_image_managed = None

        if version < 11:
            self._ports = None

        if version < 12:
            self._docker_login_config = None

        if version < 13:
            original_ports = state.get('_ports', None)
            if original_ports is not None:
                state['_ports'] = resources_utils.simplify_ports(
                    [str(port) for port in original_ports])

        if version < 14:
            # Backward compatibility: we change the default value for TPU VM to
            # True in version 14 (#1758), so we need to explicitly set it to
            # False when loading the old handle.
            accelerators = state.get('_accelerators', None)
            if accelerators is not None:
                for acc in accelerators.keys():
                    if acc.startswith('tpu'):
                        accelerator_args = state.get('_accelerator_args', {})
                        accelerator_args['tpu_vm'] = accelerator_args.get(
                            'tpu_vm', False)
                        state['_accelerator_args'] = accelerator_args

        if version < 15:
            original_disk_tier = state.get('_disk_tier', None)
            if original_disk_tier is not None:
                state['_disk_tier'] = resources_utils.DiskTier(
                    original_disk_tier)

        if version < 16:
            # Kubernetes clusters launched prior to version 16 run in privileged
            # mode and have FUSE support enabled by default. As a result, we
            # set the default to True for backward compatibility.
            state['_requires_fuse'] = state.get('_requires_fuse', True)

        if version < 17:
            state['_labels'] = state.get('_labels', None)

        if version < 18:
            self._job_recovery = state.pop('_spot_recovery', None)

        if version < 19:
            self._cluster_config_overrides = state.pop(
                '_cluster_config_overrides', None)

        if version < 20:
            # Pre-0.7.0, we used 'kubernetes' as the default region for
            # Kubernetes clusters. With the introduction of support for
            # multiple contexts, we now set the region to the context name.
            # Since we do not have information on which context the cluster
            # was run in, we default it to the current active context.
            legacy_region = 'kubernetes'
            original_cloud = state.get('_cloud', None)
            original_region = state.get('_region', None)
            if (isinstance(original_cloud, clouds.Kubernetes) and
                    original_region == legacy_region):
                current_context = (
                    kubernetes_utils.get_current_kube_config_context_name())
                state['_region'] = current_context
                # Also update the image_id dict if it contains the old region
                if isinstance(state['_image_id'], dict):
                    if legacy_region in state['_image_id']:
                        state['_image_id'][current_context] = (
                            state['_image_id'][legacy_region])
                        del state['_image_id'][legacy_region]

        if version < 21:
            self._cached_repr = None

        if version < 22:
            self._docker_username_for_runpod = state.pop(
                '_docker_username_for_runpod', None)

        if version < 23:
            self._autostop_config = None

        if version < 24:
            self._volumes = None

        if version < 25:
            if isinstance(state.get('_cloud', None), clouds.Kubernetes):
                _maybe_add_docker_prefix_to_image_id(state['_image_id'])

        if version < 26:
            self._network_tier = state.get('_network_tier', None)

        if version < 27:
            self._priority = None

        if version < 28:
            self._no_missing_accel_warnings = state.get(
                '_no_missing_accel_warnings', None)

        self.__dict__.update(state)


class LaunchableResources(Resources):
    """A class representing resources that can be launched on a cloud provider.

    This class is primarily a type hint for MyPy to indicate that an instance
    of `Resources` is launchable (i.e., `cloud` and `instance_type` are not
    None). It should not be instantiated directly.
    """

    def __init__(self, *args, **kwargs) -> None:  # pylint: disable=super-init-not-called,unused-argument
        assert False, (
            'LaunchableResources should not be instantiated directly. '
            'It is only used for type checking by MyPy.')

    @property
    def cloud(self) -> clouds.Cloud:
        assert self._cloud is not None, 'Cloud must be specified'
        return self._cloud

    @property
    def instance_type(self) -> str:
        assert self._instance_type is not None, (
            'Instance type must be specified')
        return self._instance_type

    def copy(self, **override) -> 'LaunchableResources':
        """Ensure MyPy understands the return type is LaunchableResources.

        This method is not expected to be called at runtime, as
        LaunchableResources should not be directly instantiated. It primarily
        serves as a type hint for static analysis.
        """
        self.assert_launchable()
        return typing.cast(LaunchableResources, super().copy(**override))


def _maybe_add_docker_prefix_to_image_id(
        image_id_dict: Optional[Dict[Optional[str], str]]) -> None:
    if image_id_dict is None:
        return
    for k, v in image_id_dict.items():
        if not v.startswith('docker:'):
            image_id_dict[k] = f'docker:{v}'
