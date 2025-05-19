"""Hyperbolic Cloud service catalog.

This module loads and queries the service catalog for Hyperbolic Cloud.
"""
from typing import Dict, List, Optional, Tuple

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


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    del instance_type, use_spot, region, zone  # Unused
    # TODO: Implement cost calculation
    return 0.0


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    """Returns a dict of accelerator counts for the given instance type."""
    del instance_type  # Unused
    # TODO: Implement accelerator parsing
    return None


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    """Returns the number of vCPUs for the given instance type."""
    del instance_type  # Unused
    # TODO: Implement vCPU parsing
    return None


def get_memory_from_instance_type(instance_type: str) -> Optional[float]:
    """Returns the memory in GB for the given instance type."""
    del instance_type  # Unused
    # TODO: Implement memory parsing
    return None


def get_zone_shell_cmd() -> Optional[str]:
    """Returns the shell command to obtain the zone."""
    return None


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None) -> Optional[str]:
    """Returns the default instance type."""
    del cpus, memory, disk_tier  # Unused
    # TODO: Implement default instance type selection
    return None


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    del acc_name, acc_count, cpus, memory, use_spot, region, zone  # Unused
    # TODO: Implement instance type selection for accelerators
    return None, []


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    """Returns a list of regions and zones for the given instance type."""
    del instance_type, use_spot  # Unused
    # TODO: Implement region/zone selection
    return []


def get_gen_version(instance_type: str) -> Optional[str]:
    """Returns the generation version of the instance type."""
    del instance_type  # Unused
    # TODO: Implement generation version detection
    return None


def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    case_sensitive: bool = True,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    use_spot: bool = False,
    instance_type: Optional[str] = None,
    **kwargs,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    del gpus_only, name_filter, case_sensitive, region, zone, use_spot, instance_type, kwargs  # Unused
    # TODO: Implement accelerator listing
    return {}


def get_instance_type_from_catalog() -> dict:
    # TODO: Implement this function
    return {}


def regions() -> List[cloud.Region]:
    return [cloud.Region('default')]
