""" RunPod | Catalog

This module loads the service catalog file and can be used to
query instance types and pricing information for RunPod.
"""

import fractions
import math
import re
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.adaptors import runpod
from sky.catalog import common
from sky.provision.runpod import utils as runpod_utils

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud

# Runpod has no set updated schedule for their catalog. We pull the catalog
# every 7 hours to make sure we have the latest information.
_PULL_FREQUENCY_HOURS = 7
_df = common.read_catalog('runpod/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# Sized variants retain guaranteed minima and the selected hourly estimate.
# This serialized estimate is editable, not authoritative billing or admission.
# Accounting must survive round trips without consulting current availability.
_SIZED = re.compile(r'(.+)--([1-9][0-9]*)vcpu-([1-9][0-9]*)gb-'
                    r'([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)usd$')


def _sized(
    instance_type: str
) -> Tuple[str, Optional[int], Optional[int], Optional[float]]:
    match = _SIZED.fullmatch(instance_type)
    if match is None:
        return instance_type, None, None, None
    price = float(match[4])
    if not math.isfinite(price) or price <= 0:
        return instance_type, None, None, None
    return match[1], int(match[2]), int(match[3]), price


def _quote_price(instance_type: str,
                 cpus: int,
                 memory_gb: int,
                 region: Optional[str],
                 *,
                 fresh: bool = False) -> float:
    match = re.fullmatch(r'([1-9][0-9]*)x_([\w-]+)_(SECURE|COMMUNITY)',
                         instance_type)
    if match is None:
        return math.inf
    count, gpu, cloud_type = match.groups()
    # Multi-GPU lowestPrice units are unproved; retain their static contract.
    if count != '1' or gpu not in runpod_utils.GPU_NAME_MAP:
        return math.inf
    gpu_count = int(count)
    # Admission bypasses the existing selection cache; availability and price
    # may have changed even within its 60-second lifetime.
    # pylint: disable-next=protected-access
    lookup = (runpod._get_gpu_host_quote
              if fresh else runpod.get_gpu_host_quote)
    quote = lookup(runpod_utils.GPU_NAME_MAP[gpu], gpu_count,
                   cloud_type == 'SECURE', cpus, memory_gb, region)
    if not isinstance(quote, dict):
        return math.inf
    values = [
        quote.get(key)
        for key in ('minVcpu', 'minMemory', 'uninterruptablePrice')
    ]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or
           not math.isfinite(v) or v <= 0 for v in values):
        return math.inf
    actual_cpus, actual_memory, price = typing.cast(List[float], values)
    counts = quote.get('availableGpuCounts')
    if (actual_cpus < cpus or actual_memory < memory_gb or
            quote.get('stockStatus') not in ('Low', 'Medium', 'High') or
        (counts is not None and
         (not isinstance(counts, list) or
          any(not isinstance(n, int) or isinstance(n, bool) or n <= 0
              for n in counts) or gpu_count not in counts))):
        return math.inf
    return float(price)


def _provider_memory_gb(memory_gib: str) -> int:
    # Exact arithmetic avoids rounding a provider-unit boundary up or down.
    return math.ceil(fractions.Fraction(memory_gib) * (2**30) / (10**9))


def _memory_in_gib() -> 'pd.DataFrame':
    # The legacy GPU catalog copies RunPod's nominal GB into MemoryGiB.
    # Use decimal GB as a conservative lower bound; the provider does not
    # document whether its GB means 1e9 or 2**30 bytes. Keep the raw catalog
    # intact for provisioning and leave CPU instance sizing unchanged.
    df = _df.copy()
    gpu_rows = df['AcceleratorName'].notna() & (df['AcceleratorCount'] > 0)
    df['MemoryGiB'] = df['MemoryGiB'].where(~gpu_rows,
                                            df['MemoryGiB'] * (10**9 / 2**30))
    return df


def get_native_gpu_host_resources(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    """Return CPU count and RunPod's unconverted nominal host-memory GB."""
    base, cpus, memory, _ = _sized(instance_type)
    if cpus is not None:
        return cpus, memory
    return common.get_vcpus_mem_from_instance_type_impl(_df, base)


def instance_type_exists(instance_type: str) -> bool:
    base, cpus, memory, _ = _sized(instance_type)
    if not common.instance_type_exists_impl(_df, base):
        return False
    if cpus is None:
        return True
    accelerators = common.get_accelerators_from_instance_type_impl(_df, base)
    if not accelerators or sum(accelerators.values()) != 1:
        return False
    base_cpus, base_memory = get_native_gpu_host_resources(base)
    return (base_cpus is not None and base_memory is not None and
            cpus >= base_cpus and memory is not None and memory >= base_memory)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl('runpod', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Return the selected sized-host estimate, or the static catalog price."""
    base, cpus, _, estimate = _sized(instance_type)
    if cpus is not None:
        assert estimate is not None
        if use_spot:
            raise ValueError('Sized RunPod hosts do not support spot pricing.')
        return estimate
    return common.get_hourly_cost_impl(_df, base, use_spot, region, zone)


def _current_hourly_cost(instance_type: str, use_spot: bool,
                         region: Optional[str]) -> float:
    """Fresh admission quote, separate from the durable accounting estimate."""
    base, cpus, memory, _ = _sized(instance_type)
    if cpus is not None:
        assert memory is not None
        return math.inf if use_spot else _quote_price(
            base, cpus, memory, region, fresh=True)
    return get_hourly_cost(instance_type, use_spot, region)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    base, cpus, memory, _ = _sized(instance_type)
    if cpus is not None:
        assert memory is not None
        return cpus, memory * (10**9 / 2**30)
    return common.get_vcpus_mem_from_instance_type_impl(_memory_in_gib(), base)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # RunPod does not support disk tiers.
    # NOTE: After expanding catalog to multiple entries, you may
    # want to specify a default instance type or family.
    return common.get_instance_type_for_cpus_mem_impl(_memory_in_gib(), cpus,
                                                      memory, region, zone,
                                                      use_spot, max_hourly_cost)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(
        _df,
        _sized(instance_type)[0])


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
    """Returns a list of instance types that have the given accelerator."""
    del local_disk  # unused
    result = common.get_instance_type_for_accelerator_impl(
        df=_memory_in_gib(),
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
        max_hourly_cost=max_hourly_cost)
    # The provider can enforce minima, not exact CPU/RAM or RAM:CPU ratios.
    # Preserve all existing static/spot matches and only query stronger hosts
    # when the catalog has the requested GPU count but its host floor misses.
    if (result[0] or result[0] is None or acc_count != 1 or use_spot or
            zone is not None or
            any(v is not None and not v.endswith('+') for v in (cpus, memory))):
        return result
    bases, _ = common.get_instance_type_for_accelerator_impl(_memory_in_gib(),
                                                             acc_name,
                                                             acc_count,
                                                             region=region,
                                                             use_spot=False)
    sized = []
    for base in bases or []:
        base_cpu, base_memory = get_native_gpu_host_resources(base)
        if base_cpu is None or base_memory is None:
            continue
        cpu = max(math.ceil(base_cpu),
                  math.ceil(float(cpus[:-1])) if cpus else 0)
        ram = max(math.ceil(base_memory),
                  _provider_memory_gb(memory[:-1]) if memory else 0)
        price = _quote_price(base, cpu, ram, region)
        if math.isfinite(price) and (max_hourly_cost is None or
                                     price <= max_hourly_cost):
            sized.append((price, f'{base}--{cpu}vcpu-{ram}gb-{price}usd'))
    return [name for _, name in sorted(sized)], []


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == _sized(instance_type)[0]]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in RunPod offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('RunPod', _memory_in_gib(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)
