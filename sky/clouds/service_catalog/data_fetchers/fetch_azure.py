"""A script that queries Azure API to get instance types and pricing info.

This script takes about 1 minute to finish.
"""
import argparse
import json
from multiprocessing import pool as mp_pool
import os
import subprocess
import typing
from typing import List, Optional, Set
import urllib

import numpy as np
import requests

from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

US_REGIONS = {
    'centralus',
    'eastus',
    'eastus2',
    'northcentralus',
    'southcentralus',
    'westcentralus',
    'westus',
    'westus2',
    'westus3',
}

# Exclude the following regions as they do not have ProductName in the
# pricing table. Reference: #1768 #2548
EXCLUDED_REGIONS = {
    'eastus2euap',
    'centraluseuap',
    'brazilus',
}

SINGLE_THREADED = False

# Family name to SkyPilot GPU name mapping.
#
# When adding a new accelerator:
# - The instance type is typically already fetched, but we need to find the
#   family name and add it to this mapping.
# - To inspect family names returned by Azure API, check the dataframes in
#   get_all_regions_instance_types_df().
FAMILY_NAME_TO_SKYPILOT_GPU_NAME = {
    'standardNCFamily': 'K80',
    'standardNCSv2Family': 'P100',
    'standardNCSv3Family': 'V100',
    'standardNCPromoFamily': 'K80',
    'StandardNCASv3_T4Family': 'T4',
    'standardNDSv2Family': 'V100-32GB',
    'StandardNCADSA100v4Family': 'A100-80GB',
    'standardNDAMSv4_A100Family': 'A100-80GB',
    'StandardNDASv4_A100Family': 'A100',
    'standardNVFamily': 'M60',
    'standardNVSv2Family': 'M60',
    'standardNVSv3Family': 'M60',
    'standardNVPromoFamily': 'M60',
    'standardNVSv4Family': 'MI25',
    'standardNDSFamily': 'P40',
    'StandardNVADSA10v5Family': 'A10',
    'StandardNCadsH100v5Family': 'H100',
    'standardNDSH100v5Family': 'H100',
}


def get_regions() -> List[str]:
    """Get all available regions."""
    proc = subprocess.run(
        'az account list-locations  --query "[?not_null(metadata.latitude)] '
        '.{RegionName:name , RegionDisplayName:regionalDisplayName}" -o json',
        shell=True,
        check=True,
        stdout=subprocess.PIPE)
    items = json.loads(proc.stdout.decode('utf-8'))
    regions = [
        item['RegionName']
        for item in items
        if not item['RegionName'].endswith('stg')
    ]
    return regions


# Azure secretly deprecated the M60 family which is still returned by its API.
# We have to manually remove it.
DEPRECATED_FAMILIES = ['standardNVSv2Family']

# Azure has those fractional A10 instance types, which still shows has 1 A10 GPU
# in the API response. We manually changing the number of GPUs to a float here.
# Ref: https://learn.microsoft.com/en-us/azure/virtual-machines/nva10v5-series
# TODO(zhwu,tian): Support fractional GPUs on k8s as well.
# TODO(tian): Maybe we should support literally fractional count, i.e. A10:1/6
# instead of float point count (A10:0.167).
AZURE_FRACTIONAL_A10_INS_TYPE_TO_NUM_GPUS = {
    f'Standard_NV{vcpu}ads_A10_v5': round(vcpu / 36, 3) for vcpu in [6, 12, 18]
}

USEFUL_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'GpuInfo', 'Price', 'SpotPrice', 'Region', 'Generation'
]


def get_pricing_url(region: Optional[str] = None) -> str:
    filters = [
        'serviceName eq \'Virtual Machines\'',
        'priceType eq \'Consumption\'',
    ]
    if region is not None:
        filters.append(f'armRegionName eq \'{region}\'')
    filters_str = urllib.parse.quote(' and '.join(filters))
    return f'https://prices.azure.com/api/retail/prices?$filter={filters_str}'


def get_pricing_df(region: Optional[str] = None) -> 'pd.DataFrame':
    all_items = []
    url = get_pricing_url(region)
    print(f'Getting pricing for {region}, url: {url}')
    page = 0
    while url is not None:
        page += 1
        if page % 10 == 0:
            print(f'Fetched pricing pages {page}')
        r = requests.get(url)
        r.raise_for_status()
        content_str = r.content.decode('ascii')
        content = json.loads(content_str)
        items = content.get('Items', [])
        if not items:
            break
        all_items += items
        url = content.get('NextPageLink')
    print(f'Done fetching pricing {region}')
    df = pd.DataFrame(all_items)
    assert 'productName' in df.columns, (region, df.columns)
    # Filter out the cloud services and windows products.
    # Some H100 series use ' Win' instead of ' Windows', e.g.
    # Virtual Machines NCCadsv5 Srs Win
    return df[
        (~df['productName'].str.contains(' Win| Cloud Services| CloudServices'))
        & (df['unitPrice'] > 0)]


def get_sku_df(region_set: Set[str]) -> 'pd.DataFrame':
    print('Fetching SKU list')
    # To get a complete list, --all option is necessary.
    proc = subprocess.run(
        'az vm list-skus --all --resource-type virtualMachines -o json',
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
    )
    print('Done fetching SKUs')
    items = json.loads(proc.stdout.decode('ascii'))
    filtered_items = []
    for item in items:
        # zones = item['locationInfo'][0]['zones']
        region = item['locations'][0]
        if region.lower() not in region_set:
            continue
        item['Region'] = region
        filtered_items.append(item)

    df = pd.DataFrame(filtered_items)
    return df


def get_gpu_name(family: str) -> Optional[str]:
    # NP-series offer Xilinx U250 FPGAs which are not GPUs,
    # so we do not include them here.
    # https://docs.microsoft.com/en-us/azure/virtual-machines/np-series
    family = family.replace(' ', '')
    return FAMILY_NAME_TO_SKYPILOT_GPU_NAME.get(family)


def get_all_regions_instance_types_df(region_set: Set[str]):
    if SINGLE_THREADED:
        dfs = [get_pricing_df(region) for region in region_set]
        df_sku = get_sku_df(region_set)
        df = pd.concat(dfs)
    else:
        with mp_pool.Pool() as pool:
            dfs_result = pool.map_async(get_pricing_df, region_set)
            df_sku_result = pool.apply_async(get_sku_df, (region_set,))

            dfs = dfs_result.get()
            df_sku = df_sku_result.get()
            df = pd.concat(dfs)

    print('Processing dataframes')
    df.drop_duplicates(inplace=True)

    df = df[df['unitPrice'] > 0]

    print('Getting price df')
    df['merge_name'] = df['armSkuName']
    # Use lower case for the Region, as for westus3, the SKU API returns
    # WestUS3.
    # This is inconsistent with the region name used in the pricing API, and
    # the case does not matter for launching instances, so we can safely
    # discard the case.
    df['Region'] = df['armRegionName'].str.lower()
    df['is_promo'] = df['skuName'].str.endswith(' Low Priority')
    df.rename(columns={
        'armSkuName': 'InstanceType',
    }, inplace=True)
    demand_df = df[~df['skuName'].str.contains(' Spot')][[
        'is_promo', 'InstanceType', 'Region', 'unitPrice'
    ]]
    spot_df = df[df['skuName'].str.contains(' Spot')][[
        'is_promo', 'InstanceType', 'Region', 'unitPrice'
    ]]

    demand_df.set_index(['InstanceType', 'Region', 'is_promo'], inplace=True)
    spot_df.set_index(['InstanceType', 'Region', 'is_promo'], inplace=True)

    demand_df = demand_df.rename(columns={'unitPrice': 'Price'})
    spot_df = spot_df.rename(columns={'unitPrice': 'SpotPrice'})

    print('Getting sku df')
    df_sku['is_promo'] = df_sku['name'].str.endswith('_Promo')
    df_sku.rename(columns={'name': 'InstanceType'}, inplace=True)

    df_sku['merge_name'] = df_sku['InstanceType'].str.replace('_Promo', '')
    df_sku['Region'] = df_sku['Region'].str.lower()

    print('Joining')
    df = df_sku.join(demand_df,
                     on=['merge_name', 'Region', 'is_promo'],
                     how='left')
    df = df.join(spot_df, on=['merge_name', 'Region', 'is_promo'], how='left')

    def get_capabilities(row):
        gpu_name = None
        gpu_count = np.nan
        vcpus = np.nan
        memory = np.nan
        gen_version = None
        caps = row['capabilities']
        for item in caps:
            assert isinstance(item, dict), (item, caps)
            if item['name'] == 'GPUs':
                gpu_name = get_gpu_name(row['family'])
                if gpu_name is not None:
                    gpu_count = item['value']
            elif item['name'] == 'vCPUs':
                vcpus = float(item['value'])
            elif item['name'] == 'MemoryGB':
                memory = item['value']
            elif item['name'] == 'HyperVGenerations':
                gen_version = item['value']
        return gpu_name, gpu_count, vcpus, memory, gen_version

    def get_additional_columns(row):
        gpu_name, gpu_count, vcpus, memory, gen_version = get_capabilities(row)
        return pd.Series({
            'AcceleratorName': gpu_name,
            'AcceleratorCount': gpu_count,
            'vCPUs': vcpus,
            'MemoryGiB': memory,
            'GpuInfo': gpu_name,
            'Generation': gen_version,
        })

    df_ret = pd.concat(
        [df, df.apply(get_additional_columns, axis='columns')],
        axis='columns',
    )

    def _upd_a10_gpu_count(row):
        new_gpu_cnt = AZURE_FRACTIONAL_A10_INS_TYPE_TO_NUM_GPUS.get(
            row['InstanceType'])
        if new_gpu_cnt is not None:
            return new_gpu_cnt
        return row['AcceleratorCount']

    # Manually update the GPU count for fractional A10 instance types.
    # Those instance types have fractional GPU count, but Azure API returns
    # 1 GPU count for them. We manually update the GPU count here.
    df_ret['AcceleratorCount'] = df_ret.apply(_upd_a10_gpu_count,
                                              axis='columns')

    # As of Dec 2023, a few H100 instance types fetched from Azure APIs do not
    # have pricing:
    #
    # df[df['InstanceType'].str.contains('H100')][['InstanceType', 'Price',
    # 'SpotPrice']]
    #                 InstanceType    Price  SpotPrice
    # 5830   Standard_NC40ads_H100_v5      NaN        NaN
    # 5831   Standard_NC40ads_H100_v5      NaN        NaN
    # 5875  Standard_NC80adis_H100_v5      NaN        NaN
    # 5876  Standard_NC80adis_H100_v5      NaN        NaN
    # 5901     Standard_ND48s_H100_v5      NaN        NaN
    # 5910   Standard_ND96isr_H100_v5  117.984    29.4960
    # 5911    Standard_ND96is_H100_v5  106.186    26.5465
    #
    # But these instance types are still launchable. We fill in $0 to enable
    # launching.
    h100_row_idx = df_ret['InstanceType'].str.contains('H100')
    df_ret.loc[h100_row_idx, ['Price', 'SpotPrice']] = df_ret.loc[
        h100_row_idx, ['Price', 'SpotPrice']].fillna(0)

    before_drop_len = len(df_ret)
    df_ret.dropna(subset=['InstanceType'], inplace=True, how='all')
    after_drop_len = len(df_ret)
    print(f'Dropped {before_drop_len - after_drop_len} duplicated rows')

    # Filter out deprecated families
    df_ret = df_ret.loc[~df_ret['family'].isin(DEPRECATED_FAMILIES)]
    df_ret = df_ret[USEFUL_COLUMNS]
    return df_ret


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--all-regions',
                       action='store_true',
                       help='Fetch all global regions, not just the U.S. ones.')
    group.add_argument('--regions',
                       nargs='+',
                       help='Fetch the list of specified regions.')
    parser.add_argument('--exclude',
                        nargs='+',
                        help='Exclude the list of specified regions.')
    parser.add_argument('--single-threaded',
                        action='store_true',
                        help='Run in single-threaded mode. This is useful when '
                        'running in github action, as the multiprocessing '
                        'does not work well with the azure client due '
                        'to ssl issues.')
    args = parser.parse_args()

    SINGLE_THREADED = args.single_threaded

    if args.regions:
        region_filter = set(args.regions) - EXCLUDED_REGIONS
    elif args.all_regions:
        region_filter = set(get_regions()) - EXCLUDED_REGIONS
    else:
        region_filter = US_REGIONS
    region_filter = region_filter - set(
        args.exclude) if args.exclude else region_filter

    if not region_filter:
        raise ValueError('No regions to fetch. Please check your arguments.')

    instance_df = get_all_regions_instance_types_df(region_filter)
    os.makedirs('azure', exist_ok=True)
    instance_df.to_csv('azure/vms.csv', index=False)
    print('Azure Service Catalog saved to azure/vms.csv')
