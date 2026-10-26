"""A script that generates the Runcrate catalog.

Usage:
    python -m sky.catalog.data_fetchers.fetch_runcrate [--api-key API_KEY]
        [--output OUTPUT]

The API key is read from --api-key, else from the RUNCRATE_API_KEY
environment variable, else from ~/.runcrate/api_key.

Runcrate lists every machine that can be launched right now, each a fixed
shape in one region at an on-demand hourly price. Offers come and go with
capacity, so each catalog row is a shape (GPU type and count, vCPUs, memory)
in a region, at the cheapest price on offer for it.
"""
import argparse
import csv
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky import sky_logging
from sky.provision.runcrate import utils

logger = sky_logging.init_logger(__name__)

_COLUMNS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'Price',
    'Region',
    'GpuInfo',
    'SpotPrice',
]


def get_api_key(api_key: Optional[str]) -> str:
    if api_key:
        return api_key
    api_key = os.environ.get('RUNCRATE_API_KEY')
    if api_key:
        return api_key
    return utils.read_api_key()


def fetch_offers(api_key: str) -> List[Dict[str, Any]]:
    """Every offer that can be launched right now, in one response."""
    response = requests.get(f'{utils.API_URL}/instances/types',
                            headers={'Authorization': f'Bearer {api_key}'},
                            timeout=60)
    response.raise_for_status()
    return response.json()['data']


def make_gpu_info(gpu_type: str, gpu_count: int) -> str:
    name, memory_gib, manufacturer = utils.GPU_TYPES[gpu_type]
    memory_mib = memory_gib * 1024
    gpu_info = {
        'Gpus': [{
            'Name': name,
            'Manufacturer': manufacturer,
            'Count': float(gpu_count),
            'MemoryInfo': {
                'SizeInMiB': memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': memory_mib * gpu_count,
    }
    return json.dumps(gpu_info).replace('"', '\'')


def make_rows(offers: List[Dict[str, Any]]) -> List[List[Any]]:
    """One row per instance type and region, at its cheapest offer."""
    cheapest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for offer in offers:
        if not utils.is_supported_offer(offer):
            if (offer.get('gpu_count') and
                    offer.get('gpu_type') not in utils.GPU_TYPES):
                logger.warning(f'Skipping Runcrate offer {offer.get("id")}: '
                               f'unknown GPU type {offer.get("gpu_type")!r}.')
            continue
        instance_type = utils.instance_type_name(offer)
        assert instance_type is not None, offer
        key = (instance_type, utils.region_name(offer['region']))
        if (key not in cheapest or
                offer['hourly_rate'] < cheapest[key]['hourly_rate']):
            cheapest[key] = offer

    rows = []
    for (instance_type, region), offer in sorted(cheapest.items()):
        if offer['gpu_count']:
            accelerator = utils.GPU_TYPES[offer['gpu_type']][0]
            accelerator_count: Any = float(offer['gpu_count'])
            gpu_info = make_gpu_info(offer['gpu_type'], offer['gpu_count'])
        else:
            accelerator, accelerator_count, gpu_info = '', '', ''
        rows.append([
            instance_type,
            accelerator,
            accelerator_count,
            float(offer['cpu_cores']),
            float(offer['memory_gb']),
            offer['hourly_rate'],
            region,
            gpu_info,
            '',
        ])
    return rows


def create_catalog(api_key: str, output_path: str) -> None:
    rows = make_rows(fetch_offers(api_key))
    if not rows:
        raise RuntimeError('Runcrate returned no offers.')
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow(_COLUMNS)
        writer.writerows(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Runcrate API key.')
    parser.add_argument('--output',
                        default='runcrate/vms.csv',
                        help='Path of the catalog file to write.')
    args = parser.parse_args()
    create_catalog(get_api_key(args.api_key), args.output)
    print(f'Runcrate catalog saved to {args.output}')
