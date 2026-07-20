""" Vast | Catalog

This module loads the service catalog file and can be used to
query instance types and pricing information for Vast.ai.
"""

import io
import typing
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from sky.catalog import common
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_REQUIRED_CATALOG_COLUMNS = {
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Price',
    'SpotPrice',
    'Region',
}


def _catalog_df() -> pd.DataFrame:
    """Fetch the current stable Vast instance-type metadata.

    Vast marketplace offers are selected only during provisioning. They must
    not become catalog identities because offer IDs can disappear at any time.
    This intentionally does not use SkyPilot's local catalog cache: a catalog
    request must reflect the latest hosted Vast GPU inventory.
    """
    try:
        catalog_df = pd.read_csv(
            io.StringIO(common.fetch_catalog_text('vast/vms.csv')))
    except common.CatalogFetchError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise common.CatalogFetchError(
            'Current Vast catalog is not valid CSV.') from exc

    missing_columns = _REQUIRED_CATALOG_COLUMNS.difference(catalog_df.columns)
    if missing_columns:
        missing = ', '.join(sorted(missing_columns))
        raise common.CatalogFetchError(
            f'Current Vast catalog is missing required columns: {missing}.')

    accelerator_count = pd.to_numeric(catalog_df['AcceleratorCount'],
                                      errors='coerce')
    usable_gpu_rows = (catalog_df['AcceleratorName'].notna() &
                       accelerator_count.gt(0) &
                       catalog_df['GpuInfo'].notna())
    if not usable_gpu_rows.any():
        raise common.CatalogFetchError(
            'Current Vast catalog contains no usable GPU rows.')
    return catalog_df


def _apply_datacenter_filter(df: pd.DataFrame,
                             datacenter_only: bool) -> pd.DataFrame:
    """Filter dataframe by hosting_type if datacenter_only is True.

    hosting_type: 0 = Consumer hosted, 1 = Datacenter hosted
    """
    if not datacenter_only or 'HostingType' not in df.columns:
        return df
    return df[df['HostingType'] >= 1]


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_catalog_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Vast does not support zones.')
    return common.validate_region_zone_impl('vast', _catalog_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Vast does not support zones.')
    return common.get_hourly_cost_impl(_catalog_df(), instance_type, use_spot,
                                       region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(
        _catalog_df(), instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              local_disk: Optional[str] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None,
                              use_spot: bool = False,
                              max_hourly_cost: Optional[float] = None,
                              datacenter_only: bool = False) -> Optional[str]:
    del disk_tier, local_disk
    # NOTE: After expanding catalog to multiple entries, you may
    # want to specify a default instance type or family.
    df = _apply_datacenter_filter(_catalog_df(), datacenter_only)
    return common.get_instance_type_for_cpus_mem_impl(df, cpus, memory, region,
                                                      zone, use_spot,
                                                      max_hourly_cost)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(
        _catalog_df(), instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        max_hourly_cost: Optional[float] = None,
        datacenter_only: bool = False) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types that have the given accelerator.

    Args:
        datacenter_only: If True, only return instances hosted in datacenters
            (hosting_type >= 1).
    """
    del local_disk  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Vast does not support zones.')
    df = _apply_datacenter_filter(_catalog_df(), datacenter_only)
    return common.get_instance_type_for_accelerator_impl(
        df=df,
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
    catalog_df = _catalog_df()
    df = catalog_df[catalog_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


# TODO: this differs from the fluffy catalog version
def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Vast offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('Vast', _catalog_df(), gpus_only,
                                         name_filter,
                                         region_filter, quantity_filter,
                                         case_sensitive, all_regions)
