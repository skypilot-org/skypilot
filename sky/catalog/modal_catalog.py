"""Modal service catalog.

Modal sizes CPU and memory per container, so it has no fixed instance types.
As on Kubernetes, SkyPilot builds them on the fly from the request, e.g.
`4CPU--16GB` or `32CPU--128GB--H100:2`, and prices them from the hosted
`modal/vms.csv` (made by sky/catalog/data_fetchers/fetch_modal.py). That
table uses the standard catalog columns, GCP-style, with two kinds of rows
per region: accelerator rows (no `InstanceType`, one per GPU type and count
Modal offers per container, with the `SpotPrice` of that many GPUs and
`GpuInfo` with the GPU memory), and two rate rows, `InstanceType` `cpu-core`
(one Modal core, 2 vCPUs) and `memory-gib`, whose `Price` is the
non-preemptible rate and `SpotPrice` the list rate.

Modal's list rates are for preemptible containers, which is what SkyPilot
calls spot. GPU containers are always preemptible, so GPU instance types are
offered as spot only. CPU-only instance types are offered both ways:
on-demand runs non-preemptible, at the non-preemptible rate.
"""

import ast
import math
import re
from typing import Dict, List, Optional, Set, Tuple, Union

import pandas as pd

from sky.catalog import common
from sky.clouds import cloud
from sky.utils import resources_utils
from sky.utils import ux_utils

_CLOUD_NAME = 'modal'
_DISPLAY_NAME = 'Modal'

AUTO_REGION = 'auto'

# InstanceType of the two rate rows in modal/vms.csv; every other row is an
# accelerator row. Keep in sync with fetch_modal.py.
CPU_RESOURCE = 'cpu-core'
MEMORY_RESOURCE = 'memory-gib'
# Which column of a rate row to charge: Modal's list rate is its preemptible
# rate, so it sits in SpotPrice; Price holds the non-preemptible rate.
_LIST_RATE_COLUMN = 'SpotPrice'
_NONPREEMPTIBLE_RATE_COLUMN = 'Price'

GPU_SPOT_ONLY_HINT = ('Modal GPUs are always preemptible, so SkyPilot offers '
                      'them as spot only: add --use-spot (use_spot: true).')

_PULL_FREQUENCY_HOURS = 24
_df = common.read_catalog('modal/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_DEFAULT_SKY_VCPUS = 4.0
_DEFAULT_MEMORY_GIB = 16
_DEFAULT_MEMORY_CPU_RATIO = _DEFAULT_MEMORY_GIB / _DEFAULT_SKY_VCPUS
# Without a CPU request, GPU containers get 4 vCPUs per GPU, as on Kubernetes
# (memory follows at _DEFAULT_MEMORY_CPU_RATIO).
_DEFAULT_SKY_VCPUS_PER_GPU = 4
# GPU counts shown by `sky gpus list`; a launch may use any count the catalog
# offers.
_LISTED_GPU_COUNTS = (1, 2, 4, 8)

# Modal validates these bounds when creating a Function. See:
# https://modal.com/docs/guide/resources#how-much-can-i-request
_MIN_MODAL_CPU_CORES = 0.125
_MAX_MODAL_CPU_CORES = 64.0
_MIN_MEMORY_MIB = 128
_MAX_MEMORY_MIB = 344064

_VIRTUAL_INSTANCE_TYPE_PATTERN = re.compile(
    r'^(?P<vcpus>\d+(?:\.\d+)?)CPU--'
    r'(?P<memory>\d+(?:\.\d+)?)GB'
    r'(?:--(?P<accelerator>[\w-]+):(?P<count>\d+))?$')


def _gpu_rows() -> pd.DataFrame:
    """The accelerator rows of the `auto` region: one per GPU type and count."""
    df = _df[_df['AcceleratorName'].notna()]
    return df[df['Region'] == AUTO_REGION]


def _region_names() -> Set[str]:
    return set(_df['Region'])


def _canonical_accelerator(accelerator: str) -> Optional[str]:
    accelerator_lower = accelerator.lower()
    for supported_accelerator in _gpu_rows()['AcceleratorName'].unique():
        if supported_accelerator.lower() == accelerator_lower:
            return supported_accelerator
    return None


def _offered_counts(accelerator: str) -> List[int]:
    """GPU counts per container the catalog lists for this (canonical) GPU."""
    rows = _gpu_rows()
    counts = rows[rows['AcceleratorName'] == accelerator]['AcceleratorCount']
    return sorted(int(count) for count in counts)


def _format_counts(counts: List[int]) -> str:
    if counts and counts == list(range(counts[0], counts[-1] + 1)):
        return (str(counts[0])
                if len(counts) == 1 else f'{counts[0]}-{counts[-1]}')
    return ', '.join(str(count) for count in counts)


def get_max_gpu_count(accelerator: str) -> Optional[int]:
    """Most GPUs of this type Modal offers per container; None if unknown."""
    canonical = _canonical_accelerator(accelerator)
    if canonical is None:
        return None
    return max(_offered_counts(canonical))


def _gpu_memory_gib(accelerator: str) -> int:
    rows = _gpu_rows()
    gpu_info = ast.literal_eval(
        rows[rows['AcceleratorName'] == accelerator]['GpuInfo'].iloc[0])
    return int(gpu_info['Gpus'][0]['MemoryInfo']['SizeInMiB']) // 1024


def _format_resource_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f'{value:.12f}'.rstrip('0').rstrip('.')


class ModalInstanceType:
    """Virtual instance type for a Modal resource request."""

    def __init__(self,
                 vcpus: float,
                 memory_gib: float,
                 accelerator_count: Optional[Union[int, float]] = None,
                 accelerator_type: Optional[str] = None):
        modal_cpu = vcpus / 2
        memory_mib = math.ceil(memory_gib * 1024)
        if not _MIN_MODAL_CPU_CORES <= modal_cpu <= _MAX_MODAL_CPU_CORES:
            raise ValueError('Modal CPU request is out of bounds.')
        if not _MIN_MEMORY_MIB <= memory_mib <= _MAX_MEMORY_MIB:
            raise ValueError('Modal memory request is out of bounds.')
        if (accelerator_count is None) != (accelerator_type is None):
            raise ValueError('Modal accelerator type and count must be set '
                             'together.')
        count: Optional[int] = None
        if accelerator_type is not None:
            assert accelerator_count is not None
            # A request can carry the count as a float (`T4: 1.0`); the
            # instance type name takes whole numbers only.
            if int(accelerator_count) != accelerator_count:
                raise ValueError('Modal GPU counts are whole numbers, not '
                                 f'{accelerator_count}.')
            count = int(accelerator_count)
            canonical_accelerator = _canonical_accelerator(accelerator_type)
            if canonical_accelerator is None:
                raise ValueError(
                    f'Unsupported Modal accelerator {accelerator_type!r}.')
            counts = _offered_counts(canonical_accelerator)
            if count not in counts:
                raise ValueError(
                    f'Modal offers {canonical_accelerator} in counts of '
                    f'{_format_counts(counts)} per container, not {count}.')
            accelerator_type = canonical_accelerator
        self.vcpus = vcpus
        self.memory_mib = memory_mib
        self.accelerator_count = count
        self.accelerator_type = accelerator_type

    @property
    def memory_gib(self) -> float:
        return self.memory_mib / 1024

    @property
    def modal_cpu(self) -> float:
        return self.vcpus / 2

    @property
    def name(self) -> str:
        name = (f'{_format_resource_value(self.vcpus)}CPU--'
                f'{_format_resource_value(self.memory_gib)}GB')
        if self.accelerator_type is not None:
            name += f'--{self.accelerator_type}:{self.accelerator_count}'
        return name

    @classmethod
    def from_instance_type(cls, name: str) -> 'ModalInstanceType':
        match = _VIRTUAL_INSTANCE_TYPE_PATTERN.fullmatch(name)
        if match is None:
            raise ValueError(f'Invalid Modal instance type {name!r}.')
        accelerator_count = match.group('count')
        return cls(vcpus=float(match.group('vcpus')),
                   memory_gib=float(match.group('memory')),
                   accelerator_count=(int(accelerator_count) if
                                      accelerator_count is not None else None),
                   accelerator_type=match.group('accelerator'))


def _region_rows(region: str) -> pd.DataFrame:
    rows = _df[_df['Region'] == region]
    if rows.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Invalid Modal region {region!r}.')
    return rows


class _RegionPrices:
    """Hourly prices of one region, read once from its catalog rows."""

    def __init__(self, region_rows: pd.DataFrame):
        self.region = region_rows['Region'].iloc[0]
        # {`cpu-core` or `memory-gib`: {column: rate per unit}}
        self._rates: Dict[str, Dict[str, float]] = {}
        # {(GPU, count): price of that many GPUs}
        self._gpus: Dict[Tuple[str, int], float] = {}
        for row in region_rows.itertuples(index=False):
            if pd.notna(row.AcceleratorName):
                # Catalogs fetched before GPUs became spot only have the
                # price in `Price`.
                price = row.SpotPrice if pd.notna(row.SpotPrice) else row.Price
                self._gpus[(row.AcceleratorName,
                            int(row.AcceleratorCount))] = float(price)
            else:
                self._rates[row.InstanceType] = {
                    _NONPREEMPTIBLE_RATE_COLUMN: float(row.Price),
                    _LIST_RATE_COLUMN: float(row.SpotPrice),
                }

    def rate(self, resource: str, column: str) -> float:
        """Per-unit hourly rate from the `cpu-core` or `memory-gib` row."""
        if resource not in self._rates:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'No Modal price for {resource} in region '
                                 f'{self.region!r}.')
        return self._rates[resource][column]

    def gpu_price(self, accelerator: str, count: int) -> float:
        """Hourly price of `count` GPUs, from the matching accelerator row."""
        if (accelerator, count) not in self._gpus:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'No Modal price for {accelerator}:{count} '
                                 f'in region {self.region!r}.')
        return self._gpus[(accelerator, count)]


def _region_prices(region: str) -> _RegionPrices:
    return _RegionPrices(_region_rows(region))


def _is_gpu(instance: ModalInstanceType) -> bool:
    return instance.accelerator_type is not None


def _compute_price_per_hour(instance: ModalInstanceType, prices: _RegionPrices,
                            use_spot: bool) -> float:
    """Price of a container in a region.

    Raises:
        ValueError: a GPU container on-demand; Modal has none.
    """
    if _is_gpu(instance) and not use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'No on-demand price for {instance.name}. {GPU_SPOT_ONLY_HINT}')
    column = _LIST_RATE_COLUMN if use_spot else _NONPREEMPTIBLE_RATE_COLUMN
    price = (instance.modal_cpu * prices.rate(CPU_RESOURCE, column) +
             instance.memory_gib * prices.rate(MEMORY_RESOURCE, column))
    if instance.accelerator_type is not None:
        assert instance.accelerator_count is not None
        price += prices.gpu_price(instance.accelerator_type,
                                  instance.accelerator_count)
    return price


def instance_type_exists(instance_type: str) -> bool:
    try:
        ModalInstanceType.from_instance_type(instance_type)
    except ValueError:
        return False
    return True


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    return common.validate_region_zone_impl(_CLOUD_NAME, _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    instance = ModalInstanceType.from_instance_type(instance_type)
    return _compute_price_per_hour(instance,
                                   _region_prices(region or AUTO_REGION),
                                   use_spot)


def is_gpu_instance_type(instance_type: str) -> bool:
    return _is_gpu(ModalInstanceType.from_instance_type(instance_type))


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    instance = ModalInstanceType.from_instance_type(instance_type)
    return instance.vcpus, instance.memory_gib


def _resources_from_requests(cpus: Optional[str],
                             memory: Optional[str]) -> ModalInstanceType:
    vcpus = (float(cpus.rstrip('+'))
             if cpus is not None else _DEFAULT_SKY_VCPUS)
    if memory is None:
        memory_gib = min(vcpus * _DEFAULT_MEMORY_CPU_RATIO,
                         _MAX_MEMORY_MIB / 1024)
    elif memory.endswith('+'):
        memory_gib = float(memory[:-1])
    elif memory.endswith('x'):
        memory_gib = float(memory[:-1]) * vcpus
    else:
        memory_gib = float(memory)
    return ModalInstanceType(vcpus, memory_gib)


def _gpu_instance(accelerator: str, count: int, cpus: Optional[str],
                  memory: Optional[str]) -> ModalInstanceType:
    """GPU container shape; without a CPU request, 4 vCPUs per GPU."""
    if cpus is None:
        cpus = str(_DEFAULT_SKY_VCPUS_PER_GPU * count)
    base = _resources_from_requests(cpus, memory)
    return ModalInstanceType(base.vcpus,
                             base.memory_gib,
                             accelerator_count=count,
                             accelerator_type=accelerator)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # unused
    if zone is not None:
        return None
    if region is not None and region not in _region_names():
        return None
    try:
        instance = _resources_from_requests(cpus, memory)
    except ValueError:
        return None
    if (max_hourly_cost is not None and get_hourly_cost(
            instance.name, use_spot=use_spot, region=region) > max_hourly_cost):
        return None
    return instance.name


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    instance = ModalInstanceType.from_instance_type(instance_type)
    if instance.accelerator_type is None:
        return None
    assert instance.accelerator_count is not None
    return {instance.accelerator_type: instance.accelerator_count}


def get_arch_from_instance_type(instance_type: str) -> Optional[str]:
    ModalInstanceType.from_instance_type(instance_type)
    return None


def get_local_disk_from_instance_type(instance_type: str) -> Optional[str]:
    ModalInstanceType.from_instance_type(instance_type)
    return None


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None
) -> Tuple[Optional[List[str]], List[str]]:
    del local_disk  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    if not use_spot:
        # Modal's GPU containers are always preemptible.
        return None, []
    if region is not None and region not in _region_names():
        return None, []
    canonical_accelerator = _canonical_accelerator(acc_name)
    if canonical_accelerator is None:
        # Unknown GPU: fuzzy-match the name against the listed shapes.
        return common.get_instance_type_for_accelerator_impl(
            df=_listed_instances(),
            acc_name=acc_name,
            acc_count=acc_count,
            cpus=None,
            memory=None,
            use_spot=True,
            region=region,
            zone=None,
            max_hourly_cost=max_hourly_cost)
    try:
        instance = _gpu_instance(canonical_accelerator, acc_count, cpus, memory)
    except ValueError:
        return [], []
    if (max_hourly_cost is not None and get_hourly_cost(
            instance.name, use_spot=True, region=region) > max_hourly_cost):
        return [], []
    return [instance.name], []


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List[cloud.Region]:
    instance = ModalInstanceType.from_instance_type(instance_type)
    if _is_gpu(instance) and not use_spot:
        return []
    return regions()


def _accelerator_offered(accelerator: str, count: int,
                         region: Optional[str]) -> bool:
    canonical = _canonical_accelerator(accelerator)
    if canonical is None or count not in _offered_counts(canonical):
        return False
    return region is None or region in _region_names()


def get_accelerator_hourly_cost(accelerator: str,
                                count: int,
                                use_spot: bool = False,
                                region: Optional[str] = None,
                                zone: Optional[str] = None) -> float:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')
    if not use_spot:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No on-demand price for {accelerator}:{count}. '
                             f'{GPU_SPOT_ONLY_HINT}')
    if not _accelerator_offered(accelerator, count, region):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No accelerator {accelerator}:{count} found.')
    # The GPU price is part of the instance type's hourly cost.
    return 0.0


def get_region_zones_for_accelerators(
        accelerator: str,
        count: int,
        use_spot: bool = False) -> List[cloud.Region]:
    if not use_spot or not _accelerator_offered(accelerator, count,
                                                region=None):
        return []
    return regions()


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None) -> None:
    del instance_type, accelerators  # unused
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Modal does not support zones.')


def _gpu_info(accelerator: str, count: int) -> str:
    gpu_memory_mib = _gpu_memory_gib(accelerator) * 1024
    return repr({
        'Gpus': [{
            'Name': accelerator,
            'Manufacturer': 'NVIDIA',
            'Count': count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib,
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * count,
    })


# The table of `_listed_instances()`, with the catalog index it was built
# from: rebuilt only when the catalog is reloaded (or swapped, in tests).
_listed_instances_cache: Optional[Tuple[pd.Index, pd.DataFrame]] = None


def _listed_instances() -> pd.DataFrame:
    """The shapes a launch picks without a CPU or memory request, per region.

    `sky gpus list` and region lookups read this standard catalog table,
    which is built from the catalog's rate and accelerator rows rather than
    stored. Building it takes seconds and the optimizer asks for regions per
    candidate, so it is cached per loaded catalog.
    """
    global _listed_instances_cache
    catalog_index = _df.index
    cached = _listed_instances_cache
    if cached is None or cached[0] is not catalog_index:
        cached = (catalog_index, _build_listed_instances())
        _listed_instances_cache = cached
    # A copy: callers filter and annotate the table.
    return cached[1].copy()


def _build_listed_instances() -> pd.DataFrame:
    instances = [_resources_from_requests(None, None)]
    for accelerator in _gpu_rows()['AcceleratorName'].unique():
        offered = _offered_counts(accelerator)
        instances.extend(
            _gpu_instance(accelerator, count, cpus=None, memory=None)
            for count in _LISTED_GPU_COUNTS
            if count in offered)
    gpu_infos = {
        instance.name: _gpu_info(instance.accelerator_type,
                                 instance.accelerator_count)
        for instance in instances
        if instance.accelerator_type is not None and
        instance.accelerator_count is not None
    }
    rows = []
    for region in sorted(_region_names()):
        prices = _region_prices(region)
        for instance in instances:
            count = instance.accelerator_count
            rows.append({
                'InstanceType': instance.name,
                'AcceleratorName': instance.accelerator_type,
                'AcceleratorCount': float(count) if count else None,
                'vCPUs': instance.vcpus,
                'MemoryGiB': instance.memory_gib,
                # GPU shapes have no on-demand price: they are spot only.
                'Price':
                    (None if _is_gpu(instance) else _compute_price_per_hour(
                        instance, prices, use_spot=False)),
                'SpotPrice': _compute_price_per_hour(instance,
                                                     prices,
                                                     use_spot=True),
                'Region': region,
                'GpuInfo': gpu_infos.get(instance.name),
            })
    return pd.DataFrame(rows)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    del require_price  # unused
    return common.list_accelerators_impl(_DISPLAY_NAME, _listed_instances(),
                                         gpus_only, name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def regions() -> List[cloud.Region]:
    # Cheapest first, so `auto` precedes the broad and narrow regions. Every
    # region has every shape, so the spot prices (which all shapes have)
    # order them.
    return common.get_region_zones(_listed_instances(), use_spot=True)


def get_modal_args_from_instance_type(
        instance_type: str) -> Tuple[Optional[str], float, int]:
    """Return Modal Function args: gpu, cpu cores, memory MiB."""
    try:
        instance = ModalInstanceType.from_instance_type(instance_type)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'No instance type {instance_type} found: {e}') from e
    gpu = instance.accelerator_type
    if gpu is not None and instance.accelerator_count != 1:
        gpu = f'{gpu}:{instance.accelerator_count}'
    return gpu, instance.modal_cpu, instance.memory_mib
