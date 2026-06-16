"""Modal service catalog."""

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

# Modal Sandbox + Notebooks pricing, converted from per-second pricing.
_SANDBOX_CPU_CORE_PRICE_PER_SECOND = 0.00003942
_SANDBOX_MEMORY_GIB_PRICE_PER_SECOND = 0.00000672

_DEFAULT_MODAL_CPU_CORES = 2.0
_DEFAULT_SKY_VCPUS = 4.0
_DEFAULT_MEMORY_GIB = 16

_GPU_PRICE_PER_SECOND = {
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


def _base_compute_price_per_hour(gpu_name: Optional[str] = None,
                                 gpu_count: int = 0) -> float:
    per_second = (
        _DEFAULT_MODAL_CPU_CORES * _SANDBOX_CPU_CORE_PRICE_PER_SECOND +
        _DEFAULT_MEMORY_GIB * _SANDBOX_MEMORY_GIB_PRICE_PER_SECOND)
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
            price = _base_compute_price_per_hour(gpu_name, gpu_count)
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
    return common.instance_type_exists_impl(_df, instance_type)


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
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


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
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory, region,
                                                      zone, use_spot,
                                                      max_hourly_cost)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_arch_from_instance_type(instance_type: str) -> Optional[str]:
    return common.get_arch_from_instance_type_impl(_df, instance_type)


def get_local_disk_from_instance_type(instance_type: str) -> Optional[str]:
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
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    return common.get_instance_type_for_accelerator_impl(
        df=_df,
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
        max_hourly_cost=max_hourly_cost)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
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

    accelerators = get_accelerators_from_instance_type(instance_type)
    gpu = None
    if accelerators is not None:
        assert len(accelerators) == 1, accelerators
        gpu_name, gpu_count = list(accelerators.items())[0]
        gpu = f'{gpu_name}:{gpu_count}' if gpu_count != 1 else gpu_name
    return gpu, _DEFAULT_MODAL_CPU_CORES, _DEFAULT_MEMORY_GIB * 1024
