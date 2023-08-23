"""IBM Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for IBM.
"""

from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import ibm
from sky.clouds import cloud
from sky.clouds.service_catalog import common

logger = sky_logging.init_logger(__name__)

_DEFAULT_INSTANCE_FAMILY = 'bx2'
_DEFAULT_NUM_VCPUS = '8'
_DEFAULT_MEMORY = 32

_df = common.read_catalog('ibm/vms.csv')


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(region: Optional[str], zone: Optional[str]):
    return common.validate_region_zone_impl('IBM', _df, region, zone)


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
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


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
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in IBM offering accelerators."""
    return common.list_accelerators_impl('IBM', _df, gpus_only, name_filter,
                                         region_filter, quantity_filter,
                                         case_sensitive)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None) -> Optional[str]:
    del disk_tier  # unused
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'

    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY}+'
    else:
        memory_gb_or_ratio = memory
    instance_type_prefix = f'{_DEFAULT_INSTANCE_FAMILY}-'
    df = _df[_df['InstanceType'].str.startswith(instance_type_prefix)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    vpc_client = ibm.client(region=region)
    try:
        vpc_client.get_image(tag)
    except ibm.ibm_cloud_sdk_core.ApiException as e:  # type: ignore[union-attr]
        logger.error(e.message)
        return False
    return True
