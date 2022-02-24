"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Dict, List, Optional

from sky.clouds import cloud
from sky.clouds.service_catalog import common

_df = common.read_catalog('aws.csv')

_DEFAULT_REGION = 'us-west-2'


def get_hourly_cost(
    instance_type: str, region: Optional[str] = None, use_spot: bool = False
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if region is None:
        region = _DEFAULT_REGION
    return common.get_hourly_cost_impl(_df, instance_type, region, use_spot)


def get_accelerators_from_instance_type(instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    return common.get_instance_type_for_accelerator_impl(_df, acc_name, acc_count)


def get_region_zones_for_instance_type(
    instance_type: str, use_spot: bool
) -> List[cloud.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
    gpus_only: bool, name_filter: Optional[str]
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in AWS offering accelerators."""
    return common.list_accelerators_impl('AWS', _df, gpus_only, name_filter)
