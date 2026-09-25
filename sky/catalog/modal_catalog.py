"""Modal service catalog."""

import math
import re
from typing import Dict, List, Optional, Tuple, Union

from sky.adaptors import common as adaptors_common
from sky.catalog import common
from sky.clouds import cloud
from sky.utils import resources_utils
from sky.utils import ux_utils

pd = adaptors_common.LazyImport('pandas')

_CLOUD_NAME = 'modal'
_DISPLAY_NAME = 'Modal'

AUTO_REGION = 'auto'
_BROAD_REGIONS = ('us', 'eu', 'ap', 'uk', 'ca', 'me', 'sa', 'af', 'mx')
_NARROW_REGIONS = (
    'us-east',
    'us-central',
    'us-south',
    'us-west',
    'eu-west',
    'eu-north',
    'eu-south',
    'ap-northeast',
    'ap-southeast',
    'ap-south',
    'ap-melbourne',
    'jp',
    'au',
)
_REGION_MULTIPLIERS = {
    AUTO_REGION: 1.0,
    **{region: 1.5 for region in _BROAD_REGIONS},
    **{region: 1.75 for region in _NARROW_REGIONS},
}

# Keep the supported GPU price, memory, and count tables in sync with:
# https://modal.com/pricing and https://modal.com/docs/guide/gpu.
# Modal Sandbox + Notebooks pricing is converted from per-second pricing.
_SANDBOX_CPU_CORE_PRICE_PER_SECOND = 0.00003942
_SANDBOX_MEMORY_GIB_PRICE_PER_SECOND = 0.00000672

_DEFAULT_MODAL_CPU_CORES = 2.0
_DEFAULT_SKY_VCPUS = 4.0
_DEFAULT_MEMORY_GIB = 16
_DEFAULT_MEMORY_CPU_RATIO = _DEFAULT_MEMORY_GIB / _DEFAULT_SKY_VCPUS

# Modal validates these bounds when creating a Function or Sandbox. See:
# https://modal.com/docs/guide/resources#how-much-can-i-request
_MIN_MODAL_CPU_CORES = 0.125
_MAX_MODAL_CPU_CORES = 64.0
_MIN_MEMORY_MIB = 128
_MAX_MEMORY_MIB = 344064

_VIRTUAL_INSTANCE_TYPE_PATTERN = re.compile(
    r'^(?P<vcpus>\d+(?:\.\d+)?)CPU--'
    r'(?P<memory>\d+(?:\.\d+)?)GB'
    r'(?:--(?P<accelerator>[\w-]+):(?P<count>\d+))?$')

_GPU_PRICE_PER_SECOND = {
    'B300': 0.001972,
    'B200': 0.001736,
    'H200': 0.001261,
    'H100': 0.001097,
    'RTX-PRO-6000': 0.000842,
    'A100-80GB': 0.000694,
    'A100-40GB': 0.000583,
    'A100': 0.000583,
    'L40S': 0.000542,
    'A10': 0.000306,
    'L4': 0.000222,
    'T4': 0.000164,
}

_GPU_MEMORY_GIB = {
    'B300': 288,
    'B200': 180,
    'H200': 141,
    'H100': 80,
    'RTX-PRO-6000': 96,
    'A100-80GB': 80,
    'A100-40GB': 40,
    'A100': 40,
    'L40S': 48,
    'A10': 24,
    'L4': 24,
    'T4': 16,
}

_GPU_COUNTS = {
    'B300': (1, 2, 4, 8),
    'B200': (1, 2, 4, 8),
    'H200': (1, 2, 4, 8),
    'H100': (1, 2, 4, 8),
    'RTX-PRO-6000': (1,),
    'A100-80GB': (1, 2, 4, 8),
    'A100-40GB': (1, 2, 4, 8),
    'A100': (1, 2, 4, 8),
    'L40S': (1, 2, 4, 8),
    'A10': (1, 2, 4),
    'L4': (1, 2, 4, 8),
    'T4': (1, 2, 4, 8),
}


def _format_resource_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f'{value:.12f}'.rstrip('0').rstrip('.')


def _canonical_accelerator(accelerator: str) -> Optional[str]:
    accelerator_lower = accelerator.lower()
    for supported_accelerator in _GPU_COUNTS:
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
            if accelerator_count not in _GPU_COUNTS[canonical_accelerator]:
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


def _base_compute_price_per_hour(modal_cpu: float = _DEFAULT_MODAL_CPU_CORES,
                                 memory_gib: float = _DEFAULT_MEMORY_GIB,
                                 gpu_name: Optional[str] = None,
                                 gpu_count: int = 0) -> float:
    per_second = (modal_cpu * _SANDBOX_CPU_CORE_PRICE_PER_SECOND +
                  memory_gib * _SANDBOX_MEMORY_GIB_PRICE_PER_SECOND)
    if gpu_name is not None:
        per_second += _GPU_PRICE_PER_SECOND[gpu_name] * gpu_count
    return per_second * 3600


def _gpu_info(gpu_name: str, gpu_count: int) -> str:
    gpu_memory_mib = int(_GPU_MEMORY_GIB[gpu_name] * 1024)
    total_memory_mib = gpu_memory_mib * gpu_count
    return repr({
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib,
            },
        }],
        'TotalGpuMemoryInMiB': total_memory_mib,
    })


def _instance_type_for_gpu(gpu_name: str, gpu_count: int) -> str:
    return f'modal-{gpu_name.lower()}-{gpu_count}x'


def _make_catalog_df():
    rows = []

    def add_row(instance_type: str,
                region: str,
                price: float,
                accelerator_name: Optional[str] = None,
                accelerator_count: Optional[int] = None,
                gpu_info: Optional[str] = None) -> None:
        rows.append({
            'InstanceType': instance_type,
            'AcceleratorName': accelerator_name,
            'AcceleratorCount': accelerator_count,
            'vCPUs': _DEFAULT_SKY_VCPUS,
            'MemoryGiB': _DEFAULT_MEMORY_GIB,
            'Price': price * _REGION_MULTIPLIERS[region],
            'Region': region,
            'GpuInfo': gpu_info,
            'SpotPrice': None,
        })

    cpu_instance_type = 'modal-cpu-4x-16gb'
    cpu_price = _base_compute_price_per_hour()
    for region in _REGION_MULTIPLIERS:
        add_row(cpu_instance_type, region, cpu_price)

    for gpu_name, counts in _GPU_COUNTS.items():
        for gpu_count in counts:
            instance_type = _instance_type_for_gpu(gpu_name, gpu_count)
            price = _base_compute_price_per_hour(gpu_name=gpu_name,
                                                 gpu_count=gpu_count)
            for region in _REGION_MULTIPLIERS:
                add_row(instance_type,
                        region,
                        price,
                        accelerator_name=gpu_name,
                        accelerator_count=gpu_count,
                        gpu_info=_gpu_info(gpu_name, gpu_count))

    return pd.DataFrame(rows)


_df = _make_catalog_df()


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
        if region not in _REGION_MULTIPLIERS:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Invalid Modal region {region!r}.')
        return _base_compute_price_per_hour(modal_cpu=instance.modal_cpu,
                                            memory_gib=instance.memory_gib,
                                            gpu_name=instance.accelerator_type,
                                            gpu_count=instance.accelerator_count
                                            or 0) * _REGION_MULTIPLIERS[region]
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
    if region is not None and region not in _REGION_MULTIPLIERS:
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
    if region is not None and region not in _REGION_MULTIPLIERS:
        return None, []
    canonical_accelerator = _canonical_accelerator(acc_name)
    if (canonical_accelerator is None or
            acc_count not in _GPU_COUNTS[canonical_accelerator]):
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
    """Return Modal Sandbox args: gpu, cpu cores, memory MiB."""
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
    return gpu, _DEFAULT_MODAL_CPU_CORES, _DEFAULT_MEMORY_GIB * 1024
