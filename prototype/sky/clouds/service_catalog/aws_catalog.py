"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Optional

import numpy as np

from sky import logging
from sky.clouds.service_catalog import common

logger = logging.init_logger(__name__)
_df = common.read_catalog('aws.csv')


def get_hourly_cost(instance_type: str,
                    region: str = 'us-west-2',
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot.
    """
    result = _df[(_df['InstanceType'] == instance_type) &
                 (_df['Region'] == region)]

    assert len(set(result['Price'])) == 1, (result, instance_type, region)
    if not use_spot:
        return result['Price'].iloc[0]

    cheapest_idx = result['SpotPrice'].idxmin()
    if np.isnan(cheapest_idx):
        result = _df[_df['InstanceType'] == instance_type]
        cheapest_idx = result['SpotPrice'].idxmin()
        assert not np.isnan(
            cheapest_idx
        ), f"Spot instance of {instance_type} is not avaiable in all regions"

    cheapest = result.loc[cheapest_idx]
    return cheapest['SpotPrice']


def get_instance_type_for_gpu(gpu_name: str,
                              count: int,
                              region: str = 'us-west-2') -> Optional[str]:
    """Returns the cheapest instance type that offers the required count of GPUs.
    """
    # TODO: Reorganize _df to support any accelerator (Inferentia, etc.)
    result = _df[(_df['GpuName'] == gpu_name) & (_df['GpuCount'] == count) &
                 (_df['Region'] == region)]
    if len(result) == 0:
        return None
    assert len(set(result['InstanceType'])) == 1, (result, gpu_name, count,
                                                   region)
    return result.iloc[0]['InstanceType']
