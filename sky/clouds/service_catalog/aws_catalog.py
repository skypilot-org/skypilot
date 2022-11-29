"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
import typing
from typing import Dict, List, Optional, Tuple

from sky.clouds.service_catalog import common
from sky.clouds.service_catalog import constants

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_df = common.read_catalog('aws/vms.csv')
_image_df = common.read_catalog('aws/images.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def _filter_catalog_by_area(area: Optional[str]):
    df = _df
    if area is None:
        return df
    df = df[df['Region'].str.startswith(f'{area}-')]
    return df


def validate_region_zone(
        region: Optional[str], zone: Optional[str],
        area: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    df = _filter_catalog_by_area(area)
    return common.validate_region_zone_impl(df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  area: Optional[str],
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    df = _filter_catalog_by_area(area)
    return common.accelerator_in_region_or_zone_impl(df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    area: Optional[str],
                    region: Optional[str] = None,
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _filter_catalog_by_area(area)
    return common.get_hourly_cost_impl(df, instance_type, region, use_spot)


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


def get_region_zones_for_instance_type(
        instance_type: str, use_spot: bool,
        area: Optional[str]) -> List['cloud.Region']:
    area = area or constants.DEFAULT_AREA
    df = _filter_catalog_by_area(area)
    df = df[df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(gpus_only: bool,
                      name_filter: Optional[str],
                      case_sensitive: bool = True
                     ) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in AWS offering accelerators."""
    return common.list_accelerators_impl('AWS', _df, gpus_only, name_filter,
                                         case_sensitive)


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    return common.get_image_id_from_tag_impl(_image_df, tag, region)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    return common.is_image_tag_valid_impl(_image_df, tag, region)
