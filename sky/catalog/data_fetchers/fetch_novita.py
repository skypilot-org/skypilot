"""A script that generates the Novita catalog.

Usage:
    python fetch_novita.py [-h] [--api-key API_KEY]
                               [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.novita/api_key` to look for Novita API key.
"""
import argparse
import csv
import json
import os
from typing import Dict

import requests

ENDPOINT = 'https://api.novita.ai/gpu-instance/openapi/v1/products'
DEFAULT_NOVITA_API_KEY_PATH = os.path.expanduser('~/.novita/api_key')


def parse_gpu_info(instance: Dict) -> Dict:
    """Parse GPU information for the catalog."""

    manufacturer = 'NVIDIA'
    if instance['name'] == 'MI300X':
        manufacturer = 'AMD'
    elif instance['name'] == 'GAUDI2':
        manufacturer = 'Intel'

    return {
        'GpuInfo': {
            'Id': instance['id'],
            'Name': instance['name'],
            'Manufacturer': manufacturer,
            'Count': 1,
            'MemoryInfo': {
                'SizeInGiB': instance['memoryPerGpu']
            },
            'MinRootFS': instance['minRootFS'],
            'MaxRootFS': instance['maxRootFS']
        }
    }


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Novita catalog by fetching from API."""
    headers = {'Authorization': f'Bearer {api_key}'}

    params = {'available': 'true'}

    print(f'Headers: {headers}')

    response = requests.get(ENDPOINT,
                            headers=headers,
                            params=params,
                            timeout=30)
    response.raise_for_status()

    data = response.json()
    dataArr = data.get('data', [])

    # print(f'dataArr: {dataArr}')

    instance_types = list(filter(lambda x: x.get('availableDeploy', False), dataArr))


    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])

        print(f'instance_types: {instance_types}')

        for instance in instance_types:
            name = instance['name']
            # id = instance['id']
            vcpus = int(instance['cpuPerGpu'])
            memory_gb = int(instance['memoryPerGpu'])
            # Price is in cents per hour, convert to dollars
            price = float(instance['price']) / 100000
            spotPrice = float(instance['spotPrice']) / 100000
            gpu_count = 1
            gpu_info = parse_gpu_info(instance)


            # Write entry for each available region
            for regionItem in instance.get('regions', []):
                if gpu_count > 0:
                    region = regionItem
                    writer.writerow([
                        f'{gpu_count}x_{name}',
                        name,
                        # id,
                        gpu_count,
                        vcpus,
                        memory_gb,
                        price,
                        region,
                        gpu_info,
                        spotPrice
                    ])
                    print(f'wrote row: {f"{gpu_count}x_{name}"}, {name}, {gpu_count}, {vcpus}, {memory_gb}, {price}, {region}, {gpu_info}, {spotPrice}')


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get Novita API key from cmdline or default path."""
    api_key = cmdline_args.api_key
    if api_key is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.novita/api_key
            with open(DEFAULT_NOVITA_API_KEY_PATH,
                      mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
    assert api_key is not None, (
        f'API key not found. Please provide via --api-key or place in '
        f'{DEFAULT_NOVITA_API_KEY_PATH}')
    return api_key


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Novita API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Novita API key.')
    args = parser.parse_args()
    print(f'Fetching Novita catalog from {ENDPOINT}')
    os.makedirs('novita', exist_ok=True)
    create_catalog(get_api_key(args), 'novita/vms.csv')
    print('Novita catalog saved to novita/vms.csv')
