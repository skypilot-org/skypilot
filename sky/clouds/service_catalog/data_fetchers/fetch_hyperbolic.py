"""A script that generates the Hyperbolic Cloud catalog.

Usage:
    python fetch_hyperbolic.py [--api-key API_KEY]
"""

import argparse
import csv
import json
import os

import requests

#TODO: Update the endpoint to the correct one
ENDPOINT = 'http://localhost:8080/v2/skypilot/catalog'
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
    with open('hyperbolic/vms.csv', 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'StorageGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for instance in instances:
            try:
                entry = instance.copy()
                gpu_info = {
                    'gpus': [{
                        'name': instance['AcceleratorName'].lower(),
                        'manufacturer': 'nvidia',
                        'count': instance['AcceleratorCount'],
                        'MemoryInfo': {
                            'SizeInMiB': instance['MemoryGiB'] * 1024
                        }
                    }],
                    'totalgpumemoryinmib': instance['MemoryGiB'] * 1024
                }
                entry['GpuInfo'] = json.dumps(gpu_info,
                                              ensure_ascii=False).replace(
                                                  '"', "'")  # pylint: disable=invalid-string-quote
                entry['SpotPrice'] = ''
                writer.writerow(entry)
            except (KeyError, ValueError) as instance_error:
                instance_type = instance.get('InstanceType', 'unknown')
                print(f'Error processing {instance_type}: {instance_error}')


if __name__ == '__main__':
    try:
        create_catalog()
        print('Hyperbolic Service Catalog saved to hyperbolic/vms.csv')
    except RuntimeError as catalog_error:
        print(f'Error: {catalog_error}')
        raise
