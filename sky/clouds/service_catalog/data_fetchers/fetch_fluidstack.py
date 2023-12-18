"""A script that generates the Fluidstack catalog.

Usage:
    python fetch_lambda_cloud.py [-h] [--api-key API_KEY]
                                 [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.lambda/lambda_keys` to look for Lambda API key.
"""
import argparse
import csv
import json
import os
import requests
from typing import List
from sky.clouds.service_catalog import constants

ENDPOINT = 'https://infinity-skypilot-fbc55e9eacf1.herokuapp.com/v1/plans'
DEFAULT_FLUIDSTACK_API_KEY_PATH = os.path.expanduser(
    '~/.fluidstack/fluidstack_api_key')
DEFAULT_FLUIDSTACK_API_TOKEN_PATH = os.path.expanduser(
    '~/.fluidstack/fluidstack_api_token')

GPU_MAP = {
    'H100_PCIE_80GB': 'H100',
    'A100_SXM4_80GB': 'A100-80GB',
    'A100_PCIE_80GB': 'A100-80GB',
    'A100_SXM4_40GB': 'A100',
    'A100_PCIE_40GB': 'A100',
    'Tesla_V100_SXM2_16GB': 'V100',
    'Tesla_V100_PCIE_16GB': 'V100',
    'A10_PCIE_24GB': 'A10',
    'A30_24GB': 'A30',
    'A40_48GB': 'A40',
    'RTX_A6000_48GB': 'A6000',
    'RTX_A5000_24GB': 'A5000',
    'RTX_A4000_16GB': 'A4000',
    'Quadro_RTX_5000_16GB': 'RTX5000',
    'Quadro_RTX_4000_8GB': 'RTX4000',
    'L40_48GB': 'L40',
    'Quadro_RTX_6000_16GB': 'RTX6000',
    'T4_16GB': 'T4',
    'RTX_3090_24GB': 'RTX3090',
    'RTX_3080_10GB': 'RTX3080',
}


def get_regions(plans: List) -> dict:
    """Return a list of regions where the plan is available."""
    regions = {}
    for plan in plans:
        for region in plan.get('regions', []):
            regions[region['id']] = region['id']
    return regions


def create_catalog(output_dir: str) -> None:
    response = requests.get(ENDPOINT)
    plans = response.json()
    plans = [
        plan for plan in plans if plan['minimum_commitment'] == 'hourly' and
        plan['type'] in ['preconfigured'] and plan['gpu_type'] != 'NO GPU'
    ]

    with open(os.path.join(output_dir, 'vms.csv'), mode='w') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'vCPUs',
            'MemoryGiB',
            'Price',
            'Region',
            'GpuInfo',
            'SpotPrice',
        ])

        for plan in plans:
            try:
                gpu = GPU_MAP[plan['gpu_type']]
            except KeyError:
                print(f'Could not map {plan["gpu_type"]}')
                continue
            gpu_memory = int(
                str(plan['configuration']['gpu_memory']).replace('GB',
                                                                 '')) * 1024
            gpu_cnt = int(plan['configuration']['gpu_count'])
            vcpus = float(plan['configuration']['core_count'])
            mem = float(plan['configuration']['ram'])
            price = float(plan['price']['hourly']) * gpu_cnt
            gpuinfo = {
                'Gpus': [{
                    'Name': gpu,
                    'Manufacturer': 'NVIDIA',
                    'Count': gpu_cnt,
                    'MemoryInfo': {
                        'SizeInMiB': int(gpu_memory)
                    },
                }],
                'TotalGpuMemoryInMiB': int(gpu_memory * gpu_cnt),
            }
            gpuinfo = json.dumps(gpuinfo).replace('"', "'")  # pylint: disable=invalid-string-quote
            for r in plan.get('regions', []):
                writer.writerow([
                    plan['plan_id'],
                    gpu,
                    gpu_cnt,
                    vcpus,
                    mem,
                    price,
                    r['id'],
                    gpuinfo,
                    '',
                ])


# def get_api_keys(cmdline_args: argparse.Namespace) -> str:
#     """Get Fluidstack API key from cmdline or DEFAULT_FLUIDSTACK_API*."""
#     api_key, api_token = None, None
#     if cmdline_args.api_key_path is not None and cmdline_args.api_token_path:
#         with open(cmdline_args.api_key_path, mode='r') as f:
#             api_key = f.read().strip()
#         with open(cmdline_args.api_token_path, mode='r') as f:
#             api_token = f.read().strip()
#     assert api_key is not None
#     assert api_token is not None
#     return api_key, api_token

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    _CATALOG_DIR = os.path.join(constants.LOCAL_CATALOG_DIR,
                                constants.CATALOG_SCHEMA_VERSION)
    catalog_dir = os.path.join(_CATALOG_DIR, 'fluidstack')
    os.makedirs(catalog_dir, exist_ok=True)
    create_catalog(catalog_dir)
    print('Fluidstack Cloud catalog saved to {}/vms.csv'.format(catalog_dir))
