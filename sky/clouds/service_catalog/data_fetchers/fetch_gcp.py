"""A script that generates Google Cloud GPU catalog.

Google Cloud does not have an API for querying TPU/GPU offerings, so this
part is currently using hard-coded information in the gcp_data directory.

TODO: Add support for regular VMs
https://cloud.google.com/sdk/gcloud/reference/compute/machine-types/list
"""

import pandas as pd

GCP_DATA_DIR = './gcp_data/'

# Source: https://cloud.google.com/compute/vm-instance-pricing
VM_PRICING = GCP_DATA_DIR + 'pricing/vm.csv'

# Source: https://cloud.google.com/compute/docs/gpus/gpu-regions-zones
GPU_ZONES = GCP_DATA_DIR + 'zones/gpu.csv'
NO_A100_16G_ZONES = ['asia-northeast3-a', 'asia-northeast3-b', 'us-west4-b']
# Source: https://cloud.google.com/compute/gpus-pricing
GPU_PRICING = GCP_DATA_DIR + 'pricing/gpu.csv'

# Source: https://cloud.google.com/tpu/docs/regions-zones
TPU_ZONES = GCP_DATA_DIR + 'zones/tpu.csv'
# Source: https://cloud.google.com/tpu/pricing
# NOTE: The CSV file does not completely align with the data in the website.
# Differences are:
# 1. We added us-east1 for TPU Research Cloud.
# 2. We deleted TPU v3 pods from us-central1, because we found that GCP is not
#    actually supporting them in the region.
# 3. We used estimated prices for on-demand tpu-v3-{64,...,2048} as their
#    prices are not publicly available.
# 4. For preemptible TPUs whose prices are not publicly available, we applied
#    70% discount to the on-demand prices because every known preemptible TPU
#    price follows this pricing rule.
TPU_PRICING = GCP_DATA_DIR + 'pricing/tpu.csv'

COLUMNS = [
    'InstanceType',  # None for accelerators
    'AcceleratorName',
    'AcceleratorCount',
    'MemoryGiB',  # 0 for accelerators
    'GpuInfo',  # Same as AcceleratorName
    'Price',
    'SpotPrice',
    'Region',
    'AvailabilityZone',
]


def get_gpu_df():
    """Generates the GCP service catalog for GPUs."""
    gpu_zones = pd.read_csv(GPU_ZONES)
    gpu_pricing = pd.read_csv(GPU_PRICING)

    # Remove unnecessary columns.
    gpu_zones = gpu_zones.drop(
        columns=['Location', 'NVIDIA RTX virtual workstations'])

    # Rename the columns.
    gpu_zones = gpu_zones.rename(columns={
        'Zones': 'AvailabilityZone',
        'GPU platforms': 'AcceleratorName'
    })
    gpu_pricing = gpu_pricing.rename(
        columns={
            'GPU model': 'AcceleratorName',
            'GPU counts': 'AcceleratorCount',
            'Spot price': 'SpotPrice',
        })

    # Remove zones that do not support any GPU.
    gpu_zones = gpu_zones[~gpu_zones['AcceleratorName'].isna()]

    # Remove zones not in the pricing data.
    # Currently, only US regions are supported.
    zone_to_region = lambda x: x[:-2]
    gpu_zones['Region'] = gpu_zones['AvailabilityZone'].apply(zone_to_region)
    supported_regions = gpu_pricing['Region'].unique()
    gpu_zones = gpu_zones[gpu_zones['Region'].isin(supported_regions)]

    # Explode GPU types.
    gpu_zones['AcceleratorName'] = gpu_zones['AcceleratorName'].apply(
        lambda x: x.split(', '))
    gpu_zones = gpu_zones.explode(column='AcceleratorName', ignore_index=True)

    # Merge the two dataframes.
    gpu_df = pd.merge(gpu_zones, gpu_pricing, on=['AcceleratorName', 'Region'])

    # Explode GPU counts.
    gpu_df['AcceleratorCount'] = gpu_df['AcceleratorCount'].apply(
        lambda x: x.split(', '))
    gpu_df = gpu_df.explode(column='AcceleratorCount', ignore_index=True)
    gpu_df['AcceleratorCount'] = gpu_df['AcceleratorCount'].astype(int)

    # Calculate the on-demand and spot prices.
    gpu_df['Price'] = gpu_df['AcceleratorCount'] * gpu_df['Price']
    gpu_df['SpotPrice'] = gpu_df['AcceleratorCount'] * gpu_df['SpotPrice']

    # Consider the zones that do not have 16xA100 machines.
    gpu_df = gpu_df[~(gpu_df['AvailabilityZone'].isin(NO_A100_16G_ZONES) &
                      (gpu_df['AcceleratorName'] == 'A100') &
                      (gpu_df['AcceleratorCount'] == 16))]
    return gpu_df


def get_tpu_df():
    """Generates the GCP service catalog for TPUs."""
    tpu_zones = pd.read_csv(TPU_ZONES)
    tpu_pricing = pd.read_csv(TPU_PRICING)

    # Rename the columns.
    tpu_zones = tpu_zones.rename(columns={
        'TPU type': 'AcceleratorName',
        'Zones': 'AvailabilityZone',
    })
    tpu_pricing = tpu_pricing.rename(columns={
        'TPU type': 'AcceleratorName',
        'Spot price': 'SpotPrice',
    })

    # Explode Zones.
    tpu_zones['AvailabilityZone'] = tpu_zones['AvailabilityZone'].apply(
        lambda x: x.split(', '))
    tpu_zones = tpu_zones.explode(column='AvailabilityZone', ignore_index=True)
    zone_to_region = lambda x: x[:-2]
    tpu_zones['Region'] = tpu_zones['AvailabilityZone'].apply(zone_to_region)

    # Merge the two dataframes.
    tpu_df = pd.merge(tpu_zones, tpu_pricing, on=['AcceleratorName', 'Region'])
    tpu_df['AcceleratorCount'] = 1
    return tpu_df


def get_vm_df():
    """Generates the GCP service catalog for host VMs."""
    vm_pricing = pd.read_csv(VM_PRICING)
    vm_pricing = vm_pricing.rename(
        columns={
            'Machine type': 'InstanceType',
            'Memory': 'MemoryGiB',
            'Spot price': 'SpotPrice',
        })
    vm_pricing.drop(columns=['vCPU'], inplace=True)

    # Remove suffix 'GB'
    vm_pricing['MemoryGiB'] = vm_pricing['MemoryGiB'].apply(
        lambda x: float(x[:-2]) if 'GB' in str(x) else x)

    vm_df = vm_pricing
    vm_df['AcceleratorName'] = None
    vm_df['AcceleratorCount'] = None
    vm_df['GpuInfo'] = None
    vm_df['AvailabilityZone'] = None
    return vm_df


if __name__ == '__main__':
    gpu_df = get_gpu_df()
    tpu_df = get_tpu_df()

    acc_df = pd.concat([gpu_df, tpu_df])
    acc_df['InstanceType'] = None
    acc_df['GpuInfo'] = acc_df['AcceleratorName']
    acc_df['MemoryGiB'] = 0

    vm_df = get_vm_df()
    catalog_df = pd.concat([vm_df, acc_df])

    # Reorder the columns.
    catalog_df = catalog_df[COLUMNS]

    catalog_df.to_csv('gcp.csv', index=False)
    print('GCP Service Catalog saved to gcp.csv')
