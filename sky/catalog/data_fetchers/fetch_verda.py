"""A script that fetches Verda Cloud instance types and pricing info.

This script takes about 1 minute to finish.
"""

import csv
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import requests

from sky import sky_logging

logger = sky_logging.init_logger('fetch_verda')


def _get_oauth_token(base_url: str, client_id: str, client_secret: str) -> str:
    """Get OAuth access token using client credentials.

    Args:
        base_url: Base URL for the API
        client_id: Client ID for authentication
        client_secret: Client secret for authentication

    Returns:
        str: Access token
    """

    token_url = f'{base_url}/oauth2/token'
    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    }
    headers = {'Content-type': 'application/json'}

    response = requests.post(token_url,
                             json=payload,
                             headers=headers,
                             timeout=30)
    response.raise_for_status()

    token_data = response.json()
    return token_data['access_token']


def _fetch_instance_types(base_url: str, token: str) -> List[Dict]:
    """Fetch all instance types from the API.

    Args:
        base_url: Base URL for the API
        token: OAuth access token

    Returns:
        List[Dict]: List of instance type dictionaries
    """
    url = f'{base_url}/instance-types'
    headers = {'Authorization': f'Bearer {token}'}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    return response.json()


def _fetch_instance_availability(base_url: str,
                                 token: str,
                                 is_spot: bool = False) -> List[Dict]:
    """Fetch instance availability for different regions.

    Args:
        base_url: Base URL for the API
        token: OAuth access token
        is_spot: Whether to fetch spot availability (True) or on-demand (False)

    Returns:
        List[Dict]: Availability with location_code and availabilities
    """
    url = f'{base_url}/instance-availability'
    headers = {'Authorization': f'Bearer {token}'}
    params = {'is_spot': 'true' if is_spot else 'false'}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    return response.json()


def _build_availability_map(
        base_url: str, token: str) -> Dict[str, Dict[str, Tuple[bool, bool]]]:
    """Build a map of instance types to regions and their availability."""
    availability_map: Dict[str, Dict[str, Tuple[bool, bool]]] = {}

    # Fetch on-demand availability
    on_demand_availability = _fetch_instance_availability(base_url,
                                                          token,
                                                          is_spot=False)
    for location_data in on_demand_availability:
        location_code = location_data.get('location_code', '')
        availabilities = location_data.get('availabilities', [])
        for instance_type in availabilities:
            if instance_type not in availability_map:
                availability_map[instance_type] = {}
            if location_code not in availability_map[instance_type]:
                availability_map[instance_type][location_code] = (False, False)
            availability_map[instance_type][location_code] = (
                True,
                availability_map[instance_type][location_code][1],
            )

    # Fetch spot availability
    spot_availability = _fetch_instance_availability(base_url,
                                                     token,
                                                     is_spot=True)
    for location_data in spot_availability:
        location_code = location_data.get('location_code', '')
        availabilities = location_data.get('availabilities', [])
        for instance_type in availabilities:
            if instance_type not in availability_map:
                availability_map[instance_type] = {}
            if location_code not in availability_map[instance_type]:
                availability_map[instance_type][location_code] = (False, False)
            availability_map[instance_type][location_code] = (
                availability_map[instance_type][location_code][0],
                True,
            )

    return availability_map


def _extract_gpu_model(instance: Dict) -> str:
    """Extract GPU model from instance attributes or description.

    Args:
        instance: Instance type dictionary from Verda API

    Returns:
        str: GPU model name or empty string
    """
    # Try to get model from instance dictionary
    if 'model' in instance and instance['model']:
        return instance['model']

    # Fall back to extracting from GPU description
    if ('gpu' in instance and instance['gpu'] and
            'description' in instance['gpu']):
        gpu_desc = instance['gpu'].get('description', '')
        # Extract model name (e.g., "RTX A6000" from "1x NVIDIA RTX A6000 48GB")
        match = re.search(r'(?:NVIDIA\s+)?([A-Z0-9]+\s+[A-Z0-9]+|[A-Z0-9]+)',
                          gpu_desc)
        if match:
            return match.group(1).strip()

    return ''


def _format_accelerator_name(gpu_model: str, gpu_memory_gb: float) -> str:
    """Format accelerator name for catalog format.

    Only A100 models need memory suffix (40GB/80GB) since there are two types.
    Other GPU models should not include memory in the name.
    Also drop Tesla- prefix for Tesla GPUs,
    as SkyPilot assumes all Tesla GPUs are named as "V100"
    """
    if not gpu_model:
        return ''

    # Normalize GPU model name for catalog format
    accelerator_name = gpu_model.replace(' ', '-')

    # No need of "Tesla-" prefix for Tesla GPUs,
    # as SkyPilot assumes all Tesla GPUs are named as "V100"
    if accelerator_name.startswith('Tesla-'):
        accelerator_name = accelerator_name.replace('Tesla-', '')

    # Only A100 needs suffix if it 80GB, or else it is assumed it is 40GB
    if accelerator_name.startswith('A100-'):
        # Check if memory is already in the name
        if gpu_memory_gb == 40:
            accelerator_name = 'A100'
        elif gpu_memory_gb == 80:
            accelerator_name = 'A100-80GB'
        else:
            raise ValueError(f'Unsupported A100 memory: {gpu_memory_gb}')

    if gpu_model != accelerator_name:
        print(f'Accelerator name: {gpu_model} -> {accelerator_name}')
    return accelerator_name


def _build_gpu_info(accelerator_name: str, num_gpus: int,
                    gpu_memory_gb: float) -> str:
    """Build GpuInfo JSON string.

    Args:
        accelerator_name: Formatted accelerator name
        num_gpus: Number of GPUs
        gpu_memory_gb: GPU memory per GPU in GB

    Returns:
        str: JSON string for GpuInfo field
    """
    if num_gpus <= 0 or not accelerator_name or gpu_memory_gb <= 0:
        return ''

    total_gpu_memory_mib = gpu_memory_gb * 1024
    gpu_memory_mib = int(total_gpu_memory_mib / num_gpus)
    gpu_info_dict = {
        'Gpus': [{
            'Name': accelerator_name,
            'Count': num_gpus,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': total_gpu_memory_mib,
    }
    # Convert to JSON string (csv.writer will handle proper escaping)
    return json.dumps(gpu_info_dict)


def create_catalog(output_path: str) -> None:
    """Create Verda Cloud catalog by fetching data from API.

    Args:
        output_path: Path to output CSV file
    """

    # Get authentication credentials
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    base_url: Optional[str] = None
    default_region: Optional[str] = None

    config_file_path = os.path.expanduser('~/.verda/config.json')
    if os.path.exists(config_file_path):
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            client_id = config.get('client_id', client_id)
            client_secret = config.get('client_secret', client_secret)
            base_url = config.get('base_url', base_url)
            default_region = config.get('default_region', default_region)

    client_id = os.environ.get('VERDA_CLIENT_ID', client_id)
    client_secret = os.environ.get('VERDA_CLIENT_SECRET', client_secret)
    base_url = os.environ.get('VERDA_BASE_URL', base_url)
    default_region = os.environ.get('VERDA_DEFAULT_REGION', default_region)

    if not base_url:
        base_url = 'https://api.verda.com/v1'

    if not default_region:
        default_region = 'FIN-03'

    if not client_id or not client_secret:
        raise Exception('Verda Cloud configuration not found. '
                        'Please set VERDA_CLIENT_ID and VERDA_CLIENT_SECRET '
                        'environment variables or create ~/.verda/config.json.')

    # Get OAuth token
    logger.info('Authenticating with Verda Cloud API...')
    token = _get_oauth_token(base_url, client_id, client_secret)

    # Fetch instance types
    logger.info('Fetching instance types...')
    instance_types = _fetch_instance_types(base_url, token)
    logger.info(f'Fetched {len(instance_types)} instance types')

    # Fetch availability information
    logger.info('Fetching instance availability...')
    availability_map = _build_availability_map(base_url, token)
    logger.info(
        f'Fetched availability for {len(availability_map)} instance types')

    # Create output directory if needed
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Create CSV file
    logger.info(f'Writing catalog to {output_path}')
    with open(output_path, 'w', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)

        # Write header
        writer.writerow([
            'InstanceType',
            'UpstreamCloudId',
            'vCPUs',
            'MemoryGiB',
            'AcceleratorName',
            'AcceleratorCount',
            'GpuInfo',
            'Region',
            'Price',
            'SpotPrice',
        ])

        for instance in instance_types:
            try:
                # Extract data from instance dictionary
                instance_type_id = instance.get('instance_type', '')
                cpu_data = instance.get('cpu', {})
                memory_data = instance.get('memory', {})
                gpu_data = instance.get('gpu', {})
                gpu_memory_data = instance.get('gpu_memory', {})

                vcpus = (float(cpu_data.get('number_of_cores', 0))
                         if cpu_data else 0)
                memory_gib = (float(memory_data.get('size_in_gigabytes', 0))
                              if memory_data else 0)
                price = float(instance.get('price_per_hour', 0))
                # Get spot price if available, otherwise use regular price
                spot_price_raw = instance.get('spot_price') or instance.get(
                    'spot_price_per_hour')
                spot_price = float(spot_price_raw) if spot_price_raw else price

                # GPU information
                num_gpus = gpu_data.get('number_of_gpus', 0) if gpu_data else 0
                gpu_memory_gb = (gpu_memory_data.get('size_in_gigabytes', 0)
                                 if gpu_memory_data else 0)

                # Extract and format GPU model
                gpu_model = _extract_gpu_model(instance)
                accelerator_name = _format_accelerator_name(
                    gpu_model, int(gpu_memory_gb / num_gpus))

                # Build GpuInfo JSON string
                gpu_info = _build_gpu_info(accelerator_name, num_gpus,
                                           gpu_memory_gb)

                # Get available regions for this instance type
                available_regions = availability_map.get(instance_type_id, {})

                # If no availability data, use default region
                if not available_regions:
                    available_regions = {
                        default_region: (
                            True,
                            bool(spot_price and spot_price > 0),
                        )
                    }

                # Write row(s) for each available region
                for region, availability_tuple in available_regions.items():
                    on_demand_available, spot_available = availability_tuple
                    # Only write if on-demand is available
                    if not on_demand_available:
                        continue

                    # Use empty string if no spot pricing is available
                    effective_spot_price = (spot_price if
                                            (spot_available and
                                             spot_price != price) else '')

                    # Write formatted name entry
                    if num_gpus > 0 and accelerator_name and gpu_memory_gb > 0:
                        writer.writerow([
                            instance_type_id,
                            instance_type_id,
                            vcpus,
                            memory_gib,
                            accelerator_name,
                            float(num_gpus),
                            gpu_info,
                            region,
                            price,
                            effective_spot_price,
                        ])
            except Exception as e:  # pylint: disable=broad-except
                instance_type_id = instance.get('instance_type', 'unknown')
                logger.warning(
                    f'Error processing instance type {instance_type_id}: {e}')
                continue

    logger.info(f'Verda catalog saved to {output_path}')
    logger.info(f'Processed {len(instance_types)} instance types')


def main() -> None:
    """Write SkyPilot v8 catalog for Verda Cloud.

    This is authenticated API request, so you need Verda Cloud API
    credentials configured, in ~/.verda/config.json or in environment variables
    VERDA_CLIENT_ID and VERDA_CLIENT_SECRET.

    Will write to ~/.sky/catalogs/v8/verda/vms.csv if no file name is provided.

    > python sky/catalog/data_fetchers/fetch_verda.py [<file-name>]
    """
    logging.basicConfig(level=logging.INFO,)
    args = sys.argv[1:]
    output_path = args[0] if args else '~/.sky/catalogs/v8/verda/vms.csv'
    output_file = os.path.expanduser(output_path)
    create_catalog(output_file)
    logger.info('Done!')


if __name__ == '__main__':
    main()
