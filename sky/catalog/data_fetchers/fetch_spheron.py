"""A script that generates the Spheron catalog.

Usage:
    python fetch_spheron.py [-h] [--api-key API_KEY]
                             [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.spheron/api_key` to look for the Spheron API key.
"""
import argparse
import csv
import json
import os
from typing import Dict, List

import requests

ENDPOINT = 'https://app.spheron.ai/api/gpu-offers'
DEFAULT_SPHERON_API_KEY_PATH = os.path.expanduser('~/.spheron/api_key')

# Preferred OS version keywords - we pick the first option whose name contains
# one of these keywords (case-insensitive), else fall back to the first option.
_PREFERRED_OS_KEYWORDS = ['22.04', '20.04']


def parse_gpu_info(gpu_type: str, num_gpus: int, ram_per_gpu_gb: int) -> Dict:
    """Parse GPU information for the catalog."""
    manufacturer = 'NVIDIA'
    gpu_upper = gpu_type.upper()
    if 'MI300' in gpu_upper or 'MI250' in gpu_upper or 'MI100' in gpu_upper:
        manufacturer = 'AMD'
    elif 'GAUDI' in gpu_upper or 'HABANA' in gpu_upper:
        manufacturer = 'Intel'

    # Convert GB to MiB for consistency with catalog format
    ram_mib = ram_per_gpu_gb * 1024

    return {
        'Gpus': [{
            'Name': gpu_type,
            'Manufacturer': manufacturer,
            'Count': float(num_gpus),
            'MemoryInfo': {
                'SizeInMiB': ram_mib
            },
            'TotalGpuMemoryInMiB': ram_mib * num_gpus
        }]
    }


def pick_operating_system(os_options: List[str]) -> str:
    """Pick the best operating system from available options.

    Returns the full OS string as required by the Spheron deployment API.
    Prefers Ubuntu 22.04, then 20.04, otherwise returns the first option.
    """
    lower_options = [o.lower() for o in os_options]
    for keyword in _PREFERRED_OS_KEYWORDS:
        for i, lower_opt in enumerate(lower_options):
            if 'ubuntu' in lower_opt and keyword in lower_opt:
                return os_options[i]
    return os_options[0] if os_options else 'ubuntu-22.04'


def normalize_gpu_name(gpu_type: str) -> str:
    """Normalize GPU type string to SkyPilot catalog format.

    E.g. 'rtx-4090' -> 'RTX-4090', 'h100' -> 'H100'
    """
    return gpu_type.upper()


def fetch_all_gpu_offers(api_key: str) -> List[Dict]:
    """Fetch all GPU offer groups from the Spheron API (paginated)."""
    headers = {'Authorization': f'Bearer {api_key}'}
    all_groups = []
    page = 1
    limit = 100

    while True:
        params = {'page': page, 'limit': limit}
        response = requests.get(ENDPOINT,
                                headers=headers,
                                params=params,
                                timeout=30)
        response.raise_for_status()
        data = response.json()

        gpu_groups = data.get('data', [])
        if not gpu_groups:
            break

        all_groups.extend(gpu_groups)

        # Check if there are more pages
        total_pages = data.get('totalPages', 1)
        if page >= total_pages:
            break
        page += 1

    return all_groups


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Spheron catalog by fetching from API."""
    gpu_groups = fetch_all_gpu_offers(api_key)

    with open(output_path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice', 'Provider',
            'OperatingSystem', 'SpheronInstanceType'
        ])

        rows_written = 0
        for gpu_group in gpu_groups:
            raw_gpu_type = gpu_group.get('gpuType', '')
            accelerator_name = normalize_gpu_name(raw_gpu_type)

            for offer in gpu_group.get('offers', []):
                if not offer.get('available', False):
                    continue

                if offer.get('maintenance', False):
                    continue

                gpu_count = float(offer.get('gpuCount', 0))
                if gpu_count <= 0:
                    continue

                instance_type = offer['offerId']
                vcpus = float(offer.get('vcpus', 0))
                memory_gb = float(offer.get('memory', 0))
                price = float(offer.get('price', 0))
                spot_price_raw = offer.get('spot_price')
                provider = offer.get('provider', '')
                spheron_instance_type = offer.get('instanceType', 'DEDICATED')
                gpu_memory_gb = int(offer.get('gpu_memory', 0))
                os_options = offer.get('os_options', ['ubuntu-22.04'])

                operating_system = pick_operating_system(os_options)

                # Build GpuInfo JSON
                gpuinfo_dict = parse_gpu_info(accelerator_name, int(gpu_count),
                                              gpu_memory_gb)
                gpuinfo = json.dumps(gpuinfo_dict)

                # For SPOT instances, put price in SpotPrice column
                if spheron_instance_type.upper() == 'SPOT':
                    csv_price = ''
                    csv_spot_price = price
                elif spot_price_raw is not None:
                    csv_price = price
                    csv_spot_price = float(spot_price_raw)
                else:
                    csv_price = price
                    csv_spot_price = ''

                # Write one row per region in offer['clusters']
                regions = offer.get('clusters', [offer.get('region', '')])
                for region in regions:
                    if not region:
                        continue
                    writer.writerow([
                        instance_type,
                        accelerator_name,
                        gpu_count,
                        vcpus,
                        memory_gb,
                        csv_price,
                        region,
                        gpuinfo,
                        csv_spot_price,
                        provider,
                        operating_system,
                        spheron_instance_type,
                    ])
                    rows_written += 1

    print(f'Wrote {rows_written} rows to {output_path}')


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Get Spheron API key from cmdline or default path."""
    api_key = cmdline_args.api_key
    if api_key is None:
        if cmdline_args.api_key_path is not None:
            with open(cmdline_args.api_key_path, mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
        else:
            # Read from ~/.spheron/api_key
            with open(DEFAULT_SPHERON_API_KEY_PATH,
                      mode='r',
                      encoding='utf-8') as f:
                api_key = f.read().strip()
    assert api_key is not None, (
        f'API key not found. Please provide via --api-key or place in '
        f'{DEFAULT_SPHERON_API_KEY_PATH}')
    return api_key


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Spheron API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing Spheron API key.')
    args = parser.parse_args()
    os.makedirs('spheron', exist_ok=True)
    create_catalog(get_api_key(args), 'spheron/vms.csv')
    print('Spheron catalog saved to spheron/vms.csv')
