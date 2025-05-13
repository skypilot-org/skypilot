"""Hyperbolic Cloud service catalog.

This module loads and queries the service catalog for Hyperbolic Cloud.
"""
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, Union

from sky.clouds.service_catalog import common
from sky.utils import ux_utils

if TYPE_CHECKING:
    from sky.clouds import cloud

_df = common.read_catalog('hyperbolic/vms.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Hyperbolic Cloud does not support zones.')
    return common.validate_region_zone_impl('hyperbolic', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the hourly on-demand price for the instance type."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Hyperbolic Cloud does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_accelerators_from_instance_type(
    instance_type: str,) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None) -> Optional[str]:
    """Returns the default instance type for Hyperbolic Cloud."""
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None
) -> Tuple[Optional[List[str]], Optional[str]]:
    """Returns a list of instance types satisfying the required count
    of accelerators.
    """
    instance_types, _ = common.get_instance_type_for_accelerator_impl(
        _df,
        acc_name,
        acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone)
    return instance_types, None


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    region_list = common.get_region_zones(df, use_spot)
    # Hack: Enforce US regions are always tried first
    return sorted(region_list, key=lambda r: (not r.name.startswith('us-'), r.name))  # pylint: disable=line-too-long


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Hyperbolic offering GPUs."""
    del require_price
    return common.list_accelerators_impl(
        'Hyperbolic',
        _df,
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )


def get_instance_type_from_catalog() -> dict:
    # TODO: Implement this function
    return {}
