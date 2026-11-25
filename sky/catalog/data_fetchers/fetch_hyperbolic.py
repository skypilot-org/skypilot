"""Script to fetch Hyperbolic virtual machine offerings and generate catalog.

Uses the public `GET /v2/on-demand/rental-options` endpoint, which does not
require an API key.
"""
import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

import requests

from sky.provision.hyperbolic import utils

ENDPOINT = f'{utils.BASE_URL}/v2/on-demand/rental-options'

FIELDS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'StorageGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
]

_GPU_TYPE_TO_ACCELERATOR = {
    gpu_type: acc for acc, gpu_type in utils.ACCELERATOR_TO_GPU_TYPE.items()
}
_GPU_MEMORY_GIB = {'H100': 80, 'H200': 141, 'B200': 180, 'RTXPRO6000': 96}


class HyperbolicCatalogError(Exception):
    """Base exception for Hyperbolic catalog errors."""
    pass


def get_output_path() -> str:
    """Get output path for catalog file."""
    current_dir = os.getcwd()
    if os.path.basename(current_dir) == 'hyperbolic':
        return 'vms.csv'
    hyperbolic_dir = os.path.join(current_dir, 'hyperbolic')
    os.makedirs(hyperbolic_dir, exist_ok=True)
    return os.path.join(hyperbolic_dir, 'vms.csv')


def options_to_rows(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts rental options to catalog rows, cheapest per type/region."""
    rows: Dict[Any, Dict[str, Any]] = {}
    for option in options:
        if (option.get('machineType') != utils.RENTAL_TYPE or
                not option.get('enabled')):
            continue
        accelerator = _GPU_TYPE_TO_ACCELERATOR.get(option['gpuType'])
        if accelerator is None:
            print(f'Skipping unknown GPU type {option["gpuType"]!r}',
                  file=sys.stderr)
            continue
        gpu_memory_gib = _GPU_MEMORY_GIB[accelerator]
        count = int(option['gpuCount'])
        node = option['nodes'][0]
        vcpus = int(node['vcpuCount'])
        memory = int(node['ramGb'])
        gpu_info = {
            'Gpus': [{
                'Name': accelerator,
                'Manufacturer': 'NVIDIA',
                'Count': count,
                'MemoryInfo': {
                    'SizeInMiB': gpu_memory_gib * 1024
                },
            }],
            'TotalGpuMemoryInMiB': gpu_memory_gib * 1024 * count,
        }
        row = {
            'InstanceType': f'{count}x-{accelerator}-{vcpus}-{memory}',
            'AcceleratorName': accelerator,
            'AcceleratorCount': count,
            'vCPUs': vcpus,
            'MemoryGiB': memory,
            'StorageGiB': int(node['storageGb']),
            'Price': option['costPerHourCents'] / 100,
            'Region': option['region'],
            # Single quotes, matching the format of other catalogs.
            'GpuInfo': json.dumps(gpu_info).replace('"', "'"),  # pylint: disable=invalid-string-quote
            'SpotPrice': '',
        }
        key = (row['InstanceType'], row['Region'])
        if key not in rows or row['Price'] < rows[key]['Price']:
            rows[key] = row
    return sorted(
        rows.values(),
        key=lambda r:
        (r['AcceleratorName'], r['AcceleratorCount'], r['Price'], r['Region']))


def create_catalog() -> None:
    """Generate Hyperbolic catalog CSV file."""
    try:
        response = requests.get(ENDPOINT, timeout=30)
        response.raise_for_status()
        options = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        raise HyperbolicCatalogError(
            f'Failed to fetch rental options: {e}') from e
    if not isinstance(options, list):
        raise HyperbolicCatalogError(
            f'Expected a list of rental options, got: {options}')
    rows = options_to_rows(options)
    if not rows:
        raise HyperbolicCatalogError('No virtual machine offerings found.')

    output_path = get_output_path()
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch Hyperbolic instance data')
    # Rental options are public; the flag is accepted for compatibility with
    # existing catalog workflows.
    parser.add_argument('--api-key', help='Unused.')
    parser.parse_args()

    try:
        create_catalog()
    except (HyperbolicCatalogError, OSError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    print(f'Hyperbolic Service Catalog saved to {get_output_path()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
