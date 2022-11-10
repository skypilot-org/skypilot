"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
import re
from typing import Dict, List, Optional, Tuple

import sky
from sky import resources
from sky.clouds import cloud
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

_df = common.read_catalog('azure.csv')


def _get_instance_family(instance_type: str) -> str:
    if instance_type.startswith('Basic_A'):
        return 'basic_a'

    assert instance_type.startswith('Standard_')
    # Remove the 'Standard_' prefix.
    instance_type = instance_type[len('Standard_'):]
    # Remove the '_Promo' suffix if exists.
    if '_Promo' in instance_type:
        instance_type = instance_type[:-len('_Promo')]

    # FIXME(woosuk): Use better regex.
    if '-' in instance_type:
        x = re.match(r'([A-Za-z]+)([0-9]+)(-)([0-9]+)(.*)', instance_type)
        instance_type = x.group(1) + '_' + x.group(5)
    else:
        x = re.match(r'([A-Za-z]+)([0-9]+)(.*)', instance_type)
        instance_type = x.group(1) + x.group(3)
    return instance_type


def get_feasible_resources(
        resource_filter: resources.ResourceFilter) -> List[resources.Resource]:
    df = _df
    if 'AvailabilityZone' not in df.columns:
        # TODO(woosuk): Add the 'AvailabilityZone' column to the catalog.
        df['AvailabilityZone'] = df['Region']
    if 'InstanceFamily' not in df.columns:
        # TODO(woosuk): Add the 'InstanceFamily' column to the catalog.
        df['InstanceFamily'] = df['InstanceType'].apply(_get_instance_family)

    if resource_filter.use_spot is not None:
        df = common.filter_spot(df, resource_filter.use_spot)

    if resource_filter.accelerator is None:
        acc_name = None
        acc_count = None
    else:
        acc_name = resource_filter.accelerator.name
        acc_count = resource_filter.accelerator.count
    filters = {
        'InstanceType': resource_filter.instance_type,
        'InstanceFamily': resource_filter.instance_families,
        'AcceleratorName': acc_name,
        'AcceleratorCount': acc_count,
        'vCPUs': resource_filter.num_vcpus,
        'MemoryGiB': resource_filter.cpu_memory,
        'Region': resource_filter.region,
        'AvailabilityZone': resource_filter.zone,
    }
    df = common.apply_filters(df, filters)

    azure = sky.Azure()
    return [
        resources.Resource(
            num_nodes=resource_filter.num_nodes,
            cloud=azure,
            region=row.Region,
            zone=row.AvailabilityZone,
            instance_type=row.InstanceType,
            instance_family=row.InstanceFamily,
            num_vcpus=row.vCPUs,
            cpu_memory=row.MemoryGiB,
            accelerator=resource_filter.accelerator,
            use_spot=resource_filter.use_spot,
            spot_recovery=resource_filter.spot_recovery,
            disk_size=resource_filter.disk_size,
            image_id=resource_filter.image_id,
        ) for row in df.itertuples()
    ]


def get_hourly_price(resource: resources.Resource) -> float:
    return common.get_hourly_price_impl(_df, resource.instance_type,
                                        resource.zone, resource.use_spot)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(region: Optional[str], zone: Optional[str]):
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
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
    # Ref: https://azure.microsoft.com/en-us/support/legal/offer-details/
    assert not use_spot, 'Current Azure subscription does not support spot.'
    return common.get_hourly_cost_impl(_df, instance_type, region, use_spot)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


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
    return _df[_df['InstanceType'] == instance_type]['Generation'].iloc[0]


def list_accelerators(gpus_only: bool,
                      name_filter: Optional[str],
                      case_sensitive: bool = True
                     ) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Azure offering GPUs."""
    return common.list_accelerators_impl('Azure', _df, gpus_only, name_filter,
                                         case_sensitive)
