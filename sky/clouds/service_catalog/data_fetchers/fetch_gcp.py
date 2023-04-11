"""A script that generates GCP catalog.

This script gathers the metadata of GCP from the SkyPilot catalog repository
and queries the GCP API to get the real-time prices of the VMs, GPUs, and TPUs.
"""

import argparse
import functools
import io
import multiprocessing
import os
from typing import Any, Dict, List, Optional

import google.auth
from googleapiclient import discovery
import numpy as np
import pandas as pd

from sky.adaptors import gcp

# Useful links:
# GCP SKUs: https://cloud.google.com/skus
# VM pricing: https://cloud.google.com/compute/vm-instance-pricing
# GPU pricing: https://cloud.google.com/compute/gpus-pricing
# TPU pricing: https://cloud.google.com/tpu/pricing

# Service IDs found in https://cloud.google.com/skus
GCE_SERVICE_ID = '6F81-5844-456A'
TPU_SERVICE_ID = 'E000-3F24-B8AA'

# The number of digits to round the price to.
PRICE_ROUNDING = 5

# Refer to: https://github.com/skypilot-org/skypilot/issues/1006
# C3 series is still in preview.
# G2 series has L4 GPU, which is not supported by SkyPilot yet
UNSUPPORTED_SERIES = ['f1', 'm2', 'c3', 'g2']

# This zone is only for TPU v4, and does not appear in the skus yet.
EXCLUDED_ZONES = ['us-central2-b']

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
creds, project_id = google.auth.default()
gcp_client = discovery.build('compute', 'v1')
tpu_client = discovery.build('tpu', 'v1')

# TPU v4 does not have a price list in the GCP API.
tpu_v4_df = pd.read_csv(
    io.StringIO("""\
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region,AvailabilityZone
n1-highmem-8,,,8.0,52.0,,0.473212,0.099624,us-central2,us-central2-b
,tpu-v4-8,1,,,tpu-v4-8,12.88,3.864,us-central2,us-central2-b
,tpu-v4-16,1,,,tpu-v4-16,25.76,7.728,us-central2,us-central2-b
,tpu-v4-32,1,,,tpu-v4-32,51.52,15.456,us-central2,us-central2-b
,tpu-v4-64,1,,,tpu-v4-64,103.04,30.912,us-central2,us-central2-b
,tpu-v4-128,1,,,tpu-v4-128,206.08,61.824,us-central2,us-central2-b
,tpu-v4-256,1,,,tpu-v4-256,412.16,123.648,us-central2,us-central2-b
,tpu-v4-384,1,,,tpu-v4-384,618.24,185.472,us-central2,us-central2-b
,tpu-v4-512,1,,,tpu-v4-512,824.32,247.296,us-central2,us-central2-b
,tpu-v4-640,1,,,tpu-v4-640,1030.4,309.12,us-central2,us-central2-b
,tpu-v4-768,1,,,tpu-v4-768,1236.48,370.944,us-central2,us-central2-b
,tpu-v4-896,1,,,tpu-v4-896,1442.56,432.768,us-central2,us-central2-b
,tpu-v4-1024,1,,,tpu-v4-1024,1648.64,494.592,us-central2,us-central2-b
,tpu-v4-1152,1,,,tpu-v4-1152,1854.72,556.416,us-central2,us-central2-b
,tpu-v4-1280,1,,,tpu-v4-1280,2060.8,618.24,us-central2,us-central2-b
,tpu-v4-1408,1,,,tpu-v4-1408,2266.88,680.064,us-central2,us-central2-b
,tpu-v4-1536,1,,,tpu-v4-1536,2472.96,741.888,us-central2,us-central2-b
,tpu-v4-1664,1,,,tpu-v4-1664,2679.04,803.712,us-central2,us-central2-b
,tpu-v4-1792,1,,,tpu-v4-1792,2885.12,865.536,us-central2,us-central2-b
,tpu-v4-1920,1,,,tpu-v4-1920,3091.2,927.36,us-central2,us-central2-b
,tpu-v4-2048,1,,,tpu-v4-2048,3297.28,989.184,us-central2,us-central2-b
,tpu-v4-2176,1,,,tpu-v4-2176,3503.36,1051.008,us-central2,us-central2-b
,tpu-v4-2304,1,,,tpu-v4-2304,3709.44,1112.832,us-central2,us-central2-b
,tpu-v4-2432,1,,,tpu-v4-2432,3915.52,1174.656,us-central2,us-central2-b
,tpu-v4-2560,1,,,tpu-v4-2560,4121.6,1236.48,us-central2,us-central2-b
,tpu-v4-2688,1,,,tpu-v4-2688,4327.68,1298.304,us-central2,us-central2-b
,tpu-v4-2816,1,,,tpu-v4-2816,4533.76,1360.128,us-central2,us-central2-b
,tpu-v4-2944,1,,,tpu-v4-2944,4739.84,1421.952,us-central2,us-central2-b
,tpu-v4-3072,1,,,tpu-v4-3072,4945.92,1483.776,us-central2,us-central2-b
,tpu-v4-3200,1,,,tpu-v4-3200,5152.0,1545.6,us-central2,us-central2-b
,tpu-v4-3328,1,,,tpu-v4-3328,5358.08,1607.424,us-central2,us-central2-b
,tpu-v4-3456,1,,,tpu-v4-3456,5564.16,1669.248,us-central2,us-central2-b
,tpu-v4-3584,1,,,tpu-v4-3584,5770.24,1731.072,us-central2,us-central2-b
,tpu-v4-3712,1,,,tpu-v4-3712,5976.32,1792.896,us-central2,us-central2-b
,tpu-v4-3840,1,,,tpu-v4-3840,6182.4,1854.72,us-central2,us-central2-b
,tpu-v4-3968,1,,,tpu-v4-3968,6388.48,1916.544,us-central2,us-central2-b
"""))


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
            response = cb.services().skus().list(
                parent=service_name, pageToken=page_token).execute()
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
        # Prune PD snapshot egress and VM state.
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


@functools.lru_cache(maxsize=None)
def _get_all_zones() -> List[str]:
    zones_request = gcp_client.zones().list(project=project_id)
    zones = []
    while zones_request is not None:
        zones_response = zones_request.execute()
        zones.extend([zone['name'] for zone in zones_response['items']])
        zones_request = gcp_client.zones().list_next(
            previous_request=zones_request, previous_response=zones_response)
    return zones


def _get_machine_type_for_zone(zone: str) -> pd.DataFrame:
    machine_types_request = gcp_client.machineTypes().list(project=project_id,
                                                           zone=zone)
    print(f'Fetching machine types for zone {zone!r}...')
    machine_types = []
    while machine_types_request is not None:
        machine_types_response = machine_types_request.execute()
        machine_types.extend(machine_types_response['items'])
        machine_types_request = gcp_client.machineTypes().list_next(
            previous_request=machine_types_request,
            previous_response=machine_types_response)
    machine_types = [{
        'InstanceType': machine_type['name'],
        'vCPUs': machine_type['guestCpus'],
        'MemoryGiB': machine_type['memoryMb'] / 1024,
        'Region': zone.rpartition('-')[0],
        'AvailabilityZone': zone
    } for machine_type in machine_types]
    return pd.DataFrame(machine_types).reset_index(drop=True)


def _get_machine_types(region_prefix: str) -> pd.DataFrame:
    zones = _get_all_zones()
    zones = [zone for zone in zones if zone.startswith(region_prefix)]
    with multiprocessing.Pool() as pool:
        all_machine_dfs = pool.map(_get_machine_type_for_zone, zones)
    machine_df = pd.concat(all_machine_dfs, ignore_index=True)
    return machine_df


def get_vm_df(skus: List[Dict[str, Any]], region_prefix: str) -> pd.DataFrame:
    df = _get_machine_types(region_prefix)
    # Drop the unsupported series.
    df = df[~df['InstanceType'].str.startswith(tuple(UNSUPPORTED_SERIES))]
    df = df[~df['AvailabilityZone'].str.startswith(tuple(EXCLUDED_ZONES))]

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
            if SERIES_TO_DISCRIPTION[series].lower() not in description.lower():
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


def _get_gpus_for_zone(zone: str) -> pd.DataFrame:
    gpus_request = gcp_client.acceleratorTypes().list(project=project_id,
                                                      zone=zone)
    print(f'Fetching GPUs for zone {zone!r}...')
    gpus = []
    while gpus_request is not None:
        gpus_response = gpus_request.execute()
        gpus.extend(gpus_response.get('items', []))
        gpus_request = gcp_client.acceleratorTypes().list_next(
            previous_request=gpus_request, previous_response=gpus_response)
    new_gpus = []
    for gpu in gpus:
        for sup in range(0, int(np.log2(gpu['maximumCardsPerInstance']) + 1)):
            count = int(2**sup)
            gpu_name = gpu['name']
            gpu_name = gpu_name.replace('nvidia-', '')
            gpu_name = gpu_name.replace('tesla-', '')
            gpu_name = gpu_name.upper()
            if 'VWS' in gpu_name:
                continue
            new_gpus.append({
                'AcceleratorName': gpu_name,
                'AcceleratorCount': count,
                'GpuInfo': None,
                'Region': zone.rpartition('-')[0],
                'AvailabilityZone': zone,
            })
    return pd.DataFrame(new_gpus).reset_index(drop=True)


def _get_gpus(region_prefix: str) -> pd.DataFrame:
    zones = _get_all_zones()
    zones = [zone for zone in zones if zone.startswith(region_prefix)]
    with multiprocessing.Pool() as pool:
        all_gpu_dfs = pool.map(_get_gpus_for_zone, zones)
    gpu_df = pd.concat(all_gpu_dfs, ignore_index=True)
    return gpu_df


def get_gpu_df(skus: List[Dict[str, Any]], region_prefix: str) -> pd.DataFrame:
    gpu_skus = [
        sku for sku in skus if sku['category']['resourceGroup'] == 'GPU'
    ]
    df = _get_gpus(region_prefix)

    def get_gpu_price(row: pd.Series, spot: bool) -> Optional[float]:
        ondemand_or_spot = 'OnDemand' if not spot else 'Preemptible'
        gpu_price = None
        for sku in gpu_skus:
            if row['Region'] not in sku['serviceRegions']:
                continue
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

        if gpu_price is not None:
            return gpu_price

        # Not found in the SKUs.
        # FIXME(woosuk): Remove this once the SKUs are updated.
        gpu = row['AcceleratorName']
        region = row['Region']
        if gpu == 'T4':
            assert region in ['europe-west1', 'europe-west2', 'asia-east2'], row
        elif gpu == 'P100':
            assert region in ['europe-west4', 'australia-southeast1'], row
        return None

    df['Price'] = df.apply(lambda row: get_gpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(lambda row: get_gpu_price(row, spot=True),
                               axis=1)
    # Drop invalid rows.
    df = df[df['Price'].notna() | df['SpotPrice'].notna()]
    df = df.reset_index(drop=True)
    return df


def _get_tpu_for_zone(zone: str) -> pd.DataFrame:
    tpus = []
    parent = f'projects/{project_id}/locations/{zone}'
    tpus_request = tpu_client.projects().locations().acceleratorTypes().list(
        parent=parent)
    try:
        tpus_response = tpus_request.execute()
        for tpu in tpus_response['acceleratorTypes']:
            tpus.append(tpu)
    except gcp.http_error_exception() as error:
        if error.resp.status == 403:
            print('  TPU API is not enabled or you don\'t have TPU access '
                  f'to zone: {zone!r}.')
        else:
            print(f'  An error occurred: {error}')
    new_tpus = []
    for tpu in tpus:
        tpu_name = tpu['type']
        new_tpus.append({
            'AcceleratorName': f'tpu-{tpu_name}',
            'AcceleratorCount': 1,
            'Region': zone.rpartition('-')[0],
            'AvailabilityZone': zone,
        })
    return pd.DataFrame(new_tpus).reset_index(drop=True)


def _get_tpus() -> pd.DataFrame:
    zones = _get_all_zones()
    with multiprocessing.Pool() as pool:
        all_tpu_dfs = pool.map(_get_tpu_for_zone, zones)
    tpu_df = pd.concat(all_tpu_dfs, ignore_index=True)
    return tpu_df


# TODO: the TPUs fetched fails to contain us-east1
def get_tpu_df(skus: List[Dict[str, Any]]) -> pd.DataFrame:
    df = _get_tpus()
    df = df[~df['AvailabilityZone'].str.startswith(tuple(EXCLUDED_ZONES))]

    def get_tpu_price(row: pd.Series, spot: bool) -> float:
        tpu_price = None
        for sku in skus:
            # NOTE: Because us-east1-d is a hidden zone that supports TPUs,
            # no price information is available for it. As a workaround,
            # we asssume that the price is the same as us-central1.
            tpu_region = row['Region']
            if tpu_region == 'us-east1':
                tpu_region = 'us-central1'

            if tpu_region not in sku['serviceRegions']:
                continue
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
            tpu_device_price = unit_price * row['AcceleratorCount']
            tpu_price = tpu_device_price * num_cores / 8
            break

        assert tpu_price is not None, row
        return tpu_price

    df['Price'] = df.apply(lambda row: get_tpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(lambda row: get_tpu_price(row, spot=True),
                               axis=1)
    df = df.reset_index(drop=True)
    return df


def get_catalog_df(region_prefix: str) -> pd.DataFrame:
    gcp_skus = get_skus(GCE_SERVICE_ID)
    vm_df = get_vm_df(gcp_skus, region_prefix)
    gpu_df = get_gpu_df(gcp_skus, region_prefix)

    # Drop regions without the given prefix.
    # NOTE: We intentionally do not drop any TPU regions.
    vm_df = vm_df[vm_df['Region'].str.startswith(region_prefix)]
    gpu_df = gpu_df[gpu_df['Region'].str.startswith(region_prefix)]

    gcp_tpu_skus = get_skus(TPU_SERVICE_ID)
    tpu_df = get_tpu_df(gcp_tpu_skus)

    # Merge the dataframes.
    df = pd.concat([vm_df, gpu_df, tpu_df, tpu_v4_df])

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
