"""Common utilities for service catalog."""
import json
import os
from typing import Dict, List, NamedTuple, Optional
import pandas as pd
import random

from sky.clouds import cloud as cloud_lib

catalog_config = {
    # Only retry the region/zones that has the resources.
    '_faster_retry_by_catalog': False,
    # Only retry the region/zones that is in the area. This is cloud specific.
    # us, eu, ap or more specific us-east, us-west-1, etc.
    '_retry_area': ['us', 'america'],
    '_shuffle_regions': True,
}

# Load customized catalog_config if it exists.
file_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(file_dir, '_catalog_config.json')
if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        catalog_config = json.load(f)


class InstanceTypeInfo(NamedTuple):
    """Instance type information.

    - cloud: Cloud name.
    - instance_type: String that can be used in YAML to specify this instance
      type. E.g. `p3.2xlarge`.
    - accelerator_name: Canonical name of the accelerator. E.g. `V100`.
    - accelerator_count: Number of accelerators offered by this instance type.
    - memory: Instance memory in GiB.
    - price: Regular instance price per hour.
    """
    cloud: str
    instance_type: str
    accelerator_name: str
    accelerator_count: int
    memory: float


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
    df: pd.DataFrame,
    acc_name: str,
    acc_count: int,
) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    result = df[(df['AcceleratorName'] == acc_name) &
                (df['AcceleratorCount'] == acc_count)]
    if len(result) == 0:
        return None
    instance_types = set(result['InstanceType'])
    if len(instance_types) > 1:
        # Assert that only one instance type exists for a given accelerator
        # and count. Throw so we can manually investigate. The current
        # whitelist consists of:
        if len(instance_types) == 2:
            # - M60, offered by AWS g3s.xl and g3.4xl
            # - "Promo" instance types offered by Azure
            its = sorted(instance_types)
            assert (its == ['g3.4xlarge', 'g3s.xlarge'] or
                    its == ['g5.12xlarge', 'g5.24xlarge'] or
                    its[0] + '_Promo' == its[1]), its
        elif len(instance_types) == 4:
            its = sorted(instance_types)
            assert its in ([
                'Standard_NV12s_v3', 'Standard_NV6', 'Standard_NV6_Promo',
                'Standard_NV6s_v2'
            ], ['g5g.2xlarge', 'g5g.4xlarge', 'g5g.8xlarge', 'g5g.xlarge']), its
        else:
            # - T4, offered by AWS g4dn.{1,2,4,8,16}xl
            # - T4, offered by Azure Standard_NC{4,8,16}as_T4_v3
            for t in instance_types:
                assert (t.startswith('g4dn') or t.startswith('g5') or
                        t.endswith('_T4_v3')), instance_types
        result.sort_values('Price', ascending=True, inplace=True)
    return result.iloc[0]['InstanceType']


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
        'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'MemoryGiB'
    ]].dropna(subset=['AcceleratorName']).drop_duplicates()
    if name_filter is not None:
        df = df[df['AcceleratorName'].str.contains(name_filter, regex=True)]
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    grouped = df.groupby('AcceleratorName')

    def make_list_from_df(rows):
        ret = rows.apply(
            lambda row: InstanceTypeInfo(
                cloud,
                row['InstanceType'],
                row['AcceleratorName'],
                row['AcceleratorCount'],
                row['MemoryGiB'],
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
    if catalog_config['_faster_retry_by_catalog']:
        df = df.dropna(subset=[price_str]).sort_values(price_str)
    if len(catalog_config['_retry_area']) > 0:
        # filter the area of the instances, e.g. us or eu
        areas = catalog_config['_retry_area']
        filter_str = '|'.join(areas)
        df = df[df['Region'].str.contains(filter_str)]

    regions = [cloud_lib.Region(region) for region in df['Region'].unique()]
    if 'AvailabilityZone' in df.columns:
        zones_in_region = df.groupby('Region')['AvailabilityZone'].unique(
        ).apply(lambda x: [cloud_lib.Zone(zone) for zone in x])
        for region in regions:
            zones = zones_in_region[region.name]
            region.set_zones(zones)
    if catalog_config['_shuffle_regions']:
        random.shuffle(regions)
    return regions
