"""Common utilities for service catalog."""
import os
from typing import Dict, List, NamedTuple, Optional, Tuple

import difflib
import requests
import pandas as pd

from sky import sky_logging
from sky.backends import backend_utils
from sky.clouds import cloud as cloud_lib
from sky.clouds.service_catalog import constants
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_CATALOG_DIR = os.path.join(constants.LOCAL_CATALOG_DIR,
                            constants.CATALOG_SCHEMA_VERSION)
os.makedirs(_CATALOG_DIR, exist_ok=True)


class InstanceTypeInfo(NamedTuple):
    """Instance type information.

    - cloud: Cloud name.
    - instance_type: String that can be used in YAML to specify this instance
      type. E.g. `p3.2xlarge`.
    - accelerator_name: Canonical name of the accelerator. E.g. `V100`.
    - accelerator_count: Number of accelerators offered by this instance type.
    - cpu_count: Number of vCPUs offered by this instance type.
    - memory: Instance memory in GiB.
    - price: Regular instance price per hour (cheapest across all regions).
    - spot_price: Spot instance price per hour (cheapest across all regions).
    """
    cloud: str
    instance_type: Optional[str]
    accelerator_name: str
    accelerator_count: int
    cpu_count: Optional[float]
    memory: Optional[float]
    price: float
    spot_price: float


def get_catalog_path(filename: str) -> str:
    return os.path.join(_CATALOG_DIR, filename)


def read_catalog(filename: str) -> pd.DataFrame:
    """Reads the catalog from a local CSV file.

    If the file does not exist, download the up-to-date catalog that matches
    the schema version.
    """
    assert filename.endswith('.csv'), 'The catalog file must be a CSV file.'
    catalog_path = get_catalog_path(filename)
    cloud = cloud_lib.CLOUD_REGISTRY.from_str(filename.split('.csv')[0])
    if not os.path.exists(catalog_path):
        url = f'{constants.HOSTED_CATALOG_DIR_URL}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'  # pylint: disable=line-too-long
        with backend_utils.safe_console_status(
                f'Downloading {cloud} catalog...'):
            try:
                r = requests.get(url)
                r.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.error(f'Failed to download {cloud} catalog:')
                with ux_utils.print_exception_no_traceback():
                    raise e
        # Save the catalog to a local file.
        with open(catalog_path, 'w') as f:
            f.write(r.text)
        logger.info(f'A new {cloud} catalog has been downloaded to '
                    f'{catalog_path}')

    try:
        df = pd.read_csv(catalog_path)
    except Exception as e:  # pylint: disable=broad-except
        # As users can manually modify the catalog, read_csv can fail.
        logger.error(f'Failed to read {catalog_path}. '
                     'To fix: delete the csv file and try again.')
        with ux_utils.print_exception_no_traceback():
            raise e
    return df


def _get_instance_type(
    df: pd.DataFrame,
    instance_type: str,
    region: Optional[str],
) -> pd.DataFrame:
    idx = df['InstanceType'] == instance_type
    if region is not None:
        idx &= df['Region'] == region
    return df[idx]


def instance_type_exists_impl(df: pd.DataFrame, instance_type: str) -> bool:
    """Returns True if the instance type is valid."""
    return instance_type in df['InstanceType'].unique()


def validate_region_zone_impl(df: pd.DataFrame, region: Optional[str],
                              zone: Optional[str]):
    """Validates whether region and zone exist in the catalog."""

    def _get_candidate_str(loc: str, all_loc: List[str]) -> List[str]:
        candidate_loc = difflib.get_close_matches(loc, all_loc, n=5, cutoff=0.9)
        candidate_loc = sorted(candidate_loc)
        candidate_strs = ''
        if len(candidate_loc) > 0:
            candidate_strs = ', '.join(candidate_loc)
            candidate_strs = f'\nDid you mean one of these: {candidate_strs!r}?'
        return candidate_strs

    if region is not None:
        all_regions = df['Region'].unique()
        if region not in all_regions:
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid region {region!r}')
                error_msg += _get_candidate_str(region, all_regions)
                raise ValueError(error_msg)

    if zone is not None:
        all_zones = df['AvailabilityZone'].unique()
        if zone not in all_zones:
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid zone {zone!r}')
                error_msg += _get_candidate_str(zone, all_zones)
                raise ValueError(error_msg)

    if region is not None and zone is not None:
        if zone not in df[df['Region'] == region]['AvailabilityZone'].unique():
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid zone {zone!r} for region {region!r}')
                error_msg += _get_candidate_str(
                    zone,
                    df[df['Region'] == region]['AvailabilityZone'].unique())
                raise ValueError(error_msg)


def get_hourly_cost_impl(
    df: pd.DataFrame,
    instance_type: str,
    region: Optional[str],
    use_spot: bool = False,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _get_instance_type(df, instance_type, region)
    assert pd.isnull(
        df['Price'].iloc[0]) is False, (f'Missing price for "{instance_type}, '
                                        f'Spot: {use_spot}" in the catalog.')
    # TODO(zhwu): We should handle the price difference among different regions.
    price_str = 'SpotPrice' if use_spot else 'Price'
    assert region is None or len(set(df[price_str])) == 1, df

    cheapest_idx = df[price_str].idxmin()

    cheapest = df.loc[cheapest_idx]
    return cheapest[price_str]


def get_vcpus_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Optional[float]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    assert len(set(df['vCPUs'])) == 1, ('Cannot determine the number of vCPUs '
                                        f'of the instance type {instance_type}.'
                                        f'\n{df}')
    vcpus = df['vCPUs'].iloc[0]
    if pd.isna(vcpus):
        return None
    return float(vcpus)


def get_accelerators_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Optional[Dict[str, int]]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        return None
    return {acc_name: int(acc_count)}


def get_instance_type_for_accelerator_impl(
    df: pd.DataFrame,
    acc_name: str,
    acc_count: int,
) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    result = df[(df['AcceleratorName'].str.fullmatch(acc_name, case=False)) &
                (df['AcceleratorCount'] == acc_count)]
    if len(result) == 0:
        fuzzy_result = df[
            (df['AcceleratorName'].str.contains(acc_name, case=False)) &
            (df['AcceleratorCount'] >= acc_count)]
        fuzzy_result = fuzzy_result.sort_values('Price', ascending=True)
        fuzzy_result = fuzzy_result[['AcceleratorName',
                                     'AcceleratorCount']].drop_duplicates()
        fuzzy_candidate_list = []
        if len(fuzzy_result) > 0:
            for _, row in fuzzy_result.iterrows():
                fuzzy_candidate_list.append(f'{row["AcceleratorName"]}:'
                                            f'{int(row["AcceleratorCount"])}')
        return (None, fuzzy_candidate_list)
    # Current strategy: choose the cheapest instance
    result = result.sort_values('Price', ascending=True)
    instance_types = list(result['InstanceType'].drop_duplicates())
    return (instance_types, [])


def list_accelerators_impl(
    cloud: str,
    df: pd.DataFrame,
    gpus_only: bool,
    name_filter: Optional[str],
    case_sensitive: bool = True,
) -> Dict[str, List[InstanceTypeInfo]]:
    """Lists accelerators offered in a cloud service catalog.

    `name_filter` is a regular expression used to filter accelerator names
    using pandas.Series.str.contains.

    Returns a mapping from the canonical names of accelerators to a list of
    instance types offered by this cloud.
    """
    if gpus_only:
        df = df[~pd.isna(df['GpuInfo'])]
    df = df[[
        'InstanceType',
        'AcceleratorName',
        'AcceleratorCount',
        'vCPUs',
        'MemoryGiB',
        'Price',
        'SpotPrice',
    ]].dropna(subset=['AcceleratorName']).drop_duplicates()
    if name_filter is not None:
        df = df[df['AcceleratorName'].str.contains(name_filter,
                                                   case=case_sensitive,
                                                   regex=True)]
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    grouped = df.groupby('AcceleratorName')

    def make_list_from_df(rows):
        # Only keep the lowest prices across regions.
        rows = rows.groupby([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB'
        ],
                            dropna=False).aggregate(min).reset_index()
        ret = rows.apply(
            lambda row: InstanceTypeInfo(
                cloud,
                row['InstanceType'],
                row['AcceleratorName'],
                row['AcceleratorCount'],
                row['vCPUs'],
                row['MemoryGiB'],
                row['Price'],
                row['SpotPrice'],
            ),
            axis='columns',
        ).tolist()
        ret.sort(key=lambda info: (info.accelerator_count, info.cpu_count
                                   if info.cpu_count is not None else 0))
        return ret

    return {k: make_list_from_df(v) for k, v in grouped}


def get_region_zones(df: pd.DataFrame,
                     use_spot: bool) -> List[cloud_lib.Region]:
    """Returns a list of regions/zones from a dataframe."""
    price_str = 'SpotPrice' if use_spot else 'Price'
    df = df.dropna(subset=[price_str]).sort_values(price_str)
    regions = [cloud_lib.Region(region) for region in df['Region'].unique()]
    if 'AvailabilityZone' in df.columns:
        zones_in_region = df.groupby('Region')['AvailabilityZone'].apply(
            lambda x: [cloud_lib.Zone(zone) for zone in x])
        for region in regions:
            region.set_zones(zones_in_region[region.name])
    return regions


def _accelerator_in_region(df: pd.DataFrame, acc_name: str, acc_count: int,
                           region: str) -> bool:
    """Returns True if the accelerator is in the region."""
    return len(df[(df['AcceleratorName'] == acc_name) &
                  (df['AcceleratorCount'] == acc_count) &
                  (df['Region'] == region)]) > 0


def _accelerator_in_zone(df: pd.DataFrame, acc_name: str, acc_count: int,
                         zone: str) -> bool:
    """Returns True if the accelerator is in the zone."""
    return len(df[(df['AcceleratorName'] == acc_name) &
                  (df['AcceleratorCount'] == acc_count) &
                  (df['AvailabilityZone'] == zone)]) > 0


def accelerator_in_region_or_zone_impl(
    df: pd.DataFrame,
    accelerator_name: str,
    acc_count: int,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> bool:
    """Returns True if the accelerator is in the region or zone."""
    assert region is not None or zone is not None, (
        'Both region and zone are None.')
    if zone is None:
        return _accelerator_in_region(df, accelerator_name, acc_count, region)
    else:
        return _accelerator_in_zone(df, accelerator_name, acc_count, zone)
