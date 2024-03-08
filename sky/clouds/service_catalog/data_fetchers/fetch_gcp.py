"""A script that generates GCP catalog.

This script uses the GCP APIs to query the list and real-time prices of the
VMs, GPUs, and TPUs. The script takes about 1-2 minutes to run.
"""

import argparse
import functools
import io
import multiprocessing
import os
import textwrap
from typing import Any, Callable, Dict, List, Optional, Set

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

# This zone is only for TPU v4, and does not appear in the skus yet.
TPU_V4_ZONES = ['us-central2-b']
# TPU v3 pods are available in us-east1-d, but hidden in the skus.
# We assume the TPU prices are the same as us-central1.
HIDDEN_TPU_DF = pd.read_csv(
    io.StringIO(
        textwrap.dedent("""\
 InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region,AvailabilityZone
 ,tpu-v3-32,1,,,tpu-v3-32,32.0,9.6,us-east1,us-east1-d
 ,tpu-v3-64,1,,,tpu-v3-64,64.0,19.2,us-east1,us-east1-d
 ,tpu-v3-128,1,,,tpu-v3-128,128.0,38.4,us-east1,us-east1-d
 ,tpu-v3-256,1,,,tpu-v3-256,256.0,76.8,us-east1,us-east1-d
 ,tpu-v3-512,1,,,tpu-v3-512,512.0,153.6,us-east1,us-east1-d
 ,tpu-v3-1024,1,,,tpu-v3-1024,1024.0,307.2,us-east1,us-east1-d
 ,tpu-v3-2048,1,,,tpu-v3-2048,2048.0,614.4,us-east1,us-east1-d
 """)))
# FIXME(woosuk): Remove this once the bug is fixed.
# See https://github.com/skypilot-org/skypilot/issues/1759#issue-1619614345
TPU_V4_HOST_DF = pd.read_csv(
    io.StringIO(
        textwrap.dedent("""\
 InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region,AvailabilityZone
 n1-highmem-8,,,8.0,52.0,,0.473212,0.099624,us-central2,us-central2-b
 """)))

# TODO(woosuk): Make this more robust.
# Refer to: https://github.com/skypilot-org/skypilot/issues/1006
# Unsupported Series: 'f1', 'm2'
SERIES_TO_DISCRIPTION = {
    'a2': 'A2 Instance',
    'a3': 'A3 Instance',
    'c2': 'Compute optimized',
    'c2d': 'C2D AMD Instance',
    'c3': 'C3 Instance',
    'e2': 'E2 Instance',
    'f1': 'Micro Instance with burstable CPU',
    'g1': 'Small Instance with 1 VCPU',
    'g2': 'G2 Instance',
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

SINGLE_THREADED = False
ZONES: Set[str] = set()
EXCLUDED_REGIONS: Set[str] = set()


def get_skus(service_id: str) -> List[Dict[str, Any]]:
    # Get the SKUs from the GCP API.
    cb = discovery.build('cloudbilling', 'v1')
    service_name = f'services/{service_id}'

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


def filter_zones(func: Callable[[], List[str]]) -> Callable[[], List[str]]:
    """Decorator to filter the zones returned by the decorated function.
    It first intersects the result with the global ZONES (if defined) and then
    removes any zones present in the global EXCLUDED_REGIONS (if defined).
    """

    def wrapper(*args, **kwargs) -> List[str]:  # pylint: disable=redefined-outer-name
        original_zones = set(func(*args, **kwargs))
        if ZONES:
            original_zones &= ZONES
        if EXCLUDED_REGIONS:
            original_zones -= EXCLUDED_REGIONS
        if not original_zones:
            raise ValueError('No zones to fetch. Please check your arguments.')
        return list(original_zones)

    return wrapper


@filter_zones
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
    if SINGLE_THREADED:
        all_machine_dfs = [_get_machine_type_for_zone(zone) for zone in zones]
    else:
        with multiprocessing.Pool() as pool:
            all_machine_dfs = pool.map(_get_machine_type_for_zone, zones)
    machine_df = pd.concat(all_machine_dfs, ignore_index=True)
    return machine_df


def get_vm_df(skus: List[Dict[str, Any]], region_prefix: str) -> pd.DataFrame:
    df = _get_machine_types(region_prefix)
    if df.empty:
        return df

    # Drop the unsupported series.
    df = df[df['InstanceType'].str.startswith(
        tuple(f'{series}-' for series in SERIES_TO_DISCRIPTION))]
    df = df[~df['AvailabilityZone'].str.startswith(tuple(TPU_V4_ZONES))]

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
    df = df.sort_values(['InstanceType', 'Region', 'AvailabilityZone'])
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
            if 'H100-80GB' in gpu_name:
                gpu_name = 'H100'
                if count != 8:
                    # H100 only has 8 cards.
                    continue
            if 'VWS' in gpu_name:
                continue
            if gpu_name.startswith('TPU-'):
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
    if SINGLE_THREADED:
        all_gpu_dfs = [_get_gpus_for_zone(zone) for zone in zones]
    else:
        with multiprocessing.Pool() as pool:
            all_gpu_dfs = pool.map(_get_gpus_for_zone, zones)
    gpu_df = pd.concat(all_gpu_dfs, ignore_index=True)
    return gpu_df


def get_gpu_df(skus: List[Dict[str, Any]], region_prefix: str) -> pd.DataFrame:
    gpu_skus = [
        sku for sku in skus if sku['category']['resourceGroup'] == 'GPU'
    ]
    df = _get_gpus(region_prefix)
    if df.empty:
        return df

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
            if gpu_name == 'H100':
                gpu_name = 'H100 80GB'
            if f'{gpu_name} GPU' not in sku['description']:
                continue

            unit_price = _get_unit_price(sku)
            gpu_price = unit_price * row['AcceleratorCount']
            break

        if gpu_price is not None:
            return gpu_price

        # Not found in the SKUs.
        gpu = row['AcceleratorName']
        region = row['Region']
        print(f'The price of {gpu} in {region} is not found in SKUs.')
        return None

    df['Price'] = df.apply(lambda row: get_gpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(lambda row: get_gpu_price(row, spot=True),
                               axis=1)
    # Drop invalid rows.
    df = df[df['Price'].notna() | df['SpotPrice'].notna()]
    df = df.reset_index(drop=True)
    df = df.sort_values(
        ['AcceleratorName', 'AcceleratorCount', 'Region', 'AvailabilityZone'])
    df['GpuInfo'] = df['AcceleratorName']
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
        # skip tpu v5 as we currently don't support it
        if 'v5' in tpu_name:
            continue
        new_tpus.append({
            'AcceleratorName': f'tpu-{tpu_name}',
            'AcceleratorCount': 1,
            'Region': zone.rpartition('-')[0],
            'AvailabilityZone': zone,
        })
    return pd.DataFrame(new_tpus).reset_index(drop=True)


def _get_tpus() -> pd.DataFrame:
    zones = _get_all_zones()
    # Add TPU-v4 zones.
    zones += TPU_V4_ZONES
    if SINGLE_THREADED:
        all_tpu_dfs = [_get_tpu_for_zone(zone) for zone in zones]
    else:
        with multiprocessing.Pool() as pool:
            all_tpu_dfs = pool.map(_get_tpu_for_zone, zones)
    tpu_df = pd.concat(all_tpu_dfs, ignore_index=True)
    return tpu_df


# TODO: the TPUs fetched fails to contain us-east1
def get_tpu_df(skus: List[Dict[str, Any]]) -> pd.DataFrame:
    df = _get_tpus()
    if df.empty:
        return df

    def get_tpu_price(row: pd.Series, spot: bool) -> Optional[float]:
        assert row['AcceleratorCount'] == 1, row
        tpu_price = None
        tpu_region = row['Region']
        tpu_name = row['AcceleratorName']
        tpu_version = tpu_name.split('-')[1]
        num_cores = int(tpu_name.split('-')[2])
        # For TPU-v2 and TPU-v3, the pricing API provides the prices
        # of 8 TPU cores. The prices can be different based on
        # whether the TPU is a single device or a pod.
        # For TPU-v4, the pricing is uniform, and thus the pricing API
        # only provides the price of TPU-v4 pods.
        is_pod = num_cores > 8 or tpu_version == 'v4'

        for sku in skus:
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

            if f'Tpu-{tpu_version}' not in description:
                continue
            if is_pod:
                if 'Pod' not in description:
                    continue
            else:
                if 'Pod' in description:
                    continue

            unit_price = _get_unit_price(sku)
            tpu_device_price = unit_price
            tpu_core_price = tpu_device_price / 8
            tpu_price = num_cores * tpu_core_price
            break

        if tpu_price is None:
            # Find the line with the same accelerator name, region, zone in
            # the hidden TPU dataframe for the row.
            hidden_tpu = HIDDEN_TPU_DF[
                (HIDDEN_TPU_DF['AcceleratorName'] == row['AcceleratorName']) &
                (HIDDEN_TPU_DF['Region'] == row['Region']) &
                (HIDDEN_TPU_DF['AvailabilityZone'] == row['AvailabilityZone'])]
            if not hidden_tpu.empty:
                price_str = 'SpotPrice' if spot else 'Price'
                tpu_price = hidden_tpu[price_str].values[0]
        if tpu_price is None:
            spot_str = 'spot ' if spot else ''
            print(f'The {spot_str}price of {tpu_name} in {tpu_region} is '
                  'not found in SKUs or hidden TPU price DF.')
        assert spot or tpu_price is not None, (row, hidden_tpu, HIDDEN_TPU_DF)
        return tpu_price

    df['Price'] = df.apply(lambda row: get_tpu_price(row, spot=False), axis=1)
    df['SpotPrice'] = df.apply(lambda row: get_tpu_price(row, spot=True),
                               axis=1)
    df = df.reset_index(drop=True)
    df['version_and_size'] = df['AcceleratorName'].apply(
        lambda name: (name.split('-')[1], int(name.split('-')[2])))
    df = df.sort_values(
        ['version_and_size', 'AcceleratorCount', 'Region', 'AvailabilityZone'])
    df.drop(columns=['version_and_size'], inplace=True)
    df['GpuInfo'] = df['AcceleratorName']
    return df


def get_catalog_df(region_prefix: str) -> pd.DataFrame:
    gcp_skus = get_skus(GCE_SERVICE_ID)
    vm_df = get_vm_df(gcp_skus, region_prefix)
    gpu_df = get_gpu_df(gcp_skus, region_prefix)

    # Drop regions without the given prefix.
    # NOTE: We intentionally do not drop any TPU regions.
    vm_df = vm_df[vm_df['Region'].str.startswith(region_prefix)]
    gpu_df = gpu_df[gpu_df['Region'].str.startswith(
        region_prefix)] if not gpu_df.empty else gpu_df

    gcp_tpu_skus = get_skus(TPU_SERVICE_ID)
    tpu_df = get_tpu_df(gcp_tpu_skus)

    # Merge the dataframes.
    df = pd.concat([vm_df, gpu_df, tpu_df, TPU_V4_HOST_DF])

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
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--all-regions',
                       action='store_true',
                       help='Fetch all global regions, not just the U.S. ones.')
    group.add_argument('--zones',
                       nargs='+',
                       help='Fetch the list of specified zones.')
    parser.add_argument('--exclude',
                        nargs='+',
                        help='Exclude the list of specified regions.')
    parser.add_argument('--single-threaded',
                        action='store_true',
                        help='Run in single-threaded mode. This is useful when '
                        'running in github action, as the multiprocessing '
                        'does not work well with the gcp client due '
                        'to ssl issues.')
    args = parser.parse_args()

    SINGLE_THREADED = args.single_threaded
    ZONES = set(args.zones) if args.zones else set()
    EXCLUDED_REGIONS = set(args.exclude) if args.exclude else set()

    region_prefix_filter = '' if args.zones or args.all_regions else 'us-'
    catalog_df = get_catalog_df(region_prefix_filter)

    os.makedirs('gcp', exist_ok=True)
    catalog_df.to_csv('gcp/vms.csv', index=False)
    print('GCP Service Catalog saved to gcp/vms.csv')
