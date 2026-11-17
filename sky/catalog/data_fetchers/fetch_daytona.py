"""A script that generates the Daytona catalog.

Usage:
    python fetch_daytona.py

Reads the public Daytona GPU pricing feed (no authentication required)
and generates the SkyPilot catalog. Every GPU type in the feed is
orderable, so the feed alone decides which GPUs are offered: a new
Daytona GPU appears in the catalog on the next refresh without code
changes. GPU instance types are named `<count>x-<Daytona GPU type>`
(e.g. `2x-RTX-PRO-6000`) for counts 1-8, using Daytona's representative
sandbox shapes: 8 vCPUs per GPU and 100 GiB memory per GPU (50 GiB for
GPUs with at most 32 GiB of GPU memory, i.e. consumer cards). CPU
instance types cover common CPU-only sandbox shapes. Disk is billed
separately at a per-GiB rate and is not part of the catalog price.

GPU sandboxes run in Daytona's shared GPU fleet, represented by the
`earth` region. CPU sandboxes run in shared regions (`us`, `eu`).
"""

import csv
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

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

CONSUMER_GPU_MAX_MEMORY_GIB = 32

# GPU hardware specs (manufacturer, GPU memory in GiB) keyed by SkyPilot
# accelerator name. This is spec metadata only, not an allowlist: feed
# entries that carry `vendor` and `memoryGiB` need no entry here.
GPU_SPECS: Dict[str, Tuple[str, int]] = {
    'B300': ('NVIDIA', 288),
    'B200': ('NVIDIA', 180),
    'H200': ('NVIDIA', 141),
    'H100': ('NVIDIA', 80),
    'A100': ('NVIDIA', 80),
    'RTXPRO6000': ('NVIDIA', 96),
    'L40S': ('NVIDIA', 48),
    'L40': ('NVIDIA', 48),
    'L4': ('NVIDIA', 24),
    'RTX5090': ('NVIDIA', 32),
    'RTX4090': ('NVIDIA', 24),
    'MI355X': ('AMD', 288),
    'MI325X': ('AMD', 256),
    'MI300X': ('AMD', 192),
}


def accelerator_name(daytona_type: str) -> str:
    """Maps a Daytona GPU type to a SkyPilot accelerator name.

    Daytona separates words with dashes (`RTX-PRO-6000`); SkyPilot uses
    the joined form (`RTXPRO6000`).
    """
    return daytona_type.replace('-', '').replace('_', '').upper()


def gpu_metadata(gpu: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
    """Returns (accelerator name, manufacturer, GPU memory GiB) for a feed
    GPU entry, or None if its hardware specs are unknown."""
    acc_name = accelerator_name(gpu['type'])
    manufacturer, memory = GPU_SPECS.get(acc_name, (None, None))
    manufacturer = gpu.get('vendor') or manufacturer
    memory = gpu.get('memoryGiB') or memory
    if manufacturer is None or memory is None:
        return None
    return acc_name, str(manufacturer).upper(), int(memory)


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

        # GPU instance types: <count>x-<Daytona GPU type> in the shared
        # GPU fleet. The instance type carries the identifier the sandbox
        # create API expects, so the provisioner needs no mapping.
        for gpu in pricing['gpus']:
            metadata = gpu_metadata(gpu)
            if metadata is None:
                logger.warning(
                    'No hardware specs for Daytona GPU type %s; add it to '
                    'GPU_SPECS (or publish vendor/memoryGiB in the feed). '
                    'Skipping.', gpu['type'])
                continue
            acc_name, manufacturer, gpu_memory_gb = metadata
            memory_per_gpu = (CONSUMER_MEMORY_PER_GPU_GIB
                              if gpu_memory_gb <= CONSUMER_GPU_MAX_MEMORY_GIB
                              else MEMORY_PER_GPU_GIB)
            for count in range(1, MAX_GPU_COUNT + 1):
                vcpus = CPUS_PER_GPU * count
                memory = memory_per_gpu * count
                price = (count * gpu['onDemandPricePerHour'] +
                         vcpus * on_demand['vcpuPerHour'] +
                         memory * on_demand['memoryGiBPerHour'])
                spot_price = (count * gpu['spotPricePerHour'] +
                              vcpus * spot['vcpuPerHour'] +
                              memory * spot['memoryGiBPerHour'])
                gpu_info = make_gpu_info_json(acc_name, manufacturer, count,
                                              gpu_memory_gb)
                writer.writerow([
                    f'{count}x-{gpu["type"]}',
                    acc_name,
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
