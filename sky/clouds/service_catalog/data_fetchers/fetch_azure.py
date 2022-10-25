"""A script that queries Azure API to get instance types and pricing info.

This script takes about 1 minute to finish.
"""
import json
import subprocess
from typing import Optional
import urllib

import numpy as np
import pandas as pd
import ray
import requests

US_REGIONS = [
    'centralus',
    'eastus',
    'eastus2',
    'northcentralus',
    'southcentralus',
    'westcentralus',
    'westus',
    'westus2',
    # 'WestUS3',   # WestUS3 pricing table is broken as of 2021/11.
]

# To enable all the regions, uncomment the following line.
# def get_regions() -> Tuple[str]:
#     """Get all available regions."""
#     proc = subprocess.run('az account list-locations  --query "[?not_null(metadata.latitude)] .{RegionName:name , RegionDisplayName:regionalDisplayName}" -o json', shell=True, check=True, stdout=subprocess.PIPE)
#     items = json.loads(proc.stdout.decode('utf-8'))
#     regions = [item['RegionName'] for item in items if not item['RegionName'].endswith('stg')]
#     return tuple(regions)
# all_regions = get_regions()

# REGIONS = all_regions
REGIONS = US_REGIONS
REGION_SET = set(REGIONS)
# Azure secretly deprecated the M60 family which is still returned by its API.
# We have to manually remove it.
DEPRECATED_FAMILIES = ['standardNVSv2Family']

USEFUL_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'GpuInfo', 'Price', 'SpotPrice', 'Region', 'Generation'
]


def get_pricing_url(region: Optional[str] = None) -> str:
    filters = [
        "serviceName eq 'Virtual Machines'",
        "priceType eq 'Consumption'",
    ]
    if region is not None:
        filters.append(f"armRegionName eq '{region}'")
    filters_str = urllib.parse.quote(" and ".join(filters))
    return f'https://prices.azure.com/api/retail/prices?$filter={filters_str}'


@ray.remote
def get_pricing_df(region: Optional[str] = None) -> pd.DataFrame:
    all_items = []
    url = get_pricing_url(region)
    print(f'Getting pricing for {region}')
    page = 0
    while url is not None:
        page += 1
        if page % 10 == 0:
            print(f'Fetched pricing pages {page}')
        r = requests.get(url)
        r.raise_for_status()
        content = r.content.decode('ascii')
        content = json.loads(content)
        items = content.get('Items', [])
        if len(items) == 0:
            break
        all_items += items
        url = content.get('NextPageLink')
    print(f'Done fetching pricing {region}')
    df = pd.DataFrame(all_items)
    assert 'productName' in df.columns, (region, df.columns)
    return df[(~df['productName'].str.contains(' Windows')) &
              (df['unitPrice'] > 0)]


@ray.remote
def get_all_regions_pricing_df() -> pd.DataFrame:
    dfs = ray.get([get_pricing_df.remote(region) for region in REGIONS])
    return pd.concat(dfs)


@ray.remote
def get_sku_df() -> pd.DataFrame:
    print(f'Fetching SKU list')
    # To get a complete list, --all option is necessary.
    proc = subprocess.run(
        f'az vm list-skus --all',
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
    )
    print(f'Done fetching SKUs')
    items = json.loads(proc.stdout.decode('ascii'))
    filtered_items = []
    for item in items:
        # zones = item['locationInfo'][0]['zones']
        region = item['locations'][0]
        if region not in REGION_SET:
            continue
        item['Region'] = region
        filtered_items.append(item)

    df = pd.DataFrame(filtered_items)
    df = df[(df['resourceType'] == 'virtualMachines')]
    return df


def get_gpu_name(family: str) -> str:
    gpu_data = {
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
        'standardNVSv4Family': 'Radeon MI25',
        'standardNDSFamily': 'P40',
        'StandardNVADSA10v5Family': 'A10',
    }
    # NP-series offer Xilinx U250 FPGAs which are not GPUs,
    # so we do not include them here.
    # https://docs.microsoft.com/en-us/azure/virtual-machines/np-series
    family = family.replace(' ', '')
    return gpu_data.get(family)


def get_all_regions_instance_types_df():
    df, df_sku = ray.get([
        get_all_regions_pricing_df.remote(),
        get_sku_df.remote(),
    ])
    print(f'Processing dataframes')
    df.drop_duplicates(inplace=True)

    df = df[df['unitPrice'] > 0]

    print('Getting price df')
    df['merge_name'] = df['armSkuName']
    df['is_promo'] = df['skuName'].str.endswith(' Low Priority')
    df.rename(columns={
        'armSkuName': 'InstanceType',
        'armRegionName': 'Region',
    },
              inplace=True)
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

    print('Joining')
    df = df_sku.join(demand_df,
                     on=['merge_name', 'Region', 'is_promo'],
                     how='left')
    df = df.join(spot_df, on=['merge_name', 'Region', 'is_promo'], how='left')

    def get_capabilities(row):
        gpu_name = None
        gpu_count = np.nan
        vcpus = np.nan
        memory_gb = np.nan
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
                memory_gb = item['value']
            elif item['name'] == 'HyperVGenerations':
                gen_version = item['value']
        return gpu_name, gpu_count, vcpus, memory_gb, gen_version

    def get_additional_columns(row):
        gpu_name, gpu_count, vcpus, memory_gb, gen_version = get_capabilities(
            row)
        return pd.Series({
            'AcceleratorName': gpu_name,
            'AcceleratorCount': gpu_count,
            'vCPUs': vcpus,
            'MemoryGiB': memory_gb,
            'GpuInfo': gpu_name,
            'Generation': gen_version,
        })

    df_ret = pd.concat(
        [df, df.apply(get_additional_columns, axis='columns')],
        axis='columns',
    )

    before_drop_len = len(df_ret)
    df_ret.dropna(subset=['InstanceType'], inplace=True, how='all')
    after_drop_len = len(df_ret)
    print('Dropped {} duplicated rows'.format(before_drop_len - after_drop_len))

    # Filter out deprecated families
    df_ret = df_ret.loc[~df_ret['family'].isin(DEPRECATED_FAMILIES)]
    df_ret = df_ret[USEFUL_COLUMNS]
    return df_ret


if __name__ == '__main__':
    ray.init()
    df = get_all_regions_instance_types_df()
    df.to_csv('azure.csv', index=False)
    print('Azure Service Catalog saved to azure.csv')
