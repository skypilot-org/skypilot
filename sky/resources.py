"""Resources: compute requirements of Tasks."""
from typing import Dict, Optional, Union

from sky import clouds
from sky import global_user_state
from sky import sky_logging

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

        # Specifying required resources; Sky decides the cloud/instance type.
        # The below are equivalent:
        sky.Resources(accelerators='V100')
        sky.Resources(accelerators='V100:1')
        sky.Resources(accelerators={'V100': 1})

        # TODO:
        sky.Resources(requests={'mem': '16g', 'cpu': 8})
    """

    def __init__(
        self,
        cloud: Optional[clouds.Cloud] = None,
        instance_type: Optional[str] = None,
        accelerators: Union[None, str, Dict[str, int]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        disk_size: Optional[int] = None,
    ):
        self.__version__ = '0.0.1'
        self.cloud = cloud

        # Calling the setter for instance_type.
        self.instance_type = instance_type

        self._use_spot_specified = use_spot is not None
        self.use_spot = use_spot if use_spot is not None else False

        if disk_size is not None:
            if disk_size < 50:
                raise ValueError(
                    f'OS disk size must be larger than 50GB. Got: {disk_size}.')
            if round(disk_size) != disk_size:
                raise ValueError(
                    f'OS disk size must be an integer. Got: {disk_size}.')
            self.disk_size = int(disk_size)
        else:
            self.disk_size = _DEFAULT_DISK_SIZE_GB

        self.set_accelerators(accelerators, accelerator_args)

    def __repr__(self) -> str:
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
    def instance_type(self):
        return self._instance_type

    @instance_type.setter
    def instance_type(self, instance_type: str):
        self._instance_type = instance_type

        if instance_type is None:
            return

        # Validate instance type
        if self.cloud is not None:
            valid = self.cloud.validate_instance_type(self._instance_type)
            if not valid:
                raise ValueError(
                    f'Invalid instance type {self._instance_type!r} '
                    f'for cloud {self.cloud}.')
        else:
            # If cloud not specified
            valid_clouds = []
            enabled_clouds = global_user_state.get_enabled_clouds()
            for cloud in enabled_clouds:
                valid = cloud.validate_instance_type(self._instance_type)
                if valid:
                    valid_clouds.append(cloud)
            if len(valid_clouds) == 0:
                raise ValueError(
                    f'Invalid instance type {self._instance_type!r} '
                    f'for any cloud {enabled_clouds}.')
            if len(valid_clouds) > 1:
                raise ValueError(
                    f'Ambiguous instance type {self._instance_type!r} '
                    f'Please specify cloud explicitly among {valid_clouds}.')
            logger.info(f'Cloud is not specified, using {valid_clouds[0]} '
                        f'inferred from the instance_type {instance_type!r}.')
            self.cloud = valid_clouds[0]

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

    def set_accelerators(
            self,
            accelerators: Union[None, str, Dict[str, int]],
            accelerator_args: Optional[Dict[str, str]] = None) -> None:
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
                    self.cloud = clouds.GCP()
                assert self.cloud.is_same_cloud(
                    clouds.GCP()), 'Cloud must be GCP.'
                if accelerator_args is None:
                    accelerator_args = {}
                if 'tf_version' not in accelerator_args:
                    logger.info('Missing tf_version in accelerator_args, using'
                                ' default (2.5.0)')
                    accelerator_args['tf_version'] = '2.5.0'

        self._accelerators = accelerators
        self.accelerator_args = accelerator_args
        self._try_validate_accelerators()

    def is_launchable(self) -> bool:
        return self.cloud is not None and self._instance_type is not None

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

    def get_cost(self, seconds: float):
        """Returns cost in USD for the runtime in seconds."""
        hours = seconds / 3600
        # Instance.
        hourly_cost = self.cloud.instance_type_to_hourly_cost(
            self._instance_type, self.use_spot)
        # Accelerators (if any).
        if self.accelerators is not None:
            hourly_cost += self.cloud.accelerators_to_hourly_cost(
                self.accelerators)
        return hourly_cost * hours

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

        if (self._instance_type is not None and
                self._instance_type != other.instance_type):
            return False
        # self._instance_type <= other.instance_type

        # For case insensitive comparison.
        # TODO(wei-lin): This is a hack. We may need to use our catalog to
        # handle this.
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
        return self.accelerators.keys() == other.accelerators.keys()

    def is_empty(self) -> bool:
        """Is this Resources an empty request (all fields None)?"""
        return all([
            self.cloud is None,
            self._instance_type is None,
            self.accelerators is None,
            self.accelerator_args is None,
            not self._use_spot_specified,
        ])

    def __setstate__(self, state):
        """Set state from pickled state, for backward compatibility."""
        version = state.pop('__version__', None)
        if version is None:
            instance_type = state.pop('instance_type', None)
            state['_instance_type'] = instance_type
        self.__dict__.update(state)
