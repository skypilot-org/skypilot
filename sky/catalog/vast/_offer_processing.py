"""Shared row-builder for Vast offers.

This module is the single source of truth for turning a Vast `/bundles/`
search response into SkyPilot-catalog-shaped rows. Both the offline catalog
fetcher (`sky/catalog/data_fetchers/fetch_vast.py`) and the runtime live
overlay (`sky/catalog/vast/live_overlay.py`) call into here so the
`InstanceType` merge key never drifts between them.
"""
import collections
import json
import math
import re
from typing import Any, Dict, Iterable, List, Tuple

# Same column order/semantics as historical fetch_vast.py.
_MAPPED_KEYS: Tuple[Tuple[str, str], ...] = (
    ('gpu_name', 'InstanceType'),
    ('gpu_name', 'AcceleratorName'),
    ('num_gpus', 'AcceleratorCount'),
    ('cpu_cores', 'vCPUs'),
    ('cpu_ram', 'MemoryGiB'),
    ('gpu_name', 'GpuInfo'),
    ('search.totalHour', 'Price'),
    ('min_bid', 'SpotPrice'),
    ('geolocation', 'Region'),
    ('hosting_type', 'HostingType'),
)

CSV_FIELDNAMES: List[str] = [theirs for _, theirs in _MAPPED_KEYS]

_GPU_RENAME: Dict[str, str] = {
    'TeslaV100': 'V100',
    'TeslaT4': 'T4',
    'TeslaP100': 'P100',
    'QRTX6000': 'RTX6000',
    'QRTX8000': 'RTX8000',
}


def _stubify(name: str) -> str:
    return re.sub(r'\s', '_', name)


def make_instance_type(num_gpus: int, gpu_name: str, cpu_cores: int,
                       cpu_ram_mib: int) -> str:
    """SkyPilot's invented Vast InstanceType id.

    Format matches `fetch_vast.create_instance_type` exactly:
        '{num_gpus}x-{stubify(gpu_name)}-{cpu_cores}-{cpu_ram_mib}'
    """
    return '{}x-{}-{}-{}'.format(num_gpus, _stubify(gpu_name), cpu_cores,
                                 cpu_ram_mib)


def normalize_gpu_name(raw: str) -> str:
    """Same gpu-name normalization the offline fetcher applies."""
    gpu = re.sub('Ada', '-Ada', re.sub(r'\s', '', raw))
    gpu = re.sub(r'(Ti|PCIE|SXM4|SXM|NVL)$', '', gpu)
    gpu = re.sub(r'(RTX\d0\d0)(S|D)$', r'\1', gpu)
    return _GPU_RENAME.get(gpu, gpu)


def _dot_get(d: Dict[str, Any], key: str) -> Any:
    cursor: Any = d
    for k in key.split('.'):
        cursor = cursor[k]
    return cursor


def _build_entry(offer: Dict[str, Any]) -> Dict[str, Any]:
    """One row, pre-bucketing. Mirrors the per-offer block of fetch_vast.py."""
    entry: Dict[str, Any] = {}
    for ours, theirs in _MAPPED_KEYS:
        entry[theirs] = _dot_get(offer, ours)

    entry['InstanceType'] = make_instance_type(offer['num_gpus'],
                                               offer['gpu_name'],
                                               offer['cpu_cores'],
                                               offer['cpu_ram'])

    # MemoryGiB: bundle returns MiB.
    entry['MemoryGiB'] = entry['MemoryGiB'] / 1024

    gpu = normalize_gpu_name(offer['gpu_name'])
    entry['AcceleratorName'] = gpu
    entry['GpuInfo'] = json.dumps({
        'Gpus': [{
            'Name': gpu,
            'Count': offer['num_gpus'],
            'MemoryInfo': {
                'SizeInMiB': offer['gpu_total_ram']
            }
        }],
        'TotalGpuMemoryInMiB': offer['gpu_total_ram']
    }).replace('"', '\'')

    return entry


def _bucket_and_dedup(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Median-price bucketing + 'seen' dedup, mirroring fetch_vast.py.

    Returns rows ready for CSV (Price/SpotPrice formatted as 2-decimal
    strings, same as today's vms.csv).
    """
    price_map: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for entry in entries:
        price_map[entry['InstanceType']].append(entry)

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for instance_list in price_map.values():
        prices = sorted([row['Price'] for row in instance_list])
        if not prices:
            continue
        index = math.ceil(0.5 * len(prices)) - 1
        target = prices[index]
        bucket: List[Dict[str, Any]] = []
        for instance in instance_list:
            if instance['Price'] <= target:
                instance['Price'] = '{:.2f}'.format(target)
                bucket.append(instance)
        if not bucket:
            continue
        max_bid = max(row.get('SpotPrice') or 0.0 for row in bucket)
        for instance in bucket:
            hosting_type = instance.get('HostingType', 0)
            stub = (f'{instance["InstanceType"]} '
                    f'{instance["Region"][-2:]} {hosting_type}')
            if stub in seen:
                printstub = f'{stub}#print'
                if printstub not in seen:
                    instance['SpotPrice'] = f'{max_bid:.2f}'
                    out.append(instance)
                    seen.add(printstub)
            else:
                seen.add(stub)
    return out


def build_csv_rows(offers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn raw bundle offers into CSV-ready rows.

    Output order, columns and string-formatted Price/SpotPrice match what
    today's `fetch_vast.py` writes to `vast/vms.csv`.
    """
    raw: List[Dict[str, Any]] = []
    for offer in offers:
        try:
            raw.append(_build_entry(offer))
        except (KeyError, TypeError, ValueError):
            # Fail-open: skip malformed offers instead of aborting the build.
            continue
    return _bucket_and_dedup(raw)


def offers_to_dataframe(offers: Iterable[Dict[str, Any]]):
    """Same data as `build_csv_rows` but typed for runtime use.

    Price/SpotPrice are floats (not strings), and the result is a pandas
    DataFrame with `CSV_FIELDNAMES` columns. Heavy imports are deferred so
    this module stays cheap to import.
    """
    # pylint: disable=import-outside-toplevel
    import pandas as pd
    rows = build_csv_rows(offers)
    df = pd.DataFrame(rows, columns=CSV_FIELDNAMES)
    if df.empty:
        return df
    df['Price'] = df['Price'].astype(float)
    df['SpotPrice'] = df['SpotPrice'].astype(float)
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(float)
    df['vCPUs'] = df['vCPUs'].astype(float)
    df['MemoryGiB'] = df['MemoryGiB'].astype(float)
    df['HostingType'] = df['HostingType'].astype(int)
    return df
