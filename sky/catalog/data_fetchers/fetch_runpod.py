#!/usr/bin/env python3
"""Fetches RunPod GPU types and pricing information using GraphQL API.

This script is designed to work similarly to SkyPilot's other cloud fetchers,
particularly fetch_gcp.py. It queries RunPod's GraphQL API to get GPU types
and generates a catalog CSV file compatible with SkyPilot's service catalog.

Usage:
    python fetch_runpod.py [--output OUTPUT_FILE]
"""

import argparse
import csv
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import HTTPError

# Constants
RUNPOD_GRAPHQL_ENDPOINT = 'https://api.runpod.io/graphql'
DEFAULT_OUTPUT_FILE = 'runpod-catalog.csv'
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds

VCPU_MAP = {
    'B200': 28,
    'A40': 9,
    'MI300X': 24,
    'A100 PCIe': 8,
    'A100 SXM': 16,
    'A30': 8,
    'RTX 3070': 8,
    'RTX A4500': 8,
    'RTX 3080': 7,
    'RTX 3080 Ti': 8,
    'RTX 3090': 4,
    'RTX 3090 Ti': 14,
    'RTX 4070 Ti': 14,
    'RTX 4080': 16,
    'RTX 4080 SUPER': 12,
    'RTX 4090': 8,
    'RTX 5080': 7,
    'RTX 5090': 12,
    'H100 SXM': 24,
    'H100 NVL': 19,
    'H100 PCIe': 8,
    'H200 SXM': 24,
    'L4': 12,
    'L40': 8,
    'L40S': 16,
    'RTX 2000 Ada': 6,
    'RTX 4000 Ada': 9,
    'RTX 5000 Ada': 14,
    'RTX 6000 Ada': 14,
    'RTX A2000': 4,
    'RTX A4000': 4,
    'RTX A5000': 9,
    'RTX A6000': 9,
    'RTX PRO 6000': 16,
    'V100 FHHL': 4,
    'Tesla V100': 4,
    'V100 SXM2': 10,
    'V100 SXM2 32GB': 20,
}

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# GraphQL query for GPU types
GPU_TYPES_QUERY = """
query GpuTypes {
  gpuTypes {
       maxGpuCount
    maxGpuCountCommunityCloud
    maxGpuCountSecureCloud
    minPodGpuCount
    nodeGroupGpuSizes
      nodeGroupDatacenters {
          location
          id
          name
          listed
      }
    id
    displayName
    manufacturer
    memoryInGb
    cudaCores
    secureCloud
    communityCloud
    securePrice
    clusterPrice
    communityPrice
    oneMonthPrice
    threeMonthPrice
    sixMonthPrice
    oneWeekPrice
    communitySpotPrice
    secureSpotPrice
    throughput
  }
}
"""


class RunPodFetcher:
    """Fetches RunPod GPU types and pricing information."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the fetcher with optional API key.

        Args:
            api_key: RunPod API key.
            If not provided, tries to get from RUNPOD_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get('RUNPOD_API_KEY')
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update(
                {'Authorization': f'Bearer {self.api_key}'})
        self.session.headers.update({'Content-Type': 'application/json'})

    def _make_graphql_request(self,
                              query: str,
                              variables: Optional[Dict] = None) -> Any:
        """Make a GraphQL request to RunPod API.

        Args:
            query: GraphQL query string
            variables: Optional query variables

        Returns:
            Response data as dictionary

        Raises:
            Exception: If request fails after retries
        """
        payload = {'query': query, 'variables': variables or {}}

        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.session.post(RUNPOD_GRAPHQL_ENDPOINT,
                                             json=payload,
                                             timeout=30)
                response.raise_for_status()

                data = response.json()
                if 'errors' in data:
                    raise Exception(f'GraphQL errors: {data["errors"]}')

                return data['data']

            except HTTPError as e:
                logger.warning(f'Request failed '
                               f'(attempt {attempt + 1}/{RETRY_ATTEMPTS}): {e}')
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    raise e
            except Exception as e:
                logger.warning(f'Request failed:  {e}')
                raise e
        return None

    def fetch_gpu_types(self) -> List[Dict]:
        """Fetch GPU types from RunPod API.

        Returns:
            List of GPU type dictionaries
        """
        logger.info('Fetching GPU types from RunPod...')
        data = self._make_graphql_request(GPU_TYPES_QUERY)
        gpu_types = data.get('gpuTypes', [])
        logger.info(f'Fetched {len(gpu_types)} GPU types')
        return gpu_types

    def _convert_to_catalog_format(self, gpu_types: List[Dict]) -> List[Dict]:
        """Convert RunPod GPU data to SkyPilot catalog format.

        Args:
            gpu_types: List of GPU type data from RunPod

        Returns:
            List of dictionaries in SkyPilot catalog format
        """
        catalog_entries = []

        for gpu in gpu_types:
            if gpu['id'] == 'unknown':
                continue

            pod_instances = []
            if gpu.get('secureCloud'):
                pod_instances.append({
                    'cloud_type': 'SECURE',
                    'price': gpu['securePrice']
                })

            if gpu.get('communityCloud'):
                pod_instances.append({
                    'cloud_type': 'COMMUNITY',
                    'price': gpu['communityPrice']
                })

            zones = ['CA', 'CZ', 'IS', 'NL', 'NO', 'RO', 'SE', 'US']
            spot_price = gpu['secureSpotPrice'] if gpu[
                'secureSpotPrice'] else None
            display_name = ''.join(gpu['displayName'].split())
            # Create entry for each zone
            for zone in zones:
                for pod_instance in pod_instances:
                    entry = {
                        'InstanceType': f'1x_{display_name}_'
                                        f'{pod_instance["cloud_type"]}',
                        'AcceleratorName': str(display_name),
                        'AcceleratorCount': '1',  # Default to 1
                        'vCPUs': VCPU_MAP[display_name]
                                 if display_name in VCPU_MAP else 2,
                        'MemoryGiB': float(gpu.get('memoryInGb', 0)),
                        'GpuInfo': display_name,
                        'Region': zone,
                        'SpotPrice': spot_price,
                        'Price': str(pod_instance['price']
                                    ),  # Default to on-demand price
                        'Name': gpu['id'],
                    }
                    catalog_entries.append(entry)

                    # Add entries for multi-GPU configurations if supported
                    max_gpus = gpu.get('maxGpuCount', 1)
                    if max_gpus > 1:
                        for gpu_count in [2, 4, 8]:
                            if gpu_count <= max_gpus:
                                multi_gpu_entry = entry.copy()
                                multi_gpu_entry['InstanceType'] = (
                                    f'{gpu_count}x_{display_name}_'
                                    f'{pod_instance["cloud_type"]}')
                                multi_gpu_entry['AcceleratorCount'] = float(
                                    gpu_count)
                                multi_gpu_entry['MemoryGiB'] = float(
                                    gpu_count * float(gpu.get('memoryInGb', 0)))
                                multi_gpu_entry['SpotPrice'] = (str(
                                    spot_price *
                                    gpu_count) if spot_price else None)
                                multi_gpu_entry['Price'] = str(
                                    pod_instance['price'] * gpu_count)
                                catalog_entries.append(multi_gpu_entry)

        return catalog_entries

    def write_catalog(self, output_file: str):
        """Fetch GPU types and write to catalog CSV file.

        Args:
            output_file: Path to output CSV file
        """
        # Fetch GPU types
        gpu_types = self.fetch_gpu_types()

        # Convert to catalog format
        catalog_entries = self._convert_to_catalog_format(gpu_types)

        if not catalog_entries:
            logger.warning('No catalog entries generated')
            return

        # Write to CSV
        logger.info(f'Writing {len(catalog_entries)} entries to {output_file}')

        fieldnames = [
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'vCPUs',
            'MemoryGiB',
            'GpuInfo',
            'Region',
            'SpotPrice',
            'OnDemandPrice',
            'Price',
            'Name',
        ]

        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(catalog_entries)

        logger.info(f'Successfully wrote catalog to {output_file}')


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fetch RunPod GPU types and generate SkyPilot catalog')
    parser.add_argument(
        '--output',
        '-o',
        default=DEFAULT_OUTPUT_FILE,
        help=f'Output CSV file (default: {DEFAULT_OUTPUT_FILE})',
    )
    parser.add_argument(
        '--api-key',
        help='RunPod API key (can also use RUNPOD_API_KEY env var)')
    parser.add_argument('--debug',
                        action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        fetcher = RunPodFetcher(api_key=args.api_key)
        fetcher.write_catalog(args.output)
    except Exception as e:
        logger.error(f'Failed to fetch RunPod catalog: {e}')
        raise e


if __name__ == '__main__':
    main()
