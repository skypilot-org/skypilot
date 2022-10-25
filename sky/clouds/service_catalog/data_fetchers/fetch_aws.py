"""A script that queries AWS API to get instance types and pricing information.
This script takes about 1 minute to finish.
"""
import datetime
from typing import Tuple, Union

import numpy as np
import pandas as pd
import ray

from sky.adaptors import aws

ALL_REGIONS = [
    'us-east-1',
    'us-east-2',
    'us-west-1',
    'us-west-2',
    'ca-central-1',
    'eu-central-1',
    'eu-west-1',
    'eu-west-2',
    'eu-south-1',
    'eu-west-3',
    'eu-north-1',
    'me-south-1',
    # 'me-central-1', # failed for no credential
    'af-south-1',
    'ap-east-1',
    'ap-southeast-3',
    # 'ap-south-1', # failed for no credential
    'ap-northeast-3',
    'ap-northeast-2',
    'ap-southeast-1',
    'ap-southeast-2',
    'ap-northeast-1',
]
US_REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']

REGIONS = US_REGIONS

USEFUL_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'GpuInfo', 'Price', 'SpotPrice', 'Region', 'AvailabilityZone'
]

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
    df.rename(columns={
        'Instance Type': 'InstanceType',
        'PricePerUnit': 'Price',
    },
              inplace=True)
    return df[(df['TermType'] == 'OnDemand') &
              (df['Operating System'] == 'Linux') &
              df['Pre Installed S/W'].isnull() &
              (df['CapacityStatus'] == 'Used') &
              (df['Tenancy'].isin(['Host', 'Shared'])) & df['Price'] > 0][[
                  'InstanceType', 'Price', 'vCPU', 'Memory'
              ]]


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
    df = pd.DataFrame(ret)[['InstanceType', 'AvailabilityZone', 'SpotPrice']]
    df = df.set_index(['InstanceType', 'AvailabilityZone'])
    return df


@ray.remote
def get_instance_types_df(region: str) -> Union[str, pd.DataFrame]:
    try:
        df, zone_df, pricing_df, spot_pricing_df = ray.get([
            get_instance_types.remote(region),
            get_availability_zones.remote(region),
            get_pricing_table.remote(region),
            get_spot_pricing_table.remote(region),
        ])
        print(f'{region} Processing dataframes')

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

        def get_vcpus(row) -> float:
            if not np.isnan(row['vCPU']):
                return float(row['vCPU'])
            return float(row['VCpuInfo']['DefaultVCpus'])

        def get_memory_gib(row) -> float:
            if isinstance(row['MemoryInfo'], dict):
                return row['MemoryInfo']['SizeInMiB'] // 1024
            return int(row['Memory'].split(' GiB')[0])

        def get_additional_columns(row) -> pd.Series:
            acc_name, acc_count = get_acc_info(row)
            # AWS p3dn.24xlarge offers a different V100 GPU.
            # See https://aws.amazon.com/blogs/compute/optimizing-deep-learning-on-p3-and-p3dn-with-efa/
            if row['InstanceType'] == 'p3dn.24xlarge':
                acc_name = 'V100-32GB'
            if row['InstanceType'] == 'p4de.24xlarge':
                acc_name = 'A100-80GB'
                acc_count = 8
            return pd.Series({
                'AcceleratorName': acc_name,
                'AcceleratorCount': acc_count,
                'vCPUs': get_vcpus(row),
                'MemoryGiB': get_memory_gib(row),
            })

        # The AWS API may not have all the instance types in the pricing table,
        # so we need to merge the two dataframes.
        df = df.merge(pricing_df, on=['InstanceType'], how='outer')
        df['Region'] = region
        # Cartesian product of instance types and availability zones, so that
        # we can join the spot pricing table per instance type and zone.
        df = df.merge(pd.DataFrame(zone_df), how='cross')

        # Add spot price column, by joining the spot pricing table.
        df = df.merge(spot_pricing_df,
                      left_on=['InstanceType', 'AvailabilityZone'],
                      right_index=True,
                      how='outer')

        # Extract vCPUs, memory, and accelerator info from the columns.
        df = pd.concat(
            [df, df.apply(get_additional_columns, axis='columns')],
            axis='columns')
        # patch the GpuInfo for p4de.24xlarge
        df.loc[df['InstanceType'] == 'p4de.24xlarge', 'GpuInfo'] = 'A100-80GB'
        df = df[USEFUL_COLUMNS]
    except Exception as e:
        print(f'{region} failed with {e}')
        return region
    return df


def get_all_regions_instance_types_df():
    df_or_regions = ray.get([get_instance_types_df.remote(r) for r in REGIONS])
    new_dfs = []
    for df_or_region in df_or_regions:
        if isinstance(df_or_region, str):
            print(f'{df_or_region} failed')
        else:
            new_dfs.append(df_or_region)

    df = pd.concat(new_dfs)
    df.sort_values(['InstanceType', 'Region'], inplace=True)
    return df


if __name__ == '__main__':
    ray.init()
    df = get_all_regions_instance_types_df()
    df.to_csv('aws.csv', index=False)
    print('AWS Service Catalog saved to aws.csv')
