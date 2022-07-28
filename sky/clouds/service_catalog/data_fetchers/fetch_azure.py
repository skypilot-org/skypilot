"""A script that queries Azure API to get instance types and pricing info.

This script takes about 1 minute to finish.
"""
import json
import subprocess
from typing import Optional, Tuple
import urllib

import numpy as np
import pandas as pd
import ray
import requests

REGIONS = [
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
REGION_SET = set(REGIONS)
# Azure secretly deprecated the M60 family which is still returned by its API.
# We have to manually remove it.
DEPRECATED_FAMILIES = ['standardNVSv2Family']


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
    df = pd.DataFrame(items)
    df = df[(df['resourceType'] == 'virtualMachines')]
    df['Region'] = df.apply(lambda row: row['locations'][0], axis='columns')
    return df[df.apply(lambda row: row['Region'] in REGION_SET, axis='columns')]


def get_gpu_name(family: str) -> str:
    gpu_data = {
        'standardNCFamily': 'K80',
        'standardNCSv2Family': 'P100',
        'standardNCSv3Family': 'V100',
        'standardNCPromoFamily': 'K80',
        'StandardNCASv3_T4Family': 'T4',
        'standardNDSv2Family': 'V100-32GB',
        'standardNDAMSv4_A100Family': 'A100-80GB',
        'StandardNDASv4_A100Family': 'A100',
        'standardNVFamily': 'M60',
        'standardNVSv2Family': 'M60',
        'standardNVSv3Family': 'M60',
        'standardNVPromoFamily': 'M60',
        'standardNVSv4Family': 'Radeon MI25',
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

    def get_price(row):
        is_promo = row['name'].endswith('_Promo')
        sku = row['name'].replace('_Promo', '')
        region = row['Region']
        pricing_rows = df[(df['armSkuName'] == sku) &
                          (df['armRegionName'] == region) &
                          (df['unitPrice'] > 0) &
                          (~df['skuName'].str.contains(' Spot'))]
        if is_promo:
            pricing_rows = pricing_rows[pricing_rows['skuName'].str.contains(
                ' Low Priority')]
        else:
            pricing_rows = pricing_rows[~pricing_rows['skuName'].str.
                                        contains(' Low Priority')]
        assert len(pricing_rows) <= 1, (sku, pricing_rows)
        if len(pricing_rows) == 0:
            return np.nan
        return pricing_rows.iloc[0]['unitPrice']

    def get_spot_price(row):
        sku = row['name']
        region = row['Region']
        spot_pricing_rows = df[(df['armSkuName'] == sku) &
                               (df['armRegionName'] == region) &
                               (df['unitPrice'] > 0) &
                               (df['skuName'].str.contains(' Spot'))]
        assert len(spot_pricing_rows) <= 1, (sku, spot_pricing_rows)
        if len(spot_pricing_rows) == 0:
            return np.nan
        return spot_pricing_rows.iloc[0]['unitPrice']

    def get_capabilities(row) -> Tuple[str, float]:
        gpu_name = None
        gpu_count = np.nan
        memory_gb = np.nan
        caps = row['capabilities']
        for item in caps:
            if item['name'] == 'GPUs':
                gpu_name = get_gpu_name(row['family'])
                if gpu_name is not None:
                    gpu_count = item['value']
            elif item['name'] == 'MemoryGB':
                memory_gb = item['value']
        return gpu_name, gpu_count, memory_gb

    def get_additional_columns(row):
        gpu_name, gpu_count, memory_gb = get_capabilities(row)
        return pd.Series({
            'Price': get_price(row),
            'SpotPrice': get_spot_price(row),
            'AcceleratorName': gpu_name,
            'AcceleratorCount': gpu_count,
            'MemoryGiB': memory_gb,
            'GpuInfo': gpu_name,
        })

    df_ret = pd.concat(
        [df_sku, df_sku.apply(get_additional_columns, axis='columns')],
        axis='columns',
    ).rename(columns={'name': 'InstanceType'})
    # Filter out deprecated families
    df_ret = df_ret.loc[~df_ret['family'].isin(DEPRECATED_FAMILIES)]
    return df_ret


if __name__ == '__main__':
    ray.init()
    df = get_all_regions_instance_types_df()
    df.to_csv('azure.csv', index=False)
    print('Azure Service Catalog saved to azure.csv')
