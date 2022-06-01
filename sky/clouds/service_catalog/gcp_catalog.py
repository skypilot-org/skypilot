"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""
import typing
from typing import Dict, List, Optional, Tuple

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

# This can be switched between n1 and n2.
# n2 is not allowed for launching GPUs.
_DEFAULT_HOST_VM_FAMILY = 'n1'

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
    # n2 standard
    'n2-standard-2': 0.097118,
    'n2-standard-4': 0.194236,
    'n2-standard-8': 0.388472,
    'n2-standard-16': 0.776944,
    'n2-standard-32': 1.553888,
    'n2-standard-48': 2.330832,
    'n2-standard-64': 3.107776,
    'n2-standard-80': 3.88472,
    'n2-standard-96': 4.661664,
    'n2-standard-128': 6.215552,
    # n2 highmem
    'n2-highmem-2': 0.147546,
    'n2-highmem-4': 0.295092,
    'n2-highmem-8': 0.590184,
    'n2-highmem-16': 1.180368,
    'n2-highmem-32': 2.360736,
    'n2-highmem-48': 3.541104,
    'n2-highmem-64': 4.721472,
    'n2-highmem-80': 5.90184,
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
    # n2 standard
    'n2-standard-2': 0.02354,
    'n2-standard-4': 0.04708,
    'n2-standard-8': 0.09416,
    'n2-standard-16': 0.18832,
    'n2-standard-32': 0.37664,
    'n2-standard-48': 0.56496,
    'n2-standard-64': 0.75328,
    'n2-standard-80': 0.9416,
    'n2-standard-96': 1.12992,
    'n2-standard-128': 1.50656,
    # n2 highmem
    'n2-highmem-2': 0.03392,
    'n2-highmem-4': 0.06784,
    'n2-highmem-8': 0.13568,
    'n2-highmem-16': 0.27136,
    'n2-highmem-32': 0.54272,
    'n2-highmem-48': 0.81408,
    'n2-highmem-64': 1.08544,
    'n2-highmem-80': 1.3568,
    # A2 highgpu for A100
    'a2-highgpu-1g': 0.224930,
    'a2-highgpu-2g': 0.449847,
    'a2-highgpu-4g': 0.899694,
    'a2-highgpu-8g': 1.799388,
    'a2-megagpu-16g': 2.675750,
}

# Number of CPU cores per GPU based on the AWS setting.
# GCP A100 has its own instance type mapping.
# Refer to sky/clouds/service_catalog/gcp_catalog.py
_NUM_ACC_TO_NUM_CPU = {
    # Based on p2 on AWS.
    'K80': {
        1: 4,
        2: 8,
        4: 16,
        8: 32,
        16: 64,
    },
    # Based on p3 on AWS.
    'V100': {
        1: 8,
        2: 16,
        4: 32,
        8: 64
    },
    # Based on g4dn on AWS.
    'T4': {
        1: 4,
        2: 8,  # AWS does not support 2x T4.
        4: 48,
        # 8x T4 is not supported by GCP.
    },
    # P100 is not supported on AWS, and Azure NCv2 has a weird CPU count.
    'P100': {
        1: 8,
        2: 16,
        4: 32,
        8: 64
    },
    # P4 and other GPUs/TPUs are not supported on aws and azure.
    'DEFAULT': {
        1: 8,
        2: 16,
        4: 32,
        8: 64,
        16: 128
    },
}


def _is_power_of_two(x: int) -> bool:
    """Returns true if x is a power of two."""
    # https://stackoverflow.com/questions/600293/how-to-check-if-a-number-is-a-power-of-2
    return x and not x & (x - 1)


def _closest_power_of_two(x: int) -> int:
    """Returns the closest power of 2 less than or equal to x."""
    if _is_power_of_two(x):
        return x
    return 1 << ((x - 1).bit_length() - 1)


def instance_type_exists(instance_type: str) -> bool:
    """Check the existence of the instance type."""
    return instance_type in _ON_DEMAND_PRICES


def get_hourly_cost(
    instance_type: str,
    region: str,
    use_spot: bool = False,
) -> float:
    """Returns the hourly price for a given instance type and region."""
    del region  # unused
    if use_spot:
        return _SPOT_PRICES[instance_type]
    return _ON_DEMAND_PRICES[instance_type]


def get_instance_type_for_accelerator(
        acc_name: str, acc_count: int) -> Tuple[Optional[List[str]], List[str]]:
    """Fetch instance types with similar CPU count for given accelerator.

    Return: a list with a single matched instance types and a list of candidates
    with fuzzy search (should be empty as it must have already been generated in
    caller).
    """
    if acc_name == 'A100':
        # If A100 is used, host VM type must be A2.
        # https://cloud.google.com/compute/docs/gpus#a100-gpus
        return [_A100_INSTANCE_TYPES[acc_count]]
    if acc_name not in _NUM_ACC_TO_NUM_CPU:
        acc_name = 'DEFAULT'

    num_cpus = _NUM_ACC_TO_NUM_CPU[acc_name].get(acc_count, None)
    # The (acc_name, acc_count) should be validated in the caller.
    assert num_cpus is not None, (acc_name, acc_count)
    mem_type = 'highmem'
    # patches for the number of cores per GPU, as some of the combinations
    # are not supported by GCP.
    if _DEFAULT_HOST_VM_FAMILY == 'n1':
        if num_cpus < 96:
            num_cpus = _closest_power_of_two(num_cpus)
        else:
            num_cpus = 96
    else:
        if num_cpus > 80:
            mem_type = 'standard'
    # The fuzzy candidate should have already been fetched in the caller.
    return ([f'{_DEFAULT_HOST_VM_FAMILY}-{mem_type}-{num_cpus}'], [])


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
            a100_host_vm_type = _A100_INSTANCE_TYPES[info.accelerator_count]
            new_infos.append(
                info._replace(
                    instance_type=a100_host_vm_type,
                    memory=_A100_HOST_MEMORY[info.accelerator_count],
                    # total cost = VM instance + GPU.
                    price=info.price + _ON_DEMAND_PRICES[a100_host_vm_type],
                    spot_price=info.spot_price +
                    _SPOT_PRICES[a100_host_vm_type],
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
