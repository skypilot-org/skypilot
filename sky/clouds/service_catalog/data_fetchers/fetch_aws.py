"""A script that queries AWS API to get instance types and pricing information.

This script takes about 1 minute to finish.
"""
import datetime
from typing import Tuple

from absl import app
from absl import logging
import numpy as np
import pandas as pd
import ray

from sky.clouds.service_catalog import common
from sky.cloud_adaptors import aws

REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']
# NOTE: the hard-coded us-east-1 URL is not a typo. AWS pricing endpoint is
# only available in this region, but it serves pricing information for all regions.
PRICING_TABLE_URL_FMT = 'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{region}/index.csv'


@ray.remote
def get_instance_types(region: str) -> pd.DataFrame:
    client = aws.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_instance_types')
    items = []
    for i, resp in enumerate(paginator.paginate()):
        print(f'{region} getting instance types page {i}')
        items += resp['InstanceTypes']

    return pd.DataFrame(items)


@ray.remote
def get_availability_zones(region: str) -> pd.DataFrame:
    client = aws.client('ec2', region_name=region)
    zones = []
    response = client.describe_availability_zones()
    for resp in response['AvailabilityZones']:
        zones.append({'AvailabilityZone': resp['ZoneName']})
    return pd.DataFrame(zones)


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
    client = aws.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_spot_price_history')
    response_iterator = paginator.paginate(ProductDescriptions=['Linux/UNIX'],
                                           StartTime=datetime.datetime.utcnow())
    ret = []
    for response in response_iterator:
        ret = ret + response['SpotPriceHistory']
    df = pd.DataFrame(ret).set_index(['InstanceType', 'AvailabilityZone'])
    return df


@ray.remote
def get_instance_types_df(region: str) -> pd.DataFrame:
    df, zone_df, pricing_df, spot_pricing_df = ray.get([
        get_instance_types.remote(region),
        get_availability_zones.remote(region),
        get_pricing_table.remote(region),
        get_spot_pricing_table.remote(region)
    ])
    print(f'{region} Processing dataframes')

    def get_price(row):
        t = row['InstanceType']
        try:
            return pricing_df.loc[t]['PricePerUnit']
        except KeyError:
            return np.nan

    def get_spot_price(row):
        instance = row['InstanceType']
        zone = row['AvailabilityZone']
        try:
            return spot_pricing_df.loc[(instance, zone)]['SpotPrice']
        except KeyError:
            return np.nan

    def get_acc_info(row) -> Tuple[str, float]:
        accelerator = None
        for col, info_key in [('GpuInfo', 'Gpus'),
                              ('InferenceAcceleratorInfo', 'Accelerators'),
                              ('FpgaInfo', 'Fpgas')]:
            info = row.get(col)
            if isinstance(info, dict):
                accelerator = info[info_key][0]
        if accelerator is None:
            return None, np.nan
        return accelerator['Name'], accelerator['Count']

    def get_memory_gib(row) -> float:
        return row['MemoryInfo']['SizeInMiB'] // 1024

    def get_additional_columns(row):
        acc_name, acc_count = get_acc_info(row)
        # AWS p3dn.24xlarge offers a different V100 GPU.
        # See https://aws.amazon.com/blogs/compute/optimizing-deep-learning-on-p3-and-p3dn-with-efa/
        if row['InstanceType'] == 'p3dn.24xlarge':
            acc_name = 'V100-32GB'
        return pd.Series({
            'Price': get_price(row),
            'SpotPrice': get_spot_price(row),
            'AcceleratorName': acc_name,
            'AcceleratorCount': acc_count,
            'MemoryGiB': get_memory_gib(row),
        })

    df['Region'] = region
    df = df.merge(pd.DataFrame(zone_df), how='cross')
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
    df.to_csv(common.get_data_path('aws.csv'), index=False)
    print('AWS Service Catalog saved to aws.csv')


if __name__ == '__main__':
    app.run(main)
