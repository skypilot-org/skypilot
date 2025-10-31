"""A script that generates the Runpod catalog.

Usage:
    python fetch_runpod.py [-h] [--output-dir OUTPUT_DIR] [--gpu-ids GPU_IDS]

The RUNPOD_API_KEY environment variable must be set with a valid read-access
RunPod API key.

If --gpu-ids is provided, only fetches details for
the specified GPU IDs (comma-separated). Otherwise, fetches all available GPUs.
This flag is intended for testing and debugging individual GPU configurations.
"""

import argparse
import os
import sys
import traceback
from typing import Dict, List, Optional

import pandas as pd
import runpod
from runpod.api import graphql

# for backwards compatibility, force rename some gpus.
# map the generated name to the original name
GPU_NAME_OVERRIDES = {
    'A100-PCIe': 'A100-80GB',
    'A100-SXM': 'A100-80GB-SXM',
    'H100-PCIe': 'H100',
}

# Constants
USEFUL_COLUMNS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Region',
    'Price',
    'SpotPrice',
    'AvailabilityZone',
]

# Default values for instance specifications
# These are only used if the API response does not include vCPUs or memory.
# Usually that means the gpu is not available in the desired quantity.
DEFAULT_VCPUS = 8.0
DEFAULT_MEMORY_GIB = 80.0
MEMORY_PER_GPU = 80.0  # Base memory per GPU

# Mapping of regions to their availability zones
REGION_ZONES = {
    'CA': ['CA-MTL-1', 'CA-MTL-2', 'CA-MTL-3'],
    'CZ': ['EU-CZ-1'],
    'IS': ['EUR-IS-1', 'EUR-IS-2', 'EUR-IS-3'],
    'NL': ['EU-NL-1'],
    'NO': ['EU-SE-1'],
    'RO': ['EU-RO-1'],
    'SE': ['EU-SE-1'],
    'US': [
        'US-CA-1',
        'US-CA-2',
        'US-DE-1',
        'US-GA-1',
        'US-GA-2',
        'US-IL-1',
        'US-KS-1',
        'US-KS-2',
        'US-NC-1',
        'US-TX-1',
        'US-TX-2',
        'US-TX-3',
        'US-TX-4',
        'US-WA-1',
    ],
}


def get_gpu_details(gpu_id: str, gpu_count: int = 1) -> Dict:
    """Get detailed GPU information using GraphQL query.

    This uses a custom graphql query because runpod.get_gpu(id) does not include
    full lowestPrice information.
    """
    query = f"""
    query GpuTypes {{
      gpuTypes(input: {{id: "{gpu_id}"}}) {{
        maxGpuCount
        id
        displayName
        manufacturer
        memoryInGb
        cudaCores
        secureCloud
        communityCloud
        securePrice
        communityPrice
        oneMonthPrice
        threeMonthPrice
        oneWeekPrice
        communitySpotPrice
        secureSpotPrice
        lowestPrice(input: {{gpuCount: {gpu_count}}}) {{
          minimumBidPrice
          uninterruptablePrice
          minVcpu
          minMemory
          stockStatus
          compliance
          maxUnreservedGpuCount
          availableGpuCounts
        }}
      }}
    }}
    """

    result = graphql.run_graphql_query(query)
    if 'errors' in result:
        raise ValueError(f'GraphQL errors: {result["errors"]}')

    return result['data']['gpuTypes'][0]


def format_price(price: float) -> float:
    """Format price to two decimal places."""
    return round(price, 2)


def get_gpu_counts(max_count: int) -> List[int]:
    """Generate list of GPU counts based on powers of 2 and max count.

    RunPod supports any number up to max_count. We only generate a list of
    powers of two & the max count.

    For example, if the max count is 8, create a row for each of [1, 2, 4, 8].
    For max count 7, create a row for each of [1, 2, 4, 7].
    """
    counts = []
    power = 1
    while power <= max_count:
        counts.append(power)
        power *= 2

    # Add max count if it's not already included (not a power of 2)
    if max_count not in counts:
        counts.append(max_count)

    return sorted(counts)


def format_gpu_name(gpu_type: Dict) -> str:
    """Format GPU name to match the required format.

    Programmatically generates the name from RunPod's GPU display name.
    For compatibility, some names are overridden in GPU_NAME_OVERRIDES.
    """
    # Extract base name
    base_name = (
        gpu_type['displayName']
        # handle formatting names of RTX GPUs
        .replace('RTX PRO ', 'RTXPRO')
        # skypilot has no hyphen in RTX names. ie. RTX3090, not RTX-3090
        .replace('RTX ', 'RTX')
        # replace spaces with hyphens
        .replace(' ', '-'))

    # handle name overrides for backwards compatibility
    if base_name in GPU_NAME_OVERRIDES:
        base_name = GPU_NAME_OVERRIDES[base_name]

    return base_name


def get_gpu_info(gpu_type: Dict, gpu_count: int) -> Dict:
    """Extract relevant GPU information from RunPod GPU type data."""
    # Use minVcpu and minMemory from lowestPrice if available,
    # otherwise use defaults
    vcpus = gpu_type.get('lowestPrice', {}).get('minVcpu')
    memory = gpu_type.get('lowestPrice', {}).get('minMemory')

    # Fall back to defaults if values are None
    # scale default value by gpu_count
    vcpus = DEFAULT_VCPUS * gpu_count if vcpus is None else vcpus
    memory = MEMORY_PER_GPU * gpu_count if memory is None else memory

    gpu_name = format_gpu_name(gpu_type)

    return {
        'AcceleratorName': gpu_name,
        'AcceleratorCount': float(gpu_count),
        'vCPUs': vcpus,
        'MemoryGiB': memory,
        'GpuInfo': gpu_name,
    }


def get_instance_configurations(gpu_type: Dict) -> List[Dict]:
    """Generate instance configurations for a GPU type."""
    instances = []
    base_gpu_name = format_gpu_name(gpu_type)
    gpu_counts = gpu_type.get('lowestPrice', {}).get('availableGpuCounts', [])

    for gpu_count in gpu_counts:
        # Get detailed GPU info for this count
        detailed_gpu = get_gpu_details(gpu_type['id'], gpu_count)

        # only add secure clouds. skipping community cloud instances.
        if not detailed_gpu['secureCloud']:
            continue

        for region, zones in REGION_ZONES.items():
            for zone in zones:
                spot_price = None
                if detailed_gpu['secureSpotPrice'] is not None:
                    spot_price = format_price(detailed_gpu['secureSpotPrice'] *
                                              gpu_count)

                instances.append({
                    'InstanceType': f'{gpu_count}x_{base_gpu_name}_SECURE',
                    **get_gpu_info(detailed_gpu, gpu_count),
                    'SpotPrice': spot_price,
                    'Price': format_price(detailed_gpu['securePrice'] *
                                          gpu_count),
                    'Region': region,
                    'AvailabilityZone': zone,
                })

    return instances


def fetch_runpod_catalog(gpu_ids: Optional[str] = None) -> pd.DataFrame:
    """Fetch and process RunPod GPU catalog data.

    Args:
        gpu_ids: Optional comma-separated list of RunPod GPU IDs to fetch.
                If None, fetch all available GPUs.
    """
    try:
        # Initialize RunPod client
        runpod.api_key = os.getenv('RUNPOD_API_KEY')
        if not runpod.api_key:
            raise ValueError('RUNPOD_API_KEY environment variable not set')

        # Get GPU list either from API or provided IDs
        if gpu_ids:
            gpus = [{'id': gpu_id.strip()} for gpu_id in gpu_ids.split(',')]
        else:
            gpus = runpod.get_gpus()
            if not gpus:
                raise ValueError('No GPU types returned from RunPod API')

        # Generate instances from GPU types
        instances = []
        for gpu in gpus:
            # initial gpu details. later, request specific quantity details
            gpu_type = get_gpu_details(gpu['id'])
            instances.extend(get_instance_configurations(gpu_type))

        # Create DataFrame
        df = pd.DataFrame(instances)

        # Validate required columns
        missing_columns = set(USEFUL_COLUMNS) - set(df.columns)
        if missing_columns:
            raise ValueError(f'Missing required columns: {missing_columns}')

        # Ensure all required columns are present and in correct order
        df = df[USEFUL_COLUMNS]

        # Sort for consistency
        df.sort_values(['AcceleratorName', 'InstanceType', 'AvailabilityZone'],
                       inplace=True)

        return df

    except Exception as e:
        print(traceback.format_exc())
        print(f'Failed to fetch RunPod catalog: {e}', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Update RunPod catalog for SkyPilot')
    parser.add_argument('--output-dir',
                        default='runpod',
                        help='Directory to save the catalog files')
    parser.add_argument(
        '--gpu-ids',
        help='Comma-separated list of RunPod GPU IDs to fetch. '
        'If not provided, fetch all GPUs.',
    )
    args = parser.parse_args()

    try:
        # Create output directory
        os.makedirs(args.output_dir, exist_ok=True)

        # Fetch and save catalog
        df = fetch_runpod_catalog(args.gpu_ids)
        output_path = os.path.join(args.output_dir, 'vms.csv')
        df.to_csv(output_path, index=False)
        print(f'RunPod Service Catalog saved to {output_path}')

    except ValueError as e:
        print(f'Error updating RunPod catalog: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
