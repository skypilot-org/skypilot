"""Resources: compute requirements of Tasks."""
import copy
from typing import Dict, List, Optional, Union

from sky import clouds
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.utils import accelerator_registry
from sky.utils import schemas
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_DISK_SIZE_GB = 256


class Accelerator:

    def __init__(self, name: str, count: int,
                 args: Optional[Dict[str, str]]) -> None:
        self.name = accelerator_registry.canonicalize_accelerator_name(name)
        self.count = count
        self.args = args

    def __repr__(self) -> str:
        return f'<Accelerator: {self.name}x{self.count}>'

    def __eq__(self, other: 'Accelerator') -> bool:
        if self.name != other.name:
            return False
        if self.count != other.count:
            return False
        if self.args is None:
            return other.args is None
        elif other.args is None:
            return False
        else:
            return self.args == other.args


class ResourceFilter:
    """A user-specified resource filter."""

    def __init__(
        self,
        cloud: Union[None, str, clouds.Cloud] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        instance_type: Optional[str] = None,
        accelerators: Union[None, str, Dict[str, int]] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        disk_size: Optional[int] = None,
        image_id: Optional[str] = None, # FIXME: image_id can be a dictionary.
    ) -> None:
        self.cloud = cloud
        self.region = region
        self.zone = zone
        self.instance_type = instance_type
        self._accelerators = accelerators
        self._accelerator_args = accelerator_args
        self.use_spot = use_spot
        self.spot_recovery = spot_recovery
        self.disk_size = disk_size
        self.image_id = image_id

        # Set by canonicalization.
        self.accelerator: Optional[Accelerator] = None

        self._check_syntax()
        self._check_input_types()
        self._canonicalize()
        self._assign_defaults()
        self._check_semantics()

    def _check_syntax(self) -> None:
        if self.cloud is None and (self.region is not None or
                                   self.zone is not None):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Cannot specify region/zone without cloud.')
        if self._accelerators is None and self._accelerator_args is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify accelerator_args without accelerators.')
        if ((self.use_spot is None or not self.use_spot) and
                self.spot_recovery is not None):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot specify spot_recovery for non-spot instances.')

    def _check_type(self, field: str, expected_type) -> None:
        val = getattr(self, field)
        if val is not None and not isinstance(val, expected_type):
            if field.startswith('_'):
                field = field[1:]
            if isinstance(expected_type, tuple):
                expected_type = ' or '.join([t.__name__ for t in expected_type])
            else:
                expected_type = expected_type.__name__
            with ux_utils.print_exception_no_traceback():
                raise TypeError(f'Expected Resources.{field} to be '
                                f'{expected_type}, found {type(val)}.')

    def _check_input_types(self) -> None:
        # TODO(woosuk): Do more precise type checking.
        self._check_type('cloud', (str, clouds.Cloud))
        self._check_type('region', str)
        self._check_type('zone', str)
        self._check_type('instance_type', str)
        self._check_type('_accelerators', (str, dict))
        self._check_type('_accelerator_args', dict)
        self._check_type('use_spot', bool)
        self._check_type('spot_recovery', str)
        self._check_type('disk_size', int)
        self._check_type('image_id', str)

    def _canonicalize(self) -> None:
        if self.cloud is not None:
            if isinstance(self.cloud, str):
                self.cloud = clouds.CLOUD_REGISTRY.from_str(self.cloud)
        if self.region is not None:
            self.region = self.region.lower()
        if self.zone is not None:
            self.zone = self.zone.lower()
        if self.instance_type is not None:
            # NOTE: Some azure instance types use uppercase letters.
            self.instance_type = self.instance_type.lower()
        if self.spot_recovery is not None:
            self.spot_recovery = self.spot_recovery.upper()

        if self._accelerators is None:
            return
        # Parse accelerators.
        if isinstance(self._accelerators, dict):
            if len(self._accelerators) != 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Accelerators must be specified as a single '
                        'accelerator name and count.')
            acc_name, acc_count = list(self._accelerators.items())[0]
        else:
            assert isinstance(self._accelerators, str)
            if ':' not in self._accelerators:
                acc_name = self._accelerators
                acc_count = 1
            else:
                splits = self._accelerators.split(':')
                parse_error = ('The "accelerators" field must be '
                            'either <name> or <name>:<cnt>. '
                            f'Found: {self._accelerators!r}')
                if len(splits) != 2:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(parse_error)
                try:
                    # NOTE: accelerator count must be an integer.
                    acc_name = splits[0]
                    acc_count = int(splits[1])
                except ValueError:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(parse_error) from None

        self.accelerator = Accelerator(name=acc_name,
                                       count=acc_count,
                                       args=self._accelerator_args)

    def _assign_defaults(self) -> None:
        if self.use_spot is None:
            self.use_spot = False
        if self.disk_size is None:
            self.disk_size = _DEFAULT_DISK_SIZE_GB
        if self.use_spot and self.spot_recovery is None:
            self.spot_recovery = spot.SPOT_DEFAULT_STRATEGY

    def _check_semantics(self) -> None:
        if self.disk_size < 50:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('OS disk size must be larger than 50GB. '
                                 f'Got {self.disk_size} GB.')
        if (self.spot_recovery is not None and
                self.spot_recovery not in spot.SPOT_STRATEGIES):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid spot_recovery strategy: {self.spot_recovery}.')

    @classmethod
    def from_yaml_config(cls, config: Dict[str, str]) -> 'ResourceFilter':
        # Validate the YAML schema.
        backend_utils.validate_schema(config, schemas.get_resources_schema(),
                                      'Invalid resources YAML: ')
        # Parse the YAML.
        resources_fields = dict()
        for field in [
                'cloud', 'region', 'zone', 'instance_type', 'accelerators',
                'accelerator_args', 'use_spot', 'disk_size', 'image_id'
        ]:
            val = config.pop(field, None)
            if field == 'disk_size':
                val = int(val)
            elif field == 'accelerator_args':
                val = dict(val)
            resources_fields[field] = val
        assert not config, f'Invalid resource args: {config.keys()}'
        return cls(**resources_fields)

    def to_yaml_config(self) -> Dict[str, Union[str, int]]:
        # TODO: implement this.
        config = {}
        for field in [
                'cloud', 'region', 'zone', 'instance_type',
                'use_spot', 'disk_size', 'image_id'
        ]:
            val = getattr(self, field)
            if val is not None:
                config[field] = val
        return config

    def copy(self) -> 'ResourceFilter':
        # FIXME
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        # TODO
        pass


# User-facing class.
class Resources(ResourceFilter):
    pass


class VMResources:
    """SkyPilot's internal representation of the resources in a cloud VM."""

    def __init__(
        self,
        cloud: clouds.Cloud,
        region: str,
        zone: str,
        instance_type: str,
        num_vcpus: float,
        cpu_memory: float,
        accelerator: Optional[Accelerator],
        use_spot: bool,
        spot_recovery: Optional[str],
        disk_size: int,
        image_id: Optional[str],
    ) -> None:
        # NOTE: instance_type and instance_family need NOT be lower-cased.
        # They follow the values in the cloud catalogs.
        self.cloud = cloud
        self.region = region
        self.zone = zone
        self.instance_type = instance_type
        self.num_vcpus = num_vcpus
        self.cpu_memory = cpu_memory
        self.accelerator = accelerator
        self.use_spot = use_spot
        self.spot_recovery = spot_recovery
        self.disk_size = disk_size
        self.image_id = image_id

    def get_hourly_price(self) -> float:
        return self.cloud.get_hourly_price(self)

    def __eq__(self, other: 'VMResources') -> bool:
        if not self.cloud.is_same_cloud(other.cloud):
            return False
        return (self.region == other.region and
                self.zone == other.zone and
                self.instance_type == other.instance_type and
                self.accelerator == other.accelerator and
                self.use_spot == other.use_spot and
                self.spot_recovery == other.spot_recovery and
                self.disk_size == other.disk_size and
                self.image_id == other.image_id)

    def __repr__(self) -> str:
        return (f'VMResources('
                f'cloud={self.cloud}, '
                f'region={self.region}, '
                f'zone={self.zone}, '
                f'instance_type={self.instance_type}, '
                f'accelerator={self.accelerator}, '
                f'use_spot={self.use_spot}, '
                f'disk_size={self.disk_size}, '
                f'image_id={self.image_id})')


class ClusterResources:
    """SkyPilot's internal representation of the resources in a cluster."""

    def __init__(
        self,
        vm_resources: List[VMResources],
    ) -> None:
        assert vm_resources, 'vm_resources cannot be empty.'
        self.num_nodes = len(vm_resources)
        self.vm_resources = vm_resources

        # Currently, we assume that all VMs in a cluster are identical.
        # TODO(woosuk): support heterogeneous clusters.
        self.cloud = vm_resources[0].cloud
        self.region = vm_resources[0].region
        self.zone = vm_resources[0].zone
        self.instance_type = vm_resources[0].instance_type
        self.num_vcpus = vm_resources[0].num_vcpus
        self.cpu_memory = vm_resources[0].cpu_memory
        self.accelerator = vm_resources[0].accelerator
        self.use_spot = vm_resources[0].use_spot
        self.spot_recovery = vm_resources[0].spot_recovery
        self.disk_size = vm_resources[0].disk_size
        self.image_id = vm_resources[0].image_id

    def get_hourly_price(self) -> float:
        return sum(vm.get_hourly_price() for vm in self.vm_resources)

    def get_cost(self, seconds: float) -> float:
        hours = seconds / 3600.0
        return hours * self.get_hourly_price()
