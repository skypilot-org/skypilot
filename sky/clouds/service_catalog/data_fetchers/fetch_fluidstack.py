"""A script that generates the Fluidstack catalog.

Usage:
    python fetch_fluidstack_cloud.py
"""

import csv
import json
import os
from typing import List

import requests

ENDPOINT = 'https://platform.fluidstack.io/list_available_configurations'
DEFAULT_FLUIDSTACK_API_KEY_PATH = os.path.expanduser('~/.fluidstack/api_key')

plan_vcpus_memory = [{
    'gpu_type': 'H100_SXM5_80GB',
    'gpu_count': 1,
    'min_cpu_count': 52,
    'min_memory': 450
}, {
    'gpu_type': 'H100_SXM5_80GB',
    'gpu_count': 2,
    'min_cpu_count': 52,
    'min_memory': 450
}, {
    'gpu_type': 'H100_SXM5_80GB',
    'gpu_count': 4,
    'min_cpu_count': 104,
    'min_memory': 900
}, {
    'gpu_type': 'H100_SXM5_80GB',
    'gpu_count': 8,
    'min_cpu_count': 192,
    'min_memory': 1800
}, {
    'gpu_type': 'RTX_A6000_48GB',
    'gpu_count': 2,
    'min_cpu_count': 12,
    'min_memory': 110.0
}, {
    'gpu_type': 'RTX_A6000_48GB',
    'gpu_count': 4,
    'min_cpu_count': 24,
    'min_memory': 220.0
}, {
    'gpu_type': 'A100_NVLINK_80GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 960.0
}, {
    'gpu_type': 'H100_PCIE_80GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 1440.0
}, {
    'gpu_type': 'RTX_A4000_16GB',
    'gpu_count': 2,
    'min_cpu_count': 12,
    'min_memory': 48.0
}, {
    'gpu_type': 'H100_PCIE_80GB',
    'gpu_count': 2,
    'min_cpu_count': 60,
    'min_memory': 360.0
}, {
    'gpu_type': 'RTX_A6000_48GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 464.0
}, {
    'gpu_type': 'H100_NVLINK_80GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 1440.0
}, {
    'gpu_type': 'H100_PCIE_80GB',
    'gpu_count': 1,
    'min_cpu_count': 28,
    'min_memory': 180.0
}, {
    'gpu_type': 'RTX_A5000_24GB',
    'gpu_count': 1,
    'min_cpu_count': 8,
    'min_memory': 30.0
}, {
    'gpu_type': 'RTX_A5000_24GB',
    'gpu_count': 2,
    'min_cpu_count': 16,
    'min_memory': 60.0
}, {
    'gpu_type': 'L40_48GB',
    'gpu_count': 2,
    'min_cpu_count': 64,
    'min_memory': 120.0
}, {
    'gpu_type': 'RTX_A4000_16GB',
    'gpu_count': 8,
    'min_cpu_count': 48,
    'min_memory': 192.0
}, {
    'gpu_type': 'RTX_A4000_16GB',
    'gpu_count': 1,
    'min_cpu_count': 6,
    'min_memory': 24.0
}, {
    'gpu_type': 'RTX_A4000_16GB',
    'gpu_count': 4,
    'min_cpu_count': 24,
    'min_memory': 96.0
}, {
    'gpu_type': 'A100_PCIE_80GB',
    'gpu_count': 4,
    'min_cpu_count': 124,
    'min_memory': 480.0
}, {
    'gpu_type': 'H100_PCIE_80GB',
    'gpu_count': 4,
    'min_cpu_count': 124,
    'min_memory': 720.0
}, {
    'gpu_type': 'L40_48GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 480.0
}, {
    'gpu_type': 'RTX_A5000_24GB',
    'gpu_count': 8,
    'min_cpu_count': 64,
    'min_memory': 240.0
}, {
    'gpu_type': 'L40_48GB',
    'gpu_count': 1,
    'min_cpu_count': 32,
    'min_memory': 60.0
}, {
    'gpu_type': 'RTX_A6000_48GB',
    'gpu_count': 1,
    'min_cpu_count': 6,
    'min_memory': 55.0
}, {
    'gpu_type': 'L40_48GB',
    'gpu_count': 4,
    'min_cpu_count': 126,
    'min_memory': 240.0
}, {
    'gpu_type': 'A100_PCIE_80GB',
    'gpu_count': 1,
    'min_cpu_count': 28,
    'min_memory': 120.0
}, {
    'gpu_type': 'A100_PCIE_80GB',
    'gpu_count': 8,
    'min_cpu_count': 252,
    'min_memory': 1440.0
}, {
    'gpu_type': 'A100_PCIE_80GB',
    'gpu_count': 2,
    'min_cpu_count': 60,
    'min_memory': 240.0
}, {
    'gpu_type': 'RTX_A5000_24GB',
    'gpu_count': 4,
    'min_cpu_count': 32,
    'min_memory': 120.0
}]

GPU_MAP = {
    'H100_PCIE_80GB': 'H100',
    'H100_NVLINK_80GB': 'H100',
    'A100_NVLINK_80GB': 'A100-80GB',
    'A100_SXM4_80GB': 'A100-80GB-SXM',
    'H100_SXM5_80GB': 'H100-SXM',
    'A100_PCIE_80GB': 'A100-80GB',
    'A100_SXM4_40GB': 'A100',
    'A100_PCIE_40GB': 'A100',
    'Tesla_V100_SXM2_16GB': 'V100',
    'Tesla_V100_PCIE_16GB': 'V100',
    'A10_PCIE_24GB': 'A10',
    'A30_24GB': 'A30',
    'A40_48GB': 'A40',
    'RTX_A6000_48GB': 'RTXA6000',
    'RTX_A5000_24GB': 'RTXA5000',
    'RTX_A4000_16GB': 'RTXA4000',
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
            regions[region] = region
    return regions


def create_catalog(output_dir: str) -> None:
    with open(DEFAULT_FLUIDSTACK_API_KEY_PATH, 'r', encoding='UTF-8') as f:
        api_key = f.read().strip()
    response = requests.get(ENDPOINT, headers={'api-key': api_key})
    plans = response.json()

    with open(os.path.join(output_dir, 'vms.csv'), mode='w',
              encoding='utf-8') as f:
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
                #print(f'Could not map {plan["gpu_type"]}')
                continue
            for gpu_cnt in plan['gpu_counts']:
                gpu_memory = float(plan['gpu_type'].split('_')[-1].replace(
                    'GB', '')) * 1024
                try:
                    vcpus_mem = [
                        x for x in plan_vcpus_memory
                        if x['gpu_type'] == plan['gpu_type'] and
                        x['gpu_count'] == gpu_cnt
                    ][0]
                    vcpus = vcpus_mem['min_cpu_count']
                    mem = vcpus_mem['min_memory']
                except IndexError:
                    continue
                price = float(plan['price_per_gpu_hr']) * gpu_cnt
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
                instance_type = f'{plan["gpu_type"]}::{gpu_cnt}'
                for region in plan.get('regions', []):
                    writer.writerow([
                        instance_type,
                        gpu,
                        gpu_cnt,
                        vcpus,
                        mem,
                        price,
                        region,
                        gpuinfo,
                        '',
                    ])


if __name__ == '__main__':

    os.makedirs('fluidstack', exist_ok=True)
    create_catalog('fluidstack')
    print('Fluidstack catalog saved to {}/vms.csv'.format('fluidstack'))
