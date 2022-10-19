"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""
from collections import defaultdict
import typing
from typing import Dict, List, Optional, Tuple

import pandas as pd

from sky import exceptions
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

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
_A100_INSTANCE_TYPE_DICTS = {
    'A100': {
        1: 'a2-highgpu-1g',
        2: 'a2-highgpu-2g',
        4: 'a2-highgpu-4g',
        8: 'a2-highgpu-8g',
        16: 'a2-megagpu-16g',
    },
    'A100-80GB': {
        1: 'a2-ultragpu-1g',
        2: 'a2-ultragpu-2g',
        4: 'a2-ultragpu-4g',
        8: 'a2-ultragpu-8g',
    }
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

# num_gpus -> (max_num_cpus, max_memory_gb)
# Refer to: https://cloud.google.com/compute/docs/gpus
_NUM_ACC_TO_MAX_CPU_AND_MEMORY = {
    'K80': {
        1: (8, 52),
        2: (16, 104),
        4: (32, 208),
        8: (64, 208),  # except for asia-east1-a, us-east1-d
    },
    'V100': {
        1: (12, 78),
        2: (24, 156),
        4: (48, 312),
        8: (96, 624),
    },
    'T4': {
        1: (48, 312),
        2: (48, 312),
        4: (96, 624),
    },
    'P4': {
        1: (24, 156),
        2: (48, 312),
        4: (96, 624),
    },
    'P100': {
        1: (16, 104),
        2: (32, 208),
        4: (96, 624),  # except for us-east1-c, europe-west1-d, europe-west1-b
    }
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


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    # The number of vCPUs provided with a TPU VM is not officially documented.
    if instance_type == 'TPU-VM':
        return None
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


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

    if acc_name in _A100_INSTANCE_TYPE_DICTS:
        # If A100 is used, host VM type must be A2.
        # https://cloud.google.com/compute/docs/gpus#a100-gpus
        return [_A100_INSTANCE_TYPE_DICTS[acc_name][acc_count]], []
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


def validate_region_zone(region: Optional[str], zone: Optional[str]):
    return common.validate_region_zone_impl(_df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


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
    case_sensitive: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in GCP offering GPUs."""
    results = common.list_accelerators_impl('GCP', _df, gpus_only, name_filter,
                                            case_sensitive)

    a100_infos = results.get('A100', []) + results.get('A100-80GB', [])
    if not a100_infos:
        return results

    # Unlike other GPUs that can be attached to different sizes of N1 VMs,
    # A100 GPUs can only be attached to fixed-size A2 VMs.
    # Thus, we can show their exact cost including the host VM prices.
    new_infos = defaultdict(list)
    for info in a100_infos:
        assert pd.isna(info.instance_type) and pd.isna(info.memory), a100_infos
        a100_host_vm_type = _A100_INSTANCE_TYPE_DICTS[info.accelerator_name][
            info.accelerator_count]
        df = _df[_df['InstanceType'] == a100_host_vm_type]
        cpu_count = df['vCPUs'].iloc[0]
        memory = df['MemoryGiB'].iloc[0]
        vm_price = common.get_hourly_cost_impl(_df,
                                               a100_host_vm_type,
                                               None,
                                               use_spot=False)
        vm_spot_price = common.get_hourly_cost_impl(_df,
                                                    a100_host_vm_type,
                                                    None,
                                                    use_spot=True)
        new_infos[info.accelerator_name].append(
            info._replace(
                instance_type=a100_host_vm_type,
                cpu_count=cpu_count,
                memory=memory,
                # total cost = VM instance + GPU.
                price=info.price + vm_price,
                spot_price=info.spot_price + vm_spot_price,
            ))
    results.update(new_infos)
    return results


def get_region_zones_for_accelerators(
    accelerator: str,
    count: int,
    use_spot: bool = False,
) -> List['cloud.Region']:
    """Returns a list of regions for a given accelerators."""
    df = _get_accelerator(_df, accelerator, count, region=None)
    return common.get_region_zones(df, use_spot)


def check_host_accelerator_compatibility(
        instance_type: str, accelerators: Optional[Dict[str, int]]) -> None:
    """Check if the instance type is compatible with the accelerators.

    This function ensures that TPUs and GPUs except A100 are attached to N1,
    and A100 GPUs are attached to A2 machines.
    """
    if accelerators is None:
        if instance_type.startswith('a2-'):
            # NOTE: While it is allowed to use A2 machines as CPU-only nodes,
            # we exclude this case as it is uncommon and undesirable.
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    'A2 instance types should be used with A100 GPUs. '
                    'Either use other instance types or specify the '
                    'accelerators as A100.')
        return

    acc = list(accelerators.items())
    assert len(acc) == 1, acc
    acc_name, _ = acc[0]

    # Check if the accelerator is supported by GCP.
    if not list_accelerators(gpus_only=False, name_filter=acc_name):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesUnavailableError(
                f'{acc_name} is not available in GCP. '
                'See \'sky show-gpus --cloud gcp\'')

    if acc_name.startswith('tpu-'):
        if instance_type != 'TPU-VM' and not instance_type.startswith('n1-'):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    'TPU Nodes can be only used with N1 machines. '
                    'Please refer to: '
                    'https://cloud.google.com/compute/docs/general-purpose-machines#n1_machines')  # pylint: disable=line-too-long
        return

    # Treat A100 as a special case.
    if acc_name in _A100_INSTANCE_TYPE_DICTS:
        # A100 must be attached to A2 instance type.
        if not instance_type.startswith('a2-'):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    f'A100 GPUs cannot be attached to {instance_type}. '
                    f'Use A2 machines instead. Please refer to '
                    'https://cloud.google.com/compute/docs/gpus#a100-gpus')
        return

    # Other GPUs must be attached to N1 machines.
    # Refer to: https://cloud.google.com/compute/docs/machine-types#gpus
    if not instance_type.startswith('n1-'):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name} GPUs cannot be attached to {instance_type}. '
                'Use N1 instance types instead. Please refer to: '
                'https://cloud.google.com/compute/docs/machine-types#gpus')


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None) -> None:
    """Check if the accelerators can be attached to the host.

    This function checks the max CPU count and memory of the host that
    the accelerators can be attached to.
    """
    if accelerators is None:
        return

    acc = list(accelerators.items())
    assert len(acc) == 1, acc
    acc_name, acc_count = acc[0]

    if acc_name.startswith('tpu-'):
        # TODO(woosuk): Check max vcpus and memory for each TPU type.
        assert instance_type == 'TPU-VM' or instance_type.startswith('n1-')
        return

    if acc_name in _A100_INSTANCE_TYPE_DICTS:
        valid_counts = list(_A100_INSTANCE_TYPE_DICTS[acc_name].keys())
    else:
        valid_counts = list(_NUM_ACC_TO_MAX_CPU_AND_MEMORY[acc_name].keys())
    if acc_count not in valid_counts:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name}:{acc_count} is not launchable on GCP. '
                f'The valid {acc_name} counts are {valid_counts}.')

    if acc_name in _A100_INSTANCE_TYPE_DICTS:
        a100_instance_type = _A100_INSTANCE_TYPE_DICTS[acc_name][acc_count]
        if instance_type != a100_instance_type:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    f'A100:{acc_count} cannot be attached to {instance_type}. '
                    f'Use {a100_instance_type} instead. Please refer to '
                    'https://cloud.google.com/compute/docs/gpus#a100-gpus')
        return

    # Check maximum vCPUs and memory.
    max_cpus, max_memory = _NUM_ACC_TO_MAX_CPU_AND_MEMORY[acc_name][acc_count]
    if acc_name == 'K80' and acc_count == 8:
        if zone in ['asia-east1-a', 'us-east1-d']:
            max_memory = 416
    elif acc_name == 'P100' and acc_count == 4:
        if zone in ['us-east1-c', 'europe-west1-d', 'europe-west1-b']:
            max_cpus = 64
            max_memory = 208

    # vCPU counts and memory sizes of N1 machines.
    df = _df[_df['InstanceType'] == instance_type]
    num_cpus = df['vCPUs'].iloc[0]
    memory = df['MemoryGiB'].iloc[0]

    if num_cpus > max_cpus:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name}:{acc_count} cannot be attached to '
                f'{instance_type}. The maximum number of vCPUs is {max_cpus}. '
                'Please refer to: https://cloud.google.com/compute/docs/gpus')
    if memory > max_memory:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name}:{acc_count} cannot be attached to '
                f'{instance_type}. The maximum CPU memory is {max_memory} GB. '
                'Please refer to: https://cloud.google.com/compute/docs/gpus')
