"""A script that generates the Shadeform catalog.

Usage:
    python fetch_shadeform.py [-h] [--api-key API_KEY]
                               [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.shadeform/api_key` to look for Shadeform API key.
"""
import argparse
import csv
import json
import os
from typing import Dict

import requests

ENDPOINT = 'https://api.shadeform.ai/v1/instances/types'
DEFAULT_SHADEFORM_API_KEY_PATH = os.path.expanduser('~/.shadeform/api_key')


def parse_gpu_info(gpu_type: str, num_gpus: int, ram_per_gpu: int) -> Dict:
    """Parse GPU information for the catalog."""

    manufacturer = 'NVIDIA'
    if gpu_type == 'MI300X':
        manufacturer = 'AMD'
    elif gpu_type == 'GAUDI2':
        manufacturer = 'Intel'

    return {
        'Gpus': [{
            'Name': gpu_type,
            'Manufacturer': manufacturer,
            'Count': float(num_gpus),
            'MemoryInfo': {
                'SizeInMiB': ram_per_gpu
            },
            'TotalGpuMemoryInMiB': ram_per_gpu * num_gpus
        }]
    }


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Shadeform catalog by fetching from API."""
    headers = {'X-API-KEY': api_key}

    params = {'available': 'true'}

    response = requests.get(ENDPOINT,
                            headers=headers,
                            params=params,
                            timeout=30)
    response.raise_for_status()

    data = response.json()
    instance_types = data.get('instance_types', [])

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])

        for instance in instance_types:
            config = instance['configuration']

            cloud = instance['cloud']
            shade_instance_type = instance['shade_instance_type']
            instance_type = f'{cloud}_{shade_instance_type.replace("_", "-")}'
            gpu_type = config['gpu_type'].replace('_', '-')
            gpu_count = float(config['num_gpus'])
            vcpus = float(config['vcpus'])
            memory_gb = int(config['memory_in_gb'])

            # Append "B" to instance_type and gpu_type if they end with "G"
            if instance_type.endswith('G'):
                instance_type += 'B'
            if gpu_type.endswith('G'):
                gpu_type += 'B'

            # Replace "Gx" with "GBx" (case sensitive)
            if 'Gx' in instance_type:
                instance_type = instance_type.replace('Gx', 'GBx')

            # Price is in cents per hour, convert to dollars
            price = float(instance['hourly_price']) / 100

            # Create GPU info
            gpuinfo = None
            if gpu_count > 0:
                gpuinfo_dict = parse_gpu_info(gpu_type, int(gpu_count),
                                              int(config['vram_per_gpu_in_gb']))
                gpuinfo = json.dumps(gpuinfo_dict).replace('"', '\'')

            # Write entry for each available region
            for availability in instance.get('availability', []):
                if availability['available'] and gpu_count > 0:
                    region = availability['region']
                    writer.writerow([
                        instance_type,
                        gpu_type,
                        gpu_count,
                        vcpus,
                        memory_gb,
                        price,
                        region,
                        gpuinfo,
                        ''  # No spot pricing info available
                    ])


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get Shadeform API key from cmdline or default path."""
    api_key = cmdline_args.api_key
    if api_key is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.shadeform/api_key
            with open(DEFAULT_SHADEFORM_API_KEY_PATH,
                      mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
    assert api_key is not None, (
        f'API key not found. Please provide via --api-key or place in '
        f'{DEFAULT_SHADEFORM_API_KEY_PATH}')
    return api_key


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Shadeform API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Shadeform API key.')
    args = parser.parse_args()
    os.makedirs('shadeform', exist_ok=True)
    create_catalog(get_api_key(args), 'shadeform/vms.csv')
    print('Shadeform catalog saved to shadeform/vms.csv')
