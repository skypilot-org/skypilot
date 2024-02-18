"""A script that generates the Lambda Cloud catalog.

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

ENDPOINT = 'https://cloud.lambdalabs.com/api/v1/instance-types'
DEFAULT_LAMBDA_KEYS_PATH = os.path.expanduser('~/.lambda_cloud/lambda_keys')

# List of all possible regions.
REGIONS = [
    'australia-southeast-1',
    'europe-central-1',
    'asia-south-1',
    'me-west-1',
    'europe-south-1',
    'asia-northeast-1',
    'asia-northeast-2',
    'us-east-1',
    'us-west-2',
    'us-west-1',
    'us-south-1',
    'us-west-3',
    'us-midwest-1',
]

# Source: https://lambdalabs.com/service/gpu-cloud
GPU_TO_MEMORY = {
    'A100': 40960,
    'A100-80GB': 81920,
    'A6000': 49152,
    'A10': 24576,
    'RTX6000': 24576,
    'V100': 16384,
    'H100': 81920,
}


def name_to_gpu(name: str) -> str:
    # Edge case
    if name == 'gpu_8x_a100_80gb_sxm4':
        return 'A100-80GB'
    return name.split('_')[2].upper()


def name_to_gpu_cnt(name: str) -> int:
    return int(name.split('_')[1].replace('x', ''))


def create_catalog(api_key: str, output_path: str) -> None:
    headers = {'Authorization': f'Bearer {api_key}'}
    response = requests.get(ENDPOINT, headers=headers)
    info = response.json()['data']

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])
        # We parse info.keys() in reverse order so gpu_1x_a100_sxm4 comes before
        # gpu_1x_a100 in the catalog (gpu_1x_a100_sxm4 has more availability).
        for vm in reversed(list(info.keys())):
            gpu = name_to_gpu(vm)
            gpu_cnt = float(name_to_gpu_cnt(vm))
            vcpus = float(info[vm]['instance_type']['specs']['vcpus'])
            mem = float(info[vm]['instance_type']['specs']['memory_gib'])
            price = float(info[vm]['instance_type']\
                    ['price_cents_per_hour']) / 100
            gpuinfo = {
                'Gpus': [{
                    'Name': gpu,
                    'Manufacturer': 'NVIDIA',
                    'Count': gpu_cnt,
                    'MemoryInfo': {
                        'SizeInMiB': GPU_TO_MEMORY[gpu]
                    },
                }],
                'TotalGpuMemoryInMiB': GPU_TO_MEMORY[gpu]
            }
            gpuinfo = json.dumps(gpuinfo).replace('"', "'")  # pylint: disable=invalid-string-quote
            for r in REGIONS:
                writer.writerow(
                    [vm, gpu, gpu_cnt, vcpus, mem, price, r, gpuinfo, ''])


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get Lambda API key from cmdline or DEFAULT_LAMBDA_KEYS_PATH."""
    api_key = cmdline_args.api_key
    if api_key is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.lambda_cloud/lambda_keys
            with open(DEFAULT_LAMBDA_KEYS_PATH, mode='r',
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
    parser.add_argument('--api-key', help='Lambda API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Lambda API key.')
    args = parser.parse_args()
    os.makedirs('lambda', exist_ok=True)
    create_catalog(get_api_key(args), 'lambda/vms.csv')
    print('Lambda Cloud catalog saved to lambda/vms.csv')
