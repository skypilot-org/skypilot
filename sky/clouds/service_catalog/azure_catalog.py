"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
from typing import Dict, List, Optional, Tuple

import pandas as pd

import sky
from sky import resources
from sky import clouds as cloud_lib
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

_df = common.read_catalog('azure/vms.csv')


def get_suitable_vms(
        resource_req: resources.ResourceRequirements) -> List[resources.VMSpec]:
    df = _df
    if 'AvailabilityZone' not in df.columns:
        # TODO(woosuk): Add the 'AvailabilityZone' column to the catalog.
        df['AvailabilityZone'] = df['Region']
    df = common.filter_spot(df, resource_req.use_spot)

    if resource_req.accelerators is None:
        acc_name = None
        acc_count = None
    else:
        acc_name = resource_req.accelerator_name
        acc_count = resource_req.accelerator_count
    filters = {
        'InstanceType': resource_req.instance_type,
        'AcceleratorName': acc_name,
        'AcceleratorCount': acc_count,
        'Region': resource_req.region,
        'AvailabilityZone': resource_req.zone,
    }
    df = common.apply_filters(df, filters)
    df = df.reset_index(drop=True)

    feasible_resources = []
    azure = sky.Azure()
    for row in df.itertuples():
        if pd.isna(row.AcceleratorName) or pd.isna(row.AcceleratorCount):
            acc = None
        else:
            acc = resources.AcceleratorsSpec(name=row.AcceleratorName,
                                             count=int(row.AcceleratorCount),
                                             args=None)
        feasible_resources.append(
            resources.VMSpec(
                cloud=azure,
                region=row.Region,
                zone=row.AvailabilityZone,
                instance_type=row.InstanceType,
                cpu=float(row.vCPUs),
                memory=float(row.MemoryGiB),
                accelerators=acc,
                use_spot=resource_req.use_spot,
                disk_size=resource_req.disk_size,
                image_id=resource_req.image_id,
            ))
    return feasible_resources


def get_hourly_price(resource: resources.VMSpec) -> float:
    return common.get_hourly_price_impl(_df, resource.instance_type,
                                        resource.zone, resource.use_spot)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.validate_region_zone_impl(_df, region, zone)


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


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
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
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(
        instance_type: str, use_spot: bool) -> List[cloud_lib.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def get_gen_version_from_instance_type(instance_type: str) -> Optional[int]:
    return _df[_df['InstanceType'] == instance_type]['Generation'].iloc[0]


def list_accelerators(gpus_only: bool,
                      name_filter: Optional[str],
                      case_sensitive: bool = True
                     ) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Azure offering GPUs."""
    return common.list_accelerators_impl('Azure', _df, gpus_only, name_filter,
                                         case_sensitive)
