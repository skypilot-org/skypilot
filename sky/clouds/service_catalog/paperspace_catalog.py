"""Paperspace service catalog.

This module loads the service catalog file and can be used to
query instance types and pricing information for Paperspace.
"""

import typing
from typing import Dict, List, Optional, Tuple

from sky.clouds.service_catalog import common
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_df = common.read_catalog('paperspace/vms.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Paperspace does not support zones.')
    return common.validate_region_zone_impl('Paperspace', _df, region, zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Paperspace does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[str] = None,
) -> Optional[str]:
    # NOTE: After expanding catalog to multiple entries, you may
    # want to specify a default instance type or family.
    del disk_tier  # unused
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types that have the given accelerator."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Paperspace does not support zones.')
    return common.get_instance_type_for_accelerator_impl(
        df=_df,
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
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Paperspace offering GPUs."""
    del require_price  # unused
    return common.list_accelerators_impl('Paperspace', _df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
