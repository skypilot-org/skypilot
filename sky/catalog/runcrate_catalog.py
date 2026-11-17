"""Runcrate | Catalog

This module loads the service catalog file and can be used to query instance
types and pricing information for Runcrate.
"""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

# Runcrate's offers change through the day, so the catalog is refreshed
# more often than the default.
_PULL_FREQUENCY_HOURS = 7
_DEFAULT_NUM_VCPUS = 8

_df = common.read_catalog('runcrate/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Runcrate does not support zones.')
    return common.validate_region_zone_impl('runcrate', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # Unused.
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    return common.get_instance_type_for_cpus_mem_impl(
        _df,
        cpus,
        memory,
        region=region,
        zone=zone,
        use_spot=use_spot,
        max_hourly_cost=max_hourly_cost)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


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
    """Returns a list of instance types that have the given accelerator."""
    del local_disk  # unused
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


def regions() -> List['cloud.Region']:
    return common.get_region_zones(_df, use_spot=False)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Runcrate offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('Runcrate', _df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
