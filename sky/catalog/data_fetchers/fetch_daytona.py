"""A script that generates the Daytona catalog.

Usage:
    python fetch_daytona.py

Reads the public Daytona GPU pricing feed (no authentication required)
and generates the SkyPilot catalog. GPU instance types are generated per
GPU type and count (1-8) using Daytona's representative sandbox shapes:
8 vCPUs per GPU, 100 GiB memory per GPU for datacenter GPUs and 50 GiB
per GPU for consumer GPUs. CPU instance types cover common CPU-only
sandbox shapes. Disk is billed separately at a per-GiB rate and is not
part of the catalog price.

GPU sandboxes run in Daytona's shared GPU fleet, represented by the
`earth` region. CPU sandboxes run in shared regions (`us`, `eu`).
"""

import csv
import json
import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

PRICING_URL = 'https://billing.app.daytona.io/gpu-pricing'
TIMEOUT = 60

GPU_REGION = 'earth'
CPU_REGIONS = ('us', 'eu')
MAX_GPU_COUNT = 8

CPUS_PER_GPU = 8
MEMORY_PER_GPU_GIB = 100
CONSUMER_MEMORY_PER_GPU_GIB = 50

# Maps Daytona GPU type identifiers to SkyPilot accelerator names,
# manufacturer and GPU memory (GiB). B200 is intentionally absent: it is
# priced on the rate card ahead of launch and not yet orderable.
GPU_TYPES: Dict[str, Dict[str, Any]] = {
    'B300': {
        'acc_name': 'B300',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 288,
        'memory_per_gpu': MEMORY_PER_GPU_GIB,
    },
    'H200': {
        'acc_name': 'H200',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 141,
        'memory_per_gpu': MEMORY_PER_GPU_GIB,
    },
    'H100': {
        'acc_name': 'H100',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 80,
        'memory_per_gpu': MEMORY_PER_GPU_GIB,
    },
    'RTX-PRO-6000': {
        'acc_name': 'RTXPRO6000',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 96,
        'memory_per_gpu': MEMORY_PER_GPU_GIB,
    },
    'RTX-5090': {
        'acc_name': 'RTX5090',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 32,
        'memory_per_gpu': CONSUMER_MEMORY_PER_GPU_GIB,
    },
    'RTX-4090': {
        'acc_name': 'RTX4090',
        'manufacturer': 'NVIDIA',
        'gpu_memory_gb': 24,
        'memory_per_gpu': CONSUMER_MEMORY_PER_GPU_GIB,
    },
    'MI355X': {
        'acc_name': 'MI355X',
        'manufacturer': 'AMD',
        'gpu_memory_gb': 288,
        'memory_per_gpu': MEMORY_PER_GPU_GIB,
    },
}

# CPU-only sandbox shapes: (vCPUs, memory GiB).
CPU_SHAPES = ((2, 8), (4, 16), (8, 32), (16, 64))


def make_gpu_info_json(acc_name: str, manufacturer: str, gpu_count: int,
                       gpu_memory_gb: int) -> str:
    """Create the GpuInfo JSON string for a catalog row."""
    gpu_memory_mib = gpu_memory_gb * 1024
    gpu_info = {
        'Gpus': [{
            'Name': acc_name,
            'Manufacturer': manufacturer,
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * gpu_count,
    }
    return json.dumps(gpu_info).replace('"', '\'')


def fetch_pricing() -> Dict[str, Any]:
    """Fetches the public Daytona pricing feed."""
    response = requests.get(PRICING_URL, timeout=TIMEOUT)
    response.raise_for_status()
    pricing = response.json()
    assert isinstance(pricing.get('gpus'), list), pricing
    assert isinstance(pricing.get('resources'), dict), pricing
    return pricing


def create_catalog(output_path: str = 'daytona/vms.csv') -> None:
    """Creates the Daytona catalog CSV file."""
    pricing = fetch_pricing()
    gpu_rates = {gpu['type']: gpu for gpu in pricing['gpus']}
    on_demand = pricing['resources']['onDemand']
    spot = pricing['resources']['spot']

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'vCPUs',
            'MemoryGiB',
            'Price',
            'SpotPrice',
            'Region',
            'GpuInfo',
        ])

        # GPU instance types: <count>x-<ACC_NAME> in the shared GPU fleet.
        for daytona_type, info in GPU_TYPES.items():
            rates = gpu_rates.get(daytona_type)
            if rates is None:
                logger.warning(
                    'GPU type %s not found in the pricing feed, '
                    'skipping.', daytona_type)
                continue
            for count in range(1, MAX_GPU_COUNT + 1):
                vcpus = CPUS_PER_GPU * count
                memory = info['memory_per_gpu'] * count
                price = (count * rates['onDemandPricePerHour'] +
                         vcpus * on_demand['vcpuPerHour'] +
                         memory * on_demand['memoryGiBPerHour'])
                spot_price = (count * rates['spotPricePerHour'] +
                              vcpus * spot['vcpuPerHour'] +
                              memory * spot['memoryGiBPerHour'])
                gpu_info = make_gpu_info_json(info['acc_name'],
                                              info['manufacturer'], count,
                                              info['gpu_memory_gb'])
                writer.writerow([
                    f'{count}x-{info["acc_name"]}',
                    info['acc_name'],
                    count,
                    vcpus,
                    memory,
                    round(price, 6),
                    round(spot_price, 6),
                    GPU_REGION,
                    gpu_info,
                ])

        # CPU instance types: cpu-<vcpus>x-<mem>gb in shared regions.
        # Spot is only available for GPU sandboxes, so SpotPrice is empty.
        for vcpus, memory in CPU_SHAPES:
            price = (vcpus * on_demand['vcpuPerHour'] +
                     memory * on_demand['memoryGiBPerHour'])
            for region in CPU_REGIONS:
                writer.writerow([
                    f'cpu-{vcpus}x-{memory}gb',
                    '',
                    '',
                    vcpus,
                    memory,
                    round(price, 6),
                    '',
                    region,
                    '',
                ])


if __name__ == '__main__':
    create_catalog('daytona/vms.csv')
    logger.info('Daytona catalog saved to daytona/vms.csv')
