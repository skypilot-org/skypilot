"""Lambda Cloud Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Lambda.
"""
import typing
from typing import Dict, List, Optional, Tuple

from sky.clouds.service_catalog import common
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_df = common.read_catalog('lambda/vms.csv')

# Number of vCPUS for gpu_1x_a100_sxm4
_DEFAULT_NUM_VCPUS = 30


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lambda Cloud does not support zones.')
    return common.validate_region_zone_impl('lambda', _df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lambda Cloud does not support zones.')
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'Lambda Cloud does not support spot.'
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lambda Cloud does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None) -> Optional[str]:
    if cpus is None:
        cpus = str(_DEFAULT_NUM_VCPUS)
    # Set to gpu_1x_a100_sxm4 to be the default instance type if match vCPU
    # requirement.
    df = _df[_df['InstanceType'].eq('gpu_1x_a100_sxm4')]
    instance = common.get_instance_type_for_cpus_impl(df, cpus)
    if not instance:
        instance = common.get_instance_type_for_cpus_impl(_df, cpus)
    return instance


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lambda Cloud does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    region_list = common.get_region_zones(df, use_spot)
    # Hack: Enforce US regions are always tried first
    us_region_list = []
    other_region_list = []
    for region in region_list:
        if region.name.startswith('us-'):
            us_region_list.append(region)
        else:
            other_region_list.append(region)
    return us_region_list + other_region_list


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Lambda offering GPUs."""
    return common.list_accelerators_impl('Lambda', _df, gpus_only, name_filter,
                                         region_filter, case_sensitive)
