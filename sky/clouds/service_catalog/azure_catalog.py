"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
import ast
from typing import Dict, List, Optional, Tuple

from sky.clouds import cloud
from sky.clouds.service_catalog import common

_df = common.read_catalog('azure.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def region_exists(region: str) -> bool:
    return common.region_exists_impl(_df, region)


def get_hourly_cost(instance_type: str,
                    region: Optional[str] = None,
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    # Ref: https://azure.microsoft.com/en-us/support/legal/offer-details/
    assert not use_spot, 'Current Azure subscription does not support spot.'
    return common.get_hourly_cost_impl(_df, instance_type, region, use_spot)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str, acc_count: int) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def get_gen_version_from_instance_type(instance_type: str) -> Optional[int]:
    cell = _df[_df['InstanceType'] == instance_type]['capabilities'].iloc[0]
    cap_list = ast.literal_eval(cell)
    gen_version = None
    for cap in cap_list:
        if cap['name'] == 'HyperVGenerations':
            gen_version = cap['value']
    return gen_version


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str]) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Azure offering GPUs."""
    return common.list_accelerators_impl('Azure', _df, gpus_only, name_filter)
