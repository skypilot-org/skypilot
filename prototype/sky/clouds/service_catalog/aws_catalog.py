"""This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
from typing import Optional

from sky.clouds.service_catalog.common import read_catalog

InstanceType = str

_df = read_catalog("aws.csv")


def get_hourly_cost(instance_type: InstanceType,
                    region: Optional[str] = None) -> float:
    mask = _df['InstanceType'] == instance_type
    if region is not None:
        mask &= _df['Region'] == region
    result = _df[mask]
    return result['PricePerHour'].mean()


def get_instance_type_for_gpu(gpu_name: str,
                              count: int) -> Optional[InstanceType]:
    """
    Returns the cheapest instance type that offers the required count of GPUs.
    """
    result = _df[(_df['GpuName'] == gpu_name) &
                 (_df['GpuCount'] >= count)].sort_values('PricePerHour')
    if len(result) == 0:
        return None
    return result.iloc[0]['InstanceType']
