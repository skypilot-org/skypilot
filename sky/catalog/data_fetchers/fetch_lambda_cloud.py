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
from typing import Optional, Tuple

import requests

ENDPOINT = 'https://cloud.lambdalabs.com/api/v1/instance-types'
DEFAULT_LAMBDA_KEYS_PATH = os.path.expanduser('~/.lambda_cloud/lambda_keys')

# List of all possible regions.
REGIONS = [
    'europe-central-1',
    'asia-south-1',
    'me-west-1',
    'europe-south-1',
    'asia-northeast-1',
    'asia-northeast-2',
    'australia-east-1',
    'us-east-1',
    'us-east-2',
    'us-east-3',
    'us-west-2',
    'us-west-1',
    'us-south-1',
    'us-south-2',
    'us-south-3',
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
    'GH200': 98304,
    'B200': 184320,  # 180 GB
    'GENERAL': None
}


def name_to_gpu_and_cnt(name: str) -> Optional[Tuple[str, int]]:
    """Extract GPU and count from instance type name.

    The instance type name is in the format:
      'gpu_{gpu_count}x_{gpu_name}_<suffix>'.
    """
    # Edge case
    if name == 'gpu_8x_a100_80gb_sxm4':
        return 'A100-80GB', 8
    gpu = name.split('_')[2].upper()
    if gpu == 'GENERAL':
        return None
    gpu_cnt = int(name.split('_')[1].replace('x', ''))
    return gpu, gpu_cnt


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
            gpu_and_cnt = name_to_gpu_and_cnt(vm)
            gpu: Optional[str]
            gpu_cnt: Optional[float]
            if gpu_and_cnt is None:
                gpu, gpu_cnt = None, None
            else:
                gpu = gpu_and_cnt[0]
                gpu_cnt = float(gpu_and_cnt[1])
            vcpus = float(info[vm]['instance_type']['specs']['vcpus'])
            mem = float(info[vm]['instance_type']['specs']['memory_gib'])
            price = (float(info[vm]['instance_type']['price_cents_per_hour']) /
                     100)
            gpuinfo: Optional[str] = None
            if gpu is not None:
                gpuinfo_dict = {
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
                gpuinfo = json.dumps(gpuinfo_dict).replace('"', "'")  # pylint: disable=invalid-string-quote
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
