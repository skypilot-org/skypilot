"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Optional
from numpy import isnan

from sky.clouds.service_catalog import common

_df = common.read_catalog('aws.csv')


def get_hourly_cost(instance_type: str,
                    region: str = 'us-west-2',
                    spot: bool = False) -> float:
    result = _df[(_df['InstanceType'] == instance_type) &
                 (_df['Region'] == region)]

    assert len(set(result['PricePerHour'])) == 1, (result, instance_type,
                                                   region)
    if not spot:
        return result['PricePerHour'].iloc[0]

    cheapest_idx = result['SpotPricePerHour'].idxmin()
    assert not isnan(cheapest_idx), (result, instance_type, region)
    cheapest = result.iloc[cheapest_idx]
    return cheapest['SpotPricePerHour'], cheapest['AvailabilityZone']


def get_instance_type_for_gpu(gpu_name: str,
                              count: int,
                              region: str = 'us-west-2') -> Optional[str]:
    """Returns the cheapest instance type that offers the required count of GPUs.
    """
    # TODO: Reorganize _df to support any accelerator (Inferentia, etc.)
    result = _df[(_df["GpuName"] == gpu_name) & (_df["GpuCount"] == count) &
                 (_df["Region"] == region)]
    if len(result) == 0:
        return None
    assert len(result) == 1, (result, gpu_name, count, region)
    return result.iloc[0]['InstanceType']
