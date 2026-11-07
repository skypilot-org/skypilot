"""Modal service catalog."""

import math
import re
from typing import Dict, List, Optional, Set, Tuple, Union

from sky.catalog import common
from sky.clouds import cloud
from sky.utils import resources_utils
from sky.utils import ux_utils

_CLOUD_NAME = 'modal'
_DISPLAY_NAME = 'Modal'

AUTO_REGION = 'auto'

# Prices, GPU types and counts, and regions come from the hosted catalog
# (modal/vms.csv), made by sky/catalog/data_fetchers/fetch_modal.py. Besides
# fixed 4 vCPU / 16 GiB shapes, each row carries the per-hour rates for sizing
# custom instance types in its region: CpuCoreHourlyPrice (per Modal core),
# MemoryGiBHourlyPrice and AcceleratorHourlyPrice (per GPU). CPU-only rows hold
# Modal's non-preemptible rates, since CPU-only clusters run non-preemptible.
_PULL_FREQUENCY_HOURS = 24
_df = common.read_catalog('modal/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_DEFAULT_SKY_VCPUS = 4.0
_DEFAULT_MEMORY_GIB = 16
_DEFAULT_MEMORY_CPU_RATIO = _DEFAULT_MEMORY_GIB / _DEFAULT_SKY_VCPUS

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


def _gpu_counts(accelerator: str) -> List[int]:
    """GPU counts per container Modal offers for a canonical GPU name."""
    counts = _df[_df['AcceleratorName'] == accelerator]['AcceleratorCount']
    return sorted({int(count) for count in counts.dropna()})


def _region_names() -> Set[str]:
    return set(_df['Region'])


def get_max_gpu_count(accelerator: str) -> Optional[int]:
    """Most GPUs of this type Modal offers per node; None if unknown."""
    canonical = _canonical_accelerator(accelerator)
    if canonical is None:
        return None
    return max(_gpu_counts(canonical))


def _format_resource_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f'{value:.12f}'.rstrip('0').rstrip('.')


def _canonical_accelerator(accelerator: str) -> Optional[str]:
    accelerator_lower = accelerator.lower()
    for supported_accelerator in _df['AcceleratorName'].dropna().unique():
        if supported_accelerator.lower() == accelerator_lower:
            return supported_accelerator
    return None


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
            canonical_accelerator = _canonical_accelerator(accelerator_type)
            if canonical_accelerator is None:
                raise ValueError(
                    f'Unsupported Modal accelerator {accelerator_type!r}.')
            if accelerator_count not in _gpu_counts(canonical_accelerator):
                raise ValueError(
                    f'Unsupported Modal accelerator count '
                    f'{canonical_accelerator}:{accelerator_count}.')
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


def _compute_price_per_hour(modal_cpu: float, memory_gib: float,
                            gpu_name: Optional[str], gpu_count: int,
                            region: str) -> float:
    """Price of a custom-sized container from the catalog's per-hour rates."""
    if gpu_name is None:
        idx = _df['AcceleratorName'].isna()
    else:
        idx = _df['AcceleratorName'] == gpu_name
    rates = _df[idx & (_df['Region'] == region)]
    if rates.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No Modal price for {gpu_name or "CPU"} in '
                             f'region {region!r}.')
    row = rates.iloc[0]
    price = (modal_cpu * row['CpuCoreHourlyPrice'] +
             memory_gib * row['MemoryGiBHourlyPrice'])
    if gpu_name is not None:
        price += gpu_count * row['AcceleratorHourlyPrice']
    return float(price)


def instance_type_exists(instance_type: str) -> bool:
    if common.instance_type_exists_impl(_df, instance_type):
        return True
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
    return common.validate_region_zone_impl(_CLOUD_NAME, _df, region, zone)


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
    if not common.instance_type_exists_impl(_df, instance_type):
        instance = ModalInstanceType.from_instance_type(instance_type)
        region = region or AUTO_REGION
        if region not in _region_names():
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Invalid Modal region {region!r}.')
        return _compute_price_per_hour(modal_cpu=instance.modal_cpu,
                                       memory_gib=instance.memory_gib,
                                       gpu_name=instance.accelerator_type,
                                       gpu_count=instance.accelerator_count or
                                       0,
                                       region=region)
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    if not common.instance_type_exists_impl(_df, instance_type):
        instance = ModalInstanceType.from_instance_type(instance_type)
        return instance.vcpus, instance.memory_gib
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


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
    if not common.instance_type_exists_impl(_df, instance_type):
        instance = ModalInstanceType.from_instance_type(instance_type)
        if instance.accelerator_type is None:
            return None
        assert instance.accelerator_count is not None
        return {instance.accelerator_type: instance.accelerator_count}
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_arch_from_instance_type(instance_type: str) -> Optional[str]:
    if not common.instance_type_exists_impl(_df, instance_type):
        ModalInstanceType.from_instance_type(instance_type)
        return None
    return common.get_arch_from_instance_type_impl(_df, instance_type)


def get_local_disk_from_instance_type(instance_type: str) -> Optional[str]:
    if not common.instance_type_exists_impl(_df, instance_type):
        ModalInstanceType.from_instance_type(instance_type)
        return None
    return common.get_local_disk_from_instance_type_impl(_df, instance_type)


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
    if (canonical_accelerator is None or
            acc_count not in _gpu_counts(canonical_accelerator)):
        return common.get_instance_type_for_accelerator_impl(
            df=_df,
            acc_name=acc_name,
            acc_count=acc_count,
            cpus=None,
            memory=None,
            use_spot=False,
            region=region,
            zone=None,
            max_hourly_cost=max_hourly_cost)
    try:
        base_instance = _resources_from_requests(cpus, memory)
        instance = ModalInstanceType(base_instance.vcpus,
                                     base_instance.memory_gib,
                                     accelerator_count=acc_count,
                                     accelerator_type=canonical_accelerator)
    except ValueError:
        return [], []
    if (max_hourly_cost is not None and
            get_hourly_cost(instance.name, region=region) > max_hourly_cost):
        return [], []
    return [instance.name], []


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    if not common.instance_type_exists_impl(_df, instance_type):
        ModalInstanceType.from_instance_type(instance_type)
        return [] if use_spot else regions()
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def _get_accelerator(
    accelerator: str,
    count: int,
    region: Optional[str],
    zone: Optional[str] = None,
):
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    idx = (_df['AcceleratorName'].str.fullmatch(
        accelerator, case=False)) & (_df['AcceleratorCount'] == count)
    if region is not None:
        idx &= _df['Region'] == region
    return _df[idx]


def get_accelerator_hourly_cost(accelerator: str,
                                count: int,
                                use_spot: bool = False,
                                region: Optional[str] = None,
                                zone: Optional[str] = None) -> float:
    if use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support spot instances.')
    df = _get_accelerator(accelerator, count, region, zone)
    if df.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No accelerator {accelerator}:{count} found.')
    return 0.0


def get_region_zones_for_accelerators(
        accelerator: str,
        count: int,
        use_spot: bool = False) -> List[cloud.Region]:
    if use_spot:
        return []
    df = _get_accelerator(accelerator, count, region=None)
    return common.get_region_zones(df, use_spot)


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None) -> None:
    del instance_type, accelerators  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    del require_price  # unused
    return common.list_accelerators_impl(_DISPLAY_NAME, _df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def regions() -> List[cloud.Region]:
    return common.get_region_zones(_df, use_spot=False)


def get_modal_args_from_instance_type(
        instance_type: str) -> Tuple[Optional[str], float, int]:
    """Return Modal Function args: gpu, cpu cores, memory MiB."""
    if not instance_type_exists(instance_type):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')

    if not common.instance_type_exists_impl(_df, instance_type):
        instance = ModalInstanceType.from_instance_type(instance_type)
        gpu = instance.accelerator_type
        if gpu is not None and instance.accelerator_count != 1:
            gpu = f'{gpu}:{instance.accelerator_count}'
        return gpu, instance.modal_cpu, instance.memory_mib

    accelerators = get_accelerators_from_instance_type(instance_type)
    gpu = None
    if accelerators is not None:
        assert len(accelerators) == 1, accelerators
        gpu_name, gpu_count = list(accelerators.items())[0]
        gpu = f'{gpu_name}:{gpu_count}' if gpu_count != 1 else gpu_name
    vcpus, memory_gib = get_vcpus_mem_from_instance_type(instance_type)
    assert vcpus is not None and memory_gib is not None, instance_type
    return gpu, vcpus / 2, math.ceil(memory_gib * 1024)
