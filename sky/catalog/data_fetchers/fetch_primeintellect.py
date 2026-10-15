"""Fetch the Prime Intellect catalog from its GPU availability API.

Usage:
    PRIME_API_KEY=... python -m sky.catalog.data_fetchers.fetch_primeintellect

The API key needs only the "Availability: Read" scope. Without --api-key or
PRIME_API_KEY, the key is read from ~/.prime/config.json (`prime login`).

Writes primeintellect/vms.csv (relative to the current directory, or vms.csv
when run from inside a `primeintellect` directory), in the same format the
original static catalog used, plus a `Socket` column that the provisioner
sends back to the API when it creates a pod.

What is kept:
  * secure-cloud offers with an on-demand USD price;
  * one row per (provider, GPU count/type, vCPUs, memory, country, data
    center); identical duplicates keep the cheapest offer.

What is dropped, because SkyPilot cannot launch it as the catalog would claim:
  * spot offers (`isSpot`): the provisioner always creates on-demand pods;
  * prepaid offers (`prepaidTime`): the API pre-charges those hours at order
    time, so the hourly price would understate the cost;
  * community-cloud offers and offers without a country.

SXM offers are named `<GPU>-SXM` (e.g. H100-SXM), the convention RunPod and
Cudo already use in SkyPilot, so `accelerators: H100-SXM:8` selects them and
`accelerators: H100` keeps meaning the PCIe part.
"""
import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional

import requests

ENDPOINT = 'https://api.primeintellect.ai/api/v1/availability/gpus'
CONFIG_PATH = os.path.expanduser('~/.prime/config.json')
PAGE_SIZE = 100
TIMEOUT_SECONDS = 60

FIELDNAMES = [
    'InstanceType', 'UpstreamCloudId', 'vCPUs', 'MemoryGiB', 'AcceleratorName',
    'AcceleratorCount', 'GpuInfo', 'Region', 'AvailabilityZone', 'Price',
    'SpotPrice', 'Socket'
]

CPU_GPU_TYPE = 'CPU_NODE'

# Prime Intellect GPUType -> SkyPilot accelerator name, where stripping the
# trailing `_<N>GB` would collide with, or diverge from, SkyPilot's canonical
# names (catalogs/<version>/common/accelerators.csv).
_GPU_NAME_OVERRIDES = {
    'A100_40GB': 'A100',
    'A100_80GB': 'A100-80GB',
    'V100_32GB': 'V100-32GB',
    'H200_96GB': 'H200-96GB',
    'GH200_96GB': 'GH200',
    'GH200_480GB': 'GH200-480GB',
    'GH200_624GB': 'GH200-624GB',
    'RTX_PRO_6000B_96GB': 'RTXPRO6000',
}
_MEMORY_SUFFIX = re.compile(r'_\d+GB$')


class PrimeIntellectCatalogError(Exception):
    """Raised when the catalog cannot be fetched or written."""


def get_api_key(api_key: Optional[str] = None) -> str:
    if api_key:
        return api_key
    if env_key := os.environ.get('PRIME_API_KEY'):
        return env_key
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            key = json.load(f).get('api_key')
    except (OSError, ValueError) as e:
        raise PrimeIntellectCatalogError(
            'No API key found. Pass --api-key, set PRIME_API_KEY, or run '
            '`prime login`.') from e
    if not key:
        raise PrimeIntellectCatalogError(
            f'{CONFIG_PATH} has no api_key. Pass --api-key or set '
            'PRIME_API_KEY.')
    return key


def accelerator_name(gpu_type: str, socket: Optional[str]) -> str:
    """SkyPilot accelerator name for a Prime Intellect GPU type and socket."""
    name = _GPU_NAME_OVERRIDES.get(gpu_type) or _MEMORY_SUFFIX.sub('', gpu_type)
    if socket and socket.upper().startswith('SXM'):
        name = f'{name}-SXM'
    return name


def _default_count(spec: Any) -> Optional[int]:
    if isinstance(spec, dict) and spec.get('defaultCount') is not None:
        return int(spec['defaultCount'])
    return None


def fetch_offers(
        api_key: str,
        session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Every offer the availability API returns, across all pages."""
    http = session or requests.Session()
    headers = {'Authorization': f'Bearer {api_key}'}
    offers: List[Dict[str, Any]] = []
    page = 1
    while True:
        response = http.get(ENDPOINT,
                            headers=headers,
                            params={
                                'page': page,
                                'page_size': PAGE_SIZE
                            },
                            timeout=TIMEOUT_SECONDS)
        if not response.ok:
            raise PrimeIntellectCatalogError(
                f'GET {ENDPOINT} page {page}: HTTP {response.status_code} '
                f'{response.text[:300]}')
        body = response.json()
        items = body.get('items')
        if not isinstance(items, list):
            raise PrimeIntellectCatalogError(
                f'Unexpected availability response: {str(body)[:300]}')
        offers.extend(items)
        total = body.get('totalCount')
        if not items or (isinstance(total, int) and len(offers) >= total):
            return offers
        page += 1


def offer_to_row(offer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One catalog row for an offer, or None if SkyPilot should not list it."""
    if offer.get('isSpot') or offer.get('prepaidTime'):
        return None
    if offer.get('security') != 'secure_cloud':
        return None
    prices = offer.get('prices') or {}
    price = prices.get('onDemand')
    if price is None or prices.get('currency', 'USD') != 'USD':
        return None
    country = offer.get('country')
    if not country:
        return None
    vcpus = _default_count(offer.get('vcpu'))
    memory = _default_count(offer.get('memory'))
    if not vcpus or not memory:
        return None

    provider = offer['provider']
    gpu_type = offer['gpuType']
    gpu_count = int(offer['gpuCount'])
    socket = offer.get('socket')
    if gpu_type == CPU_GPU_TYPE:
        prefix, acc_name, acc_count, gpu_info = CPU_GPU_TYPE, '', 0, ''
    else:
        prefix = f'{gpu_count}x{gpu_type}'
        acc_name = accelerator_name(gpu_type, socket)
        acc_count = gpu_count
        per_gpu_mib = int(offer['gpuMemory']) * 1024
        gpu_info = json.dumps({
            'Gpus': [{
                'Name': acc_name,
                'Manufacturer': 'AMD'
                                if gpu_type.startswith('MI') else 'NVIDIA',
                'Count': gpu_count,
                'MemoryInfo': {
                    'SizeInMiB': per_gpu_mib
                },
            }],
            'TotalGpuMemoryInMiB': per_gpu_mib * gpu_count,
        })
    return {
        'InstanceType': f'{provider}__{prefix}__{vcpus}__{memory}',
        'UpstreamCloudId': offer['cloudId'],
        'vCPUs': float(vcpus),
        'MemoryGiB': memory,
        'AcceleratorName': acc_name,
        'AcceleratorCount': float(acc_count),
        'GpuInfo': gpu_info,
        'Region': country,
        'AvailabilityZone': offer.get('dataCenter') or 'UNSPECIFIED',
        'Price': float(price),
        'SpotPrice': '',
        'Socket': socket or '',
    }


def build_rows(offers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Catalog rows, deduplicated per (InstanceType, Region, zone).

    Two offers that land on the same key with different sockets (e.g. a
    provider's PCIe and SXM boxes with identical vCPU/memory) both stay: the
    non-PCIe one gets `__<socket>` appended to its InstanceType, which the
    provisioner tolerates (it reads only the first four `__` fields).
    Otherwise the cheapest offer wins.
    """
    best: Dict[tuple, Dict[str, Any]] = {}
    for offer in offers:
        row = offer_to_row(offer)
        if row is None:
            continue
        key = (row['InstanceType'], row['Region'], row['AvailabilityZone'])
        prev = best.get(key)
        if prev is not None and prev['Socket'] != row['Socket']:
            for r in (prev, row):
                if r['Socket'] and r['Socket'] != 'PCIe':
                    r['InstanceType'] = f'{r["InstanceType"]}__{r["Socket"]}'
            best.pop(key)
            for r in (prev, row):
                best[(r['InstanceType'], r['Region'],
                      r['AvailabilityZone'])] = r
            continue
        if prev is None or row['Price'] < prev['Price']:
            best[key] = row
    return sorted(best.values(),
                  key=lambda r: (r['AcceleratorName'], r['AcceleratorCount'], r[
                      'Price'], r['InstanceType'], r['Region']))


def get_output_path() -> str:
    current_dir = os.getcwd()
    if os.path.basename(current_dir) == 'primeintellect':
        return 'vms.csv'
    out_dir = os.path.join(current_dir, 'primeintellect')
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, 'vms.csv')


def write_catalog(rows: List[Dict[str, Any]], path: str) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Fetch the Prime Intellect catalog.')
    parser.add_argument('--api-key',
                        help='Prime Intellect API key (Availability: Read).')
    args = parser.parse_args()
    try:
        rows = build_rows(fetch_offers(get_api_key(args.api_key)))
        if not rows:
            raise PrimeIntellectCatalogError(
                'The availability API returned no launchable offers; '
                'refusing to write an empty catalog.')
        path = get_output_path()
        write_catalog(rows, path)
    except (PrimeIntellectCatalogError, requests.RequestException,
            OSError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    print(f'Prime Intellect catalog ({len(rows)} rows) saved to {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
