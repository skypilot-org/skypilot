"""Script to fetch Hyperbolic instance data and generate catalog."""
import argparse
import csv
import json
import os

import requests

ENDPOINT = 'https://api.dev-hyperbolic.xyz/v2/skypilot/catalog'
API_KEY_PATH = os.path.expanduser('~/.hyperbolic/api_key')


def get_api_key(api_key=None):
    """Get API key from arg, env var, or file."""
    if api_key:
        return api_key
    if api_key := os.environ.get('HYPERBOLIC_API_KEY'):
        return api_key
    try:
        with open(API_KEY_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError as exc:
        raise RuntimeError('No API key found. Please either:\n'
                           '1. Pass --api-key\n'
                           '2. Set HYPERBOLIC_API_KEY environment variable\n'
                           '3. Create ~/.hyperbolic/api_key file') from exc


def get_output_path():
    """Get output path for catalog file."""
    current_dir = os.getcwd()
    if os.path.basename(current_dir) == 'hyperbolic':
        return 'vms.csv'
    hyperbolic_dir = os.path.join(current_dir, 'hyperbolic')
    os.makedirs(hyperbolic_dir, exist_ok=True)
    return os.path.join(hyperbolic_dir, 'vms.csv')


def create_catalog(api_key=None):
    """Generate Hyperbolic catalog CSV file."""
    try:
        response = requests.get(
            ENDPOINT,
            headers={'Authorization': f'Bearer {get_api_key(api_key)}'},
            timeout=30)
        response.raise_for_status()
        data = response.json()
        instances = data['vms']

    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        raise RuntimeError(f'Failed to fetch instance data: {e}') from e

    output_path = get_output_path()
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f,
                                fieldnames=[
                                    'InstanceType', 'AcceleratorName',
                                    'AcceleratorCount', 'vCPUs', 'MemoryGiB',
                                    'StorageGiB', 'Price', 'Region', 'GpuInfo',
                                    'SpotPrice'
                                ])
        writer.writeheader()

        for instance in instances:
            entry = instance.copy()
            # Convert GpuInfo to string format
            entry['GpuInfo'] = json.dumps(entry['GpuInfo'],
                                          ensure_ascii=False).replace('"', "'")  # pylint: disable=invalid-string-quote
            writer.writerow(entry)


def main():
    parser = argparse.ArgumentParser(
        description='Fetch Hyperbolic instance data')
    parser.add_argument('--api-key', help='Hyperbolic API key')
    args = parser.parse_args()

    create_catalog(args.api_key)
    print(f'Hyperbolic Service Catalog saved to {get_output_path()}')


if __name__ == '__main__':
    main()
