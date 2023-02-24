"""A script that generates the Lambda Cloud catalog.

Usage:
    python fetch_lambda_cloud.py [-h] [--api-key API_KEY]
                                 [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.lambda/lambda_keys` to look for Lambda api key.
"""
import argparse
import csv
import json
import os
import requests

ENDPOINT = 'https://cloud.lambdalabs.com/api/v1/instance-types'
LAMBDA_KEYS_PATH = os.path.expanduser('~/.lambda_cloud/lambda_keys')

# This is the list that Lambda Labs gave us.
regions = [
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
]

gpu_to_memory = {
    'A100': 40960,
    'A100-80GB': 81920,
    'A6000': 49152,
    'A10': 24576,
    'RTX6000': 24576,
    'V100': 16384,
}


def name_to_gpu(name: str) -> str:
    # Edge case
    if name == 'gpu_8x_a100_80gb_sxm4':
        return 'A100-80GB'
    return name.split('_')[2].upper()


def name_to_act(name: str) -> int:
    return int(name.split('_')[1].replace('x', ''))


def create_catalog(
        api_key: str,  # pylint: disable=redefined-outer-name
        output_path: str) -> None:
    headers = {'Authorization': f'Bearer {api_key}'}
    response = requests.get(ENDPOINT, headers=headers)
    info = response.json()['data']

    with open(output_path, mode='w') as f:  # pylint: disable=redefined-outer-name
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])
        # We parse info.keys() in reverse order so gpu_1x_a100_sxm4 comes before
        # gpu_1x_a100 in the catalog (gpu_1x_a100_sxm4 has more availability).
        for vm in reversed(list(info.keys())):
            aname = name_to_gpu(vm)
            act = name_to_act(vm) * 1.0
            vcpus = info[vm]['instance_type']['specs']['vcpus'] * 1.0
            mem = info[vm]['instance_type']['specs']['memory_gib'] * 1.0
            price = info[vm]['instance_type']\
                    ['price_cents_per_hour'] * 1.0 / 100
            gpuinfo = {
                'Gpus': [{
                    'Name': aname,
                    'Manufacturer': 'NVIDIA',
                    'Count': act,
                    'MemoryInfo': {
                        'SizeInMiB': gpu_to_memory[aname]
                    },
                }],
                'TotalGpuMemoryInMiB': gpu_to_memory[aname]
            }
            gpuinfo = json.dumps(gpuinfo).replace('"', "'")  # pylint: disable=invalid-string-quote
            for r in regions:
                writer.writerow(
                    [vm, aname, act, vcpus, mem, price, r, gpuinfo, ''])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Lambda API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Lambda API key.')
    args = parser.parse_args()

    api_key = args.api_key
    if api_key is None:
        if args.api_key_path is not None:
            with open(args.api_key_path, mode='r') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.lambda_cloud/lambda_keys
            with open(LAMBDA_KEYS_PATH, mode='r') as f:
                lines = [
                    line.strip() for line in f.readlines() if ' = ' in line
                ]
                for line in lines:
                    if line.split(' = ')[0] == 'api_key':
                        api_key = line.split(' = ')[1]
                        break
    assert api_key is not None
    os.makedirs('lambda', exist_ok=True)
    create_catalog(api_key, 'lambda/vms.csv')
    print('Lambda Cloud catalog saved to lambda/vms.csv')
