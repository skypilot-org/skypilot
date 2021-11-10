"""A script that queries AWS API to get instance types and pricing information.

This script takes about 1 minute to finish.
"""
from typing import Tuple

from absl import app
from absl import flags
from absl import logging
import boto3
import numpy as np
import pandas as pd
import ray
import datetime

REGIONS = ['us-west-1', 'us-west-2', 'us-east-1', 'us-east-2']
# NOTE: the hard-coded us-east-1 URL is not a typo. AWS pricing endpoint is
# only available in this region, but it serves pricing information for all regions.
PRICING_TABLE_URL_FMT = 'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{region}/index.csv'


@ray.remote
def get_instance_types(region: str) -> pd.DataFrame:
    client = boto3.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_instance_types')
    items = []
    for i, resp in enumerate(paginator.paginate()):
        print(f'{region} getting instance types page {i}')
        items += resp['InstanceTypes']

    zones = []
    response = client.describe_availability_zones()
    for resp in response['AvailabilityZones']:
        zones.append({'AvailabilityZone': resp['ZoneName']})
    return pd.DataFrame(items).merge(pd.DataFrame(zones), how='cross')


@ray.remote
def get_pricing_table(region: str) -> pd.DataFrame:
    print(f'{region} downloading pricing table')
    url = PRICING_TABLE_URL_FMT.format(region=region)
    df = pd.read_csv(url, skiprows=5, low_memory=False)
    return df[(df['TermType'] == 'OnDemand') &
              (df['Operating System'] == 'Linux') &
              df['Pre Installed S/W'].isnull() &
              (df['CapacityStatus'] == 'Used') &
              (df['Tenancy'].isin(['Host', 'Shared'])) &
              df['PricePerUnit'] > 0].set_index('Instance Type')


@ray.remote
def get_spot_pricing_table(region: str) -> pd.DataFrame:
    print(f'{region} downloading spot pricing table')
    client = boto3.client('ec2', region_name=region)
    response = client.describe_spot_price_history(
        ProductDescriptions=['Linux/UNIX'],
        StartTime=datetime.datetime.utcnow(),
    )
    df = pd.DataFrame(response['SpotPriceHistory']).set_index(
        ['InstanceType', 'AvailabilityZone'])
    return df


@ray.remote
def get_instance_types_df(region: str) -> pd.DataFrame:
    df, pricing_df, spot_pricing_df = ray.get([
        get_instance_types.remote(region),
        get_pricing_table.remote(region),
        get_spot_pricing_table.remote(region)
    ])
    print(f'{region} Processing dataframes')

    def get_price(row):
        t = row['InstanceType']
        try:
            return pricing_df.loc[t]['PricePerUnit']
        except KeyError:
            print(f'{region} WARNING: cannot find pricing for {t}')
            return np.nan

    def get_spot_price(row):
        instance = row['InstanceType']
        zone = row['AvailabilityZone']
        try:
            return spot_pricing_df.loc[(instance, zone)]['SpotPrice']
        except KeyError:
            print(
                f'{region} WARNING: cannot find spot pricing for {instance} {(zone)}'
            )
            return np.nan

    def get_gpu_info(row) -> Tuple[str, float]:
        info = row['GpuInfo']
        if not isinstance(info, dict):
            return None, np.nan
        gpu = info['Gpus'][0]
        return gpu['Name'], gpu['Count']

    def get_additional_columns(row):
        gpu_name, gpu_count = get_gpu_info(row)
        return pd.Series({
            'PricePerHour': get_price(row),
            'SpotPricePerHour': get_spot_price(row),
            'GpuName': gpu_name,
            'GpuCount': gpu_count,
        })

    df['Region'] = region
    df = pd.concat([df, df.apply(get_additional_columns, axis='columns')],
                   axis='columns')
    return df


def get_all_regions_instance_types_df():
    dfs = ray.get([get_instance_types_df.remote(r) for r in REGIONS])
    df = pd.concat(dfs)
    df.sort_values(['InstanceType', 'Region'], inplace=True)
    return df


def main(argv):
    del argv  # Unused.
    ray.init()
    logging.set_verbosity(logging.DEBUG)
    df = get_all_regions_instance_types_df()
    df.to_csv('aws.csv', index=False)
    print('AWS Service Catalog saved to aws.csv')


if __name__ == '__main__':
    app.run(main)
