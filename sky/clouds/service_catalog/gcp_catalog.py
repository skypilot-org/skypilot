"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""
import typing
from typing import Dict, List, Optional

import pandas as pd

from sky.clouds.service_catalog import common

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_df = common.read_catalog('gcp.csv')

_TPU_REGIONS = [
    'us-central1',
    'europe-west4',
    'asia-east1',
]

# TODO(zongheng): fix A100 info directly in catalog.
# https://cloud.google.com/blog/products/compute/a2-vms-with-nvidia-a100-gpus-are-ga
# count -> vm type
_A100_INSTANCE_TYPES = {
    1: 'a2-highgpu-1g',
    2: 'a2-highgpu-2g',
    4: 'a2-highgpu-4g',
    8: 'a2-highgpu-8g',
    16: 'a2-megagpu-16g',
}
# count -> host memory
_A100_HOST_MEMORY = {
    1: 85,
    2: 170,
    4: 340,
    8: 680,
    16: 1360,
}

# Pricing.  All info assumes us-central1.
# In general, query pricing from the cloud.
_ON_DEMAND_PRICES = {
    # VMs: https://cloud.google.com/compute/all-pricing.
    # N1 standard
    'n1-standard-1': 0.04749975,
    'n1-standard-2': 0.0949995,
    'n1-standard-4': 0.189999,
    'n1-standard-8': 0.379998,
    'n1-standard-16': 0.759996,
    'n1-standard-32': 1.519992,
    'n1-standard-64': 3.039984,
    'n1-standard-96': 4.559976,
    # N1 highmem
    'n1-highmem-2': 0.118303,
    'n1-highmem-4': 0.236606,
    'n1-highmem-8': 0.473212,
    'n1-highmem-16': 0.946424,
    'n1-highmem-32': 1.892848,
    'n1-highmem-64': 3.785696,
    'n1-highmem-96': 5.678544,
    # A2 highgpu for A100
    'a2-highgpu-1g': 0.749750,
    'a2-highgpu-2g': 1.499500,
    'a2-highgpu-4g': 2.998986,
    'a2-highgpu-8g': 5.997986,
    'a2-megagpu-16g': 8.919152,
}

_SPOT_PRICES = {
    # VMs: https://cloud.google.com/compute/all-pricing.
    # N1 standard
    'n1-standard-1': 0.01,
    'n1-standard-2': 0.02,
    'n1-standard-4': 0.04,
    'n1-standard-8': 0.08,
    'n1-standard-16': 0.16,
    'n1-standard-32': 0.32,
    'n1-standard-64': 0.64,
    'n1-standard-96': 0.96,
    # N1 highmem
    'n1-highmem-2': 0.024906,
    'n1-highmem-4': 0.049812,
    'n1-highmem-8': 0.099624,
    'n1-highmem-16': 0.199248,
    'n1-highmem-32': 0.398496,
    'n1-highmem-64': 0.796992,
    'n1-highmem-96': 1.195488,
    # A2 highgpu for A100
    'a2-highgpu-1g': 0.224930,
    'a2-highgpu-2g': 0.449847,
    'a2-highgpu-4g': 0.899694,
    'a2-highgpu-8g': 1.799388,
    'a2-megagpu-16g': 2.675750,
}


def instance_type_exists(instance_type: str) -> bool:
    """Check the existence of the instance type."""
    del instance_type
    # Handled in gcp.py. We don't have a proper catalog right now.
    assert False, 'Internal logic error: this function should not be called'


def region_exists(region: str) -> bool:
    return common.region_exists_impl(_df, region)


def _get_accelerator(
    df: pd.DataFrame,
    accelerator: str,
    count: int,
    region: Optional[str],
) -> pd.DataFrame:
    idx = (df['AcceleratorName'].str.fullmatch(
        accelerator, case=False)) & (df['AcceleratorCount'] == count)
    if region is not None:
        idx &= df['Region'] == region
    return df[idx]


def get_accelerator_hourly_cost(accelerator: str,
                                count: int,
                                region: Optional[str] = None,
                                use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    # NOTE: As of 2022/4/13, Prices of TPU v3-64 to v3-2048 are not available on
    # https://cloud.google.com/tpu/pricing. We put estimates in gcp catalog.
    if region is None:
        for tpu_region in _TPU_REGIONS:
            df = _get_accelerator(_df, accelerator, count, tpu_region)
            if len(set(df['Price'])) == 1:
                region = tpu_region
                break
    df = _get_accelerator(_df, accelerator, count, region)
    assert len(set(df['Price'])) == 1, df
    if not use_spot:
        return df['Price'].iloc[0]

    cheapest_idx = df['SpotPrice'].idxmin()
    if pd.isnull(cheapest_idx):
        return df['Price'].iloc[0]

    cheapest = df.loc[cheapest_idx]
    return cheapest['SpotPrice']


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str] = None,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in GCP offering GPUs."""
    results = common.list_accelerators_impl('GCP', _df, gpus_only, name_filter)

    # TODO(zongheng): fix A100 info directly in catalog.
    a100_infos = results.get('A100', None)
    if a100_infos is not None:
        new_infos = []
        for info in a100_infos:
            assert pd.isna(info.instance_type) and info.memory == 0, a100_infos
            new_infos.append(
                info._replace(
                    instance_type=_A100_INSTANCE_TYPES[info.accelerator_count],
                    memory=_A100_HOST_MEMORY[info.accelerator_count],
                    # total cost = VM instance + GPU.
                    price=info.price + _ON_DEMAND_PRICES[_A100_INSTANCE_TYPES[
                        info.accelerator_count]],
                    spot_price=info.spot_price +
                    _SPOT_PRICES[_A100_INSTANCE_TYPES[info.accelerator_count]],
                ))
        results['A100'] = new_infos
    return results


def get_region_zones_for_accelerators(
    accelerator: str,
    count: int,
    use_spot: bool = False,
) -> List['cloud.Region']:
    """Returns a list of regions for a given accelerators."""
    df = _get_accelerator(_df, accelerator, count, region=None)
    return common.get_region_zones(df, use_spot)
