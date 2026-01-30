"""Mithril Cloud service catalog.

This module loads and queries the service catalog for Mithril Cloud.
Supports on-demand catalog refresh from the Mithril API when an
accelerator or instance type is not found.
"""

import logging
import os
import time
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common
from sky.catalog.data_fetchers import fetch_mithril
from sky.provision.mithril import utils as mithril_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

logger = logging.getLogger(__name__)

# Minimum interval between on-demand catalog refreshes (in seconds)
_MIN_REFRESH_INTERVAL_SECONDS = 3600  # 1 hour

# Mithril is a private cloud - catalog comes from the Mithril API, not from
# the public skypilot-catalog GitHub repo. Set pull_frequency_hours=None to
# disable GitHub pulls.
_df: Optional[common.LazyDataFrame] = None


def _get_df() -> common.LazyDataFrame:
    """Get the catalog dataframe, fetching from API on first load."""
    global _df
    if _df is not None:
        return _df

    # Always fetch fresh catalog from API on first load (e.g., API server start)
    # Use force=True to bypass rate limiting on initial load
    refreshed = _refresh_catalog_from_api(force=True)
    if not refreshed:
        # Refresh failed; load from cache or GitHub fallback
        catalog_path = common.get_catalog_path('mithril/vms.csv')
        if os.path.exists(catalog_path):
            logger.debug('Using existing cached catalog.')
        else:
            logger.debug('Falling back to GitHub-hosted catalog...')
        _df = common.read_catalog('mithril/vms.csv', pull_frequency_hours=None)

    if _df is None:
        raise mithril_utils.MithrilError('Failed to load Mithril catalog')
    return _df


def _catalog_recently_refreshed() -> bool:
    """Check if the catalog was refreshed within the minimum interval.

    Returns:
        True if the catalog file was modified within the last hour.
    """
    catalog_path = common.get_catalog_path('mithril/vms.csv')
    if not os.path.exists(catalog_path):
        return False

    mtime = os.path.getmtime(catalog_path)
    age_seconds = time.time() - mtime
    return age_seconds < _MIN_REFRESH_INTERVAL_SECONDS


def _refresh_catalog_from_api(force: bool = False) -> bool:
    """Refresh the catalog by fetching from the Mithril API.

    Args:
        force: If True, bypass the rate limit and always refresh.

    Returns:
        True if the catalog was successfully refreshed, False otherwise.
    """
    global _df

    if not force and _catalog_recently_refreshed():
        return False  # Catalog was refreshed recently, skip

    try:
        catalog_path = common.get_catalog_path('mithril/vms.csv')
        logger.debug('Refreshing Mithril catalog from API...')

        fetch_mithril.create_catalog(output_path=catalog_path)

        # Reload the dataframe with fresh data
        _df = common.read_catalog('mithril/vms.csv', pull_frequency_hours=None)
        logger.debug('Mithril catalog refreshed successfully.')
        return True

    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to refresh Mithril catalog from API: {e}')
        return False


def refresh_catalog() -> bool:
    """Public API to refresh the Mithril catalog from the API.

    Returns:
        True if the catalog was successfully refreshed, False otherwise.
    """
    return _refresh_catalog_from_api()


def instance_type_exists(instance_type: str) -> bool:
    result = common.instance_type_exists_impl(_get_df(), instance_type)

    # If not found, try refreshing catalog (no-op if recently refreshed)
    if not result:
        if _refresh_catalog_from_api():
            result = common.instance_type_exists_impl(_get_df(), instance_type)

    return result


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')
    return common.validate_region_zone_impl('mithril', _get_df(), region, zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
    instance_type: str,) -> Tuple[Optional[float], Optional[float]]:
    try:
        return common.get_vcpus_mem_from_instance_type_impl(
            _get_df(), instance_type)
    except ValueError:
        return None, None


def get_accelerators_from_instance_type(
    instance_type: str,) -> Optional[Dict[str, Union[int, float]]]:
    try:
        return common.get_accelerators_from_instance_type_impl(
            _get_df(), instance_type)
    except ValueError:
        return None


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[resources_utils.DiskTier] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Optional[str]:
    del disk_tier  # Unused
    return common.get_instance_type_for_cpus_mem_impl(_get_df(), cpus, memory,
                                                      region, zone)


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

    If the accelerator is not found, attempts to refresh the catalog
    from the Mithril API before returning empty results.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Mithril Cloud does not support zones.')

    result = common.get_instance_type_for_accelerator_impl(
        df=_get_df(),
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
    )

    # If no matching instance types found, try refreshing catalog
    if result[0] is None:
        if _refresh_catalog_from_api():
            # Retry the lookup with refreshed catalog
            result = common.get_instance_type_for_accelerator_impl(
                df=_get_df(),
                acc_name=acc_name,
                acc_count=acc_count,
                cpus=cpus,
                memory=memory,
                use_spot=use_spot,
                region=region,
                zone=zone,
            )

    return result


def regions() -> List['cloud.Region']:
    return common.get_region_zones(_get_df(), use_spot=False)


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
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Mithril Cloud offering accelerators."""
    del require_price  # Unused
    return common.list_accelerators_impl(
        'Mithril',
        _get_df(),
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )
