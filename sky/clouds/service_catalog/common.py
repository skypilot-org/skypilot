"""Common utilities for service catalog."""
import os
from typing import Dict, List, NamedTuple, Optional

import pandas as pd

from sky.clouds import cloud as cloud_lib
from sky import sky_logging
import colorama

logger = sky_logging.init_logger(__name__)


class InstanceTypeInfo(NamedTuple):
    """Instance type information.

    - cloud: Cloud name.
    - instance_type: String that can be used in YAML to specify this instance
      type. E.g. `p3.2xlarge`.
    - accelerator_name: Canonical name of the accelerator. E.g. `V100`.
    - accelerator_count: Number of accelerators offered by this instance type.
    - memory: Instance memory in GiB.
    - price: Regular instance price per hour (cheapest across all regions).
    - spot_price: Spot instance price per hour (cheapest across all regions).
    """
    cloud: str
    instance_type: str
    accelerator_name: str
    accelerator_count: int
    memory: float
    price: float
    spot_price: float


def get_data_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                        filename)


def read_catalog(filename: str) -> pd.DataFrame:
    return pd.read_csv(get_data_path(filename))


def _get_instance_type(
    df: pd.DataFrame,
    instance_type: str,
    region: Optional[str],
) -> pd.DataFrame:
    idx = df['InstanceType'] == instance_type
    if region is not None:
        idx &= df['Region'] == region
    return df[idx]


def get_hourly_cost_impl(
    df: pd.DataFrame,
    instance_type: str,
    region: str,
    use_spot: bool = False,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _get_instance_type(df, instance_type, region)
    assert len(set(df['Price'])) == 1, df
    if not use_spot:
        return df['Price'].iloc[0]

    cheapest_idx = df['SpotPrice'].idxmin()
    if pd.isnull(cheapest_idx):
        return df['Price'].iloc[0]

    cheapest = df.loc[cheapest_idx]
    return cheapest['SpotPrice']


def get_accelerators_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Optional[Dict[str, int]]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
        raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        return None
    return {acc_name: int(acc_count)}


def get_instance_type_for_accelerator_impl(
    cloud: str,
    df: pd.DataFrame,
    acc_name: str,
    acc_count: int,
) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    result = df[(df['AcceleratorName'] == acc_name) &
                (df['AcceleratorCount'] == acc_count)]
    if len(result) == 0:
        fuzzy_result = df[(df['AcceleratorName'].str.contains(acc_name)) &
                          (df['AcceleratorCount'] >= acc_count)]
        fuzzy_result.sort_values('Price', ascending=True, inplace=True)
        fuzzy_result = fuzzy_result[['AcceleratorName',
                                     'AcceleratorCount']].drop_duplicates()
        if len(fuzzy_result) > 0:
            candidate_list = ''
            for _, row in fuzzy_result.iterrows():
                candidate_list += (f' {row["AcceleratorName"]}:'
                                   f'{int(row["AcceleratorCount"])}')
            logger.info(f'No resource satisfying {acc_name}:{int(acc_count)}'
                        f' on {cloud.upper()}. Did you mean:'
                        f'{colorama.Fore.CYAN}'
                        f'{candidate_list}'
                        f'{colorama.Style.RESET_ALL}')
        return None
    # Current strategy: choose the cheapest instance
    result.sort_values('Price', ascending=True, inplace=True)
    best_candidate = result.iloc[0]['InstanceType']
    instance_types = list(result['InstanceType'].drop_duplicates())
    if len(result) > 1:
        logger.info(f'Multiple {cloud.upper()} instances satisfy '
                    f'{acc_name}:{int(acc_count)}. '
                    f'Choosing the cheapest {best_candidate} among: '
                    f'{instance_types}.\n'
                    f'Run \'sky show-gpus {acc_name} --cloud {cloud}\' to '
                    'list more details.')
    return best_candidate


def list_accelerators_impl(
    cloud: str,
    df: pd.DataFrame,
    gpus_only: bool,
    name_filter: Optional[str],
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
        'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'MemoryGiB',
        'Price', 'SpotPrice'
    ]].dropna(subset=['AcceleratorName']).drop_duplicates()
    if name_filter is not None:
        df = df[df['AcceleratorName'].str.contains(name_filter, regex=True)]
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    grouped = df.groupby('AcceleratorName')

    def make_list_from_df(rows):
        # Only keep the lowest prices across regions.
        rows = rows.groupby([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'MemoryGiB'
        ],
                            dropna=False).aggregate(min).reset_index()
        ret = rows.apply(
            lambda row: InstanceTypeInfo(
                cloud,
                row['InstanceType'],
                row['AcceleratorName'],
                row['AcceleratorCount'],
                row['MemoryGiB'],
                row['Price'],
                row['SpotPrice'],
            ),
            axis='columns',
        ).tolist()
        ret.sort(key=lambda info: (info.accelerator_count, info.memory))
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
