"""Fetch Mithril Cloud catalog data.

This script fetches instance type information from Mithril Cloud API
and creates a catalog CSV file for SkyPilot.
"""
import argparse
import csv
import json
import os
from typing import Any, Dict, List, Optional

import requests
import yaml

# Mithril API endpoints
BASE_URL = 'https://api.mithril.ai'
INSTANCE_TYPES_ENDPOINT = f'{BASE_URL}/v2/instance-types'
SPOT_AVAILABILITY_ENDPOINT = f'{BASE_URL}/v2/spot/availability'
DEFAULT_CREDENTIALS_PATH = os.path.expanduser('~/.flow/config.yaml')


def get_output_path() -> str:
    """Get output path for catalog file."""
    current_dir = os.getcwd()
    if os.path.basename(current_dir) == 'mithril':
        return 'vms.csv'
    mithril_dir = os.path.join(current_dir, 'mithril')
    os.makedirs(mithril_dir, exist_ok=True)
    return os.path.join(mithril_dir, 'vms.csv')


# GPU memory mapping (in MiB) - Only GPUs supported by Mithril
GPU_MEMORY_MAP = {
    'A100': 81920,  # 80 GB
    'H100': 81920,  # 80 GB
}


def get_api_key(api_key: Optional[str] = None) -> str:
    """Get API key from arg, env var, or config file."""
    if api_key:
        return api_key
    env_api_key = os.environ.get('MITHRIL_API_KEY')
    if env_api_key:
        return env_api_key
    if not os.path.exists(DEFAULT_CREDENTIALS_PATH):
        raise RuntimeError(
            f'Mithril config not found at {DEFAULT_CREDENTIALS_PATH}. '
            f'Please run: flow setup')
    with open(DEFAULT_CREDENTIALS_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    api_key = config.get('api_key')
    if not api_key:
        raise RuntimeError(f'API key not found in {DEFAULT_CREDENTIALS_PATH}. '
                           f'Please run: flow setup')
    return api_key


def parse_gpu_info(gpu_name: str,
                   gpu_count: int,
                   gpu_memory_gb: Optional[int] = None) -> str:
    """Create GPU info JSON string.

    Args:
        gpu_name: Name of the GPU
        gpu_count: Number of GPUs
        gpu_memory_gb: GPU memory in GB (from API). Falls back to
            GPU_MEMORY_MAP if not provided.
    """
    if gpu_memory_gb:
        gpu_memory_mib = gpu_memory_gb * 1024
    else:
        gpu_memory_mib = GPU_MEMORY_MAP.get(gpu_name, 0)

    gpu_info = {
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * gpu_count
    }
    return json.dumps(gpu_info).replace('"', '\'')


def fetch_instance_types(api_key: str) -> List[Dict[str, Any]]:
    """Fetch instance types from Mithril API."""
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.get(INSTANCE_TYPES_ENDPOINT,
                                headers=headers,
                                timeout=30)
        response.raise_for_status()
        data = response.json()
        # Mithril API returns a dict with 'data' key containing the list
        if isinstance(data, dict):
            if 'data' in data:
                return data['data']
            elif 'instance_types' in data:
                return data['instance_types']
        # If it's already a list, return as is
        return data if isinstance(data, list) else []
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f'Failed to fetch instance types: {e}') from e


def fetch_spot_availability(
        api_key: str,
        fid_to_name: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch per-region spot availability and pricing.

    Args:
        api_key: API key for authentication
        fid_to_name: Mapping from instance FID to instance name

    Returns a mapping from instance type name to a list of entries with keys:
      { 'region': str, 'spot_price': float }
    """
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    try:
        response = requests.get(SPOT_AVAILABILITY_ENDPOINT,
                                headers=headers,
                                timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f'Failed to fetch spot availability: {e}') from e

    # Normalize into { instance_type: [ {region, spot_price}, ... ] }
    availability: Dict[str, List[Dict[str, Any]]] = {}

    # Mithril API returns a list of records with:
    # - 'instance_type': FID (e.g. 'it_XqgKWbhZ5gznAYsG')
    # - 'region': region name
    # - 'last_instance_price': price as string with '$' prefix
    records = data if isinstance(data, list) else data.get('data', [])

    if isinstance(records, list):
        for rec in records:
            # Get instance FID and map to name
            inst_fid = rec.get('instance_type')
            inst_name = fid_to_name.get(inst_fid) if inst_fid else None

            region = rec.get('region')

            # Try multiple price fields
            price_val = (rec.get('last_instance_price') or
                         rec.get('spot_price') or rec.get('price'))

            # Parse price string (e.g. "$12.00")
            if isinstance(price_val, str):
                price_str = price_val.strip().lstrip('$')
                try:
                    price = float(price_str)
                except ValueError:
                    price = None
            else:
                price = price_val

            if not inst_name or not region or price is None:
                continue
            availability.setdefault(inst_name, []).append({
                'region': region,
                'spot_price': price,
            })
    elif isinstance(records, dict):
        # e.g., { "h100-80gb.sxm.8x": { "us-central1-b": {"price": 8.0} } }
        for inst, per_region in records.items():
            if not isinstance(per_region, dict):
                continue
            for region, info in per_region.items():
                if not isinstance(info, dict):
                    continue
                price_val = info.get('spot_price') or info.get('price')
                if isinstance(price_val, str):
                    price_str = price_val.strip().lstrip('$')
                    try:
                        price = float(price_str)
                    except ValueError:
                        price = None
                else:
                    price = price_val
                if price is None:
                    continue
                availability.setdefault(inst, []).append({
                    'region': region,
                    'spot_price': price,
                })

    return availability


def create_catalog(api_key: Optional[str] = None) -> None:
    """Create Mithril catalog CSV file."""
    print('Fetching Mithril instance types...')
    api_key = get_api_key(api_key)
    instance_types = fetch_instance_types(api_key)

    # Build FID to instance name mapping
    fid_to_name: Dict[str, str] = {}
    for inst in instance_types:
        fid = inst.get('fid')
        name = inst.get('name')
        if fid and name:
            fid_to_name[fid] = name

    availability = fetch_spot_availability(api_key, fid_to_name)

    print(f'Found {len(instance_types)} instance types')

    output_path = get_output_path()
    print(f'Writing catalog to {output_path}')

    # Track unique instance type names to handle duplicates
    # Mithril API can return multiple configs with the same name
    seen_names = {}

    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'SpotPrice', 'Region', 'GpuInfo'
        ])

        for instance in instance_types:
            base_instance_type = instance.get('name')
            if not base_instance_type:
                continue
            gpu_name = instance.get('gpu_type')
            gpu_count = instance.get('num_gpus', 0)
            gpu_memory_gb = instance.get('gpu_memory_gb')
            vcpus = instance.get('num_cpus', 0)
            memory_gb = instance.get('ram_gb', 0)

            # Handle duplicate instance type names by making them unique
            # Append CPU count if there's a conflict
            if base_instance_type in seen_names:
                instance_type = f'{base_instance_type}_{vcpus}cpu'
                print(f'Duplicate instance type name found: '
                      f'{base_instance_type}, renaming to {instance_type}')
            else:
                instance_type = base_instance_type
                seen_names[base_instance_type] = True

            # Populate per-region pricing from spot availability.
            # If not found, skip writing rows for this instance type.
            regions_with_prices = (availability.get(instance_type) or
                                   availability.get(base_instance_type) or [])
            if not regions_with_prices:
                # Try matching by case-insensitive key if API returns
                # different casing
                lowered = {k.lower(): v for k, v in availability.items()}
                regions_with_prices = lowered.get(instance_type.lower(), [])
            if not regions_with_prices:
                # No availability info; skip to avoid misleading hardcoded
                # data
                continue

            # Create GPU info if GPUs are present
            gpu_info = ''
            if gpu_name and gpu_count > 0:
                gpu_info = parse_gpu_info(gpu_name, gpu_count, gpu_memory_gb)

            # Write one row per available region with current spot price.
            # As Mithril is spot-based, we mirror SpotPrice into Price for
            # compatibility with non-spot queries.
            for item in regions_with_prices:
                region = item['region']
                spot_price_val = item['spot_price']
                writer.writerow([
                    instance_type,
                    gpu_name if gpu_name else '',
                    gpu_count if gpu_count else '',
                    vcpus,
                    memory_gb,
                    spot_price_val,  # Price
                    spot_price_val,  # SpotPrice
                    region,
                    gpu_info
                ])

    print(f'Successfully created catalog at {output_path}')


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch Mithril Cloud catalog data')
    parser.add_argument('--api-key', type=str, help='Mithril API key')

    args = parser.parse_args()

    try:
        create_catalog(args.api_key)
        print(f'Mithril Service Catalog saved to {get_output_path()}')
        return 0
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error: {e}')
        return 1


if __name__ == '__main__':
    exit(main())
