"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Dict, List, Optional, Tuple

import pandas as pd

import sky
from sky import resources
from sky.clouds import cloud
from sky.clouds.service_catalog import common

_df = common.read_catalog('aws.csv')


def get_feasible_resources(
    resource_filter: resources.ResourceFilter,
) -> List[resources.VMResources]:
    df = _df
    df = common.filter_spot(df, resource_filter.use_spot)

    if resource_filter.accelerator is None:
        acc_name = None
        acc_count = None
    else:
        acc_name = resource_filter.accelerator.name
        acc_count = resource_filter.accelerator.count
    filters = {
        'InstanceType': resource_filter.instance_type,
        'AcceleratorName': acc_name,
        'AcceleratorCount': acc_count,
        'Region': resource_filter.region,
        'AvailabilityZone': resource_filter.zone,
    }
    df = common.apply_filters(df, filters)
    df = df.reset_index(drop=True)

    feasible_resources = []
    aws = sky.AWS()
    for row in df.itertuples():
        if pd.isna(row.AcceleratorName) or pd.isna(row.AcceleratorCount):
            acc = None
        else:
            acc = resources.Accelerator(name=row.AcceleratorName,
                                        count=int(row.AcceleratorCount),
                                        args=None)
        feasible_resources.append(
            resources.VMResources(
                cloud=aws,
                region=row.Region,
                zone=row.AvailabilityZone,
                instance_type=row.InstanceType,
                num_vcpus=float(row.vCPUs),
                cpu_memory=float(row.MemoryGiB),
                accelerator=acc,
                use_spot=resource_filter.use_spot,
                disk_size=resource_filter.disk_size,
                image_id=resource_filter.image_id,
            ))
    return feasible_resources


def get_hourly_price(resource: resources.VMResources) -> float:
    return common.get_hourly_price_impl(_df, resource.instance_type,
                                        resource.zone, resource.use_spot)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(region: Optional[str], zone: Optional[str]):
    return common.validate_region_zone_impl(_df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    region: Optional[str] = None,
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_df, instance_type, region, use_spot)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
) -> Tuple[Optional[List[str]], List[str]]:
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


def list_accelerators(gpus_only: bool,
                      name_filter: Optional[str],
                      case_sensitive: bool = True
                     ) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in AWS offering accelerators."""
    return common.list_accelerators_impl('AWS', _df, gpus_only, name_filter,
                                         case_sensitive)
