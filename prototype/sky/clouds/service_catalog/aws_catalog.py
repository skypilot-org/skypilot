"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Dict, List, Optional

import pandas as pd

import numpy as np

from sky.clouds.service_catalog import common

_df = common.read_catalog('aws.csv')


def _get_instance_type(instance_type: str, region: str) -> pd.Series:
    result = _df[(_df['InstanceType'] == instance_type) &
                 (_df['Region'] == region)]
    assert len(result) == 1, (result, instance_type, region)
    return result


def get_hourly_cost(instance_type: str, region: str = 'us-west-2') -> float:
    entry = _get_instance_type(instance_type, region)
    return entry['PricePerHour'].iloc[0]


def get_accelerators_from_instance_type(instance_type: str,
                                        region: str = 'us-west-2'
                                       ) -> Dict[str, int]:
    entry = _get_instance_type(instance_type, region)
    acc_name, acc_count = entry['AcceleratorName'].item(), int(
        entry['AcceleratorCount'].item())
    if len(acc_name) == 0 and acc_count == 0:
        return {}
    return {acc_name: acc_count}

    assert len(set(result['Price'])) == 1, (result, instance_type, region)
    cheapest_idx = result['SpotPrice'].idxmin()

    if not use_spot or np.isnan(cheapest_idx):
        return result['Price'].iloc[0]

    cheapest = result.loc[cheapest_idx]
    return cheapest['SpotPrice']


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        region: str = 'us-west-2') -> Optional[str]:
    """Returns the cheapest instance type that offers the required count of
    accelerators."""
    result = _df[(_df['AcceleratorName'] == acc_name) &
                 (_df['AcceleratorCount'] == acc_count) &
                 (_df['Region'] == region)]
    if len(result) == 0:
        return None
    assert len(set(result['InstanceType'])) == 1, (result, acc_name, acc_count,
                                                   region)
    return result.iloc[0]['InstanceType']


def list_accelerators() -> List[str]:
    """List the canonical names of all accelerators offered by this cloud."""
    return _df['AcceleratorName'].dropna().unique().tolist()
