"""Mithril Cloud service catalog.

This module loads and queries the service catalog for Mithril Cloud.
"""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

# Keep it synced with the frequency in
# skypilot-catalog/.github/workflows/update-mithril-catalog.yml
_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('mithril/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')
    return common.validate_region_zone_impl('mithril', _df, region, zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')
    try:
        return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                           zone)
    except ValueError:
        return 0.0


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    try:
        return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)
    except ValueError:
        return None, None


def get_accelerators_from_instance_type(
    instance_type: str,) -> Optional[Dict[str, Union[int, float]]]:
    try:
        return common.get_accelerators_from_instance_type_impl(
            _df, instance_type)
    except ValueError:
        return None


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[resources_utils.DiskTier] = None,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Optional[str]:
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
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    del local_disk  # Unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')
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
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Mithril Cloud offering accelerators."""
    del require_price  # Unused
    return common.list_accelerators_impl(
        'Mithril',
        _df,
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )
