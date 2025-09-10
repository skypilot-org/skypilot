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
from typing import Any, Dict, List, Optional

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
    'SpotPrice',
    'Price',
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
        raise RuntimeError(f'GraphQL errors: {result["errors"]}')

    try:
        gpu_type = result['data']['gpuTypes'][0]
    except Exception as e:
        error_msg = ('No GPU Types found in RunPod query with'
                     f'gpu_id={gpu_id}, gpu_count={gpu_count}')
        raise ValueError(error_msg) from e

    return gpu_type


def format_price(price: float) -> float:
    """Format price to two decimal places."""
    return round(price, 2)


def format_gpu_name(gpu_type: Dict[str, Any]) -> str:
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
    # Use minVcpu lowestPrice if available, otherwise use defaults
    vcpus = gpu_type.get('lowestPrice', {}).get('minVcpu')
    # This is the GPU memory not the CPU memory.
    memory = gpu_type.get('memoryInGb')

    # Return None if memory or vcpus not valid
    gpu_name = format_gpu_name(gpu_type)
    if not isinstance(vcpus, (float, int)) or vcpus <= 0:
        print(f"Skipping GPU {gpu_name}: vCPUs must be a positive number, not {vcpus}")
        return None
    if not isinstance(memory, (float, int)) or memory <= 0:
        print(f"Skipping GPU {gpu_name}: Memory must be a positive number, not {memory}")
        return None

    # Convert the counts, vCPUs, and memory to float
    # for consistency with skypilot's catalog format
    return {
        'AcceleratorName': gpu_name,
        'AcceleratorCount': float(gpu_count),
        'vCPUs': float(vcpus),
        'MemoryGiB': float(memory),
        'GpuInfo': gpu_name,
    }


def get_instance_configurations(gpu_id: str) -> List[Dict]:
    """Generate instance configurations for a GPU type."""
    instances = []
    detailed_gpu_1 = get_gpu_details(gpu_id, gpu_count=1)
    base_gpu_name = format_gpu_name(detailed_gpu_1)
    gpu_counts = detailed_gpu_1.get('lowestPrice',
                                    {}).get('availableGpuCounts', [])

    for gpu_count in gpu_counts:
        # Get detailed GPU info for this count
        if gpu_count == 1:
            detailed_gpu = detailed_gpu_1
        else:
            detailed_gpu = get_gpu_details(gpu_id, gpu_count)

        # only add secure clouds. skipping community cloud instances.
        if not detailed_gpu["secureCloud"]:
            continue

        # Get basic info including memory & vcpu from the returned data
        # If memory or vpcu is not available, skip this gpu count
        gpu_info = get_gpu_info(detailed_gpu, gpu_count)
        if gpu_info is None:
            continue

        spot_price = base_price = None
        if detailed_gpu['secureSpotPrice'] is not None:
            spot_price = format_price(detailed_gpu['secureSpotPrice'] * gpu_count)
        if detailed_gpu['securePrice'] is not None:
            base_price = format_price(detailed_gpu['securePrice'] * gpu_count)

        for region, zones in REGION_ZONES.items():
            for zone in zones:
                instances.append({
                    **gpu_info,
                    'InstanceType': f'{gpu_count}x_{base_gpu_name}_SECURE',
                    'SpotPrice': spot_price,
                    'Price': base_price,
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

        # Generate instances from GPU ids
        instances = [
            instance for gpu in gpus
            for instance in get_instance_configurations(gpu['id'])
        ]

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
