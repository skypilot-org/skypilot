"""Hyperbolic Cloud service catalog.

This module loads and queries the service catalog for Hyperbolic Cloud.
"""
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common
from sky.clouds import cloud  # Import cloud here for Region
from sky.utils import ux_utils

# Initialize cloud variable at module level
CLOUD = 'hyperbolic'

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


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Hyperbolic Cloud does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    vcpus, _ = get_vcpus_mem_from_instance_type(instance_type)
    return vcpus


def get_memory_from_instance_type(instance_type: str) -> Optional[float]:
    _, mem = get_vcpus_mem_from_instance_type(instance_type)
    return mem


def get_zone_shell_cmd() -> Optional[str]:
    """Returns the shell command to obtain the zone."""
    return None


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None,
                              local_disk: Optional[str] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    del disk_tier, local_disk  # Unused
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory, region,
                                                      zone)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    del local_disk  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Hyperbolic Cloud does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def get_gen_version(instance_type: str) -> Optional[str]:
    """Returns the generation version of the instance type."""
    del instance_type  # Unused
    # TODO: Implement generation version detection
    return None


def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Hyperbolic Cloud offering accelerators."""
    del require_price  # Unused
    return common.list_accelerators_impl('Hyperbolic', _df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def get_instance_type_from_catalog() -> dict:
    # TODO: Implement this function
    return {}


def regions() -> List[cloud.Region]:
    return [cloud.Region('default')]
