"""A script that generates the Lium catalog.

Lium (https://lium.io) publishes its live node inventory on an unauthenticated
endpoint, so this fetcher needs no API key and can run on a schedule.

Usage:
    python fetch_lium.py [-h] [--endpoint ENDPOINT]
"""
import argparse
import collections
import csv
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky.provision.lium import lium_utils

ENDPOINT = 'https://lium.io/api/public/v1/nodes'


def _gpu_info(acc_name: str, acc_count: int, memory_gib: int) -> str:
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


def _cheapest_offer_per_region(
        nodes: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Keeps the cheapest node per instance type and region.

    A Lium node is rented whole, so one node is one offer, and a node that is
    already in use in part is left out. Many nodes share an instance type
    inside a region; the catalog holds one row per pair, and the cheapest node
    is the one SkyPilot quotes.
    """
    cheapest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for node in nodes:
        acc_name = lium_utils.accelerator_name(node['gpu_model'])
        if acc_name is None:
            continue
        acc_count = node['gpu_count']
        if node['available_gpu_count'] < acc_count:
            continue
        region = node['country_code']
        key = (lium_utils.make_instance_type(acc_name, acc_count), region)
        price = node['price_per_node_hour']
        current = cheapest.get(key)
        if current is None or price < current['price']:
            cheapest[key] = {
                'acc_name': acc_name,
                'acc_count': acc_count,
                'price': price,
                'vcpus': node['cpu_count'],
                'memory_gib': node['ram_gb'],
                'gpu_memory_gib': node['gpu_memory_gb'],
                'region': region,
            }
    return cheapest


def create_catalog(endpoint: str, output_path: str) -> None:
    """Writes the Lium catalog from the public node feed."""
    offers = _cheapest_offer_per_region(_fetch_nodes(endpoint))

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])
        for (instance_type, region), offer in sorted(offers.items()):
            writer.writerow([
                instance_type,
                offer['acc_name'],
                float(offer['acc_count']),
                float(offer['vcpus']),
                float(offer['memory_gib']),
                round(offer['price'], 4),
                region,
                _gpu_info(offer['acc_name'], offer['acc_count'],
                          offer['gpu_memory_gib']),
                '',  # Lium has no spot instances.
            ])

    counts: 'collections.Counter[str]' = collections.Counter(
        offer['acc_name'] for offer in offers.values())
    print(f'Wrote {len(offers)} offers to {output_path}: '
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
