"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
import re
from typing import Dict, List, Optional, Tuple

from sky import clouds as cloud_lib
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

_df = common.read_catalog('azure/vms.csv')

# This is the latest general-purpose instance family as of Jan 2023.
# CPU: Intel Ice Lake 8370C.
# Memory: 4 GiB RAM per 1 vCPU.
_DEFAULT_INSTANCE_FAMILY = 'D_v5'
_DEFAULT_NUM_VCPUS = 8


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.validate_region_zone_impl('azure', _df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    # Ref: https://azure.microsoft.com/en-us/support/legal/offer-details/
    assert not use_spot, 'Current Azure subscription does not support spot.'
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


def _get_instance_family(instance_type: str) -> str:
    if instance_type.startswith('Basic_A'):
        return 'basic_a'

    assert instance_type.startswith('Standard_')
    # Remove the 'Standard_' prefix.
    instance_type = instance_type[len('Standard_'):]
    # Remove the '_Promo' suffix if exists.
    if '_Promo' in instance_type:
        instance_type = instance_type[:-len('_Promo')]

    # TODO(woosuk): Use better regex.
    if '-' in instance_type:
        x = re.match(r'([A-Za-z]+)([0-9]+)(-)([0-9]+)(.*)', instance_type)
        assert x is not None, x
        instance_family = x.group(1) + '_' + x.group(5)
    else:
        x = re.match(r'([A-Za-z]+)([0-9]+)(.*)', instance_type)
        assert x is not None, x
        instance_family = x.group(1) + x.group(3)
    return instance_family


def get_default_instance_type(cpus: Optional[str] = None) -> Optional[str]:
    if cpus is None:
        cpus = str(_DEFAULT_NUM_VCPUS)
    df = _df[_df['InstanceType'].apply(_get_instance_family) ==
             _DEFAULT_INSTANCE_FAMILY]
    return common.get_instance_type_for_cpus_impl(df, cpus)


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
            raise ValueError('Azure does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(
        instance_type: str, use_spot: bool) -> List[cloud_lib.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def get_gen_version_from_instance_type(instance_type: str) -> Optional[int]:
    return _df[_df['InstanceType'] == instance_type]['Generation'].iloc[0]


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Azure offering GPUs."""
    return common.list_accelerators_impl('Azure', _df, gpus_only, name_filter,
                                         region_filter, case_sensitive)
