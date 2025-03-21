"""SimplePod Cloud Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for SimplePod.
"""
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.clouds.service_catalog import common
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

# Keep it synced with the frequency in
# skypilot-catalog/.github/workflows/update-simplepod-catalog.yml
_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('simplepod/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# Default values for SimplePod VMs
_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 4

def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)

def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('SimplePod Cloud does not support zones.')
    return common.validate_region_zone_impl('simplepod', _df, region, zone)

def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'SimplePod Cloud does not support spot.'
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('SimplePod Cloud does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)
