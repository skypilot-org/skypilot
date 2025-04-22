"""A script that generates the Hyperstack Cloud catalog.

Usage:
    python fetch_hyperstack.py [-h] [--api-key API_KEY]
                                    [--api-key-path API_KEY_PATH]

If the --api-key parameter is provided, it will be used, if it is not provided
and the --api-key-path parameter is provided, the --api-key-path will be used,
if none of those parameters are provided,
this script will parse `~/.hyperstack/api_key` to look for Hyperstack API key.
"""
import argparse
import csv
import json
import os
import re
from typing import List

import requests

FLAVORS_ENDPOINT = 'https://infrahub-api.nexgencloud.com/v1/core/flavors'
PRICEBOOK_ENDPOINT = 'https://infrahub-api.nexgencloud.com/v1/pricebook'
DEFAULT_HYPERSTACK_API_KEY_PATH = os.path.expanduser('~/.hyperstack/api_key')


def _get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get Hyperstack API key from cmdline or DEFAULT_HYPERSTACK_API_KEY_PATH.
    """
    api_key_res = cmdline_args.api_key
    if api_key_res is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key_res = f.read().strip()
        else:
            # Read from ~/.hyperstack/api_key
            with open(DEFAULT_HYPERSTACK_API_KEY_PATH,
                      mode='r',
                      encoding='utf-8') as f:
                api_key_res = f.read().strip()
    assert api_key_res is not None
    return api_key_res


def _fetch_flavors(api_key: str) -> List[dict]:
    headers = {
        'api_key': api_key,
        'Accept': 'application/json',
    }
    flavors_response = requests.get(url=FLAVORS_ENDPOINT, headers=headers)
    flavors_response_json = flavors_response.json()
    if flavors_response_json['status'] is True:
        return flavors_response_json['data']
    else:
        raise RuntimeError(flavors_response_json.message)


def _fetch_prices(api_key: str) -> List[dict]:
    headers = {
        'api_key': api_key,
        'Accept': 'application/json',
    }
    prices_response = requests.get(url=PRICEBOOK_ENDPOINT, headers=headers)
    prices_response_json = prices_response.json()
    if prices_response_json:
        return prices_response_json
    else:
        raise RuntimeError('Error fetching the Hyperstack pricebook.')


def _get_gpu_memory(gpu_name: str) -> int:
    gpu_memory = 0
    m = re.match(r'.*-([0-9]+)G-.*', gpu_name)
    if m:
        gpu_memory = int(m.group(1))
    elif gpu_name == 'L40':
        gpu_memory = 48
    elif gpu_name == 'RTX-A4000':
        gpu_memory = 16
    elif gpu_name == 'RTX-A6000':
        gpu_memory = 48
    else:
        raise ValueError(f'Unknown accelerator type: {gpu_name}')

    return gpu_memory


def create_catalog(flavor_group_list: List[dict], price_list: List[dict],
                   output_path: str) -> None:
    print(f'Create catalog file {output_path} based on '
          'the fetched VM flavors and prices.')

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'GpuInfo', 'Price', 'Region', 'SpotPrice'
        ])

        price_map = {}
        for price in price_list:
            price_map[price['name']] = price['value']

        for flavor_group in flavor_group_list:
            gpu = flavor_group['gpu']
            if gpu is not None and gpu != '':
                region_name = flavor_group['region_name']
                gpu_memory = _get_gpu_memory(gpu)
                flavor_list = flavor_group['flavors']
                for flavor in flavor_list:
                    if gpu in price_map:
                        name = flavor['name']
                        gpu_count = flavor['gpu_count']
                        gpu_memory_mb = gpu_memory * 1024
                        gpu_memory_total_mb = gpu_memory_mb * gpu_count
                        price_unit = price_map[gpu]
                        price = gpu_count * float(price_unit)
                        cpu = flavor['cpu']
                        ram = flavor['ram']

                        gpuinfo_dict = {
                            'Gpus': [{
                                'Name': gpu,
                                'Manufacturer': None,
                                'Count': gpu_count,
                                'MemoryInfo': {
                                    'SizeInMiB': gpu_memory_mb
                                },
                            }],
                            'TotalGpuMemoryInMiB': gpu_memory_total_mb
                        }
                        gpuinfo = json.dumps(gpuinfo_dict).replace('"', '\'')

                        writer.writerow([
                            name, gpu, gpu_count, cpu, ram, gpuinfo, price,
                            region_name, ''
                        ])
                    else:
                        # Should not happen but currently there is an
                        # unavailable gpu H200-141G-SXM5 without a price.
                        print(f'The GPU "{gpu}" listed but not found in the '
                              'Hyperstack price list, skipping the GPU!')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Hyperstack API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Hyperstack API key.')

    args = parser.parse_args()
    _api_key = _get_api_key(args)

    os.makedirs('hyperstack', exist_ok=True)
    prices_list = _fetch_prices(api_key=_api_key)
    flavors_list = _fetch_flavors(api_key=_api_key)
    create_catalog(flavors_list, prices_list, 'hyperstack/vms.csv')
    print('Hyperstack Cloud catalog saved to hyperstack/vms.csv')
