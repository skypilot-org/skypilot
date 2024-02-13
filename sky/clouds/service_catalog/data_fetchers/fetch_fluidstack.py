"""A script that generates the Fluidstack catalog.

Usage:
    python fetch_fluidstack_cloud.py
"""

import copy
import csv
import json
import os
from typing import List

import requests

ENDPOINT = 'https://api.fluidstack.io/v1/plans'
DEFAULT_FLUIDSTACK_API_KEY_PATH = os.path.expanduser('~/.fluidstack/api_key')
DEFAULT_FLUIDSTACK_API_TOKEN_PATH = os.path.expanduser(
    '~/.fluidstack/api_token')

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

CUSTOM_PLANS_CONFIG = [
    dict(gpu_count=1, cpu_count=8, nvme_storage=750, ram=64),
    dict(gpu_count=2, cpu_count=16, nvme_storage=1024, ram=128),
    #dict(gpu_count=4, cpu_count=32, nvme_storage=1200, ram=160),
    #dict(gpu_count=8, cpu_count=64, nvme_storage=1500, ram=200)
]


def get_regions(plans: List) -> dict:
    """Return a list of regions where the plan is available."""
    regions = {}
    for plan in plans:
        for region in plan.get('regions', []):
            regions[region['id']] = region['id']
    return regions


def plans_from_custom_plan(plan: dict) -> List[dict]:
    prices = dict(cpu=plan['price']['cpu']['hourly'],
                  ram=plan['price']['ram']['hourly'],
                  storage=plan['price']['storage']['hourly'],
                  gpu=plan['price']['gpu']['hourly'])
    new_plans = []
    for i, config in enumerate(CUSTOM_PLANS_CONFIG):
        new_plan = copy.deepcopy(plan)
        price = (prices['cpu'] *
                 config['cpu_count']) + (prices['ram'] * config['ram']) + (
                     prices['storage'] * config['nvme_storage']) + (
                         prices['gpu'] * config['gpu_count'])
        new_plan['price']['hourly'] = price / config['gpu_count']
        new_plan['configuration']['core_count'] = config['cpu_count']
        new_plan['configuration']['ram'] = config['ram']
        new_plan['configuration']['gpu_count'] = config['gpu_count']
        new_plan['configuration']['nvme_storage'] = config['nvme_storage']
        new_plan['plan_id'] = f'custom:{i}:{plan["plan_id"]}'
        new_plans.append(new_plan)
    return new_plans


def create_catalog(output_dir: str) -> None:
    response = requests.get(ENDPOINT)
    plans = response.json()
    custom_plans = [
        plan for plan in plans if plan['minimum_commitment'] == 'hourly' and
        plan['type'] in ['custom'] and plan['gpu_type'] != 'NO GPU'
    ]
    #plans = [plan for plan in plans if len(plan['regions']) > 0]
    plans = [
        plan for plan in plans if plan['minimum_commitment'] == 'hourly' and
        plan['type'] in ['preconfigured'] and
        plan['gpu_type'] not in ['NO GPU', 'RTX_3080_10GB', 'RTX_3090_24GB']
    ]

    plans = plans + [
        plan for plan in custom_plans for plan in plans_from_custom_plan(plan)
    ]
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
                if r['id'] == 'india_2':
                    continue
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


if __name__ == '__main__':

    os.makedirs('fluidstack', exist_ok=True)
    create_catalog('fluidstack')
    print('Fluidstack catalog saved to {}/vms.csv'.format('fluidstack'))
