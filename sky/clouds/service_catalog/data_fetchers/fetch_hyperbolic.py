"""A script that generates the Hyperbolic Cloud catalog.

Usage:
    python fetch_hyperbolic.py [--api-key API_KEY]
"""

import argparse
import csv
import os

import requests

#TODO update to prod endpoint
ENDPOINT = 'https://api.dev-hyperbolic.xyz/v2/skypilot/catalog'
API_KEY_PATH = os.path.expanduser('~/.hyperbolic/api_key')


def get_api_key() -> str:
    """Get API key from file or command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Hyperbolic API key')
    args = parser.parse_args()

    if args.api_key:
        return args.api_key
    with open(API_KEY_PATH, 'r', encoding='utf-8') as f:
        return f.read().strip()


def create_catalog() -> None:
    """Generate the Hyperbolic catalog CSV file."""
    try:
        response = requests.get(
            ENDPOINT,
            headers={'Authorization': f'Bearer {get_api_key()}'},
            timeout=30)
        if not response.ok:
            raise RuntimeError(f'API request failed: {response.text}')
        instances = response.json()['vms']
    except requests.exceptions.RequestException as request_error:
        raise RuntimeError(f'Failed to fetch instance data: {request_error}'
                          ) from request_error

    os.makedirs('hyperbolic', exist_ok=True)
    with open('hyperbolic/vms.csv', 'w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'vCPUs',
            'MemoryGiB',
            'StorageGiB',
            'Price',
            'Region',
            'GpuInfo',
            'SpotPrice',
        ])
        for instance in instances:
            writer.writerow([
                instance.get('InstanceType'),
                instance.get('AcceleratorName'),
                instance.get('AcceleratorCount'),
                instance.get('vCPUs'),
                instance.get('MemoryGiB'),
                instance.get('StorageGiB'),
                instance.get('Price'),
                instance.get('Region', 'default'),
                str(instance.get('GpuInfo', {})),
                instance.get('SpotPrice', ''),
            ])


if __name__ == '__main__':
    try:
        create_catalog()
        print('Hyperbolic Service Catalog saved to hyperbolic/vms.csv')
    except RuntimeError as catalog_error:
        print(f'Error: {catalog_error}')
        raise
