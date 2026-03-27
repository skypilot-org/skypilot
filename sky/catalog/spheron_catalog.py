"""Spheron | Catalog

This module loads pricing and instance information from the Spheron API
and can be used to query instance types and pricing information for Spheron.
"""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.adaptors import common as adaptors_common
from sky.catalog import common

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')

_df = None


def _get_df():
    """Get the dataframe, fetching from catalog CSV if available."""
    global _df
    if _df is None:
        try:
            df = common.read_catalog('spheron/vms.csv')
        except FileNotFoundError:
            _df = pd.DataFrame(columns=[
                'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
                'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice',
                'Provider', 'OperatingSystem', 'SpheronInstanceType'
            ])
        else:
            df = df[df['InstanceType'].notna()]
            if 'AcceleratorName' in df.columns:
                df = df[df['AcceleratorName'].notna()]
                df = df.assign(AcceleratorName=df['AcceleratorName'].astype(
                    str).str.strip())
            _df = df.reset_index(drop=True)
    return _df


def _get_df_for_pricing(use_spot: bool) -> 'pd.DataFrame':
    """Return catalog rows matching the requested pricing mode.

    Spheron's SPOT instances are distinct offer types (SpheronInstanceType=SPOT)
    that can never be provisioned as on-demand, and DEDICATED instances cannot
    be provisioned as spot.  We must filter strictly so the optimizer never
    selects a DEDICATED row for a spot request (or vice-versa).
    """
    df = _get_df()
    if use_spot:
        return df[df['SpheronInstanceType'].str.upper() == 'SPOT']
    return df[df['SpheronInstanceType'].str.upper() != 'SPOT']


def _is_not_found_error(err: ValueError) -> bool:
    msg = str(err).lower()
    return 'not found' in msg or 'not supported' in msg


def _call_or_default(func, default):
    try:
        return func()
    except ValueError as err:
        if _is_not_found_error(err):
            return default
        raise


def get_instance_info(instance_type: str, region: str,
                      use_spot: bool = False) -> Dict[str, Optional[str]]:
    """Get Spheron-specific metadata for a given instance type and region.

    Returns a dict with Provider, OperatingSystem, SpheronInstanceType,
    AcceleratorName, AcceleratorCount for use in make_deploy_resources_variables.
    """
    df = _get_df()
    mask = (df['InstanceType'] == instance_type) & (df['Region'] == region)
    matched = df[mask]
    if matched.empty:
        # Fall back to any row with this instance type
        mask = df['InstanceType'] == instance_type
        matched = df[mask]
    if matched.empty:
        return {
            'Provider': '',
            'OperatingSystem': 'ubuntu-22.04',
            'SpheronInstanceType': 'SPOT' if use_spot else 'DEDICATED',
            'AcceleratorName': '',
            'AcceleratorCount': 1,
        }
    # Prefer a row whose SpheronInstanceType matches the requested pricing mode
    # to avoid returning SPOT metadata for an on-demand launch and vice versa.
    preferred_type = 'SPOT' if use_spot else 'DEDICATED'
    typed_rows = matched[matched['SpheronInstanceType'].str.upper() ==
                         preferred_type]
    row = (typed_rows if not typed_rows.empty else matched).iloc[0]
    return {
        'Provider': str(row.get('Provider', '')),
        'OperatingSystem': str(row.get('OperatingSystem', 'ubuntu-22.04')),
        'SpheronInstanceType': str(row.get('SpheronInstanceType', 'DEDICATED')),
        'AcceleratorName': str(row.get('AcceleratorName', '')),
        'AcceleratorCount': int(float(row.get('AcceleratorCount', 1))),
    }


def instance_type_exists(instance_type: str) -> bool:
    """Check if an instance type exists."""
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validate region and zone for Spheron."""
    return common.validate_region_zone_impl('spheron', _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    """Get vCPUs and memory from instance type."""
    return _call_or_default(
        lambda: common.get_vcpus_mem_from_instance_type_impl(
            _get_df(), instance_type), (None, None))


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    """Get default instance type based on requirements."""
    del disk_tier, local_disk  # Spheron doesn't support custom disk tiers
    return _call_or_default(
        lambda: common.get_instance_type_for_cpus_mem_impl(
            _get_df_for_pricing(use_spot), cpus, memory, region, zone,
            use_spot, max_hourly_cost),
        None)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    """Get accelerator information from instance type."""
    return _call_or_default(
        lambda: common.get_accelerators_from_instance_type_impl(
            _get_df(), instance_type), None)


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
    """Returns a list of instance types that have the given accelerator."""
    del local_disk  # unused

    return _call_or_default(
        lambda: common.get_instance_type_for_accelerator_impl(
            df=_get_df_for_pricing(use_spot),
            acc_name=acc_name,
            acc_count=acc_count,
            cpus=cpus,
            memory=memory,
            use_spot=use_spot,
            region=region,
            zone=zone,
            max_hourly_cost=max_hourly_cost), (None, []))


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    """Get regions and zones for an instance type."""
    df = _get_df()
    df_filtered = df[df['InstanceType'] == instance_type]
    return _call_or_default(
        lambda: common.get_region_zones(df_filtered, use_spot), [])


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Spheron offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('Spheron', _get_df(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
