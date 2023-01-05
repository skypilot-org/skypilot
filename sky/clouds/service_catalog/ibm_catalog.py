"""
IBM Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for IBM.
"""

from sky.clouds import cloud
from sky.clouds.service_catalog import common
from typing import Dict, List, Optional, Tuple
import pandas as pd
from numpy import nan

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
    """Returns all instance types in IBM offering accelerators."""
    return common.list_accelerators_impl('IBM', _df, gpus_only, name_filter,
                                         case_sensitive)

def get_default_data():
    """
    temporarily hard-coded until the implementation of
    clouds/service_catalog/data_fetchers/fetch_ibm.py.
    replacing the equivalent call: _df = common.read_catalog('ibm/vms.csv')
    """
    # pylint: disable=line-too-long
    data = [
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-south', 'us-south-1'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-south', 'us-south-2'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-south','us-south-3'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-east', 'us-east-1'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-east', 'us-east-2'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.497, 0, 'us-east', 'us-east-3'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.572, 0, 'ca-tor', 'ca-tor-1'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.572, 0, 'ca-tor', 'ca-tor-2'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.572, 0, 'ca-tor', 'ca-tor-3'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.746, 0, 'eu-de', 'eu-de-1'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.746, 0, 'eu-de', 'eu-de-2'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.746, 0, 'eu-de','eu-de-3'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.672, 0, 'eu-gb', 'eu-gb-1'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.672, 0, 'eu-gb', 'eu-gb-2'],
    ['gx2-8x64x1v100', 'V100', 1.0, 8, 64, 'V100', 2.672, 0, 'eu-gb','eu-gb-3'],

    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-east', 'us-east-1'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-east', 'us-east-2'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-east', 'us-east-3'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-south', 'us-south-1'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-south', 'us-south-2'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.417, 0, 'us-south', 'us-south-3'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.430, 0, 'ca-tor', 'ca-tor-1'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.430, 0, 'ca-tor', 'ca-tor-2'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.430, 0, 'ca-tor', 'ca-tor-3'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.455, 0, 'eu-de', 'eu-de-1'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.455, 0, 'eu-de', 'eu-de-2'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.455, 0, 'eu-de', 'eu-de-3'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.444, 0, 'eu-gb', 'eu-gb-1'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.444, 0, 'eu-gb', 'eu-gb-2'],
    ['bx2d-8x32', nan, nan, 8, 32, nan, 0.444, 0, 'eu-gb', 'eu-gb-3'],
    ]
    columns = ['InstanceType', 'AcceleratorName', 'AcceleratorCount',
                'vCPUs', 'MemoryGiB', 'GpuInfo', 'Price',
                'SpotPrice', 'Region', 'AvailabilityZone']
    return pd.DataFrame(data, columns=columns)

_df = get_default_data()
