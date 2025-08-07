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
from typing import Dict, List, Optional

import requests

ENDPOINT = 'https://api.shadeform.ai/v1/instances/types'
DEFAULT_SHADEFORM_API_KEY_PATH = os.path.expanduser('~/.shadeform/api_key')

# Datacrunch regions based on user specification
DATACRUNCH_REGIONS = [
    'helsinki-finland-1',
    'helsinki-finland-2',
    'helsinki-finland-5',
    'reykjanesbaer-iceland-1',
]

# GPU memory mapping for common GPU types
# Based on common NVIDIA GPU specifications
GPU_TO_MEMORY = {
    'A100': 40960,  # A100 40GB
    'A100_80G': 81920,  # A100 80GB
    'H100': 81920,  # H100 80GB
    'A6000': 49152,  # RTX A6000 48GB
    'A5000': 24576,  # RTX A5000 24GB
    'A4000': 16384,  # RTX A4000 16GB
    'V100': 16384,  # V100 16GB
    'T4': 16384,  # T4 16GB
    'RTX4090': 24576,  # RTX 4090 24GB
    'RTX3090': 24576,  # RTX 3090 24GB
}


def parse_gpu_info(gpu_type: str, num_gpus: int) -> Dict:
    """Parse GPU information for the catalog."""
    # Handle Shadeform GPU type naming
    gpu_name = gpu_type.replace('_80G', '-80GB').replace('_', ' ')

    # Get memory info, default to 16GB if unknown
    memory_mib = GPU_TO_MEMORY.get(gpu_type, 16384)

    return {
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',  # Assume NVIDIA for now
            'Count': float(num_gpus),
            'MemoryInfo': {
                'SizeInMiB': memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': memory_mib
    }


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Shadeform catalog by fetching from API."""
    headers = {'X-API-KEY': api_key}

    # Filter for datacrunch cloud only and available instances
    params = {'cloud': 'datacrunch', 'available': 'true'}

    response = requests.get(ENDPOINT, headers=headers, params=params)
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

            # Use shade_instance_type as the primary instance type
            instance_type = instance['shade_instance_type']
            gpu_type = config['gpu_type']
            gpu_count = float(config['num_gpus'])
            vcpus = float(config['vcpus'])
            memory_gb = float(config['memory_in_gb'])

            # Price is in cents per hour, convert to dollars
            price = float(instance['hourly_price']) / 100

            # Create GPU info
            gpuinfo = None
            if gpu_count > 0:
                gpuinfo_dict = parse_gpu_info(gpu_type, int(gpu_count))
                gpuinfo = json.dumps(gpuinfo_dict).replace('"', "'")

            # Write entry for each available region
            for availability in instance.get('availability', []):
                if availability['available']:
                    region = availability['region']
                    # Only include datacrunch regions for now
                    if region in DATACRUNCH_REGIONS:
                        writer.writerow([
                            instance_type,
                            gpu_type if gpu_count > 0 else None,
                            gpu_count if gpu_count > 0 else None,
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
    assert api_key is not None, f"API key not found. Please provide via --api-key or place in {DEFAULT_SHADEFORM_API_KEY_PATH}"
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
