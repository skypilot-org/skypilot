"""Resources: compute requirements of Tasks."""
import copy
from typing import Dict, List, Optional, Union

from sky import check
from sky import clouds
from sky import global_user_state
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.utils import accelerator_registry
from sky.utils import schemas
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_DEFAULT_DISK_SIZE_GB = 256


class AcceleratorsSpec:

    def __init__(self, name: str, count: int,
                 args: Optional[Dict[str, str]]) -> None:
        self.name = accelerator_registry.canonicalize_accelerator_name(name)
        self.count = count
        self.args = args  # Only used for TPUs.
        # TODO(woosuk): Add more fields such as memory, interconnect, etc.

    def __repr__(self) -> str:
        if self.args is None:
            return f'{self.count}x{self.name}'
        else:
            return f'{self.count}x{self.name} (args: {self.args})'

    def __eq__(self, other: 'AcceleratorsSpec') -> bool:
        return self.name == other.name and \
               self.count == other.count and \
               self.args == other.args


# TODO(woosuk): Define CPU class to represent different types of CPUs.
# TODO(woosuk): Define Disks class to represent different types of disks.


class ResourceRequirements:
    """User-specified resource requirements."""

    def __init__(
        self,
        cloud: Union[None, str, clouds.Cloud] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        instance_type: Optional[str] = None,
        accelerators: Union[None, str, Dict[str, int], AcceleratorsSpec] = None,
        accelerator_args: Optional[Dict[str, str]] = None,
        use_spot: Optional[bool] = None,
        spot_recovery: Optional[str] = None,
        disk_size: Optional[int] = None,
        image_id: Optional[str] = None,
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
        self.accelerators: Optional[AcceleratorsSpec] = None

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
        if isinstance(self._accelerators, AcceleratorsSpec):
            # Here, we use the assert statement instead of raising
            # a user-friendly error because AcceleratorsSpec is not a
            # user-facing class.
            assert self._accelerator_args is not None, (
                'When accelerators is AcceleratorsSpec, accelerator_args '
                'must be specified in accelerators.args.')
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
                raise TypeError(f'Expected {self.__class__.__name__}.{field} '
                                f'to be {expected_type}, found '
                                f'{type(val).__name__}.')

    def _check_input_types(self) -> None:
        # TODO(woosuk): Do more precise type checking.
        self._check_type('cloud', (str, clouds.Cloud))
        self._check_type('region', str)
        self._check_type('zone', str)
        self._check_type('instance_type', str)
        self._check_type('_accelerators', (str, dict, AcceleratorsSpec))
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
            # NOTE: Azure instance types include uppercase letters.
            self.instance_type = self.instance_type.lower()
        if self.spot_recovery is not None:
            self.spot_recovery = self.spot_recovery.upper()

        if self._accelerators is None:
            return
        if isinstance(self._accelerators, AcceleratorsSpec):
            self.accelerators = self._accelerators
            return

        # Parse accelerators.
        if isinstance(self._accelerators, dict):
            if len(self._accelerators) != 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Accelerators must be specified as a single '
                        'accelerator name and count.')
            # TODO: Check that acc_name is string and acc_count is int
            # in _check_input_types.
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

        self.accelerators = AcceleratorsSpec(name=acc_name,
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
    def from_yaml_config(cls, config: Dict[str, str]) -> 'ResourceRequirements':
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
                'cloud', 'region', 'zone', 'instance_type', 'use_spot',
                'disk_size', 'image_id'
        ]:
            val = getattr(self, field)
            if val is not None:
                config[field] = val
        return config

    def copy(self) -> 'ResourceRequirements':
        # FIXME
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}('
                f'cloud={self.cloud}, '
                f'region={self.region}, '
                f'zone={self.zone}, '
                f'instance_type={self.instance_type}, '
                f'accelerators={self.accelerators}, '
                f'use_spot={self.use_spot}, '
                f'spot_recovery={self.spot_recovery}, '
                f'disk_size={self.disk_size}, '
                f'image_id={self.image_id})')


# User-facing class.
class Resources(ResourceRequirements):
    pass


class VMSpec:
    """SkyPilot's internal representation of the resources in a cloud VM."""

    def __init__(
        self,
        cloud: clouds.Cloud,
        region: str,
        zone: Optional[str],
        instance_type: str,
        cpu: float,
        memory: float,
        accelerators: Optional[AcceleratorsSpec],
        use_spot: bool,
        disk_size: int,
        image_id: Optional[str],
    ) -> None:
        # NOTE: instance_type and instance_family need NOT be lower-cased.
        # They follow the values in the cloud catalogs.
        self.cloud = cloud
        self.region = region
        self.zone = zone
        self.instance_type = instance_type
        self.cpu = cpu
        self.memory = memory
        self.accelerators = accelerators
        self.use_spot = use_spot
        self.disk_size = disk_size
        self.image_id = image_id

    def get_hourly_price(self) -> float:
        return self.cloud.get_hourly_price(self)

    def __eq__(self, other: 'VMSpec') -> bool:
        if not self.cloud.is_same_cloud(other.cloud):
            return False
        return (self.region == other.region and \
                self.zone == other.zone and
                self.instance_type == other.instance_type and
                self.cpu == other.cpu and \
                self.memory == other.memory and
                self.accelerators == other.accelerators and
                self.use_spot == other.use_spot and
                self.disk_size == other.disk_size and
                self.image_id == other.image_id)

    def __repr__(self) -> str:
        return ('VMSpec('
                f'cloud={self.cloud}, '
                f'region={self.region}, '
                f'zone={self.zone}, '
                f'instance_type={self.instance_type}, '
                f'cpu={self.cpu}, '
                f'memory={self.memory}, '
                f'accelerators={self.accelerators}, '
                f'use_spot={self.use_spot}, '
                f'disk_size={self.disk_size}, '
                f'image_id={self.image_id})')


class ClusterSpec:
    """SkyPilot's internal representation of the resources in a cluster."""

    def __init__(
        self,
        vm_specs: List[VMSpec],
    ) -> None:
        assert vm_specs, 'vm_specs cannot be empty.'
        self.num_nodes = len(vm_specs)
        self.vm_specs = vm_specs

        # NOTE: Currently, we assume that all VMs in a cluster are identical.
        # TODO(woosuk): support heterogeneous clusters.
        head_node = self.get_head_node()
        self.cloud = head_node.cloud
        self.region = head_node.region
        self.zone = head_node.zone
        self.instance_type = head_node.instance_type
        self.cpu = head_node.cpu
        self.memory = head_node.memory
        self.accelerators = head_node.accelerators
        self.use_spot = head_node.use_spot
        self.disk_size = head_node.disk_size
        self.image_id = head_node.image_id

        # TODO(woosuk): Add cluster-wide configurations such as interconnects.

    def get_head_node(self) -> VMSpec:
        return self.vm_specs[0]

    def get_hourly_price(self) -> float:
        return sum(vm.get_hourly_price() for vm in self.vm_specs)

    def get_cost(self, seconds: float) -> float:
        hours = seconds / 3600.0
        return hours * self.get_hourly_price()

    def __eq__(self, other: 'ClusterSpec') -> bool:
        if self.num_nodes != other.num_nodes:
            return False
        # NOTE: this relies on the assumption that all VMs in a cluster are
        # identical.
        head_node = self.get_head_node()
        other_head_node = other.get_head_node()
        return head_node == other_head_node

    def __repr__(self) -> str:
        return ('ClusterSpec('
                f'num_nodes={self.num_nodes}, '
                f'head_node={self.get_head_node()})')


# User-facing class.
class JobResources:

    def __init__(
        self,
        num_nodes: Optional[int] = None,
        gpus: Union[None, str, Dict[str, float]] = None,
    ) -> None:
        self.num_nodes = num_nodes
        self._gpus = gpus

        # Set by canonicalization.
        self.acc_name = None
        self.acc_count = None

        self._check_input_types()
        self._canonicalize()
        self._assign_defaults()
        self._check_semantics()

    def _check_type(self, field: str, expected_type) -> None:
        # FIXME(woosuk): Duplicated code. Refactor.
        val = getattr(self, field)
        if val is not None and not isinstance(val, expected_type):
            if field.startswith('_'):
                field = field[1:]
            if isinstance(expected_type, tuple):
                expected_type = ' or '.join([t.__name__ for t in expected_type])
            else:
                expected_type = expected_type.__name__
            with ux_utils.print_exception_no_traceback():
                raise TypeError(f'Expected {self.__class__.__name__}.{field} '
                                f'to be {expected_type}, found '
                                f'{type(val).__name__}.')

    def _check_input_types(self) -> None:
        self._check_type('num_nodes', (int))
        self._check_type('_gpus', (str, dict))

    def _canonicalize(self) -> None:
        if self._gpus is None:
            return

        # FIXME(woosuk): Duplicated code. Refactor.
        # Parse gpus.
        if isinstance(self._gpus, dict):
            if len(self._gpus) != 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('gpus must be specified as a single '
                                     'accelerator name and count.')
            # TODO: Check that acc_name is string and acc_count is float
            # in _check_input_types.
            acc_name, acc_count = list(self._gpus.items())[0]
            acc_count = float(acc_count)
        else:
            assert isinstance(self._gpus, str)
            if ':' not in self._gpus:
                acc_name = self._gpus
                acc_count = 1.0
            else:
                splits = self._gpus.split(':')
                parse_error = ('gpus must be either <name> or <name>:<cnt>. '
                               f'Found: {self._gpus!r}')
                if len(splits) != 2:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(parse_error)
                try:
                    acc_name = splits[0]
                    acc_count = float(splits[1])
                except ValueError:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(parse_error) from None

        # NEVER use AcceleratorsSpec class here.
        self.acc_name = accelerator_registry.canonicalize_accelerator_name(
            acc_name)
        self.acc_count = acc_count

    def _assign_defaults(self) -> None:
        if self.num_nodes is None:
            self.num_nodes = 1

    def _check_semantics(self) -> None:
        if self.num_nodes < 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('num_nodes must be >= 1.')

        if self.acc_count is None:
            return
        if self.acc_count <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('The number of GPUs must be > 0.')
        elif self.acc_count > 1:
            with ux_utils.print_exception_no_traceback():
                if not self.acc_count.is_integer():
                    raise ValueError('num_gpus must be an integer if > 1.')


class ResourceMapper:

    def __init__(self) -> None:
        self.enabled_clouds = global_user_state.get_enabled_clouds()
        self.retry = True

    def _get_feasible_clouds(
            self, cloud: Optional[clouds.Cloud]) -> List[clouds.Cloud]:
        if cloud is None:
            feasible_clouds = self.enabled_clouds
        else:
            feasible_clouds = [cloud]

        # FIXME(woosuk): Exclude local cloud for now.
        assert str(cloud) != 'Local'
        feasible_clouds = [c for c in feasible_clouds if str(c) != 'Local']
        if feasible_clouds:
            # Found a cloud that matches the filter.
            return feasible_clouds

        if not self.retry:
            # No matching cloud found.
            return []

        # Run `sky check` and try again.
        check.check(quiet=True)
        self.retry = False
        self.enabled_clouds = global_user_state.get_enabled_clouds()

        for c in self.enabled_clouds:
            if cloud is None:
                feasible_clouds.append(c)
            elif cloud.is_same_cloud(c):
                feasible_clouds.append(c)
        return feasible_clouds

    def map_suitable_vms(self,
                         resource_req: ResourceRequirements) -> List[VMSpec]:
        feasible_clouds = self._get_feasible_clouds(resource_req.cloud)
        if not feasible_clouds:
            # TODO: Print a warning.
            return []

        vms = []
        for cloud in feasible_clouds:
            # TODO: Support on-prem.
            vms += cloud.get_suitable_vms(resource_req)

        if vms:
            # Found VMs that meet the requirement.
            return vms

        return []

    def map_suitable_clusters(
        self,
        num_nodes: int,
        resource_req: ResourceRequirements,
    ) -> List[ClusterSpec]:
        vms = self.map_suitable_vms(resource_req)
        clusters = [ClusterSpec(vm_specs=[vm] * num_nodes) for vm in vms]
        return clusters
