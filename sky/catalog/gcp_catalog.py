"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""
import typing
from typing import Dict, List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.catalog import common
from sky.clouds import GCP
from sky.utils import resources_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)

# Pull the latest catalog every week.
# GCP guarantees that the catalog is updated at most once per 30 days, but
# different VMs can be updated at different time. Thus, we pull the catalog
# every 7 hours to make sure we have the latest information.
_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('gcp/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)
_image_df = common.read_catalog('gcp/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_quotas_df = common.read_catalog('gcp/accelerator_quota_mapping.csv',
                                 pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# We will select from the following six CPU instance families:
_DEFAULT_INSTANCE_FAMILY = [
    # This is a widely used general-purpose instance family as of July 2025.
    # CPU: Primarily Intel Ice Lake (3rd Gen Intel Xeon Scalable Processors)
    #  or Cascade Lake (2nd Gen Intel Xeon Scalable Processors).
    # Memory: 4 GiB RAM per 1 vCPU;
    'n2-standard',
    # This is a memory-optimized instance family as of July 2025.
    # CPU: Primarily Intel Ice Lake (3rd Gen Intel Xeon Scalable Processors)
    # or Cascade Lake (2nd Gen Intel Xeon Scalable Processors).
    # Memory: 8 GiB RAM per 1 vCPU;
    'n2-highmem',
    # This is a compute-optimized instance family as of July 2025.
    # CPU: Primarily Intel Ice Lake (3rd Gen Intel Xeon Scalable Processors)
    #  or Cascade Lake (2nd Gen Intel Xeon Scalable Processors).
    # Memory: 1 GiB RAM per 1 vCPU;
    'n2-highcpu',
    # This is the latest general-purpose instance family as of July 2025.
    # CPU: Intel 5th Gen Xeon Scalable processor (Emerald Rapids).
    # Memory: 4 GiB RAM per 1 vCPU;
    'n4-standard',
    # This is the latest general-purpose instance family
    # with a higher vCPU to memory ratio as of July 2025.
    # CPU: Intel 5th Gen Xeon Scalable processor (Emerald Rapids).
    # Memory: 2 GiB RAM per 1 vCPU;
    'n4-highcpu',
    # This is the latest general-purpose instance family
    # with a lower vCPU to memory ratio as of July 2025.
    # CPU: Intel 5th Gen Xeon Scalable processor (Emerald Rapids).
    # Memory: 8 GiB RAM per 1 vCPU;
    'n4-highmem',
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
    'H100': {
        1: ['a3-highgpu-1g'],
        2: ['a3-highgpu-2g'],
        4: ['a3-highgpu-4g'],
        8: ['a3-highgpu-8g'],
    },
    'H100-MEGA': {
        8: ['a3-megagpu-8g'],
    },
    'H200': {
        8: ['a3-ultragpu-8g'],
    },
    'B200': {
        8: ['a4-highgpu-8g'],
    },
}
# Enable GPU type inference from instance types
_INSTANCE_TYPE_TO_ACC = {
    instance_type: {
        acc_name: acc_count
    } for acc_name, acc_count_to_instance_type in
    _ACC_INSTANCE_TYPE_DICTS.items()
    for acc_count, instance_types in acc_count_to_instance_type.items()
    for instance_type in instance_types
}
GCP_ACC_INSTANCE_TYPES = list(_INSTANCE_TYPE_TO_ACC.keys())

# Number of CPU cores per GPU based on the AWS setting.
# GCP A100 has its own instance type mapping.
# Refer to sky/clouds/catalog/gcp_catalog.py
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
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
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

    def _filter_disk_type(instance_type: str) -> bool:
        valid, _ = GCP.check_disk_tier(instance_type, disk_tier)
        return valid

    df = df.loc[df['InstanceType'].apply(_filter_disk_type)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio,
                                                      region, zone)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    """Infer the GPU type from the instance type.

    This inference logic is GCP-specific. Unlike other clouds, we don't call
    the internal implementation defined in common.py.

    Args:
        instance_type: the instance type to use.

    Returns:
        A dictionary mapping from the accelerator name to the accelerator count.
    """
    if instance_type in GCP_ACC_INSTANCE_TYPES:
        return _INSTANCE_TYPE_TO_ACC[instance_type]
    else:
        # General CPU instance types don't come with pre-attached accelerators.
        return None


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
        instance_types = _ACC_INSTANCE_TYPE_DICTS[acc_name].get(acc_count, None)
        if instance_types is None:
            return None, []
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


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def _get_accelerator(
    df: 'pd.DataFrame',
    accelerator: str,
    count: int,
    region: Optional[str],
    zone: Optional[str] = None,
) -> 'pd.DataFrame':
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

    df = _get_accelerator(_df, accelerator, count, region, zone)
    if region is not None:
        assert len(set(df['Price'])) == 1, df
    min_price = df['Price'].min()
    if not use_spot:
        return min_price

    cheapest_idx = df['SpotPrice'].idxmin()
    if pd.isnull(cheapest_idx):
        return min_price

    cheapest = df.loc[cheapest_idx]
    return cheapest['SpotPrice']


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in GCP offering GPUs."""

    # This is a simplified version of get_instance_type_for_accelerator, as it
    # has to be applied to all the entries in acc_host_df, and the input is more
    # restricted.
    def _get_host_instance_type(acc_name: str, acc_count: int, region: str,
                                zone: str) -> Optional[str]:
        df = _df[(_df['Region'].str.lower() == region.lower()) &
                 (_df['AvailabilityZone'].str.lower() == zone.lower()) &
                 (_df['InstanceType'].notna())]
        if acc_name in _ACC_INSTANCE_TYPE_DICTS:
            instance_types = _ACC_INSTANCE_TYPE_DICTS[acc_name][acc_count]
            df = df[df['InstanceType'].isin(instance_types)]
        else:
            if acc_name not in _NUM_ACC_TO_NUM_CPU:
                acc_name = 'DEFAULT'

            cpus = _NUM_ACC_TO_NUM_CPU[acc_name][acc_count]
            # The memory size should be at least 4x the requested number of
            # vCPUs to be consistent with all other clouds.
            memory = cpus * _DEFAULT_GPU_MEMORY_CPU_RATIO
            df = df[df['InstanceType'].str.startswith(_DEFAULT_HOST_VM_FAMILY)]
            df = df[(df['vCPUs'] >= cpus) & (df['MemoryGiB'] >= memory)]
        df = df.dropna(subset=['Price'])
        # Some regions may not have the host VM available, although the GPU is
        # offered, e.g., a2-megagpu-16g in asia-northeast1.
        if df.empty:
            return None
        row = df.loc[df['Price'].idxmin()]
        return row['InstanceType']

    acc_host_df = _df
    tpu_df = None
    # If price information is not required, we do not need to fetch the host VM
    # information.
    if require_price:
        acc_host_df = _df[_df['AcceleratorName'].notna()]
        # Filter the acc_host_df first before fetching the host VM information.
        # This is to reduce the rows to be processed for optimization.
        # TODO: keep it sync with `list_accelerators_impl`
        if gpus_only:
            acc_host_df = acc_host_df[~acc_host_df['GpuInfo'].isna()]
        if name_filter is not None:
            acc_host_df = acc_host_df[
                acc_host_df['AcceleratorName'].str.contains(name_filter,
                                                            case=case_sensitive,
                                                            regex=True)]
        if region_filter is not None:
            acc_host_df = acc_host_df[acc_host_df['Region'].str.contains(
                region_filter, case=case_sensitive, regex=True)]
        acc_host_df['AcceleratorCount'] = acc_host_df[
            'AcceleratorCount'].astype(int)
        if quantity_filter is not None:
            acc_host_df = acc_host_df[acc_host_df['AcceleratorCount'] ==
                                      quantity_filter]
        # Split the TPU and GPU information, as we should not combine the price
        # of the TPU and the host VM.
        # TODO: Fix the price for TPU VMs.
        is_tpu = acc_host_df['AcceleratorName'].str.startswith('tpu-')
        tpu_df = acc_host_df[is_tpu]
        acc_host_df = acc_host_df[~is_tpu]
        if not acc_host_df.empty:
            assert acc_host_df['InstanceType'].isna().all(), acc_host_df
            acc_host_df = acc_host_df.drop(
                columns=['InstanceType', 'vCPUs', 'MemoryGiB'])
            # Fetch the host VM information for each accelerator in its region
            # and zone.
            acc_host_df['InstanceType'] = acc_host_df.apply(
                lambda x: _get_host_instance_type(x['AcceleratorName'],
                                                  x['AcceleratorCount'],
                                                  region=x['Region'],
                                                  zone=x['AvailabilityZone']),
                axis=1)
            acc_host_df.dropna(subset=['InstanceType', 'Price'], inplace=True)
            # Combine the price of the host VM and the GPU.
            acc_host_df = pd.merge(
                acc_host_df,
                _df[[
                    'InstanceType', 'vCPUs', 'MemoryGiB', 'Region',
                    'AvailabilityZone', 'Price', 'SpotPrice'
                ]],
                on=['InstanceType', 'Region', 'AvailabilityZone'],
                how='inner',
                suffixes=('', '_host'))
            # Combine the price of the host instance type and the GPU.
            acc_host_df['Price'] = (acc_host_df['Price'] +
                                    acc_host_df['Price_host'])
            acc_host_df['SpotPrice'] = (acc_host_df['SpotPrice'] +
                                        acc_host_df['SpotPrice_host'])
            acc_host_df.rename(columns={
                'vCPUs_host': 'vCPUs',
                'MemoryGiB_host': 'MemoryGiB'
            },
                               inplace=True)
        acc_host_df = pd.concat([acc_host_df, tpu_df])
    results = common.list_accelerators_impl('GCP', acc_host_df, gpus_only,
                                            name_filter, region_filter,
                                            quantity_filter, case_sensitive,
                                            all_regions)
    if tpu_df is not None and not tpu_df.empty:
        # Combine the entries for TPUs with the same version.
        # TODO: The current TPU prices are the price of the TPU itself, not the
        # TPU VM. We should fix this in the future.
        for acc_name in list(results.keys()):
            if acc_name.startswith('tpu-'):
                version = acc_name.split('-')[1]
                acc_info = results.pop(acc_name)
                tpu_group = f'tpu-{version}'
                if tpu_group not in results:
                    results[tpu_group] = []
                results[tpu_group].extend(acc_info)
        for acc_group in list(results.keys()):
            if acc_group.startswith('tpu-'):
                results[acc_group] = sorted(
                    results[acc_group],
                    key=lambda x: (x.price, x.spot_price, x.region),
                )
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
        if instance_type in GCP_ACC_INSTANCE_TYPES:
            # Infer the GPU type from the instance type
            accelerators = _INSTANCE_TYPE_TO_ACC[instance_type]
        else:
            # Skip the following checks if instance_type is a general CPU
            # instance without accelerators
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
    global _image_df

    image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    if image_id is None:
        # Refresh the image catalog and try again, if the image tag is not
        # found.
        logger.debug('Refreshing the image catalog and trying again.')
        _image_df = common.read_catalog('gcp/images.csv',
                                        pull_frequency_hours=0)
        image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    return image_id


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    # GCP images are not region-specific.
    del region  # unused
    return get_image_id_from_tag(tag, None) is not None
