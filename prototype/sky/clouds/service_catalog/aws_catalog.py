"""This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Optional

from numpy import isnan

import sky.clouds.service_catalog.common as common

InstanceType = str
Region = str

_df = common.read_catalog('aws.csv')


def get_hourly_cost(instance_type: InstanceType,
                    region: Optional[Region] = 'us-west-2',
                    spot: bool = False) -> float:
    mask = _df['InstanceType'] == instance_type
    if region is not None:
        mask &= _df['Region'] == region

    result = _df[mask]
    assert len(set(result['PricePerHour'])) == 1, (result, instance_type,
                                                   region)
    if not spot:
        return result['PricePerHour'].iloc[0]

    cheapest_idx = result['SpotPricePerHour'].idxmin()
    assert not isnan(cheapest_idx), (result, instance_type, region)
    cheapest = result.iloc[cheapest_idx]
    return cheapest['SpotPricePerHour'], cheapest['AvailabilityZone']


def get_instance_type_for_gpu(gpu_name: str,
                              count: int) -> Optional[InstanceType]:
    """Returns the cheapest instance type that offers the required count of GPUs.
    """
    # TODO: Reorganize _df to support any accelerator (Inferentia, etc.)
    result = _df[(_df['GpuName'] == gpu_name) &
                 (_df['GpuCount'] == count)].sort_values('PricePerHour')
    if len(result) == 0:
        return None
    return result.iloc[0]['InstanceType']
