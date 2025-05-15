"""Hyperbolic Cloud service catalog.

This module loads and queries the service catalog for Hyperbolic Cloud.
"""
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, Union

from sky.clouds import cloud  # Import cloud here for Region
from sky.clouds.service_catalog import common
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


def get_hourly_cost(instance_type: str,
                    use_spot: bool,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the hourly on-demand price for the instance type.
    
    Args:
        instance_type: The instance type in format 'Nx-MODEL-CPU-MEM' (e.g., '1x-T4-4-17')
        use_spot: Whether to use spot instances
        region: Ignored for Hyperbolic
        zone: The zone to get the price for (not supported)
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Hyperbolic Cloud does not support zones.')
    
    # DEBUG: Print all instance types in the DataFrame
    print('DEBUG: All InstanceTypes:', list(_df['InstanceType'].unique()))
    print(f'DEBUG: Filtering for InstanceType={instance_type!r}')
    
    df = _df[_df['InstanceType'] == instance_type]
    print('DEBUG: Filtered DataFrame shape:', df.shape)
    if len(df) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Instance type {instance_type!r} not found. '
                'Please check the instance type format and ensure it exists in the catalog.')
    
    if len(df) > 1:
        cheapest_idx = df['Price'].idxmin()  # Select the cheapest instance
        df = df.loc[[cheapest_idx]]
    
    return common.get_hourly_cost_impl(df, instance_type, use_spot, None, zone)


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


def get_region_zones_for_instance_type(
    _instance_type: str,
    _use_spot: bool,
    clouds: Optional[str] = None,
) -> List[cloud.Region]:
    """Returns a dummy region for the given instance type (regionless cloud)."""
    del clouds, _instance_type, _use_spot  # unused
    return [cloud.Region('default')]


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
        'hyperbolic',
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


def regions() -> List[cloud.Region]:
    return [cloud.Region('default')]
