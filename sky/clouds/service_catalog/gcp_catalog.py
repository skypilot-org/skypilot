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
    if instance_type == 'TPU-VM':
        return True
    return common.instance_type_exists_impl(_df, instance_type)


def get_hourly_cost(
    instance_type: str,
    region: Optional[str] = None,
    use_spot: bool = False,
) -> float:
    """Returns the hourly price for a given instance type and region."""
    if instance_type == 'TPU-VM':
        # Currently the host VM of TPU does not cost extra.
        return 0
    return common.get_hourly_cost_impl(_df, instance_type, region, use_spot)


def get_instance_type_for_accelerator(
        acc_name: str, acc_count: int) -> Tuple[Optional[List[str]], List[str]]:
    """Fetch instance types with similar CPU count for given accelerator.

    Return: a list with a single matched instance type and a list of candidates
    with fuzzy search (should be empty as it must have already been generated in
    caller).
    """
    (instance_list,
     fuzzy_candidate_list) = common.get_instance_type_for_accelerator_impl(
         df=_df, acc_name=acc_name, acc_count=acc_count)
    if instance_list is None:
        return None, fuzzy_candidate_list

    if acc_name == 'A100':
        # If A100 is used, host VM type must be A2.
        # https://cloud.google.com/compute/docs/gpus#a100-gpus
        return [_A100_INSTANCE_TYPES[acc_count]], []
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
    return [f'{_DEFAULT_HOST_VM_FAMILY}-{mem_type}-{num_cpus}'], []


def region_exists(region: str) -> bool:
    return common.region_exists_impl(_df, region)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


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

    a100_infos = results.get('A100', None)
    if a100_infos is None:
        return results

    # Unlike other GPUs that can be attached to different sizes of N1 VMs,
    # A100 GPUs can only be attached to fixed-size A2 VMs.
    # Thus, we can show their exact cost including the host VM prices.
    new_infos = []
    for info in a100_infos:
        assert pd.isna(info.instance_type) and info.memory == 0, a100_infos
        a100_host_vm_type = _A100_INSTANCE_TYPES[info.accelerator_count]
        df = _df[_df['InstanceType'] == a100_host_vm_type]
        memory = df['MemoryGiB'].iloc[0]
        vm_price = common.get_hourly_cost_impl(_df,
                                               a100_host_vm_type,
                                               None,
                                               use_spot=False)
        vm_spot_price = common.get_hourly_cost_impl(_df,
                                                    a100_host_vm_type,
                                                    None,
                                                    use_spot=True)
        new_infos.append(
            info._replace(
                instance_type=a100_host_vm_type,
                memory=memory,
                # total cost = VM instance + GPU.
                price=info.price + vm_price,
                spot_price=info.spot_price + vm_spot_price,
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
