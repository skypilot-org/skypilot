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
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import runpod
from runpod.api import graphql

# The API currently returns a dynamic number of vCPUs per pod that
# changes frequently (less than 30 mins)
# Therefore we hard code a default number of vCPUs from:
# 1. The previous catalog, if the GPU exists there
# 2. Or if not, the pricing page https://www.runpod.io/pricing
# 3. Otherwise, the minimum of the returned# vCPU count from the API
# The max count of GPUs per pod is set to 8 apart from A40 at 10
DEFAULT_MAX_GPUS = 8
DEFAULT_GPU_INFO: Dict[str, Dict[str, Union[int, float]]] = {
    'A100-80GB': {
        'vcpus': 8.0,
        'memory': 117.0,
        'max_count': 8
    },
    'A100-80GB-SXM': {
        'vcpus': 16.0,
        'memory': 117.0,
        'max_count': 8
    },
    'A30': {
        'vcpus': 12.0,
        'memory': 39.0,
        'max_count': 8
    },
    'A40': {
        'vcpus': 9.0,
        'memory': 48.0,
        'max_count': 10
    },
    'B200': {
        'vcpus': 28.0,
        'memory': 180.0,
        'max_count': 8
    },
    'H100': {
        'vcpus': 16.0,
        'memory': 176.0,
        'max_count': 8
    },
    'H100-NVL': {
        'vcpus': 16.0,
        'memory': 94.0,
        'max_count': 10
    },
    'H100-SXM': {
        'vcpus': 20.0,
        'memory': 125.0,
        'max_count': 8
    },
    'H200-SXM': {
        'vcpus': 12.0,
        'memory': 188.0,
        'max_count': 8
    },
    'L4': {
        'vcpus': 8.0,
        'memory': 45.0,
        'max_count': 10
    },
    'L40': {
        'vcpus': 9.0,
        'memory': 125.0,
        'max_count': 10
    },
    'L40S': {
        'vcpus': 12.0,
        'memory': 62.0,
        'max_count': 8
    },
    'MI300X': {
        'vcpus': 24.0,
        'memory': 283.0,
        'max_count': 8
    },
    'RTX2000-Ada': {
        'vcpus': 6.0,
        'memory': 31.0,
        'max_count': 8
    },
    'RTX3070': {
        'vcpus': 8.0,
        'memory': 30.0,
        'max_count': 8
    },
    'RTX3080': {
        'vcpus': 8.0,
        'memory': 14.0,
        'max_count': 4
    },
    'RTX3080-Ti': {
        'vcpus': 8.0,
        'memory': 18.0,
        'max_count': 5
    },
    'RTX3090': {
        'vcpus': 4.0,
        'memory': 25.0,
        'max_count': 8
    },
    'RTX3090-Ti': {
        'vcpus': 8.0,
        'memory': 24.0,
        'max_count': 9
    },
    'RTX4000-Ada': {
        'vcpus': 8.0,
        'memory': 47.0,
        'max_count': 8
    },
    'RTX4080': {
        'vcpus': 8.0,
        'memory': 22.0,
        'max_count': 5
    },
    'RTX4080-SUPER': {
        'vcpus': 12.0,
        'memory': 62.0,
        'max_count': 6
    },
    'RTX4090': {
        'vcpus': 5.0,
        'memory': 29.0,
        'max_count': 8
    },
    'RTX5000-Ada': {
        'vcpus': 6.0,
        'memory': 62.0,
        'max_count': 8
    },
    'RTX5080': {
        'vcpus': 5.0,
        'memory': 30.0,
        'max_count': 8
    },
    'RTX5090': {
        'vcpus': 6.0,
        'memory': 46.0,
        'max_count': 8
    },
    'RTX6000-Ada': {
        'vcpus': 10.0,
        'memory': 62.0,
        'max_count': 8
    },
    'RTXA4000': {
        'vcpus': 6.0,
        'memory': 35.0,
        'max_count': 12
    },
    'RTXA4500': {
        'vcpus': 7.0,
        'memory': 30.0,
        'max_count': 4
    },
    'RTXA5000': {
        'vcpus': 3.0,
        'memory': 25.0,
        'max_count': 10
    },
    'RTXA6000': {
        'vcpus': 8.0,
        'memory': 50.0,
        'max_count': 10
    },
    'RTXPRO6000': {
        'vcpus': 14.0,
        'memory': 125.0,
        'max_count': 9
    },
    'RTXPRO6000-MaxQ': {
        'vcpus': 18.0,
        'memory': 215.0,
        'max_count': 7
    },
    'RTXPRO6000-WK': {
        'vcpus': 12.0,
        'memory': 186.0,
        'max_count': 4
    },
    'V100-SXM2': {
        'vcpus': 10.0,
        'memory': 62.0,
        'max_count': 8
    },
    'V100-SXM2-32GB': {
        'vcpus': 20.0,
        'memory': 93.0,
        'max_count': 4
    }
}

# A manual list of all CPU IDs RunPod currently supports
# These are named as cpu{generation}{tier}
# TODO: Investigate if these can be found from the API in an automated way
#       currently there is little documentation or API to obtain them.
DEFAULT_CPU_ONLY_IDS = ['cpu3c', 'cpu3g', 'cpu3m', 'cpu5c', 'cpu5g', 'cpu5m']

# for backwards compatibility, force rename some gpus.
# map the generated name to the original name.
# RunPod GPU names currently supported are listed here:
# https://docs.runpod.io/references/gpu-types
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
    'Region',
    'SpotPrice',
    'Price',
    'AvailabilityZone',
    'GpuInfo',
]

# Mapping of regions to their availability zones
# TODO: Investigate if these can be found from the API in an automated way
#       currently there is little documentation or API to obtain them.
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


def get_gpu_details(gpu_id: str, gpu_count: int = 1) -> Dict[str, Any]:
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
        gpu_query_result = result['data']['gpuTypes'][0]
    except Exception as e:
        error_msg = ('No GPU Types found in RunPod query with '
                     f'gpu_id={gpu_id}, gpu_count={gpu_count}')
        raise ValueError(error_msg) from e

    return gpu_query_result


def query_cpu_id(cpu_id: str) -> List[Dict[str, Any]]:
    query = f"""
    query SecureCpuTypes {{
      cpuFlavors(input: {{id: "{cpu_id}"}}) {{
        id
        groupId
        displayName
        minVcpu
        maxVcpu
        vcpuBurstable
        ramMultiplier
        diskLimitPerVcpu
      }}
    }}"""
    result = graphql.run_graphql_query(query)

    if 'errors' in result:
        raise RuntimeError(f'GraphQL errors: {result["errors"]}')

    try:
        cpu_query_result = result['data']['cpuFlavors']
    except Exception as e:
        error_msg = (f'No CPU Types found in RunPod query with cpu_id={cpu_id}')
        raise ValueError(error_msg) from e

    return cpu_query_result


def query_cpu_specifics(cpu_id: str,
                        cpu_spec_id: str,
                        data_center_id: str = '') -> List[Dict[str, Any]]:
    query = f"""
    query SecureCpuTypes {{
      cpuFlavors(input: {{id: "{cpu_id}"}}) {{
        id
        groupId
        displayName
        specifics(input: {{instanceId: "{cpu_spec_id}", dataCenterId: "{data_center_id}"}}) {{
          stockStatus
          securePrice
          slsPrice
        }}
      }}
    }}"""
    result = graphql.run_graphql_query(query)

    if 'errors' in result:
        raise RuntimeError(f'GraphQL errors: {result["errors"]}')

    try:
        cpu_query_result = result['data']['cpuFlavors']
    except Exception as e:
        error_msg = ('No CPU Types found in RunPod query with '
                     f'cpu_id={cpu_id} cpu_spec_id={cpu_spec_id}')
        raise ValueError(error_msg) from e

    return cpu_query_result


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


def get_gpu_info(base_gpu_name: str, gpu_type: Dict[str, Any],
                 gpu_count: int) -> Optional[Dict[str, Any]]:
    """Extract relevant GPU information from RunPod GPU type data."""

    # Use minVcpu & minMemory in the lowestPrice info if defaults not available
    # Don't use this value by default as it is dynamic and changes often
    vcpus = DEFAULT_GPU_INFO.get(base_gpu_name, {}).get('vcpus')
    if vcpus is None:
        vcpus = gpu_type.get('lowestPrice', {}).get('minVcpu')
    else:
        vcpus = vcpus * gpu_count

    # This is the (minimum) pod RAM memory (scaled to count)
    memory = DEFAULT_GPU_INFO.get(base_gpu_name, {}).get('memory')
    if memory is None:
        memory = gpu_type.get('lowestPrice', {}).get('minMemory')

    # This is the VRAM memory per GPU (not scaled to count)
    gpu_memory = gpu_type.get('memoryInGb', 0)

    # Return None if memory or vcpus not valid
    if not isinstance(vcpus, (float, int)) or vcpus <= 0:
        print(f'Skipping GPU {base_gpu_name}:'
              ' vCPUs must be a positive number, not {vcpus}')
        return None
    if not isinstance(memory, (float, int)) or memory <= 0:
        print(f'Skipping GPU {base_gpu_name}:'
              ' Memory must be a positive number, not {memory}')
        return None

    gpu_info_dict = {
        'Gpus': [{
            'Name': gpu_type['displayName'],
            'Manufacturer': gpu_type['manufacturer'],
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory * gpu_count,
    }
    gpu_info = json.dumps(gpu_info_dict).replace('"', '\'')

    # Convert the counts, vCPUs, and memory to float
    # for consistency with skypilot's catalog format
    return {
        'vCPUs': float(vcpus),
        'MemoryGiB': float(memory * gpu_count),
        'GpuInfo': gpu_info,
    }


def get_cpu_instance_configurations(cpu_id: str) -> List[Dict[str, Any]]:
    """Retrieves available CPU instance configurations for a CPU ID.
    This function queries the available vCPU and memory combinations
    for given CPU types over all supported regions and zones.
    Args:
        cpu_id (str): The identifier for the CPU type to query.
    Returns:
        List[Dict]: A list of dictionaries, each representing an instance
            configuration with the following keys:
                - 'InstanceType': Unique identifier for the instance type (str)
                - 'AcceleratorName': Name of accelerator (None for CPU-only)
                - 'AcceleratorCount': Number of accelerators (None for CPU-only)
                - 'vCPUs': Number of virtual CPUs (float).
                - 'SpotPrice': Spot price for the instance (None currently)
                - 'MemoryGB': Amount of memory in GB (float).
                - 'Price': Secure price for the instance (float).
                - 'Region': Cloud region name (str).
                - 'AvailabilityZone': Availability zone within the region (str).
    """

    instances = []

    # Get vCPU and memory combinations for this CPU type
    for cpu_info in query_cpu_id(cpu_id):
        if not cpu_info.get('minVcpu') or not cpu_info.get(
                'maxVcpu') or not cpu_info.get('ramMultiplier'):
            print(f'Skipping CPU {cpu_id} due to missing vCPU or memory info')
            continue
        min_vcpu = int(cpu_info['minVcpu'])
        max_vcpu = int(cpu_info['maxVcpu'])
        ram_multiplier = int(cpu_info['ramMultiplier'])

        # Iterate over possible vCPU counts (powers of 2 up to 2**8=512 vCPUs)
        vcpu_counts = [
            2**ii
            for ii in range(1, 9)
            if 2**ii >= min_vcpu and 2**ii <= max_vcpu
        ]
        for vcpus in vcpu_counts:
            memory = int(vcpus * ram_multiplier)
            cpu_spec_id = f'{cpu_id}-{vcpus}-{memory}'

            # Iterate over all regions and zones
            for region, zones in REGION_ZONES.items():
                for zone in zones:
                    for cpu_spec_output in query_cpu_specifics(
                            cpu_id, cpu_spec_id, zone):
                        instances.append({
                            'InstanceType': cpu_spec_id,
                            'AcceleratorName': None,
                            'AcceleratorCount': None,
                            'vCPUs': float(vcpus),
                            'SpotPrice': None,
                            'MemoryGiB': float(memory),
                            'Price': float(
                                cpu_spec_output['specifics']['securePrice']),
                            'Region': region,
                            'AvailabilityZone': zone,
                            'GpuInfo': None,
                        })

    return instances


def get_gpu_instance_configurations(gpu_id: str) -> List[Dict[str, Any]]:
    """Retrieves available GPU instance configurations for a given GPU ID.
    Only secure cloud instances are included (community cloud instances
    are skipped).  Each configuration includes pricing (spot and base), region,
    availabilityzone, and hardware details.
    If the GPU type is not found a default maximum GPU count & memory is used.
    Args:
        gpu_id (str): The identifier of the GPU type
    Returns:
        List[Dict]: A list of dictionaries, each representing an instance
            configuration with the following keys:
                - 'InstanceType': String describing the instance type
                - 'AcceleratorName': Name of the GPU accelerator.
                - 'AcceleratorCount': Number of GPUs in the instance.
                - 'SpotPrice': Spot price for the instance (if available).
                - 'Price': Base price for the instance (if available).
                - 'Region': Cloud region.
                - 'AvailabilityZone': Availability zone within the region.
                - Additional hardware info (e.g., memory, vCPU) from GPU info.
    """

    instances = []
    detailed_gpu_1 = get_gpu_details(gpu_id, gpu_count=1)
    base_gpu_name = format_gpu_name(detailed_gpu_1)

    # If the GPU isn't in DEFAULT_GPU_INFO we default to a max of 8 GPUs
    if base_gpu_name in DEFAULT_GPU_INFO:
        max_gpu_count = DEFAULT_GPU_INFO[base_gpu_name].get(
            'max_count', DEFAULT_MAX_GPUS)
    else:
        max_gpu_count = DEFAULT_MAX_GPUS

    for gpu_count in range(1, int(max_gpu_count) + 1):
        # Get detailed GPU info for this count
        if gpu_count == 1:
            detailed_gpu = detailed_gpu_1
        else:
            detailed_gpu = get_gpu_details(gpu_id, gpu_count)

        # Only add secure clouds skipping community cloud instances.
        if not detailed_gpu['secureCloud']:
            continue

        # Get basic info including memory & vcpu from the returned data
        # If memory or vpcu is not available, skip this gpu count
        gpu_info = get_gpu_info(base_gpu_name, detailed_gpu, gpu_count)
        if gpu_info is None:
            continue

        spot_price = base_price = None
        if detailed_gpu['secureSpotPrice'] is not None:
            spot_price = format_price(detailed_gpu['secureSpotPrice'] *
                                      gpu_count)
        if detailed_gpu['securePrice'] is not None:
            base_price = format_price(detailed_gpu['securePrice'] * gpu_count)

        for region, zones in REGION_ZONES.items():
            for zone in zones:
                instances.append({
                    'InstanceType': f'{gpu_count}x_{base_gpu_name}_SECURE',
                    'AcceleratorName': base_gpu_name,
                    'AcceleratorCount': float(gpu_count),
                    'SpotPrice': spot_price,
                    'Price': base_price,
                    'Region': region,
                    'AvailabilityZone': zone,
                    **gpu_info
                })

    return instances


def fetch_runpod_catalog(no_gpu: bool, no_cpu: bool) -> pd.DataFrame:
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

        # Get GPU list from API
        instances = []
        if not no_gpu:
            gpus = runpod.get_gpus()
            if not gpus:
                raise ValueError('No GPU types returned from RunPod API')

            # Generate instances from GPU ids
            instances.extend([
                instance for gpu in gpus
                for instance in get_gpu_instance_configurations(gpu['id'])
            ])

        if not no_cpu:
            # Generate instances from CPU ids
            instances.extend([
                instance for cpu_id in DEFAULT_CPU_ONLY_IDS
                for instance in get_cpu_instance_configurations(cpu_id)
            ])

        return instances

    except Exception as e:
        print(traceback.format_exc())
        print(f'Failed to fetch RunPod catalog: {e}', file=sys.stderr)
        raise


def save_catalog(instances: List[Dict[str, Any]], output_file: str) -> None:
    """Save the catalog to a CSV file."""

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

    df.to_csv(output_file, index=False)
    print(f'RunPod catalog saved to {output_file}')


def main():
    parser = argparse.ArgumentParser(
        description='Update RunPod catalog for SkyPilot')
    parser.add_argument('--output-dir',
                        default='runpod',
                        help='Directory to save the catalog files')
    parser.add_argument('--no-gpu',
                        help='Do not fetch and store catalog for RunPod GPUs',
                        default=False,
                        action='store_true')
    parser.add_argument(
        '--no-cpu',
        help='Do not fetch and store catalog for RunPod CPUs (serverless)',
        default=False,
        action='store_true')
    args = parser.parse_args()

    try:
        os.makedirs(args.output_dir, exist_ok=True)

        catalog = fetch_runpod_catalog(args.no_gpu, args.no_cpu)

        output_file_location = os.path.join(args.output_dir, 'vms.csv')
        save_catalog(catalog, output_file_location)

    except ValueError as e:
        print(f'Error updating RunPod catalog: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
