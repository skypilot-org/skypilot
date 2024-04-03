"""Vsphere catalog."""
import io
import os
import typing
from typing import Dict, List, Optional, Tuple

from sky.adaptors import common as adaptors_common
from sky.clouds.service_catalog import common

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')

_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 4
_CLOUD_VSPHERE = 'vsphere'

VSPHERE_CATALOG_HEADER = (
    'InstanceType,AcceleratorName,AcceleratorCount,vCPUs,'
    'MemoryGiB,GpuInfo,Price,SpotPrice,Region,AvailabilityZone')

_LOCAL_CATALOG = common.get_catalog_path('vsphere/vms.csv')
_df = None


def _get_df() -> 'pd.DataFrame':
    """Returns the catalog as a DataFrame."""
    global _df
    if _df is not None:
        return _df
    if os.path.exists(_LOCAL_CATALOG):
        _df = pd.read_csv(_LOCAL_CATALOG)
    else:
        header_content = io.StringIO(VSPHERE_CATALOG_HEADER + '\n')
        _df = pd.read_csv(header_content)
    return _df


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl(_CLOUD_VSPHERE, _get_df(), region,
                                            zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'vSphere does not support spot.'
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> (Tuple)[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[str] = None,
) -> Optional[str]:
    del disk_tier  # unused
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory
    return common.get_instance_type_for_cpus_mem_impl(_get_df(), cpus,
                                                      memory_gb_or_ratio)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(
        _get_df(), instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return common.get_instance_type_for_accelerator_impl(
        df=_get_df(),
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
    )


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    origin_df = _get_df()
    df = origin_df[origin_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in vSphere offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl(
        _CLOUD_VSPHERE,
        _get_df(),
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )
