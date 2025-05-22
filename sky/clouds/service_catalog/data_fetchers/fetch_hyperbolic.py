"""A script that generates the Hyperbolic Cloud catalog.

Usage:
    python fetch_hyperbolic.py [--api-key API_KEY]
"""

import argparse
import csv
import json
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

    # Deduplicate instances by type and region, keeping the cheapest
    unique_instances = {}
    for instance in instances:
        instance_type = instance.get('InstanceType')
        region = instance.get('Region', 'default')
        key = (instance_type, region)

        # Convert price to float for comparison
        try:
            current_price = float(instance.get('Price', float('inf')))
        except (ValueError, TypeError):
            current_price = float('inf')

        # Keep the instance with the lowest price
        if key not in unique_instances:
            unique_instances[key] = instance
        else:
            existing_price = float(unique_instances[key].get(
                'Price', float('inf')))
            if current_price < existing_price:
                unique_instances[key] = instance

    os.makedirs('hyperbolic', exist_ok=True)
    with open('hyperbolic/vms.csv', 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'InstanceType', 'AcceleratorCount', 'AcceleratorName', 'MemoryGiB',
            'StorageGiB', 'vCPUs', 'Price', 'Region', 'GpuInfo'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for instance in unique_instances.values():
            try:
                entry = instance.copy()
                # Add default region if not present
                if 'Region' not in entry:
                    entry['Region'] = 'default'
                # Use GpuInfo directly from the API response
                entry['GpuInfo'] = json.dumps(instance.get('GpuInfo', {}),
                                              ensure_ascii=False).replace(
                                                  '"', "'")  # pylint: disable=invalid-string-quote
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
