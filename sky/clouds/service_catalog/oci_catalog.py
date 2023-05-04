"""OCI Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for OCI.

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""

import typing
import logging
from typing import Dict, List, Optional, Tuple
from sky.clouds.service_catalog import common
from sky.skylet.providers.oci.config import oci_conf

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

logger = logging.getLogger(__name__)

_df = common.read_catalog('oci/vms.csv')
_image_df = common.read_catalog('oci/images.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl('oci', _df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)



def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None) -> Optional[str]:
    del disk_tier  # unused
    if cpus is None:
        cpus = f'{oci_conf._DEFAULT_NUM_VCPUS}+'

    if memory is None:
        memory_gb_or_ratio = f'{oci_conf._DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory

    instance_type_prefix = tuple(
        f'{family}' for family in oci_conf._DEFAULT_INSTANCE_FAMILY)
    
    df = _df[_df['InstanceType'].notna()]
    df = df[df['InstanceType'].str.startswith(instance_type_prefix)]

    logger.debug(f"# get_default_instance_type: {df}")
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio)
    

def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return common.get_instance_type_for_accelerator_impl(df = _df,
                                                         acc_name = acc_name,
                                                         acc_count = acc_count,
                                                         cpus = cpus,
                                                         memory = memory,
                                                         use_spot = use_spot,
                                                         region = region,
                                                         zone = zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in OCI offering GPUs."""
    return common.list_accelerators_impl('OCI', _df, gpus_only,
                                         name_filter, region_filter,
                                         case_sensitive)

def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    logger.debug(f"* get_image_id_from_tag: {tag}-{region}")
    image_str = common.get_image_id_from_tag_impl(_image_df, tag, region)
    df = _image_df[_image_df['Tag'].str.fullmatch(tag)]
    AppCatalogListingId = df['AppCatalogListingId'].iloc[0]
    ResourceVersion = df['ResourceVersion'].iloc[0]
    return f"{image_str}{oci_conf.IMAGE_TAG_SPERATOR}{AppCatalogListingId}{oci_conf.IMAGE_TAG_SPERATOR}{ResourceVersion}"


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    return common.is_image_tag_valid_impl(_image_df, tag, region)
    