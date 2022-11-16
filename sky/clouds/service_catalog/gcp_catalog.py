"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""
from collections import defaultdict
import typing
from typing import Dict, List, Optional, Tuple

import pandas as pd

import sky
from sky import exceptions
from sky import resources
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


def _get_instance_family(instance_type: Optional[str]) -> Optional[str]:
    if pd.isna(instance_type):
        return None
    return instance_type.split('-')[0].lower()


def _is_valid(resource: resources.ClusterResources) -> bool:
    if resource.accelerator is None:
        if resource.instance_family == 'a2':
            # A2 machines should be used together with A100 GPUs.
            return False
        else:
            # No accelerator, no problem.
            return True

    acc_name = resource.accelerator.name
    acc_count = resource.accelerator.count

    if acc_name.startswith('tpu'):
        # TODO: Investigate the host VM restrictions for TPU Nodes.
        return True

    if acc_name in _A100_INSTANCE_TYPE_DICTS:
        return True

    max_cpus, max_memory = _NUM_ACC_TO_MAX_CPU_AND_MEMORY[acc_name][acc_count]
    if acc_name == 'K80' and acc_count == 8:
        if resource.zone in ['asia-east1-a', 'us-east1-d']:
            max_memory = 416
    elif acc_name == 'P100' and acc_count == 4:
        if resource.zone in ['us-east1-c', 'europe-west1-d', 'europe-west1-b']:
            max_cpus = 64
            max_memory = 208

    if resource.num_vcpus > max_cpus:
        return False
    if resource.cpu_memory > max_memory:
        return False
    return True


def _get_default_host_size(
    acc_name: str,
    acc_count: int,
) -> Tuple[Optional[float], Optional[float]]:
    if acc_name.startswith('tpu'):
        # TPUs.
        assert acc_count == 1
        num_tpu_cores = int(acc_name.split('-')[2])
        num_vcpus = min(1.0 * num_tpu_cores, 96.0)
        cpu_memory = min(4.0 * num_tpu_cores, 624.0)
    elif acc_name in ['K80', 'P4', 'T4']:
        # Low-end GPUs.
        num_vcpus = 4.0 * acc_count
        cpu_memory = 16.0 * acc_count
    elif acc_name in ['V100', 'P100']:
        # Mid-range GPUs.
        num_vcpus = 8.0 * acc_count
        cpu_memory = 32.0 * acc_count
    else:
        # High-end GPUs (e.g., A100)
        num_vcpus = None
        cpu_memory = None
    return num_vcpus, cpu_memory


def get_feasible_resources(
    resource_filter: resources.ResourceFilter,
    get_smallest_vms: bool = False,
) -> List[resources.ClusterResources]:
    df = _df
    if 'InstanceFamily' not in df.columns:
        # TODO(woosuk): Add the 'InstanceFamily' column to the catalog.
        df['InstanceFamily'] = df['InstanceType'].apply(_get_instance_family)

    if resource_filter.use_spot is not None:
        df = common.filter_spot(df, resource_filter.use_spot)

    # Search the accelerator first.
    acc_df = None
    if resource_filter.accelerator is not None:
        acc_name = resource_filter.accelerator.name
        acc_count = resource_filter.accelerator.count
        acc_filter = {
            'AcceleratorName': acc_name,
            'AcceleratorCount': acc_count,
        }
        acc_df = common.apply_filters(df, acc_filter)
        if acc_df.empty:
            return []

        # Set the host VM type that matches the accelerator.
        if resource_filter.instance_type == 'tpu-vm':
            # TPU VM.
            pass
        elif acc_name in _A100_INSTANCE_TYPE_DICTS:
            # A100 GPUs are attached to fixed size VMs.
            assert acc_count in _A100_INSTANCE_TYPE_DICTS[acc_name]
            instance_type = _A100_INSTANCE_TYPE_DICTS[acc_name][acc_count]
            if resource_filter.instance_type is None:
                resource_filter.instance_type = instance_type
            elif resource_filter.instance_type != instance_type:
                return []

            # A100 can be only attached to A2 machines.
            if resource_filter.instance_families is None:
                resource_filter.instance_families = ['a2']
            elif 'a2' in resource_filter.instance_families:
                resource_filter.instance_families = ['a2']
            else:
                return []
        else:
            # TPUs and other GPUs.
            # These accelerators can be only attached to N1 machines.
            if resource_filter.instance_families is None:
                resource_filter.instance_families = ['n1']
            elif 'n1' in resource_filter.instance_families:
                resource_filter.instance_families = ['n1']
            else:
                return []

            if resource_filter.instance_type is None:
                if (resource_filter.num_vcpus is None and
                        resource_filter.cpu_memory is None):
                    min_vcpus, min_memory = _get_default_host_size(
                        acc_name, acc_count)
                    if min_vcpus is not None:
                        resource_filter.num_vcpus = f'{min_vcpus}+'
                    if min_memory is not None:
                        resource_filter.cpu_memory = f'{min_memory}+'
            elif not resource_filter.instance_type.startswith('n1-'):
                return []

    # Search the host VM.
    if resource_filter.instance_type == 'tpu-vm':
        # Treat TPU VM as a special case.
        if resource_filter.accelerator is None:
            return []
        elif not resource_filter.accelerator.name.startswith('tpu'):
            return []
    else:
        filters = {
            'InstanceType': resource_filter.instance_type,
            'InstanceFamily': resource_filter.instance_families,
            'vCPUs': resource_filter.num_vcpus,
            'MemoryGiB': resource_filter.cpu_memory,
            'Region': resource_filter.region,
            'AvailabilityZone': resource_filter.zone,
        }
        vm_df = common.apply_filters(df, filters)
        if vm_df.empty:
            return []

    if resource_filter.accelerator is None:
        df = vm_df
    else:
        assert acc_df is not None
        if resource_filter.instance_type == 'tpu-vm':
            df = acc_df.copy()
            # TODO: Add tpu-vm to the catalog.
            df['InstanceType'] = 'tpu-vm'
            df['InstanceFamily'] = 'tpu-vm'
            df['vCPUs'] = 96.0
            df['MemoryGiB'] = 624.0
        else:
            # Join the two dataframes.
            vm_df = vm_df.drop(columns=['AcceleratorName', 'AcceleratorCount'])
            acc_df = acc_df[[
                'Region',
                'AvailabilityZone',
                'AcceleratorName',
                'AcceleratorCount',
            ]]
            df = pd.merge(vm_df, acc_df, on=['Region', 'AvailabilityZone'])

    if get_smallest_vms:
        grouped = df.groupby(['InstanceFamily', 'Region', 'AvailabilityZone'])
        price_str = 'SpotPrice' if resource_filter.use_spot else 'Price'
        df = df.loc[grouped[price_str].idxmin()]
    df = df.reset_index(drop=True)

    gcp = sky.GCP()
    feasible_resources = [
        resources.ClusterResources(
            num_nodes=resource_filter.num_nodes,
            cloud=gcp,
            region=row.Region,
            zone=row.AvailabilityZone,
            instance_type=row.InstanceType,
            instance_family=row.InstanceFamily,
            num_vcpus=row.vCPUs,
            cpu_memory=row.MemoryGiB,
            accelerator=resource_filter.accelerator,
            use_spot=resource_filter.use_spot,
            spot_recovery=resource_filter.spot_recovery,
            disk_size=resource_filter.disk_size,
            image_id=resource_filter.image_id,
        ) for row in df.itertuples()
    ]

    # Filter out invalid combinations.
    return [r for r in feasible_resources if _is_valid(r)]


def is_subset_of(instance_family_a: str, instance_family_b: str) -> bool:
    if instance_family_a == instance_family_b:
        return True
    return False


def get_default_instance_families() -> List[str]:
    # These instance families provide the latest x86-64 Intel and AMD CPUs
    # without any accelerator or optimized storage.
    return ['n2', 'n2d']


def get_hourly_price(resource: resources.ClusterResources) -> float:
    if resource.instance_type == 'tpu-vm':
        host_price = 0.0
    else:
        host_price = common.get_hourly_price_impl(_df, resource.instance_type,
                                                  resource.zone,
                                                  resource.use_spot)

    acc_price = 0.0
    if resource.accelerator is not None:
        df = _df[_df['AcceleratorName'] == resource.accelerator.name]
        df = df[df['AcceleratorCount'] == resource.accelerator.count]
        df = df[df['AvailabilityZone'] == resource.zone]
        assert len(df) == 1
        if resource.use_spot:
            acc_price = df['SpotPrice'].iloc[0]
        else:
            acc_price = df['Price'].iloc[0]
    return host_price + acc_price


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
