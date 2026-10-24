"""Lium | Catalog

This module loads the service catalog file and can be used to query instance
types and pricing information for Lium.
"""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.catalog import common
from sky.catalog.data_fetchers import fetch_lium
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import pandas as pd
    import requests

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

_catalog: Optional['pd.DataFrame'] = None


def _get_df() -> 'pd.DataFrame':
    """Returns the catalog, from the published file or from the live feed."""
    global _catalog
    if _catalog is None:
        catalog = _read_published_catalog()
        if catalog is None:
            catalog = _build_catalog_from_node_feed()
        _catalog = catalog
    return _catalog


def _read_published_catalog() -> Optional['pd.DataFrame']:
    """Returns the catalog SkyPilot publishes, or None when it is missing.

    A cloud reaches a SkyPilot release before its file reaches the catalog
    repository, so the file is not there for every version that knows Lium.
    """
    # Lium node inventory changes through the day, so the catalog is refreshed
    # more often than the clouds with a fixed instance list.
    catalog = common.read_catalog('lium/vms.csv', pull_frequency_hours=7)
    try:
        # The read is lazy, so the download runs on this first column read.
        catalog.columns  # pylint: disable=pointless-statement
    except (exceptions.CloudError,
            requests.exceptions.RequestException) as fetch_error:
        logger.warning(
            f'The published Lium catalog is not available: {fetch_error}')
        return None
    return catalog


def _build_catalog_from_node_feed() -> 'pd.DataFrame':
    """Builds the catalog out of the public Lium node feed.

    The feed needs no API key and carries the offers the published catalog is
    written from. An empty catalog is the last resort: Lium then offers
    nothing, rather than breaking every command that walks the clouds.
    """
    try:
        rows = fetch_lium.catalog_rows()
    except requests.exceptions.RequestException as fetch_error:
        logger.warning(f'The Lium node feed is not available: {fetch_error}')
        rows = []
    return pd.DataFrame(rows, columns=fetch_lium.CATALOG_COLUMNS)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lium does not support zones.')
    return common.validate_region_zone_impl('lium', _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the hourly price of the instance type in the region."""
    if use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Lium does not support spot instances.')
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # Lium has no disk tiers.
    return common.get_instance_type_for_cpus_mem_impl(_get_df(), cpus, memory,
                                                      region, zone, use_spot,
                                                      max_hourly_cost)


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
    max_hourly_cost: Optional[float] = None
) -> Tuple[Optional[List[str]], List[str]]:
    """Returns the instance types that have the given accelerator."""
    del local_disk  # unused
    if use_spot:
        return None, ['Lium does not support spot instances.']
    return common.get_instance_type_for_accelerator_impl(
        df=_get_df(),
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
        max_hourly_cost=max_hourly_cost)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    if use_spot:
        return []
    catalog = _get_df()
    df = catalog[catalog['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Lium offering GPUs."""
    del require_price  # unused
    return common.list_accelerators_impl('Lium', _get_df(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
