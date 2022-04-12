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
    if region is None:
        for region in _TPU_REGIONS:
            df = _get_accelerator(_df, accelerator, count, region)
            if len(set(df['Price'])) == 1: break
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
                    memory=_A100_HOST_MEMORY[info.accelerator_count]))
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
