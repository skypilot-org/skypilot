"""Nebius Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Nebius.
"""
import os
import threading
import time
import typing
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from sky import sky_logging
from sky import skypilot_config
from sky.catalog import common
from sky.clouds import Nebius
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

# Keep it synced with the frequency in
# skypilot-catalog/.github/workflows/update-Nebius-catalog.yml
_PULL_FREQUENCY_HOURS = 7

_vms_file = 'nebius/vms.csv'
_vms_tenant_file = 'nebius/vms_{}.csv'

_static_df = common.read_catalog(_vms_file,
                                 pull_frequency_hours=_PULL_FREQUENCY_HOURS)
_personal_df = {}

_apply_personal_pricing_lock = threading.Lock()
logger = sky_logging.init_logger(__name__)


# pylint: disable=import-outside-toplevel
def _get_df() -> 'pd.DataFrame':
    if not skypilot_config.get_nested(
        ('nebius', 'use_personal_pricing'), default_value=True):
        return _static_df

    try:

        from sky.adaptors import nebius
        tenant_id = nebius.get_tenant_id()

        with _apply_personal_pricing_lock:
            if not tenant_id in _personal_df:
                user_catalog = _fetch_user_catalog()
                _personal_df[tenant_id] = pd.concat([
                    _static_df, user_catalog
                ]).drop_duplicates(subset=['InstanceType', 'Region'],
                                   keep='last')

        return _personal_df[tenant_id]
    except (RuntimeError, ImportError) as e:
        logger.warning('Failed to fetch personal pricing. '
                       f'{common_utils.format_exception(e)}')
    return _static_df


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Nebius does not support zones.')
    return common.validate_region_zone_impl('nebius', _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Nebius does not support zones.')
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del local_disk  # unused

    def _filter_disk_type(instance_type: str) -> bool:
        valid, _ = Nebius.check_disk_tier(instance_type, disk_tier)
        return valid

    df = _get_df()
    df = df.loc[df['InstanceType'].apply(_filter_disk_type)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus, memory, region,
                                                      zone, use_spot,
                                                      max_hourly_cost)


def _load_existing_catalog(catalog_file) -> Optional[common.LazyDataFrame]:
    catalog_path = common.get_catalog_path(catalog_file)

    if not os.path.exists(catalog_path):
        return None
    last_update = os.path.getmtime(catalog_path)
    if last_update + _PULL_FREQUENCY_HOURS * 3600 < time.time():
        return None

    return common.read_catalog(catalog_file)


# pylint: disable=import-outside-toplevel
def _get_region_projects() -> Dict[str, str]:
    """Returns a mapping from region to project ID for the current tenant."""
    from sky.adaptors import nebius
    from sky.adaptors.nebius import iam

    projects_response = nebius.sync_call(iam().ProjectServiceClient(
        nebius.sdk()).list(
            iam().ListProjectsRequest(parent_id=nebius.get_tenant_id()),
            timeout=nebius.READ_TIMEOUT))

    region_to_project_id: Dict[str, str] = {}
    for project in projects_response.items:
        region = project.status.region
        if region not in region_to_project_id:
            # Respects the override logic from get_project_by_region.
            config_project_id = skypilot_config.get_effective_region_config(
                cloud='nebius',
                region=region,
                keys=('project_id',),
                default_value=None)
            region_to_project_id[
                region] = config_project_id or project.metadata.id
    return region_to_project_id


# pylint: disable=import-outside-toplevel
def _fetch_user_catalog() -> pd.DataFrame:
    from sky.adaptors import nebius
    from sky.adaptors.nebius import billing
    from sky.catalog.data_fetchers import fetch_nebius

    catalog_file = _vms_tenant_file.format(nebius.get_tenant_id())

    existing_catalog = _load_existing_catalog(catalog_file)
    if existing_catalog is not None:
        return existing_catalog

    presets = []
    for region, project_id in _get_region_projects().items():
        platforms = fetch_nebius.fetch_platforms_for_project(project_id)
        if not platforms:
            continue

        presets.extend(
            fetch_nebius.estimate_platforms(
                platforms=platforms,
                parent_id=project_id,
                region=region,
                offer_types=[billing().OfferType.OFFER_TYPE_CONTRACT_PRICE]))

    fetch_nebius.write_preset_prices(presets,
                                     common.get_catalog_path(catalog_file))
    return common.read_catalog(catalog_file)


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
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    del local_disk  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Nebius does not support zones.')
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
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Nebius offering GPUs."""

    del require_price  # Unused.
    return common.list_accelerators_impl('nebius', _get_df(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
