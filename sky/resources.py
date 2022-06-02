"""Resources: compute requirements of Tasks."""
from typing import Dict, Optional, Union

from sky import clouds
from sky import global_user_state
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_DISK_SIZE_GB = 256


def _get_cloud(cloud: str) -> clouds.Cloud:
    cloud_obj = clouds.CLOUD_REGISTRY.from_str(cloud)
    if cloud is not None and cloud_obj is None:
        # Overwritten later in launch() in cli.py
        return clouds.Local()
    return cloud_obj


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

        # Specifying required resources; Sky decides the cloud/instance type.
        # The below are equivalent:
        sky.Resources(accelerators='V100')
        sky.Resources(accelerators='V100:1')
        sky.Resources(accelerators={'V100': 1})

        # TODO:
        sky.Resources(requests={'mem': '16g', 'cpu': 8})
    """
    # If any fields changed:
    # 1. Increment the version. For backward compatibility.
    # 2. Change the __setstate__ method to handle the new fields.
    # 3. Modify the to_config method to handle the new fields.
    _VERSION = 3

    def __init__(
        self,
        cloud: Optional[clouds.Cloud] = None,
        instance_type: Optional[str] = None,
        accelerators: Union[None, str, Dict[str, int]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        disk_size: Optional[int] = None,
        region: Optional[str] = None,
    ):
        self._version = self._VERSION
        self._cloud = cloud
        self._region: Optional[str] = None
        self._set_region(region)

        self._instance_type = instance_type

        self._use_spot_specified = use_spot is not None
        self._use_spot = use_spot if use_spot is not None else False
        self._spot_recovery = None
        if spot_recovery is not None:
            self._spot_recovery = spot_recovery.upper()

        if disk_size is not None:
            if disk_size < 50:
                raise ValueError(
                    f'OS disk size must be larger than 50GB. Got: {disk_size}.')
            if round(disk_size) != disk_size:
                raise ValueError(
                    f'OS disk size must be an integer. Got: {disk_size}.')
            self._disk_size = int(disk_size)
        else:
            self._disk_size = _DEFAULT_DISK_SIZE_GB

        self.local_ips = None
        self.local_resources = None
        if (self._cloud is not None and
                isinstance(self._cloud, clouds.Local) and
                str(self._cloud) != clouds.Local.DEFAULT_LOCAL_NAME):
            self.local_ips = self._cloud.get_local_ips()
            self.set_local_resources()
            self._try_validate_local()

        self._set_accelerators(accelerators, accelerator_args)

        self._try_validate_instance_type()
        self._try_validate_accelerators()
        self._try_validate_spot()

    def __repr__(self) -> str:
        if isinstance(self.cloud, clouds.Local):
            return f'Local({self.cloud}, {self.local_resources})'
        accelerators = ''
        accelerator_args = ''
        if self.accelerators is not None:
            accelerators = f', {self.accelerators}'
            if self.accelerator_args is not None:
                accelerator_args = f', accelerator_args={self.accelerator_args}'
        use_spot = ''
        if self.use_spot:
            use_spot = '[Spot]'
        return (f'{self.cloud}({self._instance_type}{use_spot}'
                f'{accelerators}{accelerator_args})')

    @property
    def cloud(self):
        return self._cloud

    @property
    def region(self):
        return self._region

    @property
    def instance_type(self):
        return self._instance_type

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
                        raise ValueError(parse_error)
                    try:
                        num = float(splits[1])
                        num = int(num) if num.is_integer() else num
                        accelerators = {splits[0]: num}
                    except ValueError:
                        raise ValueError(parse_error) from None
            assert len(accelerators) == 1, accelerators

            acc, _ = list(accelerators.items())[0]
            if 'tpu' in acc.lower():
                if self.cloud is None:
                    self._cloud = clouds.GCP()
                assert self.cloud.is_same_cloud(
                    clouds.GCP()), 'Cloud must be GCP.'
                if accelerator_args is None:
                    accelerator_args = {}
                if 'tf_version' not in accelerator_args:
                    logger.info('Missing tf_version in accelerator_args, using'
                                ' default (2.5.0)')
                    accelerator_args['tf_version'] = '2.5.0'

        self._accelerators = accelerators
        self._accelerator_args = accelerator_args

    def is_launchable(self) -> bool:
        return self.cloud is not None and self._instance_type is not None

    def _set_region(self, region: Optional[str]) -> None:
        self._region = region
        if region is None:
            return

        # Validate region.
        if self._cloud is not None:
            if not self._cloud.region_exists(region):
                raise ValueError(f'Invalid region {region!r} '
                                 f'for cloud {self.cloud}.')
        else:
            # If cloud not specified
            valid_clouds = []
            enabled_clouds = global_user_state.get_enabled_clouds()
            for cloud in enabled_clouds:
                if cloud.region_exists(region):
                    valid_clouds.append(cloud)
            if len(valid_clouds) == 0:
                if len(enabled_clouds) == 1:
                    cloud_str = f'for cloud {enabled_clouds[0]}'
                else:
                    cloud_str = f'for any cloud among {enabled_clouds}'
                raise ValueError(f'Invalid region {region!r} '
                                 f'{cloud_str}.')
            if len(valid_clouds) > 1:
                raise ValueError(
                    f'Ambiguous region {region!r} '
                    f'Please specify cloud explicitly among {valid_clouds}.')
            logger.debug(f'Cloud is not specified, using {valid_clouds[0]} '
                         f'inferred from the region {region!r}.')
            self._cloud = valid_clouds[0]
        self._region = region

    def _try_validate_instance_type(self):
        if self.instance_type is None:
            return

        # Validate instance type
        if self.cloud is not None:
            valid = self.cloud.instance_type_exists(self._instance_type)
            if not valid:
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
                raise ValueError(
                    f'Invalid instance type {self._instance_type!r} '
                    f'{cloud_str}.')
            if len(valid_clouds) > 1:
                raise ValueError(
                    f'Ambiguous instance type {self._instance_type!r}. '
                    f'Please specify cloud explicitly among {valid_clouds}.')
            logger.debug(
                f'Cloud is not specified, using {valid_clouds[0]} '
                f'inferred from the instance_type {self.instance_type!r}.')
            self._cloud = valid_clouds[0]

    def _try_validate_accelerators(self) -> None:
        """Try-validates accelerators against the instance type."""
        if self.is_launchable() and not isinstance(self.cloud, clouds.GCP):
            # GCP attaches accelerators to VMs, so no need for this check.
            acc_requested = self.accelerators
            if acc_requested is None:
                return
            acc_from_instance_type = (
                self.cloud.get_accelerators_from_instance_type(
                    self._instance_type))
            if not Resources(accelerators=acc_requested).less_demanding_than(
                    Resources(accelerators=acc_from_instance_type)):
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

    def _try_validate_spot(self) -> None:
        if self._spot_recovery is None:
            return
        if not self._use_spot:
            raise ValueError(
                'Cannot specify spot_recovery without use_spot set to True.')
        if self._spot_recovery not in spot.SPOT_STRATEGIES:
            raise ValueError(f'Spot recovery strategy {self._spot_recovery} '
                             'is not supported. The strategy should be among '
                             f'{list(spot.SPOT_STRATEGIES.keys())}')

    def _try_validate_local(self) -> None:
        if self._instance_type is not None:
            raise ValueError('Local/On-prem mode does not support instance '
                             f'type {self._instance_type}')
        if self._use_spot:
            raise ValueError('Local/On-prem mode does not support spot '
                             'instances.')
        if not self.local_ips:
            raise ValueError('Local/On-prem mode needs IPs specified. '
                             'Please check the yaml in ~/.sky/local.')

    def get_cost(self, seconds: float) -> float:
        """Returns cost in USD for the runtime in seconds."""
        hours = seconds / 3600
        # Instance.
        hourly_cost = self.cloud.instance_type_to_hourly_cost(
            self._instance_type, self.use_spot)
        # Accelerators (if any).
        if self.accelerators is not None:
            hourly_cost += self.cloud.accelerators_to_hourly_cost(
                self.accelerators, self.use_spot)
        return hourly_cost * hours

    def set_local_resources(self, auth_config: dict = None) -> None:
        """Sets local resources for the local cluster.

        Attempts to fetch local accelerator resources from global state.
        If not, fetches the local accelerator resources by querying the
        local cluster nodes.
        """
        assert isinstance(self.cloud, clouds.Local), 'Must be local cloud.'
        cluster_name = str(self.cloud)
        custom_resources = None
        if auth_config is None:
            # Attempt to fetch from global state, elsewise do nothing
            records = global_user_state.get_clusters(
                include_cloud_clusters=False, include_local_clusters=True)
            for record in records:
                resources = record['handle'].launched_resources
                record_name = str(resources.cloud)
                if cluster_name == record_name:
                    custom_resources = resources.local_resources
                    break
        else:
            custom_resources = backend_utils.get_local_custom_resources(
                self.local_ips, auth_config)
        self.local_resources = custom_resources

    def is_same_resources(self, other: 'Resources') -> bool:
        """Returns whether two resources are the same.

        Returns True if they are the same, False if not.
        """
        if (self.cloud is None) != (other.cloud is None):
            # self and other's cloud should be both None or both not None
            return False

        if self.cloud is not None and not self.cloud.is_same_cloud(other.cloud):
            return False
        # self.cloud == other.cloud

        if (self.region is None) != (other.region is None):
            # self and other's region should be both None or both not None
            return False

        if self.region is not None and self.region != other.region:
            return False
        # self.region <= other.region

        if (self._instance_type is not None and
                self._instance_type != other.instance_type):
            return False
        # self._instance_type == other.instance_type

        other_accelerators = other.accelerators
        accelerators = self.accelerators
        if accelerators != other_accelerators:
            return False
        # self.accelerators == other.accelerators

        if self.accelerator_args != other.accelerator_args:
            return False
        # self.accelerator_args == other.accelerator_args

        if self.use_spot != other.use_spot:
            return False

        # self == other
        return True

    def less_demanding_than(self, other: 'Resources') -> bool:
        """Returns whether this resources is less demanding than the other."""
        if self.cloud is not None and not self.cloud.is_same_cloud(other.cloud):
            return False
        # self.cloud <= other.cloud

        if self.region is not None and self.region != other.region:
            return False
        # self.region <= other.region

        if (self._instance_type is not None and
                self._instance_type != other.instance_type):
            return False
        # self._instance_type <= other.instance_type

        # For case insensitive comparison.
        # TODO(wei-lin): This is a hack. We may need to use our catalog to
        # handle this.
        if isinstance(other.cloud, clouds.Local):
            # We are comparing if a Task's resources fits the cluster's
            # resources. The cluster's resources is in
            # other.local_resources. The other field, other.accelerators,
            # denotes the task resources of the previous job launched
            # (that was saved into CloudVMHandle).
            list_resources = other.local_resources
            other_accelerators = {}
            for d in list_resources:
                for k in d:
                    other_accelerators[k] = other_accelerators.get(k, 0) + d[k]
        else:
            other_accelerators = other.accelerators
        if other_accelerators is not None:
            other_accelerators = {
                acc.upper(): num_acc
                for acc, num_acc in other_accelerators.items()
            }
        if self.accelerators is not None and other_accelerators is None:
            return False

        if self.accelerators is not None:
            for acc in self.accelerators:
                if acc.upper() not in other_accelerators:
                    return False
                if self.accelerators[acc] > other_accelerators[acc.upper()]:
                    return False
        # self.accelerators <= other.accelerators

        if (self.accelerator_args is not None and
                self.accelerator_args != other.accelerator_args):
            return False
        # self.accelerator_args == other.accelerator_args

        if self.use_spot != other.use_spot:
            return False

        # self <= other
        return True

    def is_launchable_fuzzy_equal(self, other: 'Resources') -> bool:
        """Whether the resources are the fuzzily same launchable resources."""
        assert self.cloud is not None and other.cloud is not None
        if not self.cloud.is_same_cloud(other.cloud):
            return False
        if self._instance_type is not None or other.instance_type is not None:
            return self._instance_type == other.instance_type
        # For GCP, when a accelerator type fails to launch, it should be blocked
        # regardless of the count, since the larger number will fail either.
        return (self.accelerators is None and other.accelerators is None
               ) or self.accelerators.keys() == other.accelerators.keys()

    def is_empty(self) -> bool:
        """Is this Resources an empty request (all fields None)?"""
        return all([
            self.cloud is None,
            self._instance_type is None,
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
            accelerators=override.pop('accelerators', self.accelerators),
            accelerator_args=override.pop('accelerator_args',
                                          self.accelerator_args),
            use_spot=override.pop('use_spot', use_spot),
            spot_recovery=override.pop('spot_recovery', self.spot_recovery),
            disk_size=override.pop('disk_size', self.disk_size),
            region=override.pop('region', self.region),
        )
        # This field is dynamically generated.
        # To save time, the field is directly passed in.
        resources.local_resources = self.local_resources
        assert len(override) == 0
        return resources

    @classmethod
    def from_yaml_config(cls, config: Optional[Dict[str, str]]) -> 'Resources':
        if config is None:
            return Resources()
        resources_fields = dict()
        if config.get('cloud') is not None:
            resources_fields['cloud'] = _get_cloud(config.pop('cloud'))
        if config.get('instance_type') is not None:
            resources_fields['instance_type'] = config.pop('instance_type')
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

        if len(config) > 0:
            raise ValueError(f'Unknown fields in resources config: {config}')
        return Resources(**resources_fields)

    def to_yaml_config(self) -> Dict[str, Union[str, int]]:
        """Returns a yaml-style dict of config for this resource bundle."""
        config = {}

        def add_if_not_none(key, value):
            if value is not None and value != 'None':
                config[key] = value

        add_if_not_none('cloud', str(self.cloud))
        add_if_not_none('instance_type', self.instance_type)
        add_if_not_none('accelerators', self.accelerators)
        add_if_not_none('accelerator_args', self.accelerator_args)

        if self._use_spot_specified:
            add_if_not_none('use_spot', self.use_spot)
        config['spot_recovery'] = self.spot_recovery
        config['disk_size'] = self.disk_size
        add_if_not_none('region', self.region)
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
            cloud = state.pop('cloud')
            state['_cloud'] = cloud

            instance_type = state.pop('instance_type')
            state['_instance_type'] = instance_type

            use_spot = state.pop('use_spot')
            state['_use_spot'] = use_spot

            accelerator_args = state.pop('accelerator_args')
            state['_accelerator_args'] = accelerator_args

            disk_size = state.pop('disk_size')
            state['_disk_size'] = disk_size

        if version < 2:
            self._region = None

        if version < 3:
            self._spot_recovery = None
        self.__dict__.update(state)
