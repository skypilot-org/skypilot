"""Modal service catalog.

Modal sizes CPU and memory per container, so it has no fixed instance types.
As on Kubernetes, SkyPilot builds them on the fly from the request, e.g.
`4CPU--16GB` or `32CPU--128GB--H100:2`, and prices them from the hosted
pricing table `modal/pricing.csv` (made by
sky/catalog/data_fetchers/fetch_modal.py). That table has one row per
resource and region: per-hour rates for a Modal CPU core (2 vCPUs), a GiB of
memory and each GPU type, plus each GPU's maximum count per container and its
memory. CPU and memory also carry Modal's non-preemptible rates, which
CPU-only clusters pay since they run non-preemptible.
"""

import math
import re
from typing import Dict, List, Optional, Set, Tuple, Union

import pandas as pd

from sky.catalog import common
from sky.clouds import cloud
from sky.utils import resources_utils
from sky.utils import ux_utils

_CLOUD_NAME = 'modal'
_DISPLAY_NAME = 'Modal'

AUTO_REGION = 'auto'

# Names of the CPU and memory rows in modal/pricing.csv; every other row is a
# GPU. Keep in sync with fetch_modal.py.
CPU_RESOURCE = 'cpu-core'
MEMORY_RESOURCE = 'memory-gib'

_PULL_FREQUENCY_HOURS = 24
_pricing = common.read_catalog('modal/pricing.csv',
                               pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_DEFAULT_SKY_VCPUS = 4.0
_DEFAULT_MEMORY_GIB = 16
_DEFAULT_MEMORY_CPU_RATIO = _DEFAULT_MEMORY_GIB / _DEFAULT_SKY_VCPUS
# Without a CPU request, GPU containers get 4 vCPUs per GPU, as on Kubernetes
# (memory follows at _DEFAULT_MEMORY_CPU_RATIO).
_DEFAULT_SKY_VCPUS_PER_GPU = 4
# GPU counts shown by `sky gpus list`; a launch may use any count up to the
# GPU's maximum.
_LISTED_GPU_COUNTS = (1, 2, 4, 8)

# Modal validates these bounds when creating a Function. See:
# https://modal.com/docs/guide/resources#how-much-can-i-request
_MIN_MODAL_CPU_CORES = 0.125
_MAX_MODAL_CPU_CORES = 64.0
_MIN_MEMORY_MIB = 128
_MAX_MEMORY_MIB = 344064

_VIRTUAL_INSTANCE_TYPE_PATTERN = re.compile(
    r'^(?P<vcpus>\d+(?:\.\d+)?)CPU--'
    r'(?P<memory>\d+(?:\.\d+)?)GB'
    r'(?:--(?P<accelerator>[\w-]+):(?P<count>\d+))?$')


def _gpu_rows() -> pd.DataFrame:
    """One pricing row per GPU type, with its MaxCount and GpuMemoryGiB."""
    df = _pricing[~_pricing['Resource'].isin((CPU_RESOURCE, MEMORY_RESOURCE))]
    return df[df['Region'] == AUTO_REGION]


def _region_names() -> Set[str]:
    return set(_pricing['Region'])


def _canonical_accelerator(accelerator: str) -> Optional[str]:
    accelerator_lower = accelerator.lower()
    for supported_accelerator in _gpu_rows()['Resource']:
        if supported_accelerator.lower() == accelerator_lower:
            return supported_accelerator
    return None


def get_max_gpu_count(accelerator: str) -> Optional[int]:
    """Most GPUs of this type Modal offers per container; None if unknown."""
    canonical = _canonical_accelerator(accelerator)
    if canonical is None:
        return None
    rows = _gpu_rows()
    return int(rows[rows['Resource'] == canonical]['MaxCount'].iloc[0])


def _gpu_memory_gib(accelerator: str) -> int:
    rows = _gpu_rows()
    return int(rows[rows['Resource'] == accelerator]['GpuMemoryGiB'].iloc[0])


def _format_resource_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f'{value:.12f}'.rstrip('0').rstrip('.')


class ModalInstanceType:
    """Virtual instance type for a Modal resource request."""

    def __init__(self,
                 vcpus: float,
                 memory_gib: float,
                 accelerator_count: Optional[int] = None,
                 accelerator_type: Optional[str] = None):
        modal_cpu = vcpus / 2
        memory_mib = math.ceil(memory_gib * 1024)
        if not _MIN_MODAL_CPU_CORES <= modal_cpu <= _MAX_MODAL_CPU_CORES:
            raise ValueError('Modal CPU request is out of bounds.')
        if not _MIN_MEMORY_MIB <= memory_mib <= _MAX_MEMORY_MIB:
            raise ValueError('Modal memory request is out of bounds.')
        if (accelerator_count is None) != (accelerator_type is None):
            raise ValueError('Modal accelerator type and count must be set '
                             'together.')
        if accelerator_type is not None:
            assert accelerator_count is not None
            canonical_accelerator = _canonical_accelerator(accelerator_type)
            if canonical_accelerator is None:
                raise ValueError(
                    f'Unsupported Modal accelerator {accelerator_type!r}.')
            max_count = get_max_gpu_count(canonical_accelerator)
            assert max_count is not None
            if not 1 <= accelerator_count <= max_count:
                raise ValueError(
                    f'Modal offers up to {max_count} {canonical_accelerator} '
                    f'per container, not {accelerator_count}.')
            accelerator_type = canonical_accelerator
        self.vcpus = vcpus
        self.memory_mib = memory_mib
        self.accelerator_count = accelerator_count
        self.accelerator_type = accelerator_type

    @property
    def memory_gib(self) -> float:
        return self.memory_mib / 1024

    @property
    def modal_cpu(self) -> float:
        return self.vcpus / 2

    @property
    def name(self) -> str:
        name = (f'{_format_resource_value(self.vcpus)}CPU--'
                f'{_format_resource_value(self.memory_gib)}GB')
        if self.accelerator_type is not None:
            name += f'--{self.accelerator_type}:{self.accelerator_count}'
        return name

    @classmethod
    def from_instance_type(cls, name: str) -> 'ModalInstanceType':
        match = _VIRTUAL_INSTANCE_TYPE_PATTERN.fullmatch(name)
        if match is None:
            raise ValueError(f'Invalid Modal instance type {name!r}.')
        accelerator_count = match.group('count')
        return cls(vcpus=float(match.group('vcpus')),
                   memory_gib=float(match.group('memory')),
                   accelerator_count=(int(accelerator_count) if
                                      accelerator_count is not None else None),
                   accelerator_type=match.group('accelerator'))


def _rate(region_rates: pd.DataFrame, resource: str, column: str) -> float:
    rows = region_rates[region_rates['Resource'] == resource]
    if rows.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No Modal price for {resource} in region '
                             f'{region_rates["Region"].iloc[0]!r}.')
    return float(rows[column].iloc[0])


def _compute_price_per_hour(instance: ModalInstanceType, region: str) -> float:
    """Price of a container from the region's per-hour rates."""
    region_rates = _pricing[_pricing['Region'] == region]
    if region_rates.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Invalid Modal region {region!r}.')
    # GPU containers run preemptible at Modal's list rates; CPU-only
    # containers run non-preemptible.
    column = ('HourlyPrice' if instance.accelerator_type is not None else
              'NonPreemptibleHourlyPrice')
    price = (instance.modal_cpu * _rate(region_rates, CPU_RESOURCE, column) +
             instance.memory_gib * _rate(region_rates, MEMORY_RESOURCE, column))
    if instance.accelerator_type is not None:
        assert instance.accelerator_count is not None
        price += instance.accelerator_count * _rate(
            region_rates, instance.accelerator_type, 'HourlyPrice')
    return price


def instance_type_exists(instance_type: str) -> bool:
    try:
        ModalInstanceType.from_instance_type(instance_type)
    except ValueError:
        return False
    return True


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    return common.validate_region_zone_impl(_CLOUD_NAME, _pricing, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    if use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support spot instances.')
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    instance = ModalInstanceType.from_instance_type(instance_type)
    return _compute_price_per_hour(instance, region or AUTO_REGION)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    instance = ModalInstanceType.from_instance_type(instance_type)
    return instance.vcpus, instance.memory_gib


def _resources_from_requests(cpus: Optional[str],
                             memory: Optional[str]) -> ModalInstanceType:
    vcpus = (float(cpus.rstrip('+'))
             if cpus is not None else _DEFAULT_SKY_VCPUS)
    if memory is None:
        memory_gib = min(vcpus * _DEFAULT_MEMORY_CPU_RATIO,
                         _MAX_MEMORY_MIB / 1024)
    elif memory.endswith('+'):
        memory_gib = float(memory[:-1])
    elif memory.endswith('x'):
        memory_gib = float(memory[:-1]) * vcpus
    else:
        memory_gib = float(memory)
    return ModalInstanceType(vcpus, memory_gib)


def _gpu_instance(accelerator: str, count: int, cpus: Optional[str],
                  memory: Optional[str]) -> ModalInstanceType:
    """GPU container shape; without a CPU request, 4 vCPUs per GPU."""
    if cpus is None:
        cpus = str(_DEFAULT_SKY_VCPUS_PER_GPU * count)
    base = _resources_from_requests(cpus, memory)
    return ModalInstanceType(base.vcpus,
                             base.memory_gib,
                             accelerator_count=count,
                             accelerator_type=accelerator)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # unused
    if use_spot or zone is not None:
        return None
    if region is not None and region not in _region_names():
        return None
    try:
        instance = _resources_from_requests(cpus, memory)
    except ValueError:
        return None
    if (max_hourly_cost is not None and
            get_hourly_cost(instance.name, region=region) > max_hourly_cost):
        return None
    return instance.name


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    instance = ModalInstanceType.from_instance_type(instance_type)
    if instance.accelerator_type is None:
        return None
    assert instance.accelerator_count is not None
    return {instance.accelerator_type: instance.accelerator_count}


def get_arch_from_instance_type(instance_type: str) -> Optional[str]:
    ModalInstanceType.from_instance_type(instance_type)
    return None


def get_local_disk_from_instance_type(instance_type: str) -> Optional[str]:
    ModalInstanceType.from_instance_type(instance_type)
    return None


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None
) -> Tuple[Optional[List[str]], List[str]]:
    del local_disk  # unused
    if use_spot or zone is not None:
        if use_spot:
            return None, []
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    if region is not None and region not in _region_names():
        return None, []
    canonical_accelerator = _canonical_accelerator(acc_name)
    if canonical_accelerator is None:
        # Unknown GPU: fuzzy-match the name against the listed shapes.
        return common.get_instance_type_for_accelerator_impl(
            df=_listed_instances(),
            acc_name=acc_name,
            acc_count=acc_count,
            cpus=None,
            memory=None,
            use_spot=False,
            region=region,
            zone=None,
            max_hourly_cost=max_hourly_cost)
    try:
        instance = _gpu_instance(canonical_accelerator, acc_count, cpus, memory)
    except ValueError:
        return [], []
    if (max_hourly_cost is not None and
            get_hourly_cost(instance.name, region=region) > max_hourly_cost):
        return [], []
    return [instance.name], []


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    ModalInstanceType.from_instance_type(instance_type)
    return [] if use_spot else regions()


def _accelerator_offered(accelerator: str, count: int,
                         region: Optional[str]) -> bool:
    max_count = get_max_gpu_count(accelerator)
    if max_count is None or not 1 <= count <= max_count:
        return False
    return region is None or region in _region_names()


def get_accelerator_hourly_cost(accelerator: str,
                                count: int,
                                use_spot: bool = False,
                                region: Optional[str] = None,
                                zone: Optional[str] = None) -> float:
    if use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support spot instances.')
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    if not _accelerator_offered(accelerator, count, region):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No accelerator {accelerator}:{count} found.')
    # The GPU price is part of the instance type's hourly cost.
    return 0.0


def get_region_zones_for_accelerators(
        accelerator: str,
        count: int,
        use_spot: bool = False) -> List[cloud.Region]:
    if use_spot or not _accelerator_offered(accelerator, count, region=None):
        return []
    return regions()


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None) -> None:
    del instance_type, accelerators  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')


def _gpu_info(accelerator: str, count: int) -> str:
    gpu_memory_mib = _gpu_memory_gib(accelerator) * 1024
    return repr({
        'Gpus': [{
            'Name': accelerator,
            'Manufacturer': 'NVIDIA',
            'Count': count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib,
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * count,
    })


def _listed_instances() -> pd.DataFrame:
    """The shapes a launch picks without a CPU or memory request, per region.

    `sky gpus list` and region lookups read this standard catalog table,
    which is built from the pricing rows rather than stored.
    """
    instances = [_resources_from_requests(None, None)]
    for accelerator, max_count in zip(_gpu_rows()['Resource'],
                                      _gpu_rows()['MaxCount']):
        instances.extend(
            _gpu_instance(accelerator, count, cpus=None, memory=None)
            for count in _LISTED_GPU_COUNTS
            if count <= int(max_count))
    rows = []
    for region in sorted(_region_names()):
        for instance in instances:
            accelerator = instance.accelerator_type
            count = instance.accelerator_count
            rows.append({
                'InstanceType': instance.name,
                'AcceleratorName': accelerator,
                'AcceleratorCount': float(count) if count else None,
                'vCPUs': instance.vcpus,
                'MemoryGiB': instance.memory_gib,
                'Price': _compute_price_per_hour(instance, region),
                'SpotPrice': None,
                'Region': region,
                'GpuInfo': (_gpu_info(accelerator, count)
                            if accelerator is not None and count else None),
            })
    return pd.DataFrame(rows)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    del require_price  # unused
    return common.list_accelerators_impl(_DISPLAY_NAME, _listed_instances(),
                                         gpus_only, name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def regions() -> List[cloud.Region]:
    # Cheapest first, so `auto` precedes the broad and narrow regions.
    return common.get_region_zones(_listed_instances(), use_spot=False)


def get_modal_args_from_instance_type(
        instance_type: str) -> Tuple[Optional[str], float, int]:
    """Return Modal Function args: gpu, cpu cores, memory MiB."""
    try:
        instance = ModalInstanceType.from_instance_type(instance_type)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'No instance type {instance_type} found: {e}') from e
    gpu = instance.accelerator_type
    if gpu is not None and instance.accelerator_count != 1:
        gpu = f'{gpu}:{instance.accelerator_count}'
    return gpu, instance.modal_cpu, instance.memory_mib
