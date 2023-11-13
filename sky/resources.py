"""Resources: compute requirements of Tasks."""
import functools
import textwrap
from typing import Dict, List, Optional, Set, Tuple, Union

import colorama
from typing_extensions import Literal

from sky import clouds
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky import spot
from sky.clouds import service_catalog
from sky.provision import docker_utils
from sky.skylet import constants
from sky.utils import accelerator_registry
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import tpu_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_DISK_SIZE_GB = 256


class Resources:
    """Resources: compute requirements of Tasks.

    This class is immutable once created (to ensure some validations are done
    whenever properties change). To update the property of an instance of
    Resources, use `resources.copy(**new_properties)`.

    Used:

    * for representing resource requests for tasks/apps
    * as a "filter" to get concrete launchable instances
    * for calculating billing
    * for provisioning on a cloud

    """
    # If any fields changed, increment the version. For backward compatibility,
    # modify the __setstate__ method to handle the old version.
    _VERSION = 13

    def __init__(
        self,
        cloud: Optional[clouds.Cloud] = None,
        instance_type: Optional[str] = None,
        cpus: Union[None, int, float, str] = None,
        memory: Union[None, int, float, str] = None,
        accelerators: Union[None, str, Dict[str, int]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        image_id: Union[Dict[str, str], str, None] = None,
        disk_size: Optional[int] = None,
        disk_tier: Optional[Literal['high', 'medium', 'low']] = None,
        ports: Optional[Union[int, str, List[str], Tuple[str]]] = None,
        # Internal use only.
        _docker_login_config: Optional[docker_utils.DockerLoginConfig] = None,
        _is_image_managed: Optional[bool] = None,
    ):
        """Initialize a Resources object.

        All fields are optional.  ``Resources.is_launchable`` decides whether
        the Resources is fully specified to launch an instance.

        Examples:
          .. code-block:: python

            # Fully specified cloud and instance type (is_launchable() is True).
            sky.Resources(clouds.AWS(), 'p3.2xlarge')
            sky.Resources(clouds.GCP(), 'n1-standard-16')
            sky.Resources(clouds.GCP(), 'n1-standard-8', 'V100')

            # Specifying required resources; the system decides the
            # cloud/instance type. The below are equivalent:
            sky.Resources(accelerators='V100')
            sky.Resources(accelerators='V100:1')
            sky.Resources(accelerators={'V100': 1})
            sky.Resources(cpus='2+', memory='16+', accelerators='V100')

        Args:
          cloud: the cloud to use.
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
          use_spot: whether to use spot instances. If None, defaults to
            False.
          spot_recovery: the spot recovery strategy to use for the managed
            spot to recover the cluster from preemption. Refer to
            `recovery_strategy module <https://github.com/skypilot-org/skypilot/blob/master/sky/spot/recovery_strategy.py>`__ # pylint: disable=line-too-long
            for more details.
          region: the region to use.
          zone: the zone to use.
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
          ports: the ports to open on the instance.
          _docker_login_config: the docker configuration to use. This include
            the docker username, password, and registry server. If None, skip
            docker login.
        """
        self._version = self._VERSION
        self._cloud = cloud
        self._region: Optional[str] = None
        self._zone: Optional[str] = None
        self._validate_and_set_region_zone(region, zone)

        self._instance_type = instance_type

        self._use_spot_specified = use_spot is not None
        self._use_spot = use_spot if use_spot is not None else False
        self._spot_recovery = None
        if spot_recovery is not None:
            if spot_recovery.strip().lower() != 'none':
                self._spot_recovery = spot_recovery.upper()

        if disk_size is not None:
            if round(disk_size) != disk_size:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'OS disk size must be an integer. Got: {disk_size}.')
            self._disk_size = int(disk_size)
        else:
            self._disk_size = _DEFAULT_DISK_SIZE_GB

        # self._image_id is a dict of {region: image_id}.
        # The key is None if the same image_id applies for all regions.
        self._image_id = image_id
        if isinstance(image_id, str):
            self._image_id = {self._region: image_id.strip()}
        elif isinstance(image_id, dict):
            if None in image_id:
                self._image_id = {self._region: image_id[None].strip()}
            else:
                self._image_id = {
                    k.strip(): v.strip() for k, v in image_id.items()
                }
        self._is_image_managed = _is_image_managed

        self._disk_tier = disk_tier

        if ports is not None:
            if isinstance(ports, tuple):
                ports = list(ports)
            if not isinstance(ports, list):
                ports = [ports]
            ports = resources_utils.simplify_ports(
                [str(port) for port in ports])
            if not ports:
                # Set to None if empty. This is mainly for resources from
                # cli, which will comes in as an empty tuple.
                ports = None
        self._ports = ports

        self._docker_login_config = _docker_login_config

        self._set_cpus(cpus)
        self._set_memory(memory)
        self._set_accelerators(accelerators, accelerator_args)

        self._try_validate_local()
        self._try_validate_instance_type()
        self._try_validate_cpus_mem()
        self._try_validate_spot()
        self._try_validate_image_id()
        self._try_validate_disk_tier()
        self._try_validate_ports()

    # When querying the accelerators inside this func (we call self.accelerators
    # which is a @property), we will check the cloud's catalog, which can error
    # if it fails to fetch some account specific catalog information (e.g., AWS
    # zone mapping). It is fine to use the default catalog as this function is
    # only for display purposes.
    @service_catalog.fallback_to_default_catalog
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
        accelerators = ''
        accelerator_args = ''
        if self.accelerators is not None:
            accelerators = f', {self.accelerators}'
            if self.accelerator_args is not None:
                accelerator_args = f', accelerator_args={self.accelerator_args}'

        cpus = ''
        if self.cpus is not None:
            cpus = f', cpus={self.cpus}'

        memory = ''
        if self.memory is not None:
            memory = f', mem={self.memory}'

        if isinstance(self.cloud, clouds.Local):
            return f'{self.cloud}({self.accelerators})'

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
            disk_tier = f', disk_tier={self.disk_tier}'

        disk_size = ''
        if self.disk_size != _DEFAULT_DISK_SIZE_GB:
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
            f'{disk_tier}{disk_size}{ports}')
        # It may have leading ',' (for example, instance_type not set) or empty
        # spaces.  Remove them.
        while hardware_str and hardware_str[0] in (',', ' '):
            hardware_str = hardware_str[1:]

        cloud_str = '<Cloud>'
        if self.cloud is not None:
            cloud_str = f'{self.cloud}'

        return f'{cloud_str}({hardware_str})'

    @property
    def repr_with_region_zone(self) -> str:
        region_str = ''
        if self.region is not None:
            region_str = f', region={self.region}'
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
    def cloud(self):
        return self._cloud

    @property
    def region(self):
        return self._region

    @property
    def zone(self):
        return self._zone

    @property
    def instance_type(self):
        return self._instance_type

    @property
    def cpus(self) -> Optional[str]:
        """Returns the number of vCPUs that each instance must have.

        For example, cpus='4' means each instance must have exactly 4 vCPUs,
        and cpus='4+' means each instance must have at least 4 vCPUs.

        (Developer note: The cpus field is only used to select the instance type
        at launch time. Thus, Resources in the backend's ResourceHandle will
        always have the cpus field set to None.)
        """
        return self._cpus

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
    @functools.lru_cache(maxsize=1)
    def accelerators(self) -> Optional[Dict[str, int]]:
        """Returns the accelerators field directly or by inferring.

        For example, Resources(AWS, 'p3.2xlarge') has its accelerators field
        set to None, but this function will infer {'V100': 1} from the instance
        type.
        """
        if self._accelerators is not None:
            return self._accelerators
        if self.cloud is not None and self._instance_type is not None:
            return self.cloud.get_accelerators_from_instance_type(
                self._instance_type)
        return None

    @property
    def accelerator_args(self) -> Optional[Dict[str, str]]:
        return self._accelerator_args

    @property
    def use_spot(self) -> bool:
        return self._use_spot

    @property
    def use_spot_specified(self) -> bool:
        return self._use_spot_specified

    @property
    def spot_recovery(self) -> Optional[str]:
        return self._spot_recovery

    @property
    def disk_size(self) -> int:
        return self._disk_size

    @property
    def image_id(self) -> Optional[Dict[str, str]]:
        return self._image_id

    @property
    def disk_tier(self) -> str:
        return self._disk_tier

    @property
    def ports(self) -> Optional[List[str]]:
        return self._ports

    @property
    def is_image_managed(self) -> Optional[bool]:
        return self._is_image_managed

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

        self._memory = str(memory)
        if isinstance(memory, str):
            if memory.endswith('+'):
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
        else:
            memory_gb = float(memory)

        if memory_gb <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'The "cpus" field should be positive. Found: {memory!r}')

    def _set_accelerators(
        self,
        accelerators: Union[None, str, Dict[str, int]],
        accelerator_args: Optional[Dict[str, str]],
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

            # Ignore check for the local cloud case.
            # It is possible the accelerators dict can contain multiple
            # types of accelerators for some on-prem clusters.
            if not isinstance(self._cloud, clouds.Local):
                assert len(accelerators) == 1, accelerators

            # Canonicalize the accelerator names.
            accelerators = {
                accelerator_registry.canonicalize_accelerator_name(acc):
                acc_count for acc, acc_count in accelerators.items()
            }

            acc, _ = list(accelerators.items())[0]
            if 'tpu' in acc.lower():
                if self.cloud is None:
                    self._cloud = clouds.GCP()
                assert self.cloud.is_same_cloud(
                    clouds.GCP()), 'Cloud must be GCP.'
                if accelerator_args is None:
                    accelerator_args = {}
                use_tpu_vm = accelerator_args.get('tpu_vm', False)
                if use_tpu_vm:
                    tpu_utils.check_gcp_cli_include_tpu_vm()
                if self.instance_type is not None and use_tpu_vm:
                    if self.instance_type != 'TPU-VM':
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                'Cannot specify instance type'
                                f' (got "{self.instance_type}") for TPU VM.')
                if 'runtime_version' not in accelerator_args:
                    if use_tpu_vm:
                        accelerator_args['runtime_version'] = 'tpu-vm-base'
                    else:
                        accelerator_args['runtime_version'] = '2.12.0'
                    logger.info(
                        'Missing runtime_version in accelerator_args, using'
                        f' default ({accelerator_args["runtime_version"]})')

        self._accelerators = accelerators
        self._accelerator_args = accelerator_args

    def is_launchable(self) -> bool:
        return self.cloud is not None and self._instance_type is not None

    def need_cleanup_after_preemption(self) -> bool:
        """Returns whether a spot resource needs cleanup after preemption."""
        assert self.is_launchable(), self
        return self.cloud.need_cleanup_after_preemption(self)

    def _validate_and_set_region_zone(self, region: Optional[str],
                                      zone: Optional[str]) -> None:
        if region is None and zone is None:
            return

        if self._cloud is None:
            # Try to infer the cloud from region/zone, if unique. If 0 or >1
            # cloud corresponds to region/zone, errors out.
            valid_clouds = []
            enabled_clouds = global_user_state.get_enabled_clouds()
            cloud_to_errors = {}
            for cloud in enabled_clouds:
                try:
                    cloud.validate_region_zone(region, zone)
                except ValueError as e:
                    cloud_to_errors[repr(cloud)] = e
                    continue
                valid_clouds.append(cloud)

            if len(valid_clouds) == 0:
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
                        for cloud, error in cloud_to_errors.items():
                            reason_str = '\n'.join(textwrap.wrap(
                                str(error), 80))
                            table.add_row([str(cloud), reason_str])
                        hint = table.get_string()
                    raise ValueError(
                        f'Invalid (region {region!r}, zone {zone!r}) '
                        f'{cloud_str}. Details:\n{hint}')
            elif len(valid_clouds) > 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Cannot infer cloud from (region {region!r}, zone '
                        f'{zone!r}). Multiple enabled clouds have region/zone '
                        f'of the same names: {valid_clouds}. '
                        f'To fix: explicitly specify `cloud`.')
            logger.debug(f'Cloud is not specified, using {valid_clouds[0]} '
                         f'inferred from region {region!r} and zone {zone!r}')
            self._cloud = valid_clouds[0]

        # Validate if region and zone exist in the catalog, and set the region
        # if zone is specified.
        self._region, self._zone = self._cloud.validate_region_zone(
            region, zone)

    def get_valid_regions_for_launchable(self) -> List[clouds.Region]:
        """Returns a set of `Region`s that can provision this Resources.

        Each `Region` has a list of `Zone`s that can provision this Resources.

        (Internal) This function respects any config in skypilot_config that
        may have restricted the regions to be considered (e.g., a
        ssh_proxy_command dict with region names as keys).
        """
        assert self.is_launchable(), self

        regions = self._cloud.regions_with_offering(self._instance_type,
                                                    self.accelerators,
                                                    self._use_spot,
                                                    self._region, self._zone)
        if self._image_id is not None and None not in self._image_id:
            regions = [r for r in regions if r.name in self._image_id]

        # Filter the regions by the skypilot_config
        ssh_proxy_command_config = skypilot_config.get_nested(
            (str(self._cloud).lower(), 'ssh_proxy_command'), None)
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
            enabled_clouds = global_user_state.get_enabled_clouds()
            for cloud in enabled_clouds:
                if cloud.instance_type_exists(self._instance_type):
                    valid_clouds.append(cloud)
            if len(valid_clouds) == 0:
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
        if self.cpus is None and self.memory is None:
            return
        if self.instance_type is not None:
            # The assertion should be true because we have already executed
            # _try_validate_instance_type() before this method.
            # The _try_validate_instance_type() method infers and sets
            # self.cloud if self.instance_type is not None.
            assert self.cloud is not None
            cpus, mem = self.cloud.get_vcpus_mem_from_instance_type(
                self.instance_type)
            if self.cpus is not None:
                if self.cpus.endswith('+'):
                    if cpus < float(self.cpus[:-1]):
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                f'{self.instance_type} does not have enough '
                                f'vCPUs. {self.instance_type} has {cpus} '
                                f'vCPUs, but {self.cpus} is requested.')
                elif cpus != float(self.cpus):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'{self.instance_type} does not have the requested '
                            f'number of vCPUs. {self.instance_type} has {cpus} '
                            f'vCPUs, but {self.cpus} is requested.')
            if self.memory is not None:
                if self.memory.endswith('+'):
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

    def _try_validate_spot(self) -> None:
        if self._spot_recovery is None:
            return
        if not self._use_spot:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify spot_recovery without use_spot set to True.'
                )
        if self._spot_recovery not in spot.SPOT_STRATEGIES:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Spot recovery strategy {self._spot_recovery} '
                    'is not supported. The strategy should be among '
                    f'{list(spot.SPOT_STRATEGIES.keys())}')

    def _try_validate_local(self) -> None:
        if isinstance(self._cloud, clouds.Local):
            if self._use_spot:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Local/On-prem mode does not support spot '
                                     'instances.')
            local_instance = clouds.Local.get_default_instance_type()
            if (self._instance_type is not None and
                    self._instance_type != local_instance):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Local/On-prem mode does not support instance type:'
                        f' {self._instance_type}.')
            if self._image_id is not None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Local/On-prem mode does not support custom '
                        'images.')

    def extract_docker_image(self) -> Optional[str]:
        if self.image_id is None:
            return None
        if len(self.image_id) == 1 and self.region in self.image_id:
            image_id = self.image_id[self.region]
            if image_id.startswith('docker:'):
                return image_id[len('docker:'):]
        return None

    def _try_validate_image_id(self) -> None:
        if self._image_id is None:
            return

        if self.extract_docker_image() is not None:
            # TODO(tian): validate the docker image exists / of reasonable size
            if self.accelerators is not None:
                for acc in self.accelerators.keys():
                    if acc.lower().startswith('tpu'):
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                'Docker image is not supported for TPU VM.')
            if self.cloud is not None:
                self.cloud.check_features_are_supported(
                    {clouds.CloudImplementationFeatures.DOCKER_IMAGE})
            return

        if self.cloud is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cloud must be specified when image_id is provided.')

        # Apr, 2023 by Hysun(hysun.he@oracle.com): Added support for OCI
        if not self._cloud.is_same_cloud(
                clouds.AWS()) and not self._cloud.is_same_cloud(
                    clouds.GCP()) and not self._cloud.is_same_cloud(
                        clouds.IBM()) and not self._cloud.is_same_cloud(
                            clouds.OCI()):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'image_id is only supported for AWS/GCP/IBM/OCI, please '
                    'explicitly specify the cloud.')

        if self._region is not None:
            if self._region not in self._image_id:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'image_id {self._image_id} should contain the image '
                        f'for the specified region {self._region}.')
            # Narrow down the image_id to the specified region.
            self._image_id = {self._region: self._image_id[self._region]}

        # Check the image_id's are valid.
        for region, image_id in self._image_id.items():
            if (image_id.startswith('skypilot:') and
                    not self._cloud.is_image_tag_valid(image_id, region)):
                region_str = f' ({region})' if region else ''
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Image tag {image_id!r} is not valid, please make sure'
                        f' the tag exists in {self._cloud}{region_str}.')

            if (self._cloud.is_same_cloud(clouds.AWS()) and
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
        if self.disk_tier is None:
            return
        if self.disk_tier not in ['high', 'medium', 'low']:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid disk_tier {self.disk_tier}. '
                    'Please use one of "high", "medium", or "low".')
        if self.instance_type is None:
            return
        if self.cloud is not None:
            self.cloud.check_disk_tier_enabled(self.instance_type,
                                               self.disk_tier)

    def _try_validate_ports(self) -> None:
        if self.ports is None:
            return
        if skypilot_config.get_nested(('aws', 'security_group_name'),
                                      None) is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify ports when AWS security group name is '
                    'specified.')
        if self.cloud is not None:
            self.cloud.check_features_are_supported(
                {clouds.CloudImplementationFeatures.OPEN_PORTS})
        # We don't need to check the ports format since we already done it
        # in resources_utils.simplify_ports

    def get_cost(self, seconds: float) -> float:
        """Returns cost in USD for the runtime in seconds."""
        hours = seconds / 3600
        # Instance.
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
            self, cluster_name_on_cloud: str, region: clouds.Region,
            zones: Optional[List[clouds.Zone]]) -> Dict[str, Optional[str]]:
        """Converts planned sky.Resources to resource variables.

        These variables are divided into two categories: cloud-specific and
        cloud-agnostic. The cloud-specific variables are generated by the
        cloud.make_deploy_resources_variables() method, and the cloud-agnostic
        variables are generated by this method.
        """
        cloud_specific_variables = self.cloud.make_deploy_resources_variables(
            self, cluster_name_on_cloud, region, zones)
        docker_image = self.extract_docker_image()
        return dict(
            cloud_specific_variables,
            **{
                # Docker config
                # Docker image. The image name used to pull the image, e.g.
                # ubuntu:latest.
                'docker_image': docker_image,
                # Docker container name. The name of the container. Default to
                # `sky_container`.
                'docker_container_name':
                    constants.DEFAULT_DOCKER_CONTAINER_NAME,
                # Docker login config (if any). This helps pull the image from
                # private registries.
                'docker_login_config': self._docker_login_config
            })

    def get_reservations_available_resources(
            self, specific_reservations: Set[str]) -> Dict[str, int]:
        """Returns the number of available reservation resources."""
        if self.use_spot:
            # GCP's & AWS's reservations do not support spot instances. We
            # assume other clouds behave the same. We can move this check down
            # to each cloud if any cloud supports reservations for spot.
            return {}
        return self.cloud.get_reservations_available_resources(
            self._instance_type, self._region, self._zone,
            specific_reservations)

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
            if self.disk_tier != other.disk_tier:
                types = ['low', 'medium', 'high']
                return types.index(self.disk_tier) < types.index(
                    other.disk_tier)

        if check_ports:
            if self.ports is not None:
                if other.ports is None:
                    return False
                self_ports = resources_utils.port_ranges_to_set(self.ports)
                other_ports = resources_utils.port_ranges_to_set(other.ports)
                if not self_ports <= other_ports:
                    return False

        # self <= other
        return True

    def should_be_blocked_by(self, blocked: 'Resources') -> bool:
        """Whether this Resources matches the blocked Resources.

        If a field in `blocked` is None, it should be considered as a wildcard
        for that field.
        """
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
        return is_matched

    def is_empty(self) -> bool:
        """Is this Resources an empty request (all fields None)?"""
        return all([
            self.cloud is None,
            self._instance_type is None,
            self.cpus is None,
            self.memory is None,
            self.accelerators is None,
            self.accelerator_args is None,
            not self._use_spot_specified,
            self.disk_size == _DEFAULT_DISK_SIZE_GB,
            self.disk_tier is None,
            self._image_id is None,
            self.ports is None,
            self._docker_login_config is None,
        ])

    def copy(self, **override) -> 'Resources':
        """Returns a copy of the given Resources."""
        use_spot = self.use_spot if self._use_spot_specified else None
        resources = Resources(
            cloud=override.pop('cloud', self.cloud),
            instance_type=override.pop('instance_type', self.instance_type),
            cpus=override.pop('cpus', self.cpus),
            memory=override.pop('memory', self.memory),
            accelerators=override.pop('accelerators', self.accelerators),
            accelerator_args=override.pop('accelerator_args',
                                          self.accelerator_args),
            use_spot=override.pop('use_spot', use_spot),
            spot_recovery=override.pop('spot_recovery', self.spot_recovery),
            disk_size=override.pop('disk_size', self.disk_size),
            region=override.pop('region', self.region),
            zone=override.pop('zone', self.zone),
            image_id=override.pop('image_id', self.image_id),
            disk_tier=override.pop('disk_tier', self.disk_tier),
            ports=override.pop('ports', self.ports),
            _docker_login_config=override.pop('_docker_login_config',
                                              self._docker_login_config),
            _is_image_managed=override.pop('_is_image_managed',
                                           self._is_image_managed),
        )
        assert len(override) == 0
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
        if self.disk_tier is not None:
            features.add(clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER)
        if self.extract_docker_image() is not None:
            features.add(clouds.CloudImplementationFeatures.DOCKER_IMAGE)
        if self.ports is not None:
            features.add(clouds.CloudImplementationFeatures.OPEN_PORTS)
        return features

    @classmethod
    def from_yaml_config(cls, config: Optional[Dict[str, str]]) -> 'Resources':
        if config is None:
            return Resources()

        common_utils.validate_schema(config, schemas.get_resources_schema(),
                                     'Invalid resources YAML: ')

        resources_fields = {}
        resources_fields['cloud'] = clouds.CLOUD_REGISTRY.from_str(
            config.pop('cloud', None))
        resources_fields['instance_type'] = config.pop('instance_type', None)
        resources_fields['cpus'] = config.pop('cpus', None)
        resources_fields['memory'] = config.pop('memory', None)
        resources_fields['accelerators'] = config.pop('accelerators', None)
        resources_fields['accelerator_args'] = config.pop(
            'accelerator_args', None)
        resources_fields['use_spot'] = config.pop('use_spot', None)
        resources_fields['spot_recovery'] = config.pop('spot_recovery', None)
        resources_fields['disk_size'] = config.pop('disk_size', None)
        resources_fields['region'] = config.pop('region', None)
        resources_fields['zone'] = config.pop('zone', None)
        resources_fields['image_id'] = config.pop('image_id', None)
        resources_fields['disk_tier'] = config.pop('disk_tier', None)
        resources_fields['ports'] = config.pop('ports', None)
        resources_fields['_docker_login_config'] = config.pop(
            '_docker_login_config', None)
        resources_fields['_is_image_managed'] = config.pop(
            '_is_image_managed', None)

        if resources_fields['cpus'] is not None:
            resources_fields['cpus'] = str(resources_fields['cpus'])
        if resources_fields['memory'] is not None:
            resources_fields['memory'] = str(resources_fields['memory'])
        if resources_fields['accelerator_args'] is not None:
            resources_fields['accelerator_args'] = dict(
                resources_fields['accelerator_args'])
        if resources_fields['disk_size'] is not None:
            resources_fields['disk_size'] = int(resources_fields['disk_size'])

        assert not config, f'Invalid resource args: {config.keys()}'
        return Resources(**resources_fields)

    def to_yaml_config(self) -> Dict[str, Union[str, int]]:
        """Returns a yaml-style dict of config for this resource bundle."""
        config = {}

        def add_if_not_none(key, value):
            if value is not None and value != 'None':
                config[key] = value

        add_if_not_none('cloud', str(self.cloud))
        add_if_not_none('instance_type', self.instance_type)
        add_if_not_none('cpus', self.cpus)
        add_if_not_none('memory', self.memory)
        add_if_not_none('accelerators', self.accelerators)
        add_if_not_none('accelerator_args', self.accelerator_args)

        if self._use_spot_specified:
            add_if_not_none('use_spot', self.use_spot)
            add_if_not_none('spot_recovery', self.spot_recovery)
        add_if_not_none('disk_size', self.disk_size)
        add_if_not_none('region', self.region)
        add_if_not_none('zone', self.zone)
        add_if_not_none('image_id', self.image_id)
        add_if_not_none('disk_tier', self.disk_tier)
        add_if_not_none('ports', self.ports)
        add_if_not_none('_docker_login_config', self._docker_login_config)
        if self._is_image_managed is not None:
            config['_is_image_managed'] = self._is_image_managed
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

            disk_size = state.pop('disk_size', _DEFAULT_DISK_SIZE_GB)
            state['_disk_size'] = disk_size

        if version < 2:
            self._region = None

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
                    accelerator_registry.canonicalize_accelerator_name(acc):
                    acc_count for acc, acc_count in accelerators.items()
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

        self.__dict__.update(state)
