"""Seeweb service catalog.

This module loads the service catalog file and can be used to
query instance types and pricing information for Seeweb.
"""

import os
import typing
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from sky.catalog import common
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud


# Use standard SkyPilot catalog path for Seeweb
def get_seeweb_catalog_path() -> str:
    """Get the standard catalog path for Seeweb."""
    return common.get_catalog_path('seeweb/vms.csv')


def clean_gpu_name(gpu_name: str) -> str:
    """Clean GPU name by replacing spaces with hyphens for SkyPilot compatibility."""
    if not gpu_name or pd.isna(gpu_name):
        return ''
    return str(gpu_name).replace(' ', '-')


def clean_accelerator_names_in_df(df: pd.DataFrame) -> pd.DataFrame:
    """Clean accelerator names in the DataFrame by replacing spaces with underscores."""
    if 'AcceleratorName' in df.columns:
        df = df.copy()
        df['AcceleratorName'] = df['AcceleratorName'].apply(clean_gpu_name)
    return df


# Load catalog using standard SkyPilot system
try:
    catalog_path = get_seeweb_catalog_path()
    if os.path.exists(catalog_path):
        _df = pd.read_csv(catalog_path)
        # Clean accelerator names
        _df = clean_accelerator_names_in_df(_df)
    else:
        # Create empty DataFrame if catalog doesn't exist
        # This allows SkyPilot to work even without a catalog file
        _df = pd.DataFrame()
except Exception as e:
    # Create empty DataFrame as fallback
    # This ensures SkyPilot doesn't crash if catalog loading fails
    _df = pd.DataFrame()


def instance_type_exists(instance_type: str) -> bool:
    result = common.instance_type_exists_impl(_df, instance_type)
    return result


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Seeweb does not support zones.')

    result = common.validate_region_zone_impl('Seeweb', _df, region, zone)
    return result


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Seeweb does not support zones.')

    result = common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                         zone)
    return result


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    result = common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)
    return result


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    del disk_tier  # unused
    result = common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory,
                                                        region, zone)
    return result


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    # Filter the dataframe for the specific instance type
    df_filtered = _df[_df['InstanceType'] == instance_type]
    if df_filtered.empty:
        return None

    # Get the first row (all rows for same instance type should have same accelerator info)
    row = df_filtered.iloc[0]
    acc_name = row['AcceleratorName']
    acc_count = row['AcceleratorCount']

    # Check if the instance has accelerators
    if pd.isna(acc_name) or pd.isna(
            acc_count) or acc_name == '' or acc_count == '':
        return None

    # Convert accelerator count to int/float
    try:
        if int(acc_count) == acc_count:
            acc_count = int(acc_count)
        else:
            acc_count = float(acc_count)
    except (ValueError, TypeError):
        return None

    result = {acc_name: acc_count}
    return result


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """
    Restituisce i dati per l'elenco finale che 
    arriva all'utente.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Seeweb does not support zones.')

    result = common.get_instance_type_for_accelerator_impl(df=_df,
                                                           acc_name=acc_name,
                                                           acc_count=acc_count,
                                                           cpus=cpus,
                                                           memory=memory,
                                                           use_spot=use_spot,
                                                           region=region,
                                                           zone=zone)
    return result


def regions() -> List['cloud.Region']:
    result = common.get_region_zones(_df, use_spot=False)
    return result


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool = False
                                      ) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    region_list = common.get_region_zones(df, use_spot)

    # Hack: Enforce hierarchical region priority
    # Priority order: 1. Frosinone (it-fr2), 2. Milano (it-mi2), 3. Lugano (ch-lug1), 4. Bulgaria (bg-sof1)
    priority_regions = ['it-fr2', 'it-mi2', 'ch-lug1', 'bg-sof1']
    prioritized_regions = []
    other_regions = []

    # First, add regions in priority order if they exist
    for priority_region in priority_regions:
        for region in region_list:
            if region.name == priority_region:
                prioritized_regions.append(region)
                break

    # Then, add any remaining regions that weren't in the priority list
    for region in region_list:
        if region.name not in priority_regions:
            other_regions.append(region)

    result = prioritized_regions + other_regions
    return result


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    result = common.list_accelerators_impl('Seeweb', _df, gpus_only,
                                           name_filter, region_filter,
                                           quantity_filter, case_sensitive,
                                           all_regions)
    return result
