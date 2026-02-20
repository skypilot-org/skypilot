"""OCI Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for OCI.

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 - Hysun He (hysun.he@oracle.com) @ Jun, 2023: Reduce retry times by
   excluding those unsubscribed regions.
 - Hysun He (hysun.he@oracle.com) @ Oct 14, 2024: Bug fix for validation
   of the Marketplace images
"""

import logging
import threading
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.adaptors import oci as oci_adaptor
from sky.catalog import common
from sky.clouds import OCI
from sky.clouds.utils import oci_utils
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud  # pylint: disable=ungrouped-imports

logger = logging.getLogger(__name__)

_df = None
_image_df = common.read_catalog('oci/images.csv')

_lock = threading.RLock()


def _get_df() -> 'pd.DataFrame':
    with _lock:
        global _df
        if _df is not None:
            return _df

        df = common.read_catalog('oci/vms.csv')
        try:
            oci_adaptor.oci.load_module()
        except ImportError:
            _df = df
            return _df

        try:
            config_profile = oci_utils.oci_config.get_profile()
            client = oci_adaptor.get_identity_client(profile=config_profile)

            subscriptions = client.list_region_subscriptions(
                tenancy_id=oci_adaptor.get_oci_config(
                    profile=config_profile)['tenancy']).data

            subscribed_regions = [r.region_name for r in subscriptions]

        except (oci_adaptor.oci.exceptions.ConfigFileNotFound,
                oci_adaptor.oci.exceptions.InvalidConfig) as e:
            # This should only happen in testing where oci config is
            # missing, because it means the 'sky check' will fail if
            # enter here (meaning OCI disabled).
            logger.debug(f'It is OK goes here when testing: {str(e)}')
            subscribed_regions = []

        except oci_adaptor.oci.exceptions.ServiceError as e:
            # Should never expect going here. However, we still catch
            # it so that if any OCI call failed, the program can still
            # proceed with try-and-error way.
            logger.warning(
                f'Unexpected exception when handle catalog: {str(e)}')
            subscribed_regions = []

        if subscribed_regions:
            _df = df[df['Region'].isin(subscribed_regions)]
        else:
            _df = df

        return _df


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl('oci', _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              local_disk: Optional[str] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    del local_disk  # unused
    if cpus is None:
        cpus = f'{oci_utils.oci_config.DEFAULT_NUM_VCPUS}+'

    if memory is None:
        memory_gb_or_ratio = f'{oci_utils.oci_config.DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory

    def _filter_disk_type(instance_type: str) -> bool:
        valid, _ = OCI.check_disk_tier(instance_type, disk_tier)
        return valid

    instance_type_prefix = tuple(
        f'{family}' for family in oci_utils.oci_config.DEFAULT_INSTANCE_FAMILY)

    df = _get_df()
    df = df[df['InstanceType'].notna()]
    df = df[df['InstanceType'].str.startswith(instance_type_prefix)]
    df = df.loc[df['InstanceType'].apply(_filter_disk_type)]

    logger.debug(f'# get_default_instance_type: {df}')
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio,
                                                      region, zone)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(
        _get_df(), instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    del local_disk  # unused
    return common.get_instance_type_for_accelerator_impl(df=_get_df(),
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _get_df()
    df = df[df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in OCI offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('OCI', _get_df(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    # Always try get region-specific imageid first (for backward compatible)
    image_str = common.get_image_id_from_tag_impl(_image_df, tag, region)
    if image_str is None:
        # Support cross-region (general) imageid
        image_str = common.get_image_id_from_tag_impl(_image_df, tag, None)

    df = _image_df[_image_df['Tag'].str.fullmatch(tag)]
    app_catalog_listing_id = df['AppCatalogListingId'].iloc[0]
    resource_version = df['ResourceVersion'].iloc[0]

    return (f'{image_str}{oci_utils.oci_config.IMAGE_TAG_SPERATOR}'
            f'{app_catalog_listing_id}{oci_utils.oci_config.IMAGE_TAG_SPERATOR}'
            f'{resource_version}')


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    # Oct.14, 2024 by Hysun He: Marketplace images are region neutral, so don't
    # check with region for the Marketplace images.
    df = _image_df[_image_df['Tag'].str.fullmatch(tag)]
    if df.empty:
        return False
    app_catalog_listing_id = df['AppCatalogListingId'].iloc[0]
    if app_catalog_listing_id:
        return True
    return common.is_image_tag_valid_impl(_image_df, tag, region)


def get_image_os_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    del region
    df = _image_df[_image_df['Tag'].str.fullmatch(tag)]
    if df.empty:
        os_type = oci_utils.oci_config.get_default_image_os()
    else:
        os_type = df['OS'].iloc[0]

    logger.debug(f'Operation system for the image {tag} is {os_type}')
    return os_type
