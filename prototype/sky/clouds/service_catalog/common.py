"""Common utilities for service catalog."""
import os
from typing import Dict, List, Optional

import pandas as pd

from sky.clouds import cloud


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
            assert its == ['g3.4xlarge', 'g3s.xlarge'
                          ] or its[0] + '_Promo' == its[1], its
        elif len(instance_types) == 4:
            its = sorted(instance_types)
            assert its == [
                'Standard_NV12s_v3', 'Standard_NV6', 'Standard_NV6_Promo',
                'Standard_NV6s_v2'
            ], its
        else:
            # - T4, offered by AWS g4dn.{1,2,4,8,16}xl
            # - T4, offered by Azure Standard_NC{4,8,16}as_T4_v3
            for t in instance_types:
                assert t.startswith('g4dn') or t.endswith(
                    '_T4_v3'), instance_types
        result.sort_values('Price', ascending=True, inplace=True)
    return result.iloc[0]['InstanceType']


def list_accelerators_impl(df: pd.DataFrame,
                           gpus_only: bool) -> Dict[str, List[int]]:
    """Lists accelerators offered in a cloud service catalog.

    Returns a mapping from the canonical names of accelerators to a list of
    counts, each representing an instance type offered by this cloud.
    """
    if gpus_only:
        df = df[~pd.isna(df['GpuInfo'])]
    df = df[['AcceleratorName', 'AcceleratorCount']].dropna().drop_duplicates()
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    groupby = df.groupby('AcceleratorName')
    return groupby['AcceleratorCount'].apply(lambda xs: sorted(list(xs))
                                            ).to_dict()


def get_region_zones_for_instance_type(df: pd.DataFrame, instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    """Returns a list of regions for a given instance type."""
    price_str = 'SpotPrice' if use_spot else 'Price'
    df = df[df['InstanceType'] == instance_type].sort_values(price_str)
    regions = [cloud.Region(region) for region in df['Region'].unique()]
    if 'AvailabilityZone' in df.columns:
        zones_in_region = df.groupby('Region')['AvailabilityZone'].apply(
            lambda x: [cloud.Zone(zone) for zone in x])
        for region in regions:
            region.set_zones(zones_in_region[region.name])
    return regions
