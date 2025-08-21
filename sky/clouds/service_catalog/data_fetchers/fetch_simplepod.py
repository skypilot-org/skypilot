"""A script that generates the SimplePod Cloud catalog.

Usage:
    python fetch_simplepod_cloud.py [-h] [--api-key API_KEY]
                                   [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.simplepod_cloud/simplepod_keys` to look for SimplePod API key.
"""
import argparse
import csv
import json
import os
from typing import Optional

import requests

ENDPOINT = 'https://api.simplepod.ai/instances/market/list?rentalStatus=active&itemsPerPage=1000'
DEFAULT_SIMPLEPOD_KEYS_PATH = os.path.expanduser('~/.simplepod/simplepod_keys')


def create_catalog(DEFAULT_SIMPLEPOD_KEYS_PATH: str, output_path: str) -> None:
    headers = {'X-AUTH-TOKEN': DEFAULT_SIMPLEPOD_KEYS_PATH}
    response = requests.get(ENDPOINT, headers=headers)
    instances = response.json()

    seen_instances = set()  # Track unique instances

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo'
        ])

        for instance in instances:
            gpu_model = instance['gpuModel']
            gpu_count = int(instance['gpuCount'])
            vcpus = int(float(instance['cpuCoreCount']))  # Convert to int
            memory = int(float(instance['systemMemory']) /
                         1024)  # Convert to int GiB
            price = float(instance['pricePerGpu'])

            # Region handling
            region = f"{instance['rig']['region']}-{instance['rig']['country']}"

            gpuinfo_dict = {
                'Gpus': [{
                    'Name': gpu_model,
                    'Manufacturer': 'NVIDIA',
                    'Count': gpu_count,
                    'MemoryInfo': {
                        'SizeInMiB': instance['gpuMemorySize']
                    },
                }],
                'TotalGpuMemoryInMiB': instance['gpuMemorySize']
            }
            gpuinfo = json.dumps(gpuinfo_dict).replace('"', "'")

            # Create a unique identifier for the instance
            instance_key = (gpu_model, gpu_count, vcpus, memory, price, region)

            if instance_key not in seen_instances:
                seen_instances.add(instance_key)  # Mark as seen
                writer.writerow([
                    f"gpu_{gpu_count}x_{gpu_model.lower().replace(' ', '')}_{vcpus}",  # InstanceType now includes vCPUs
                    gpu_model.replace(" ", ""),  # AcceleratorName
                    gpu_count,  # AcceleratorCount
                    vcpus,  # vCPUs
                    memory,  # MemoryGiB
                    f"{price:.3f}",  # Price with 3 decimal places
                    region,  # Region
                    gpuinfo,  # GpuInfo
                ])


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get SimplePod API key from cmdline or DEFAULT_SIMPLEPOD_KEYS_PATH."""
    api_key = cmdline_args.api_key
    if api_key is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.simplepod_cloud/simplepod_keys
            with open(DEFAULT_SIMPLEPOD_KEYS_PATH, mode='r',
                      encoding='utf-8') as f:
                lines = [
                    line.strip() for line in f.readlines() if ' = ' in line
                ]
                for line in lines:
                    if line.split(' = ')[0] == 'api_key':
                        api_key = line.split(' = ')[1]
                        break
    assert api_key is not None
    return api_key


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='SimplePod API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing SimplePod API key.')
    args = parser.parse_args()
    os.makedirs('simplepod', exist_ok=True)
    create_catalog(get_api_key(args), 'simplepod/vms.csv')
    print('SimplePod Cloud catalog saved to simplepod/vms.csv')
