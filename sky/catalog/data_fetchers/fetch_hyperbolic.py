"""Script to fetch Hyperbolic instance data and generate catalog."""
import argparse
import csv
import json
import os
import sys
from typing import Any, Dict

import requests

ENDPOINT = 'https://api.hyperbolic.xyz/v2/skypilot/catalog'
API_KEY_PATH = os.path.expanduser('~/.hyperbolic/api_key')

REQUIRED_FIELDS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'StorageGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
]


class HyperbolicCatalogError(Exception):
    """Base exception for Hyperbolic catalog errors."""
    pass


def get_api_key(api_key=None) -> str:
    """Get API key from arg, env var, or file."""
    if api_key:
        return api_key
    if api_key := os.environ.get('HYPERBOLIC_API_KEY'):
        return api_key
    try:
        with open(API_KEY_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError as exc:
        raise HyperbolicCatalogError(
            'No API key found. Please either:\n'
            '1. Pass --api-key\n'
            '2. Set HYPERBOLIC_API_KEY environment variable\n'
            '3. Create ~/.hyperbolic/api_key file') from exc


def get_output_path() -> str:
    """Get output path for catalog file."""
    current_dir = os.getcwd()
    if os.path.basename(current_dir) == 'hyperbolic':
        return 'vms.csv'
    hyperbolic_dir = os.path.join(current_dir, 'hyperbolic')
    os.makedirs(hyperbolic_dir, exist_ok=True)
    return os.path.join(hyperbolic_dir, 'vms.csv')


def validate_instance_data(instance: Dict[str, Any]) -> None:
    """Validate instance data has all required fields."""
    missing_fields = [
        field for field in REQUIRED_FIELDS if field not in instance
    ]
    if missing_fields:
        raise HyperbolicCatalogError(
            f'Instance data missing required fields: {missing_fields}')


def create_catalog(api_key=None) -> None:
    """Generate Hyperbolic catalog CSV file."""
    try:
        response = requests.get(
            ENDPOINT,
            headers={'Authorization': f'Bearer {get_api_key(api_key)}'},
            timeout=30)
        response.raise_for_status()

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise HyperbolicCatalogError(
                f'Invalid JSON response from API: {response.text}') from e

        if 'vms' not in data:
            raise HyperbolicCatalogError(
                f'Missing "vms" field in API response: {data}')

        instances = data['vms']
        if not isinstance(instances, list):
            raise HyperbolicCatalogError(
                f'Expected list of instances, got {type(instances)}')

        if not instances:
            raise HyperbolicCatalogError('No instances found in API response')

        # Validate each instance
        for instance in instances:
            validate_instance_data(instance)

    except requests.exceptions.RequestException as e:
        raise HyperbolicCatalogError(
            f'Failed to fetch instance data: {e}') from e

    output_path = get_output_path()
    try:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=REQUIRED_FIELDS)
            writer.writeheader()

            for instance in instances:
                entry = instance.copy()
                # Convert GpuInfo to string format
                entry['GpuInfo'] = json.dumps(entry['GpuInfo'],
                                              ensure_ascii=False).replace(
                                                  '"', "'")  # pylint: disable=invalid-string-quote
                writer.writerow(entry)
    except (IOError, OSError) as e:
        raise HyperbolicCatalogError(
            f'Failed to write catalog file to {output_path}: {e}') from e


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch Hyperbolic instance data')
    parser.add_argument('--api-key', help='Hyperbolic API key')
    args = parser.parse_args()

    try:
        create_catalog(args.api_key)
        print(f'Hyperbolic Service Catalog saved to {get_output_path()}')
        return 0
    except HyperbolicCatalogError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    except (requests.exceptions.RequestException, json.JSONDecodeError, IOError,
            OSError) as e:
        print(f'Unexpected error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
