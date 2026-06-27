"""A script that generates the Modal catalog.

Usage:
    python fetch_modal.py
"""

import csv
import html
import json
import os
import re
from typing import Dict, List, Optional

import requests

PRICING_URL = 'https://modal.com/pricing'
GPU_URL = 'https://modal.com/docs/guide/gpu'
REGION_URL = 'https://modal.com/docs/guide/region-selection'

AUTO_REGION = 'auto'
BROAD_REGIONS = ('us', 'eu', 'ap', 'uk', 'ca', 'me', 'sa', 'af', 'mx')
DEFAULT_MODAL_CPU_CORES = 2.0
DEFAULT_SKY_VCPUS = 4.0
DEFAULT_MEMORY_GIB = 16

GPU_MEMORY_GIB = {
    'B200': 180,
    'H200': 141,
    'H100': 80,
    'RTX-PRO-6000': 96,
    'A100-80GB': 80,
    'A100-40GB': 40,
    'A100': 40,
    'L40S': 48,
    'A10': 24,
    'L4': 24,
    'T4': 16,
}

GPU_COUNTS = {
    'B200': (1, 2, 4, 8),
    'H200': (1, 2, 4, 8),
    'H100': (1, 2, 4, 8),
    'RTX-PRO-6000': (1,),
    'A100-80GB': (1, 2, 4, 8),
    'A100-40GB': (1, 2, 4, 8),
    'A100': (1, 2, 4, 8),
    'L40S': (1, 2, 4, 8),
    'A10': (1, 2, 4),
    'L4': (1, 2, 4, 8),
    'T4': (1, 2, 4, 8),
}

PRICE_LABEL_TO_GPU = {
    'Nvidia B200': 'B200',
    'Nvidia H200': 'H200',
    'Nvidia H100': 'H100',
    'Nvidia RTX PRO 6000': 'RTX-PRO-6000',
    'Nvidia A100, 80 GB': 'A100-80GB',
    'Nvidia A100, 40 GB': 'A100-40GB',
    'Nvidia L40S': 'L40S',
    'Nvidia A10': 'A10',
    'Nvidia L4': 'L4',
    'Nvidia T4': 'T4',
}


def _page_text(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    text = re.sub(r'<[^>]+>', '\n', response.text)
    return html.unescape(text)


def _extract_price_per_second(page_text: str, label: str) -> float:
    label_offset = page_text.find(label)
    if label_offset < 0:
        raise RuntimeError(f'Failed to find Modal price label {label!r}.')
    text_after_label = page_text[label_offset:label_offset + 500]
    match = re.search(r'\$(\d+\.\d+)\s*/[^\n]*sec', text_after_label,
                      re.MULTILINE)
    if match is None:
        raise RuntimeError(f'Failed to find Modal price for {label!r}.')
    return float(match.group(1))


def _extract_regions(page_text: str) -> Dict[str, float]:
    regions = {AUTO_REGION: 1.0}
    region_section = page_text.split('Container region options', 1)[1].split(
        'Need access to more granular region definitions?', 1)[0]
    for region in re.findall(r'"([a-z-]+)"', region_section):
        regions[region] = 1.5 if region in BROAD_REGIONS else 1.75
    return regions


def _extract_gpu_names(page_text: str) -> List[str]:
    gpu_section = page_text.split('Modal supports the following values',
                                  1)[1].split('For instance', 1)[0]
    names = []
    for raw_name in re.findall(r'`([^`]+)`', gpu_section):
        name = raw_name.split('/')[0].removesuffix('!').removesuffix('+')
        if name not in names:
            names.append(name)
    return names


def _gpu_info(gpu_name: str, gpu_count: int) -> str:
    gpu_memory_mib = int(GPU_MEMORY_GIB[gpu_name] * 1024)
    return json.dumps({
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib,
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * gpu_count,
    }).replace('"', '\'')


def _base_compute_price_per_hour(cpu_price_per_second: float,
                                 memory_price_per_second: float,
                                 gpu_price_per_second: Optional[float] = None,
                                 gpu_count: int = 0) -> float:
    per_second = (DEFAULT_MODAL_CPU_CORES * cpu_price_per_second +
                  DEFAULT_MEMORY_GIB * memory_price_per_second)
    if gpu_price_per_second is not None:
        per_second += gpu_price_per_second * gpu_count
    return per_second * 3600


def create_catalog(output_path: str) -> None:
    pricing_text = _page_text(PRICING_URL)
    gpu_text = _page_text(GPU_URL)
    region_text = _page_text(REGION_URL)

    sandbox_pricing_text = pricing_text.split(
        'Modal Sandbox + Notebooks Pricing', 1)[1]
    cpu_price_per_second = _extract_price_per_second(sandbox_pricing_text,
                                                     'CPU')
    memory_price_per_second = _extract_price_per_second(sandbox_pricing_text,
                                                        'Memory')
    gpu_prices = {
        gpu_name: _extract_price_per_second(pricing_text, price_label)
        for price_label, gpu_name in PRICE_LABEL_TO_GPU.items()
    }
    regions = _extract_regions(region_text)
    gpu_names = _extract_gpu_names(gpu_text)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])

        cpu_instance_type = 'modal-cpu-4x-16gb'
        cpu_price = _base_compute_price_per_hour(cpu_price_per_second,
                                                 memory_price_per_second)
        for region, multiplier in regions.items():
            writer.writerow([
                cpu_instance_type, None, None, DEFAULT_SKY_VCPUS,
                DEFAULT_MEMORY_GIB, cpu_price * multiplier, region, None, None
            ])

        for gpu_name in gpu_names:
            for gpu_count in GPU_COUNTS[gpu_name]:
                price = _base_compute_price_per_hour(
                    cpu_price_per_second,
                    memory_price_per_second,
                    gpu_prices[gpu_name],
                    gpu_count,
                )
                for region, multiplier in regions.items():
                    writer.writerow([
                        f'modal-{gpu_name.lower()}-{gpu_count}x',
                        gpu_name,
                        gpu_count,
                        DEFAULT_SKY_VCPUS,
                        DEFAULT_MEMORY_GIB,
                        price * multiplier,
                        region,
                        _gpu_info(gpu_name, gpu_count),
                        None,
                    ])


if __name__ == '__main__':
    create_catalog('modal/vms.csv')
    print('Modal catalog saved to modal/vms.csv')
