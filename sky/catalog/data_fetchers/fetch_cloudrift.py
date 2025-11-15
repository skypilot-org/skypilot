"""A script that fetches CloudRift instance types and generates a CSV catalog.

Usage:
    python fetch_cloudrift.py
"""

import csv
import os
import sys
from typing import Dict, List

# # Add the parent directory to the path so we can import sky modules
# sys.path.insert(
#     0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from sky.provision.cloudrift.utils import get_cloudrift_client

# Constants
BYTES_TO_GIB = 1024 * 1024 * 1024  # 1 GiB = 1024^3 bytes


def extract_region_from_dc(dc_name: str, providers_data: List[Dict]) -> str:
    """Extract region information from datacenter name.

    Uses provider data to extract country code as region.
    Falls back to original extraction method if datacenter
    not found in providers data.

    Args:
        dc_name: The datacenter name, e.g. 'us-east-nc-nr-1'
        providers_data: List of provider dictionaries from CloudRift API

    Returns:
        Region string (country code) if found in providers data, otherwise
        extracts region from datacenter name
        (e.g. 'us-east-nc-nr-1' -> 'us-east-nc-nr')
    """
    # First try to find the datacenter in providers data
    for provider in providers_data:
        for datacenter in provider.get('datacenters', []):
            if datacenter.get('name') == dc_name:
                # Use country code as region
                return datacenter.get('country_code', '')

    # Fall back to original extraction method
    parts = dc_name.split('-')
    if parts[-1].isdigit():
        return '-'.join(parts[:-1])
    return dc_name


def create_catalog(output_dir: str) -> None:
    """Create the CSV file catalog by querying CloudRift API"""
    client = get_cloudrift_client()

    # Get instance types
    instance_types = client.get_instance_types()

    # Get providers data to extract region information
    providers = client.get_providers()

    with open(os.path.join(output_dir, 'vms.csv'), mode='w',
              encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'GpuInfo', 'Region', 'SpotPrice', 'Price',
            'AvailabilityZone'
        ])

        for instance_type in instance_types:
            # Process each variant
            for variant in instance_type.get('variants', []):
                instance_name = variant.get('name')
                gpu_count = variant.get('gpu_count', 0)

                # Skip instances without GPUs
                if gpu_count == 0:
                    continue

                # Extract instance properties
                vcpus = variant.get('logical_cpu_count', 0)
                memory_bytes = variant.get('dram', 0)
                memory_gib = memory_bytes / BYTES_TO_GIB

                # Get price (convert from cents to dollars)
                price = variant.get('cost_per_hour', 0) / 100.0

                # Extract accelerator name from brand_short
                accelerator_name = instance_type.get('brand_short', '')

                # Get available datacenters
                dcs = variant.get('nodes_per_dc', {})

                # If there are no datacenters, use empty values
                # but still include the instance
                if not dcs:
                    writer.writerow([
                        instance_name,
                        accelerator_name,
                        gpu_count,
                        vcpus,
                        round(memory_gib, 1),
                        accelerator_name,
                        '',  # Region
                        0.0,  # SpotPrice
                        # (CloudRift doesn't have spot instances yet)
                        price,
                        ''  # AvailabilityZone
                    ])
                    continue

                # Write a row for each datacenter
                for dc_name in dcs.keys():
                    region = extract_region_from_dc(dc_name, providers)

                    writer.writerow([
                        instance_name,
                        accelerator_name,
                        gpu_count,
                        vcpus,
                        round(memory_gib, 1),
                        accelerator_name,
                        region,
                        0.0,  # SpotPrice
                        # (CloudRift doesn't have spot instances yet)
                        price,
                        dc_name  # Using the datacenter name
                        # as the availability zone
                    ])


if __name__ == '__main__':
    os.makedirs('cloudrift', exist_ok=True)
    create_catalog('cloudrift')
    print('CloudRift catalog saved to cloudrift/vms.csv')
