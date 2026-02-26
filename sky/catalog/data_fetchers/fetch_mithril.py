"""A script that generates the Mithril Cloud catalog.

Usage:
    python fetch_mithril.py

Requires MITHRIL_API_KEY environment variable or a Mithril config file at
${XDG_CONFIG_HOME:-~/.config}/mithril/config.yaml with current_profile set.
"""

import csv
import json
import logging
import os
from typing import Any, Dict, List

import requests

from sky.provision.mithril import utils as mithril_utils

logger = logging.getLogger(__name__)


def _get_headers(api_key: str) -> Dict[str, str]:
    """Build auth headers for Mithril API requests."""
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }


def make_gpu_info_json(gpu_name: str, gpu_count: int,
                       gpu_memory_gb: int) -> str:
    """Create GPU info JSON string.

    Args:
        gpu_name: Name of the GPU
        gpu_count: Number of GPUs
        gpu_memory_gb: GPU memory in GB
    """
    gpu_memory_mib = gpu_memory_gb * 1024

    gpu_info = {
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * gpu_count,
    }
    return json.dumps(gpu_info).replace('"', '\'')


def fetch_instance_types(api_key: str, api_url: str) -> List[Dict[str, Any]]:
    """Fetch instance types from Mithril API."""
    endpoint = f'{api_url}/v2/instance-types'

    try:
        response = requests.get(endpoint,
                                headers=_get_headers(api_key),
                                timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise mithril_utils.MithrilError(
            f'Failed to fetch instance types: {e}') from e


def fetch_instance_pricing(api_key: str, api_url: str,
                           instance_fid: str) -> float:
    """Fetch minimum price for an instance type from Mithril Pricing API.

    Args:
        api_key: API key for authentication
        api_url: Base URL for the API
        instance_fid: Instance type FID (e.g., 'it_XqgKWbhZ5gznAYsG')

    Returns:
        Price in dollars (converted from minimum_price_cents).
    """
    endpoint = f'{api_url}/v2/pricing/current'

    try:
        response = requests.get(
            endpoint,
            headers=_get_headers(api_key),
            params={'instance_type': instance_fid},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data['minimum_price_cents'] / 100.0
    except requests.exceptions.RequestException as e:
        raise mithril_utils.MithrilError(
            f'Failed to fetch pricing for {instance_fid}: {e}') from e


def fetch_spot_availability(api_key: str,
                            api_url: str) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch per-region spot availability and pricing.

    Args:
        api_key: API key for authentication.
        api_url: Base URL for the API.

    Returns:
        A dict mapping instance FID to a list of dicts, each containing:
            - 'region': Region identifier string.
            - 'spot_price': Spot price in dollars.
    """
    endpoint = f'{api_url}/v2/spot/availability'

    try:
        response = requests.get(endpoint,
                                headers=_get_headers(api_key),
                                timeout=60)
        response.raise_for_status()
        records = response.json()
    except requests.exceptions.RequestException as e:
        raise mithril_utils.MithrilError(
            f'Failed to fetch spot availability: {e}') from e

    availability: Dict[str, List[Dict[str, Any]]] = {}

    for rec in records:
        fid = rec['instance_type']
        availability.setdefault(fid, []).append({
            'region': rec['region'],
            'spot_price': float(rec['last_instance_price'].lstrip('$')),
        })

    return availability


def create_catalog(output_path: str = 'mithril/vms.csv') -> None:
    """Create Mithril catalog CSV file."""
    config = mithril_utils.resolve_current_config()
    api_key = config['api_key']
    api_url = config['api_url']

    logger.info('Fetching Mithril instance types...')
    instance_types = fetch_instance_types(api_key, api_url)
    logger.info('Found %d instance types.', len(instance_types))

    logger.info('Fetching pricing for each instance type...')
    instance_pricing: Dict[str, float] = {}
    for inst in instance_types:
        instance_pricing[inst['fid']] = fetch_instance_pricing(
            api_key, api_url, inst['fid'])

    logger.info('Fetching spot availability...')
    availability = fetch_spot_availability(api_key, api_url)

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

        for inst in instance_types:
            instance_type = inst['name']
            gpu_name = inst['gpu_type']
            gpu_count = inst['num_gpus']
            gpu_memory_gb = inst['gpu_memory_gb']
            vcpus = inst['num_cpus']
            memory_gb = inst['ram_gb']

            regions_with_prices = availability.get(inst['fid'], [])
            if not regions_with_prices:
                logger.warning(
                    'No availability found for instance type %s, '
                    'skipping.', instance_type)
                continue

            gpu_info = (make_gpu_info_json(gpu_name, gpu_count, gpu_memory_gb)
                        if gpu_count else '')

            price = instance_pricing[inst['fid']]

            for item in regions_with_prices:
                writer.writerow([
                    instance_type,
                    gpu_name or '',
                    gpu_count or '',
                    vcpus,
                    memory_gb,
                    price,
                    price,
                    item['region'],
                    gpu_info,
                ])


if __name__ == '__main__':
    os.makedirs('mithril', exist_ok=True)
    create_catalog('mithril/vms.csv')
    logger.info('Mithril catalog saved to mithril/vms.csv')
