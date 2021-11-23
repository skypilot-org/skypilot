"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from sky.clouds.service_catalog import common

_df = common.read_catalog('aws.csv')


def _get_instance_type(instance_type: str, region: str) -> pd.DataFrame:
    return _df[(_df['InstanceType'] == instance_type) &
               (_df['Region'] == region)]


def get_hourly_cost(instance_type: str,
                    region: str = 'us-west-2',
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _get_instance_type(instance_type, region)
    assert len(set(df['Price'])) == 1, (df, instance_type, region)
    if not use_spot:
        return df['Price'].iloc[0]

    cheapest_idx = df['SpotPrice'].idxmin()
    if np.isnan(cheapest_idx):
        return df['Price'].iloc[0]

    cheapest = df.loc[cheapest_idx]
    return cheapest['SpotPrice']


def get_accelerators_from_instance_type(
        instance_type: str,
        region: str = 'us-west-2',
) -> Optional[Dict[str, int]]:
    df = _get_instance_type(instance_type, region)
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        # Happens for e.g., m4.2xlarge.
        assert pd.isnull(acc_count), (acc_name, acc_count)
        return None
    return {acc_name: int(acc_count)}


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        region: str = 'us-west-2',
) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    result = _df[(_df['AcceleratorName'] == acc_name) &
                 (_df['AcceleratorCount'] == acc_count) &
                 (_df['Region'] == region)]
    if len(result) == 0:
        return None
    assert len(set(result['InstanceType'])) == 1, (result, acc_name, acc_count,
                                                   region)
    return result.iloc[0]['InstanceType']


def list_accelerators(gpus_only: bool) -> Dict[str, List[int]]:
    """Returns a mapping from the canonical names of accelerators to a list of
    counts, each representing an instance type offered by this cloud.
    """
    df = _df
    if gpus_only:
        df = df[~pd.isna(df['GpuInfo'])]
    df = df[['AcceleratorName', 'AcceleratorCount']].dropna().drop_duplicates()
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    groupby = df.groupby('AcceleratorName')
    return groupby['AcceleratorCount'].apply(lambda xs: sorted(list(xs))
                                            ).to_dict()
