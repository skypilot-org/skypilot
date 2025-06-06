"""Script to fetch Hyperbolic instance data and generate catalog."""
import argparse
import csv
import json
import os
from typing import Optional, Tuple

import requests

ENDPOINT = 'https://api.dev-hyperbolic.xyz/v2/skypilot/catalog'
API_KEY_PATH = os.path.expanduser('~/.hyperbolic/api_key')


def get_api_key(api_key: Optional[str] = None) -> str:
    """Get the Hyperbolic API key from environment, file, or argument.
    Priority order:
    1. Command line argument (--api-key)
    2. Environment variable (HYPERBOLIC_API_KEY)
    3. Local file (~/.hyperbolic/api_key)
    """
    if api_key is not None:
        return api_key

    api_key_env = os.environ.get('HYPERBOLIC_API_KEY')
    if api_key_env is not None:
        return api_key_env

    try:
        with open(API_KEY_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError as exc:
        raise RuntimeError('No API key found. Please either:\n'
                           '1. Pass --api-key\n'
                           '2. Set HYPERBOLIC_API_KEY environment variable\n'
                           '3. Create ~/.hyperbolic/api_key file') from exc
    except Exception as e:
        raise RuntimeError(
            f'Error reading API key from {API_KEY_PATH}: {e}') from e


def get_output_path() -> Tuple[str, str]:
    """Get the output path for the catalog file.
    
    Returns:
        Tuple[str, str]: (output_path, display_path)
            - output_path: Path to write the file to
            - display_path: Path to show in messages
    """
    if os.path.basename(os.getcwd()) == 'hyperbolic':
        return 'vms.csv', 'vms.csv'
    os.makedirs('hyperbolic', exist_ok=True)
    return 'hyperbolic/vms.csv', 'hyperbolic/vms.csv'


def create_catalog(api_key: Optional[str] = None) -> None:
    """Generate the Hyperbolic catalog CSV file."""
    try:
        response = requests.get(
            ENDPOINT,
            headers={'Authorization': f'Bearer {get_api_key(api_key)}'},
            timeout=30)
        if not response.ok:
            raise RuntimeError(f'API request failed: {response.text}')

        # Parse response as JSON
        try:
            data = response.json()

            if not isinstance(data, dict):
                raise RuntimeError(
                    f'Expected dictionary response, got {type(data)}')

            # Extract instances from the response
            instances = data.get('vms', [])

            if not isinstance(instances, list):
                raise RuntimeError(f'Expected list of instances in response, '
                                   f'got {type(instances)}')

        except json.JSONDecodeError as e:
            raise RuntimeError(
                f'Failed to parse API response as JSON: {e}') from e

    except requests.exceptions.RequestException as request_error:
        raise RuntimeError(f'Failed to fetch instance data: {request_error}'
                          ) from request_error

    output_path, _ = get_output_path()
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'StorageGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for instance in instances:
            try:
                if not isinstance(instance, dict):
                    continue

                entry = instance.copy()
                # Use existing GpuInfo if available, otherwise create it
                if 'GpuInfo' not in entry:
                    gpu_info = {
                        'Gpus': [{
                            'Name': instance['AcceleratorName'],
                            'Manufacturer': 'NVIDIA',
                            'Count': instance['AcceleratorCount'],
                            'MemoryInfo': {
                                'SizeInMiB': instance['MemoryGiB'] * 1024
                            }
                        }],
                        'TotalGpuMemoryInMiB': instance['MemoryGiB'] * 1024
                    }
                    entry['GpuInfo'] = json.dumps(gpu_info,
                                                  ensure_ascii=False).replace(
                                                      '"', "'")  # pylint: disable=invalid-string-quote
                entry['SpotPrice'] = ''
                writer.writerow(entry)
            except (KeyError, ValueError) as instance_error:
                instance_type = instance.get('InstanceType', 'unknown')
                print(f'Error processing {instance_type}: {instance_error}')


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch Hyperbolic instance data')
    parser.add_argument(
        '--api-key',
        help='Hyperbolic API key. '
        'If not provided, will try environment variable or local file')
    args = parser.parse_args()

    create_catalog(args.api_key)
    _, display_path = get_output_path()
    print(f'Hyperbolic Service Catalog saved to {display_path}')


if __name__ == '__main__':
    main()
