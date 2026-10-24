"""A script that generates the Lium catalog.

Lium (https://lium.io) publishes its live node inventory on an unauthenticated
endpoint, so this fetcher needs no API key and can run on a schedule.

Usage:
    python fetch_lium.py [-h] [--endpoint ENDPOINT]
"""
import argparse
import collections
import csv
import dataclasses
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky.provision.lium import lium_utils

ENDPOINT = 'https://lium.io/api/public/v1/nodes'

CATALOG_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'Price', 'Region', 'GpuInfo', 'SpotPrice'
]


@dataclasses.dataclass(frozen=True)
class _Offer:
    """One catalog row: the cheapest whole node of one shape in one region."""
    instance_type: str
    accelerator_name: str
    accelerator_count: int
    price_per_hour: float
    vcpus: int
    memory_gib: int
    gpu_memory_gib: int
    region: str


def _gpu_info_column(acc_name: str, acc_count: int, memory_gib: int) -> str:
    """Builds the GpuInfo column, in the format the catalog reader expects."""
    memory_mib = memory_gib * 1024
    info: Dict[str, Any] = {
        'Gpus': [{
            'Name': acc_name,
            'Manufacturer': 'NVIDIA',
            'Count': float(acc_count),
            'MemoryInfo': {
                'SizeInMiB': memory_mib
            },
            'TotalGpuMemoryInMiB': memory_mib * acc_count
        }]
    }
    return json.dumps(info).replace('"', '\'')


def _fetch_nodes(endpoint: str) -> List[Dict[str, Any]]:
    response = requests.get(endpoint, timeout=30)
    response.raise_for_status()
    return response.json()['nodes']


def _cheapest_offer_per_type_and_region(
        nodes: List[Dict[str, Any]]) -> List[_Offer]:
    """Keeps the cheapest node per instance type and region.

    A Lium node is rented whole, so one node is one offer, and a node that is
    already in use in part is left out. A spot node is left out too: it can be
    taken back at any time, and SkyPilot rents from Lium as on-demand. Many
    nodes share an instance type inside a region; the catalog holds one row per
    pair, and the cheapest node is the one SkyPilot quotes.
    """
    cheapest: Dict[Tuple[str, str], _Offer] = {}
    for node in nodes:
        acc_name = lium_utils.accelerator_name(node['gpu_model'])
        if acc_name is None:
            continue
        acc_count = node['gpu_count']
        if node['available_gpu_count'] < acc_count:
            continue
        if node['tier'] != lium_utils.SECURE_TIER:
            continue
        shape = lium_utils.InstanceTypeShape(accelerator_name=acc_name,
                                             accelerator_count=acc_count,
                                             vcpus=node['cpu_count'],
                                             memory_gib=node['ram_gb'])
        offer = _Offer(instance_type=lium_utils.make_instance_type(shape),
                       accelerator_name=acc_name,
                       accelerator_count=acc_count,
                       price_per_hour=node['price_per_node_hour'],
                       vcpus=shape.vcpus,
                       memory_gib=shape.memory_gib,
                       gpu_memory_gib=node['gpu_memory_gb'],
                       region=node['country_code'])
        key = (offer.instance_type, offer.region)
        current = cheapest.get(key)
        if current is None or offer.price_per_hour < current.price_per_hour:
            cheapest[key] = offer
    return sorted(cheapest.values(),
                  key=lambda offer: (offer.instance_type, offer.region))


def _catalog_row(offer: _Offer) -> List[Any]:
    """Turns one offer into a catalog row, in the column order above."""
    return [
        offer.instance_type,
        offer.accelerator_name,
        float(offer.accelerator_count),
        float(offer.vcpus),
        float(offer.memory_gib),
        round(offer.price_per_hour, 4),
        offer.region,
        _gpu_info_column(offer.accelerator_name, offer.accelerator_count,
                         offer.gpu_memory_gib),
        '',  # Lium has no spot instances.
    ]


def catalog_rows(endpoint: str = ENDPOINT) -> List[List[Any]]:
    """Reads the node feed and returns the catalog rows it yields."""
    offers = _cheapest_offer_per_type_and_region(_fetch_nodes(endpoint))
    return [_catalog_row(offer) for offer in offers]


def create_catalog(endpoint: str, output_path: str) -> None:
    """Writes the Lium catalog from the public node feed."""
    rows = catalog_rows(endpoint)

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow(CATALOG_COLUMNS)
        writer.writerows(rows)

    counts: 'collections.Counter[str]' = collections.Counter(
        row[1] for row in rows)
    print(f'Wrote {len(rows)} offers to {output_path}: '
          f'{dict(sorted(counts.items()))}')


def _parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint',
                        default=ENDPOINT,
                        help='Lium public node feed URL.')
    parser.add_argument('--output-dir',
                        default='lium',
                        help='Directory to write vms.csv into.')
    return parser.parse_args(args)


if __name__ == '__main__':
    parsed_args = _parse_args()
    os.makedirs(parsed_args.output_dir, exist_ok=True)
    create_catalog(parsed_args.endpoint,
                   os.path.join(parsed_args.output_dir, 'vms.csv'))
