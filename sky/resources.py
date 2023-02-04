"""Resources: compute requirements of Tasks."""
from typing import Dict, List, Optional, Union

from sky import clouds
from sky import global_user_state
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.utils import accelerator_registry
from sky.utils import schemas
from sky.utils import tpu_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_DISK_SIZE_GB = 256


class Resources:
    """A cloud resource bundle.

    Used
      * for representing resource requests for tasks/apps
      * as a "filter" to get concrete launchable instances
      * for calculating billing
      * for provisioning on a cloud

    Examples:

        # Fully specified cloud and instance type (is_launchable() is True).
        sky.Resources(clouds.AWS(), 'p3.2xlarge')
        sky.Resources(clouds.GCP(), 'n1-standard-16')
        sky.Resources(clouds.GCP(), 'n1-standard-8', 'V100')

        # Specifying required resources; the system decides the cloud/instance
        # type. The below are equivalent:
        sky.Resources(accelerators='V100')
        sky.Resources(accelerators='V100:1')
        sky.Resources(accelerators={'V100': 1})

        # TODO:
        sky.Resources(requests={'mem': '16g', 'cpu': 8})
    """
    # If any fields changed, increment the version. For backward compatibility,
    # modify the __setstate__ method to handle the old version.
    _VERSION = 7

    def __init__(
        self,
        cloud: Optional[clouds.Cloud] = None,
        instance_type: Optional[str] = None,
        cpus: Union[None, int, float, str] = None,
        accelerators: Union[None, str, Dict[str, int]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        disk_size: Optional[int] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        image_id: Union[Dict[str, str], str, None] = None,
    ):
        self._version = self._VERSION
        self._cloud = cloud
        self._region: Optional[str] = None
        self._zone: Optional[str] = None
        self._set_region_zone(region, zone)

        self._instance_type = instance_type

        self._use_spot_specified = use_spot is not None
        self._use_spot = use_spot if use_spot is not None else False
        self._spot_recovery = None
        if spot_recovery is not None:
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

        self._set_cpus(cpus)
        self._set_accelerators(accelerators, accelerator_args)

        self._try_validate_local()
        self._try_validate_instance_type()
        self._try_validate_cpus()
        self._try_validate_accelerators()
        self._try_validate_spot()
        self._try_validate_image_id()

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
                image_id = f', image_id={self.image_id!r}'

        disk_size = ''
        if self.disk_size != _DEFAULT_DISK_SIZE_GB:
            disk_size = f', disk_size={self.disk_size}'

        if self._instance_type is not None:
            instance_type = f'{self._instance_type}'
        else:
            instance_type = ''

        hardware_str = (
            f'{instance_type}{use_spot}'
            f'{cpus}{accelerators}{accelerator_args}{image_id}{disk_size}')
        # It may have leading ',' (for example, instance_type not set) or empty
        # spaces.  Remove them.
        while hardware_str and hardware_str[0] in (',', ' '):
            hardware_str = hardware_str[1:]

        cloud_str = '<Cloud>'
        if self.cloud is not None:
            cloud_str = f'{self.cloud}'

        return f'{cloud_str}({hardware_str})'

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
                        accelerator_args['runtime_version'] = '2.5.0'
                    logger.info(
                        'Missing runtime_version in accelerator_args, using'
                        f' default ({accelerator_args["runtime_version"]})')

        self._accelerators = accelerators
        self._accelerator_args = accelerator_args

    def is_launchable(self) -> bool:
        return self.cloud is not None and self._instance_type is not None

    def need_cleanup_after_preemption(self) -> bool:
        """Returns whether a spot resource needs cleanup after preeemption."""
        assert self.is_launchable(), self
        return self.cloud.need_cleanup_after_preemption(self)

    def _set_region_zone(self, region: Optional[str],
                         zone: Optional[str]) -> None:
        if region is None and zone is None:
            return

        if self._cloud is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cloud must be specified when region/zone are specified.')

        # Validate whether region and zone exist in the catalog, and set the
        # region if zone is specified.
        self._region, self._zone = self._cloud.validate_region_zone(
            region, zone)

    def get_offering_regions_for_launchable(self) -> List[clouds.Region]:
        """Returns a set of `Region`s that can provision this Resources.

        Each `Region` has a list of `Zone`s that can provision this Resources.
        """
        assert self.is_launchable()
        regions = self._cloud.regions_with_offering(self._instance_type,
                                                    self.accelerators,
                                                    self._use_spot,
                                                    self._region, self._zone)
        if self._image_id is not None and None not in self._image_id:
            regions = [r for r in regions if r.name in self._image_id]
        return regions

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

    def _try_validate_cpus(self) -> None:
        if self.cpus is None:
            return
        if self.instance_type is not None:
            # The assertion should be true because we have already executed
            # _try_validate_instance_type() before this method.
            # The _try_validate_instance_type() method infers and sets
            # self.cloud if self.instance_type is not None.
            assert self.cloud is not None
            cpus = self.cloud.get_vcpus_from_instance_type(self.instance_type)
            if self.cpus.endswith('+'):
                if cpus < float(self.cpus[:-1]):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'{self.instance_type} does not have enough vCPUs. '
                            f'{self.instance_type} has {cpus} vCPUs, '
                            f'but {self.cpus} is requested.')
            elif cpus != float(self.cpus):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'{self.instance_type} does not have the requested '
                        f'number of vCPUs. {self.instance_type} has {cpus} '
                        f'vCPUs, but {self.cpus} is requested.')

    def _try_validate_accelerators(self) -> None:
        """Validate accelerators against the instance type and region/zone."""
        acc_requested = self.accelerators
        if (isinstance(self.cloud, clouds.GCP) and
                self.instance_type is not None):
            # Do this check even if acc_requested is None.
            clouds.GCP.check_host_accelerator_compatibility(
                self.instance_type, acc_requested)

        if acc_requested is None:
            return

        if self.is_launchable() and not isinstance(self.cloud, clouds.GCP):
            # GCP attaches accelerators to VMs, so no need for this check.
            acc_requested = self.accelerators
            acc_from_instance_type = (
                self.cloud.get_accelerators_from_instance_type(
                    self._instance_type))
            if not Resources(accelerators=acc_requested).less_demanding_than(
                    Resources(accelerators=acc_from_instance_type)):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Infeasible resource demands found:\n'
                        f'  Instance type requested: {self._instance_type}\n'
                        f'  Accelerators for {self._instance_type}: '
                        f'{acc_from_instance_type}\n'
                        f'  Accelerators requested: {acc_requested}\n'
                        f'To fix: either only specify instance_type, or change '
                        'the accelerators field to be consistent.')
            # NOTE: should not clear 'self.accelerators' even for AWS/Azure,
            # because e.g., the instance may have 4 GPUs, while the task
            # specifies to use 1 GPU.

        # Validate whether accelerator is available in specified region/zone.
        acc, acc_count = list(acc_requested.items())[0]
        # Fractional accelerators are temporarily bumped up to 1.
        if 0 < acc_count < 1:
            acc_count = 1
        if self.region is not None or self.zone is not None:
            if not self._cloud.accelerator_in_region_or_zone(
                    acc, acc_count, self.region, self.zone):
                error_str = (f'Accelerator "{acc}" is not available in '
                             '"{}".')
                if self.zone:
                    error_str = error_str.format(self.zone)
                else:
                    error_str = error_str.format(self.region)
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(error_str)

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

    def _try_validate_image_id(self) -> None:
        if self._image_id is None:
            return

        if self.cloud is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cloud must be specified when image_id is provided.')

        if not self._cloud.is_same_cloud(
                clouds.AWS()) and not self._cloud.is_same_cloud(clouds.GCP()):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'image_id is only supported for AWS and GCP, please '
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

    def less_demanding_than(self,
                            other: Union[List['Resources'], 'Resources'],
                            requested_num_nodes: int = 1) -> bool:
        """Returns whether this resources is less demanding than the other.

        Args:
            other: Resources of the launched cluster. If the cluster is
              heterogeneous, it is represented as a list of Resource objects.
            requested_num_nodes: Number of nodes that the current task
              requests from the cluster.
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
            self.accelerators is None,
            self.accelerator_args is None,
            not self._use_spot_specified,
        ])

    def copy(self, **override) -> 'Resources':
        """Returns a copy of the given Resources."""
        use_spot = self.use_spot if self._use_spot_specified else None
        resources = Resources(
            cloud=override.pop('cloud', self.cloud),
            instance_type=override.pop('instance_type', self.instance_type),
            cpus=override.pop('cpus', self.cpus),
            accelerators=override.pop('accelerators', self.accelerators),
            accelerator_args=override.pop('accelerator_args',
                                          self.accelerator_args),
            use_spot=override.pop('use_spot', use_spot),
            spot_recovery=override.pop('spot_recovery', self.spot_recovery),
            disk_size=override.pop('disk_size', self.disk_size),
            region=override.pop('region', self.region),
            zone=override.pop('zone', self.zone),
            image_id=override.pop('image_id', self.image_id),
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

    @classmethod
    def from_yaml_config(cls, config: Optional[Dict[str, str]]) -> 'Resources':
        if config is None:
            return Resources()

        backend_utils.validate_schema(config, schemas.get_resources_schema(),
                                      'Invalid resources YAML: ')

        resources_fields = dict()
        if config.get('cloud') is not None:
            resources_fields['cloud'] = clouds.CLOUD_REGISTRY.from_str(
                config.pop('cloud'))
        if config.get('instance_type') is not None:
            resources_fields['instance_type'] = config.pop('instance_type')
        if config.get('cpus') is not None:
            resources_fields['cpus'] = str(config.pop('cpus'))
        if config.get('accelerators') is not None:
            resources_fields['accelerators'] = config.pop('accelerators')
        if config.get('accelerator_args') is not None:
            resources_fields['accelerator_args'] = dict(
                config.pop('accelerator_args'))
        if config.get('use_spot') is not None:
            resources_fields['use_spot'] = config.pop('use_spot')
        if config.get('spot_recovery') is not None:
            resources_fields['spot_recovery'] = config.pop('spot_recovery')
        if config.get('disk_size') is not None:
            resources_fields['disk_size'] = int(config.pop('disk_size'))
        if config.get('region') is not None:
            resources_fields['region'] = config.pop('region')
        if config.get('zone') is not None:
            resources_fields['zone'] = config.pop('zone')
        if config.get('image_id') is not None:
            logger.warning('image_id in resources is experimental. It only '
                           'supports AWS/GCP.')
            resources_fields['image_id'] = config.pop('image_id')

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
        add_if_not_none('accelerators', self.accelerators)
        add_if_not_none('accelerator_args', self.accelerator_args)

        if self._use_spot_specified:
            add_if_not_none('use_spot', self.use_spot)
        config['spot_recovery'] = self.spot_recovery
        config['disk_size'] = self.disk_size
        add_if_not_none('region', self.region)
        add_if_not_none('zone', self.zone)
        add_if_not_none('image_id', self.image_id)
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

        image_id = state.get('_image_id', None)
        if isinstance(image_id, str):
            state['_image_id'] = {state.get('_region', None): image_id}

        self.__dict__.update(state)
