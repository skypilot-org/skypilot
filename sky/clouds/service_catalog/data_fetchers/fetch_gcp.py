"""A script that generates GCP catalog.

This script gathers the metadata of GCP from the SkyPilot catalog repository
and queries the GCP API to get the real-time prices of the VMs, GPUs, and TPUs.
"""

import argparse
import os
from typing import Any, Dict, List

from googleapiclient import discovery
import pandas as pd

# Useful links:
# GCP SKUs: https://cloud.google.com/skus
# VM pricing: https://cloud.google.com/compute/vm-instance-pricing
# GPU pricing: https://cloud.google.com/compute/gpus-pricing
# TPU pricing: https://cloud.google.com/tpu/pricing

# FIXME: fix-gcp -> master
GCP_METADATA_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/fix-gcp/metadata/gcp/'  # pylint: disable=line-too-long

GCE_SERVICE_ID = '6F81-5844-456A'
TPU_SERVICE_ID = 'E000-3F24-B8AA'

# The number of digits to round the price to.
PRICE_ROUNDING = 5

# Refer to: https://github.com/skypilot-org/skypilot/issues/1006
UNSUPPORTED_SERIES = ['f1', 'm2']

# TODO(woosuk): Make this more robust.
SERIES_TO_DISCRIPTION = {
    'a2': 'A2 Instance',
    'c2': 'Compute optimized',
    'c2d': 'C2D AMD Instance',
    'e2': 'E2 Instance',
    'f1': 'Micro Instance with burstable CPU',
    'g1': 'Small Instance with 1 VCPU',
    'm1': 'Memory-optimized Instance',
    # FIXME(woosuk): Support M2 series.
    'm3': 'M3 Memory-optimized Instance',
    'n1': 'N1 Predefined Instance',
    'n2': 'N2 Instance',
    'n2d': 'N2D AMD Instance',
    't2a': 'T2A Arm Instance',
    't2d': 'T2D AMD Instance',
}


def get_skus(service_id: str) -> List[Dict[str, Any]]:
    # Get the SKUs from the GCP API.
    cb = discovery.build('cloudbilling', 'v1')
    service_name = 'services/' + service_id

    skus = []
    page_token = ''
    while True:
        if page_token == '':
            response = cb.services().skus().list(parent=service_name).execute()
        else:
            response = cb.services().skus().list(parent=service_name,
                                                 pageToken=page_token).execute()
        skus += response['skus']
        page_token = response['nextPageToken']
        if not page_token:
            break

    # Prune unnecessary SKUs.
    new_skus = []
    for sku in skus:
        # Prune SKUs that are not Compute (i.e., Storage, Network, and License).
        if sku['category']['resourceFamily'] != 'Compute':
            continue
        # Prune PD snapshot egress and Vm state.
        if sku['category']['resourceGroup'] in ['PdSnapshotEgress', 'VmState']:
            continue
        # Prune commitment SKUs.
        if sku['category']['usageType'] not in ['OnDemand', 'Preemptible']:
            continue
        # Prune custom SKUs.
        if 'custom' in sku['description'].lower():
            continue
        # Prune premium SKUs.
        if 'premium' in sku['description'].lower():
            continue
        # Prune reserved SKUs.
        if 'reserved' in sku['description'].lower():
            continue
        # Prune sole-tenant SKUs.
        # See https://cloud.google.com/compute/docs/nodes/sole-tenant-nodes
        if 'sole tenancy' in sku['description'].lower():
            continue
        new_skus.append(sku)
    return new_skus


def _get_unit_price(sku: Dict[str, Any]) -> float:
    pricing_info = sku['pricingInfo'][0]['pricingExpression']
    unit_price = pricing_info['tieredRates'][0]['unitPrice']
    units = int(unit_price['units'])
    nanos = unit_price['nanos'] / 1e9
    return units + nanos


def get_vm_df(skus: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.read_csv(GCP_METADATA_URL + 'instances.csv')
    # Drop the unsupported series.
    df = df[~df['InstanceType'].str.startswith(tuple(UNSUPPORTED_SERIES))]

    # TODO(woosuk): Make this more efficient.
    def get_vm_price(row: pd.Series, spot: bool) -> float:
        series = row['InstanceType'].split('-')[0].lower()

        ondemand_or_spot = 'OnDemand' if not spot else 'Preemptible'
        cpu_price = None
        memory_price = None
        for sku in skus:
            if sku['category']['usageType'] != ondemand_or_spot:
                continue
            if row['Region'] not in sku['serviceRegions']:
                continue

            # Check if the SKU is for the correct series.
            description = sku['description']
            if SERIES_TO_DISCRIPTION[series] not in description:
                continue
            # Special check for M1 instances.
            if series == 'm1' and 'M3' in description:
                continue

            resource_group = sku['category']['resourceGroup']
            # Skip GPU SKUs.
            if resource_group == 'GPU':
                continue

            # Is it CPU or memory?
            is_cpu = False
            is_memory = False
            if resource_group in ['CPU', 'F1Micro', 'G1Small']:
                is_cpu = True
            elif resource_group == 'RAM':
                is_memory = True
            else:
                assert resource_group == 'N1Standard'
                if 'Core' in description:
                    is_cpu = True
                elif 'Ram' in description:
                    is_memory = True

            # Calculate the price.
            unit_price = _get_unit_price(sku)
            if is_cpu:
                cpu_price = unit_price * row['vCPUs']
            elif is_memory:
                memory_price = unit_price * row['MemoryGiB']

        # Special case for F1 and G1 instances.
        # Memory is not charged for these instances.
        if series in ['f1', 'g1']:
            memory_price = 0.0

        assert cpu_price is not None, row
        assert memory_price is not None, row
        return cpu_price + memory_price

    df['Price'] = df.apply(lambda row: get_vm_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(lambda row: get_vm_price(row, spot=True), axis=1)
    df = df.reset_index(drop=True)
    return df


def get_gpu_df(skus: List[Dict[str, Any]]) -> pd.DataFrame:
    gpu_skus = [
        sku for sku in skus if sku['category']['resourceGroup'] == 'GPU']
    df = pd.read_csv(GCP_METADATA_URL + 'gpus.csv')

    def get_gpu_price(row: pd.Series, spot: bool) -> float:
        ondemand_or_spot = 'OnDemand' if not spot else 'Preemptible'
        gpu_price = None
        for sku in gpu_skus:
            if sku['category']['usageType'] != ondemand_or_spot:
                continue

            gpu_name = row['AcceleratorName']
            if gpu_name == 'A100-80GB':
                gpu_name = 'A100 80GB'
            if f'{gpu_name} GPU' not in sku['description']:
                continue

            unit_price = _get_unit_price(sku)
            gpu_price = unit_price * row['AcceleratorCount']
            break

        assert gpu_price is not None, row
        return gpu_price

    df['Price'] = df.apply(lambda row: get_gpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(
        lambda row: get_gpu_price(row, spot=True), axis=1)
    df = df.reset_index(drop=True)
    return df


def get_tpu_df(skus: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.read_csv(GCP_METADATA_URL + 'tpus.csv')

    def get_tpu_price(row: pd.Series, spot: bool) -> float:
        tpu_price = None
        for sku in skus:
            description = sku['description']
            # NOTE: 'usageType' of preemptible TPUs are 'OnDemand'.
            if spot:
                if 'Preemptible' not in description:
                    continue
            else:
                if 'Preemptible' in description:
                    continue

            tpu_name = row['AcceleratorName']
            tpu_version = tpu_name.split('-')[1]
            num_cores = int(tpu_name.split('-')[2])
            is_pod = num_cores > 8

            if f'Tpu-{tpu_version}' not in description:
                continue
            if is_pod:
                if 'Pod' not in description:
                    continue
            else:
                if 'Pod' in description:
                    continue

            unit_price = _get_unit_price(sku)
            tpu_price = unit_price * row['AcceleratorCount']
            break

        assert tpu_price is not None, row
        return tpu_price

    df['Price'] = df.apply(lambda row: get_tpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(
        lambda row: get_tpu_price(row, spot=True), axis=1)
    df = df.reset_index(drop=True)
    return df


def get_catalog_df(region_prefix: str) -> pd.DataFrame:
    gcp_skus = get_skus(GCE_SERVICE_ID)
    vm_df = get_vm_df(gcp_skus)
    gpu_df = get_gpu_df(gcp_skus)

    # Drop regions without the given prefix.
    # NOTE: We intentionally do not drop any TPU regions.
    vm_df = vm_df[vm_df['Region'].str.startswith(region_prefix)]
    gpu_df = gpu_df[gpu_df['Region'].str.startswith(region_prefix)]

    gcp_tpu_skus = get_skus(TPU_SERVICE_ID)
    tpu_df = get_tpu_df(gcp_tpu_skus)

    # Merge the dataframes.
    df = pd.concat([vm_df, gpu_df, tpu_df])

    # Reorder the columns.
    df = df[[
        'InstanceType',
        'vCPUs',
        'MemoryGiB',
        'AcceleratorName',
        'AcceleratorCount',
        'GpuInfo',
        'Region',
        'AvailabilityZone',
        'Price',
        'SpotPrice',
    ]]

    # Round the prices.
    df['Price'] = df['Price'].round(PRICE_ROUNDING)
    df['SpotPrice'] = df['SpotPrice'].round(PRICE_ROUNDING)
    return df


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--all-regions',
        action='store_true',
        help='Fetch all global regions, not just the U.S. ones.')
    args = parser.parse_args()

    region_prefix_filter = '' if args.all_regions else 'us-'
    catalog_df = get_catalog_df(region_prefix_filter)

    os.makedirs('gcp', exist_ok=True)
    catalog_df.to_csv('gcp/vms.csv', index=False)
    print('GCP Service Catalog saved to gcp/vms.csv')
