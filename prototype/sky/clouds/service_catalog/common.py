import os
from typing import Dict, List, Optional

import pandas as pd


def get_data_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                        filename)


def read_catalog(filename: str) -> pd.DataFrame:
    return pd.read_csv(get_data_path(filename))


def _get_instance_type(
        df: pd.DataFrame,
        instance_type: str,
        region: str,
) -> pd.DataFrame:
    return df[(df['InstanceType'] == instance_type) & (df['Region'] == region)]


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
        region: str,
) -> Optional[Dict[str, int]]:
    df = _get_instance_type(df, instance_type, region)
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        return None
    return {acc_name: int(acc_count)}


def get_instance_type_for_accelerator_impl(
        df: pd.DataFrame,
        acc_name: str,
        acc_count: int,
        region: str,
) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    result = df[(df['AcceleratorName'] == acc_name) &
                (df['AcceleratorCount'] == acc_count) &
                (df['Region'] == region)]
    if len(result) == 0:
        return None
    instance_types = set(result['InstanceType'])
    if len(instance_types) > 1:
        for t in instance_types:
            # Assert that only one instance type exists for a given accelerator
            # and count. For now, the only exception is that T4 is offered by
            # multiple instance types in both AWS and Azure. For all other cases,
            # throw so we can manually investigate.
            assert t.startswith('g4dn') or t.endswith('_T4_v3'), result
    result.sort_values('Price', ascending=True, inplace=True)
    return result.iloc[0]['InstanceType']


def list_accelerators_impl(df: pd.DataFrame,
                           gpus_only: bool) -> Dict[str, List[int]]:
    """Returns a mapping from the canonical names of accelerators to a list of
    counts, each representing an instance type offered by this cloud.
    """
    if gpus_only:
        df = df[~pd.isna(df['GpuInfo'])]
    df = df[['AcceleratorName', 'AcceleratorCount']].dropna().drop_duplicates()
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    groupby = df.groupby('AcceleratorName')
    return groupby['AcceleratorCount'].apply(lambda xs: sorted(list(xs))
                                            ).to_dict()
