"""Cudo Compute Offerings Catalog."""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common
from sky.provision.cudo import cudo_machine_type as cudo_mt
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_PULL_FREQUENCY_HOURS = 7
_df = common.read_catalog(cudo_mt.VMS_CSV,
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 2


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cudo does not support zones.')
    return common.validate_region_zone_impl('cudo', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'Cudo does not support spot.'
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cudo does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    del disk_tier
    # NOTE: After expanding catalog to multiple entries, you may
    # want to specify a default instance type or family.
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'

    memory_gb_or_ratio = memory
    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus,
                                                      memory_gb_or_ratio,
                                                      region, zone)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cudo does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = False,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Cudo offering GPUs."""
    return common.list_accelerators_impl('Cudo', _df, gpus_only, name_filter,
                                         region_filter, quantity_filter,
                                         case_sensitive, all_regions,
                                         require_price)
