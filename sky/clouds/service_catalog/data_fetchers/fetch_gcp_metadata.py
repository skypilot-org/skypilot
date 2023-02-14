"""Fetches the metadata of GCP instances and accelerators.

This script fetches the metadata of GCP from a public GitHub repository
and saves them as CSV files. The metadata include all the information
except the (on-demand and spot) prices.

NOTE: The TPU metadata is not fetched by this script. It is manually
maintained by the SkyPilot developers.
"""
import os
from typing import List, Tuple

import pandas as pd

# We rely on the data from https://github.com/Cyclenerd/google-cloud-pricing-cost-calculator  # pylint: disable=line-too-long
# TODO(woosuk): use the official GCP APIs to fetch the data.
VM_CSV_URL = 'https://raw.githubusercontent.com/Cyclenerd/google-cloud-pricing-cost-calculator/master/tools/machinetypezone.csv'  # pylint: disable=line-too-long
GPU_CSV_URL = 'https://raw.githubusercontent.com/Cyclenerd/google-cloud-pricing-cost-calculator/master/tools/acceleratortypezone.csv'  # pylint: disable=line-too-long

# Official list of the zones in GCP.
GCP_VM_ZONES_URL = 'https://cloud.google.com/compute/docs/regions-zones'

# Regions that do not exist in https://cloud.google.com/compute/docs/regions-zones
PRIVATE_REGIONS = [
    'us-east2',
    'us-east7',
    'us-central2',  # NOTE: us-central2-b has TPU v4.
    'europe-west5',
]

# Zones that do not exist in https://cloud.google.com/compute/docs/regions-zones
PRIVATE_ZONES = [
    'us-east1-a',
    'us-central1-d',
]

# GPU names in GCP -> GPU names in SkyPilot.
GPU_NAMES = {
    'nvidia-a100-80gb': 'A100-80GB',
    'nvidia-tesla-a100': 'A100',
    'nvidia-tesla-k80': 'K80',
    'nvidia-tesla-p100': 'P100',
    'nvidia-tesla-p4': 'P4',
    'nvidia-tesla-t4': 'T4',
    'nvidia-tesla-v100': 'V100',
}

# Refer to: https://cloud.google.com/compute/docs/gpus
# NOTE: 16xA100 machines are only supported in certain zones.
# See https://cloud.google.com/compute/docs/gpus/gpu-regions-zones#limitations
GPU_TYPES_TO_COUNTS = {
    'A100-80GB': [1, 2, 4, 8],
    'A100': [1, 2, 4, 8, 16],
    'K80': [1, 2, 4, 8],
    'P100': [1, 2, 4],
    'P4': [1, 2, 4],
    'T4': [1, 2, 4],
    'V100': [1, 2, 4, 8],
}


def drop_private_zones(df: pd.DataFrame) -> pd.DataFrame:
    # Remove private regions.
    df = df[~df['Region'].isin(PRIVATE_REGIONS)]
    # Remove private zones.
    df = df[~df['AvailabilityZone'].isin(PRIVATE_ZONES)]
    return df


def assert_zones_are_valid(zones: List[str]) -> None:
    # Double-check that all the regions and zones are valid.
    gcp_vm_zones_df = pd.read_html(GCP_VM_ZONES_URL)[0]
    gcp_zones = gcp_vm_zones_df['Zones'].unique()
    for zone in zones:
        assert zone in gcp_zones


def get_vm_df() -> Tuple[pd.DataFrame, List[str]]:
    vm_df = pd.read_csv(VM_CSV_URL, delimiter=';')
    # Drop deprecated VMs.
    vm_df = vm_df[vm_df['DEPRECATED'].isna()]
    # Drop unused columns.
    vm_df = vm_df[['NAME', 'CPUS', 'MEMORY_GB', 'ZONE']]
    # Rename the columns.
    vm_df = vm_df.rename(
        columns={
            'NAME': 'InstanceType',
            'CPUS': 'vCPUs',
            'MEMORY_GB': 'MemoryGiB',
            'ZONE': 'AvailabilityZone',
        })
    # Add the Region column.
    vm_df['Region'] = vm_df['AvailabilityZone'].apply(lambda zone: zone[:-2])

    # Drop private regions and zones.
    vm_df = drop_private_zones(vm_df)

    # Convert vCPUs and MemoryGiB to float.
    vm_df['vCPUs'] = vm_df['vCPUs'].astype(float)
    vm_df['MemoryGiB'] = vm_df['MemoryGiB'].astype(float)

    # Double-check that all the regions and zones are valid.
    assert_zones_are_valid(vm_df['AvailabilityZone'].unique())

    # Reorder the columns.
    vm_df = vm_df[[
        'InstanceType',
        'vCPUs',
        'MemoryGiB',
        'Region',
        'AvailabilityZone',
    ]]
    vm_df = vm_df.reset_index(drop=True)

    # Get the list of zones that support 16xA100.
    a2_megagpu_16g_df = vm_df[vm_df['InstanceType'] == 'a2-megagpu-16g']
    a2_megagpu_16g_zones = a2_megagpu_16g_df['AvailabilityZone'].unique()
    return vm_df, a2_megagpu_16g_zones


def get_gpu_df(a2_megagpu_16g_zones: List[str]) -> pd.DataFrame:
    gpu_df = pd.read_csv(GPU_CSV_URL, delimiter=';')
    # Rename the columns.
    gpu_df = gpu_df.rename(columns={
        'NAME': 'AcceleratorName',
        'ZONE': 'AvailabilityZone',
    })
    # Add the Region column.
    gpu_df['Region'] = gpu_df['AvailabilityZone'].apply(lambda zone: zone[:-2])

    # Drop VWS GPUs.
    gpu_df = gpu_df[~gpu_df['AcceleratorName'].str.contains('vws')]
    # Rename the GPUs.
    gpu_df['AcceleratorName'] = gpu_df['AcceleratorName'].apply(
        lambda name: GPU_NAMES[name])

    # Drop private regions and zones.
    gpu_df = drop_private_zones(gpu_df)

    # Add the AcceleratorCount column.
    gpu_df['AcceleratorCount'] = gpu_df['AcceleratorName'].apply(
        lambda acc_name: GPU_TYPES_TO_COUNTS[acc_name])
    gpu_df = gpu_df.explode('AcceleratorCount', ignore_index=True)
    gpu_df['AcceleratorCount'] = gpu_df['AcceleratorCount'].astype(int)

    # Remove 16xA100 machines from zones that do not support them.
    gpu_df = gpu_df[~((gpu_df['AcceleratorName'] == 'A100') &
                      (gpu_df['AcceleratorCount'] == 16) &
                      (~gpu_df['AvailabilityZone'].isin(a2_megagpu_16g_zones)))]

    # Double-check that all the regions and zones are valid.
    assert_zones_are_valid(gpu_df['AvailabilityZone'].unique())

    # Add GPUInfo column.
    gpu_df['GpuInfo'] = ''
    # Reorder the columns.
    gpu_df = gpu_df[[
        'AcceleratorName',
        'AcceleratorCount',
        'GpuInfo',
        'Region',
        'AvailabilityZone',
    ]]
    gpu_df = gpu_df.reset_index(drop=True)
    return gpu_df


if __name__ == '__main__':
    os.makedirs('metadata/gcp/', exist_ok=True)

    gcp_vm_df, gcp_a2_megagpu_16g_zones = get_vm_df()
    gcp_vm_df.to_csv('metadata/gcp/instances.csv', index=False)
    print('GCP VM metadata saved to metadata/gcp/instances.csv')

    gcp_gpu_df = get_gpu_df(gcp_a2_megagpu_16g_zones)
    gcp_gpu_df.to_csv('metadata/gcp/gpus.csv', index=False)
    print('GCP GPU metadata saved to metadata/gcp/gpus.csv')
