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

# Pull the latest catalog every week.
# GCP guarantees that the catalog is updated at most once per 30 days, but
# different VMs can be updated at different time. Thus, we pull the catalog
# every 7 hours to make sure we have the latest information.
_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('gcp/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)
_image_df = common.read_catalog('gcp/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_quotas_df = common.read_catalog('gcp/accelerator_quota_mapping.csv')

_TPU_REGIONS = [
    'us-central1',
    'europe-west4',
    'asia-east1',
]

# We will select from the following three CPU instance families:
_DEFAULT_INSTANCE_FAMILY = [
    # This is the latest general-purpose instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8373C or Cascade Lake 6268CL.
    # Memory: 4 GiB RAM per 1 vCPU;
    'n2-standard',
    # This is the latest memory-optimized instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8373C or Cascade Lake 6268CL.
    # Memory: 8 GiB RAM per 1 vCPU;
    'n2-highmem',
    # This is the latest compute-optimized instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8373C or Cascade Lake 6268CL.
    # Memory: 1 GiB RAM per 1 vCPU;
    'n2-highcpu',
]
# n2 is not allowed for launching GPUs for now.
_DEFAULT_HOST_VM_FAMILY = (
    'n1-standard',
    'n1-highmem',
    'n1-highcpu',
)
_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 4

_DEFAULT_GPU_MEMORY_CPU_RATIO = 4

# TODO(zongheng): fix A100 info directly in catalog.
# https://cloud.google.com/blog/products/compute/a2-vms-with-nvidia-a100-gpus-are-ga

# If A100 is used, host VM type must be A2; if L4 is used, VM type must be G2.
# Conversely, A2 can only be used with A100, and G2 only with L4.
# https://cloud.google.com/compute/docs/gpus
# acc_type -> count -> vm types
_ACC_INSTANCE_TYPE_DICTS = {
    'A100': {
        1: ['a2-highgpu-1g'],
        2: ['a2-highgpu-2g'],
        4: ['a2-highgpu-4g'],
        8: ['a2-highgpu-8g'],
        16: ['a2-megagpu-16g'],
    },
    'A100-80GB': {
        1: ['a2-ultragpu-1g'],
        2: ['a2-ultragpu-2g'],
        4: ['a2-ultragpu-4g'],
        8: ['a2-ultragpu-8g'],
    },
    'L4': {
        1: [
            'g2-standard-4',
            'g2-standard-8',
            'g2-standard-12',
            'g2-standard-16',
            'g2-standard-32',
        ],
        2: ['g2-standard-24'],
        4: ['g2-standard-48'],
        8: ['g2-standard-96'],
    },
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
    return bool(x and not x & (x - 1))


def _closest_power_of_two(x: int) -> int:
    """Returns the closest power of 2 less than or equal to x."""
    if _is_power_of_two(x):
        return x
    return 1 << ((x - 1).bit_length() - 1)


def get_quota_code(accelerator: str, use_spot: bool) -> Optional[str]:
    """Get the quota code based on `accelerator` and `use_spot`.

    The quota code is fetched from `_quotas_df` based on the accelerator
    specified, and will then be utilized in a GCP CLI command in order
    to check for a non-zero quota.
    """

    if use_spot:
        spot_header = 'SpotInstanceCode'
    else:
        spot_header = 'OnDemandInstanceCode'
    try:
        quota_code = _quotas_df.loc[_quotas_df['Accelerator'] == accelerator,
                                    spot_header].values[0]
        return quota_code

    except IndexError:
        return None


def instance_type_exists(instance_type: str) -> bool:
    """Check the existence of the instance type."""
    if instance_type == 'TPU-VM':
        return True
    return common.instance_type_exists_impl(_df, instance_type)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    if instance_type == 'TPU-VM':
        # Currently the host VM of TPU does not cost extra.
        return 0
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    # The number of vCPUs and memory size provided with a TPU VM is not
    # officially documented.
    if instance_type == 'TPU-VM':
        return None, None
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None) -> Optional[str]:
    del disk_tier  # unused
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory
    instance_type_prefix = tuple(
        f'{family}-' for family in _DEFAULT_INSTANCE_FAMILY)
    df = _df[_df['InstanceType'].notna()]
    df = df[df['InstanceType'].str.startswith(instance_type_prefix)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """Fetch instance types with similar CPU count for given accelerator.

    Return: a list with a single matched instance type and a list of candidates
    with fuzzy search (should be empty as it must have already been generated in
    caller).
    """
    (instance_list,
     fuzzy_candidate_list) = common.get_instance_type_for_accelerator_impl(
         _df, acc_name, acc_count, cpus, memory, use_spot, region, zone)
    if instance_list is None:
        return None, fuzzy_candidate_list

    if acc_name in _ACC_INSTANCE_TYPE_DICTS:
        df = _df[_df['InstanceType'].notna()]
        instance_types = _ACC_INSTANCE_TYPE_DICTS[acc_name][acc_count]
        df = df[df['InstanceType'].isin(instance_types)]

        # Check the cpus and memory specified by the user.
        instance_type = common.get_instance_type_for_cpus_mem_impl(
            df, cpus, memory)
        if instance_type is None:
            return None, []
        return [instance_type], []

    if acc_name not in _NUM_ACC_TO_NUM_CPU:
        acc_name = 'DEFAULT'

    default_host_cpus = _NUM_ACC_TO_NUM_CPU[acc_name].get(acc_count, None)
    if cpus is None and memory is None:
        assert default_host_cpus is not None, (acc_name, acc_count)
        cpus = f'{default_host_cpus}+'
    if memory is None:
        assert cpus is not None, (acc_name, acc_count)
        cpu_val = int(cpus.strip('+').strip('x'))
        # The memory size should be at least 4x the requested number of vCPUs
        # to be consistent with all other clouds.
        memory = f'{cpu_val * _DEFAULT_GPU_MEMORY_CPU_RATIO}+'
    df = _df[_df['InstanceType'].notna()]
    df = df[df['InstanceType'].str.startswith(_DEFAULT_HOST_VM_FAMILY)]

    instance_type = common.get_instance_type_for_cpus_mem_impl(df, cpus, memory)
    # The fuzzy candidate should have already been fetched in the caller.
    if instance_type is None:
        return None, []
    return [instance_type], []


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl('gcp', _df, region, zone)


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
    zone: Optional[str] = None,
) -> pd.DataFrame:
    idx = (df['AcceleratorName'].str.fullmatch(
        accelerator, case=False)) & (df['AcceleratorCount'] == count)
    if region is not None:
        idx &= df['Region'] == region
    if zone is not None:
        idx &= df['AvailabilityZone'] == zone
    return df[idx]


def get_accelerator_hourly_cost(accelerator: str,
                                count: int,
                                use_spot: bool = False,
                                region: Optional[str] = None,
                                zone: Optional[str] = None) -> float:
    # NOTE: As of 2022/4/13, Prices of TPU v3-64 to v3-2048 are not available on
    # https://cloud.google.com/tpu/pricing. We put estimates in gcp catalog.
    if region is None:
        for tpu_region in _TPU_REGIONS:
            df = _get_accelerator(_df, accelerator, count, tpu_region)
            if len(set(df['Price'])) == 1:
                region = tpu_region
                break

    df = _get_accelerator(_df, accelerator, count, region, zone)
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
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    case_sensitive: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in GCP offering GPUs."""
    results = common.list_accelerators_impl('GCP', _df, gpus_only, name_filter,
                                            region_filter, quantity_filter,
                                            case_sensitive)

    # Remove GPUs that are unsupported by SkyPilot.
    new_results = {}
    for acc_name, acc_info in results.items():
        if (acc_name.startswith('tpu') or
                acc_name in _NUM_ACC_TO_MAX_CPU_AND_MEMORY or
                acc_name in _ACC_INSTANCE_TYPE_DICTS):
            new_results[acc_name] = acc_info
    results = new_results

    # Unlike other GPUs that can be attached to different sizes of N1 VMs,
    # A100 GPUs can only be attached to fixed-size A2 VMs,
    # and L4 GPUs can only be attached to G2 VMs.
    # Thus, we can show their exact cost including the host VM prices.

    acc_infos: List[common.InstanceTypeInfo] = sum(
        [results.get(a, []) for a in _ACC_INSTANCE_TYPE_DICTS], [])
    if not acc_infos:
        return results

    new_infos = defaultdict(list)
    for info in acc_infos:
        assert pd.isna(info.instance_type) and pd.isna(info.memory), acc_infos
        vm_types = _ACC_INSTANCE_TYPE_DICTS[info.accelerator_name][
            info.accelerator_count]
        for vm_type in vm_types:
            df = _df[_df['InstanceType'] == vm_type]
            cpu_count = df['vCPUs'].iloc[0]
            memory = df['MemoryGiB'].iloc[0]
            vm_price = common.get_hourly_cost_impl(_df,
                                                   vm_type,
                                                   use_spot=False,
                                                   region=None,
                                                   zone=None)
            vm_spot_price = common.get_hourly_cost_impl(_df,
                                                        vm_type,
                                                        use_spot=True,
                                                        region=None,
                                                        zone=None)
            new_infos[info.accelerator_name].append(
                info._replace(
                    instance_type=vm_type,
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


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None) -> None:
    """Check if the accelerators can be attached to the host.

    This function checks the max CPU count and memory of the host that
    the accelerators can be attached to.

    Raises:
        exceptions.ResourcesMismatchError: If the accelerators cannot be
            attached to the host.
    """
    if accelerators is None:
        for acc_name, val in _ACC_INSTANCE_TYPE_DICTS.items():
            if instance_type in sum(val.values(), []):
                # NOTE: While it is allowed to use A2/G2 VMs as CPU-only nodes,
                # we exclude this case as it is uncommon and undesirable.
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesMismatchError(
                        f'{instance_type} instance types should be used with '
                        f'{acc_name} GPUs. Either use other instance types or '
                        f'specify the accelerators as {acc_name}.')
        return

    acc = list(accelerators.items())
    assert len(acc) == 1, acc
    acc_name, acc_count = acc[0]

    # Check if the accelerator is supported by GCP.
    if not list_accelerators(gpus_only=False, name_filter=acc_name):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
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

    if acc_name in _ACC_INSTANCE_TYPE_DICTS:
        matching_types = _ACC_INSTANCE_TYPE_DICTS[acc_name][acc_count]
        if instance_type not in matching_types:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    f'{acc_name} GPUs cannot be attached to {instance_type}. '
                    f'Use one of {matching_types} instead. Please refer to '
                    'https://cloud.google.com/compute/docs/gpus')
    elif not instance_type.startswith('n1-'):
        # Other GPUs must be attached to N1 machines.
        # Refer to: https://cloud.google.com/compute/docs/machine-types#gpus
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name} GPUs cannot be attached to {instance_type}. '
                'Use N1 instance types instead. Please refer to: '
                'https://cloud.google.com/compute/docs/machine-types#gpus')

    if acc_name in _ACC_INSTANCE_TYPE_DICTS:
        valid_counts = list(_ACC_INSTANCE_TYPE_DICTS[acc_name].keys())
    else:
        assert acc_name in _NUM_ACC_TO_MAX_CPU_AND_MEMORY, acc_name
        valid_counts = list(_NUM_ACC_TO_MAX_CPU_AND_MEMORY[acc_name].keys())
    if acc_count not in valid_counts:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ResourcesMismatchError(
                f'{acc_name}:{acc_count} is not launchable on GCP. '
                f'The valid {acc_name} counts are {valid_counts}.')

    # Check maximum vCPUs and memory.
    if acc_name in _ACC_INSTANCE_TYPE_DICTS:
        max_cpus, max_memory = get_vcpus_mem_from_instance_type(instance_type)
    else:
        max_cpus, max_memory = _NUM_ACC_TO_MAX_CPU_AND_MEMORY[acc_name][
            acc_count]
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


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    return common.get_image_id_from_tag_impl(_image_df, tag, region)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    # GCP images are not region-specific.
    del region  # Unused.
    return common.is_image_tag_valid_impl(_image_df, tag, None)
