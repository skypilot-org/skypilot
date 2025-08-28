"""A script that queries AWS API to get instance types and pricing information.
This script takes about 1 minute to finish.
"""
import argparse
import collections
import datetime
import itertools
from multiprocessing import pool as mp_pool
import os
import re
import subprocess
import sys
import textwrap
import traceback
import typing
from typing import List, Optional, Set, Tuple, Union

import numpy as np

from sky import exceptions
from sky.adaptors import aws
from sky.adaptors import common as adaptors_common
from sky.utils import log_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from mypy_boto3_ec2 import type_defs as ec2_type_defs
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

# Enable most of the regions. Each user's account may have a subset of these
# enabled; this is ok because we take the intersection of the list here with
# the user-specific enabled regions in `aws_catalog`, via the "availability
# zone mapping".
# TODO(zhwu): fix the regions with no supported AMI (maybe by finding AMIs
# similar to the Deep Learning AMI).
ALL_REGIONS = [
    'us-east-1',
    'us-east-2',
    'us-west-1',
    'us-west-2',
    'ca-central-1',
    'eu-central-1',
    # 'eu-central-2', # no supported AMI
    'eu-west-1',
    'eu-west-2',
    'eu-south-1',
    'eu-south-2',
    'eu-west-3',
    'eu-north-1',
    'me-south-1',
    'me-central-1',
    'af-south-1',
    'ap-east-1',
    'ap-southeast-3',
    'ap-south-1',
    # 'ap-south-2', # no supported AMI
    'ap-northeast-3',
    'ap-northeast-2',
    'ap-southeast-1',
    'ap-southeast-2',
    'ap-northeast-1',
]
US_REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']

# The following columns will be included in the final catalog.
USEFUL_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'GpuInfo', 'Price', 'SpotPrice', 'Region', 'AvailabilityZone'
]

# NOTE: the hard-coded us-east-1 URL is not a typo. AWS pricing endpoint is
# only available in this region, but it serves pricing information for all
# regions.
PRICING_TABLE_URL_FMT = 'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{region}/index.csv'  # pylint: disable=line-too-long
# Hardcode the regions that offer p4de.24xlarge as our credential does not have
# the permission to query the offerings of the instance.
# Ref: https://aws.amazon.com/ec2/instance-types/p4/
P4DE_REGIONS = ['us-east-1', 'us-west-2']
# g6f instances have fractional GPUs, but the API returns Count: 1 under
# GpuInfo. However, the GPU memory is properly scaled. Taking the instance GPU
# divided by the total memory of an L4 will give us the fraction of the GPU.
L4_GPU_MEMORY = 22888

regions_enabled: Optional[Set[str]] = None


def get_enabled_regions() -> Set[str]:
    # Should not be called concurrently.
    global regions_enabled
    if regions_enabled is None:
        aws_client = aws.client('ec2', region_name='us-east-1')
        try:
            user_cloud_regions = aws_client.describe_regions()['Regions']
        except aws.botocore_exceptions().ClientError as e:
            if e.response['Error']['Code'] == 'UnauthorizedOperation':
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Failed to retrieve AWS regions. '
                        'Please ensure that the `ec2:DescribeRegions` action '
                        'is enabled for your AWS account in IAM. '
                        'Ref: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeRegions.html'  # pylint: disable=line-too-long
                    ) from None
            else:
                raise
        regions_enabled = {r['RegionName'] for r in user_cloud_regions}
        regions_enabled = regions_enabled.intersection(set(ALL_REGIONS))
    return regions_enabled


def _get_instance_types(region: str) -> 'pd.DataFrame':
    client = aws.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_instance_types')
    items = []
    for i, resp in enumerate(paginator.paginate()):
        print(f'{region} getting instance types page {i}')
        items += resp['InstanceTypes']

    return pd.DataFrame(items)


def _get_instance_type_offerings(region: str) -> 'pd.DataFrame':
    client = aws.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_instance_type_offerings')
    items = []
    for i, resp in enumerate(
            paginator.paginate(LocationType='availability-zone')):
        print(f'{region} getting instance type offerings page {i}')
        items += resp['InstanceTypeOfferings']

    return pd.DataFrame(items).rename(
        columns={'Location': 'AvailabilityZoneName'})


def _get_availability_zones(region: str) -> 'pd.DataFrame':
    client = aws.client('ec2', region_name=region)
    zones = []
    try:
        response = client.describe_availability_zones()
    except aws.botocore_exceptions().ClientError as e:
        if e.response['Error']['Code'] == 'AuthFailure':
            # The user's AWS account may not have access to this region.
            # The error looks like:
            # botocore.exceptions.ClientError: An error occurred
            # (AuthFailure) when calling the DescribeAvailabilityZones
            # operation: AWS was not able to validate the provided
            # access credentials
            with ux_utils.print_exception_no_traceback():
                raise exceptions.AWSAzFetchingError(
                    region,
                    reason=exceptions.AWSAzFetchingError.Reason.AUTH_FAILURE
                ) from None
        elif e.response['Error']['Code'] == 'UnauthorizedOperation':
            with ux_utils.print_exception_no_traceback():
                raise exceptions.AWSAzFetchingError(
                    region,
                    reason=exceptions.AWSAzFetchingError.Reason.
                    AZ_PERMISSION_DENIED) from None
        else:
            raise
    for resp in response['AvailabilityZones']:
        zones.append({
            'AvailabilityZoneName': resp['ZoneName'],
            'AvailabilityZone': resp['ZoneId'],
        })
    return pd.DataFrame(zones)


def _get_pricing_table(region: str) -> 'pd.DataFrame':
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


def _get_spot_pricing_table(region: str) -> 'pd.DataFrame':
    """Get spot pricing table for a region.

    Example output:
        InstanceType  AvailabilityZoneName  SpotPrice
        p3.2xlarge    us-east-1a            1.0000
    """
    print(f'{region} downloading spot pricing table')
    client = aws.client('ec2', region_name=region)
    paginator = client.get_paginator('describe_spot_price_history')
    response_iterator = paginator.paginate(ProductDescriptions=['Linux/UNIX'],
                                           StartTime=datetime.datetime.utcnow())
    ret: List['ec2_type_defs.SpotPriceTypeDef'] = []
    for response in response_iterator:
        # response['SpotPriceHistory'] is a list of dicts, each dict is like:
        # {
        #  'AvailabilityZone': 'us-east-2c',  # This is the AZ name.
        #  'InstanceType': 'm6i.xlarge',
        #  'ProductDescription': 'Linux/UNIX',
        #  'SpotPrice': '0.041900',
        #  'Timestamp': datetime.datetime
        # }
        ret = ret + response['SpotPriceHistory']
    df = pd.DataFrame(ret)[['InstanceType', 'AvailabilityZone', 'SpotPrice']]
    df = df.rename(columns={'AvailabilityZone': 'AvailabilityZoneName'})
    df = df.set_index(['InstanceType', 'AvailabilityZoneName'])
    return df


def _patch_p4de(region: str, df: 'pd.DataFrame',
                pricing_df: 'pd.DataFrame') -> 'pd.DataFrame':
    # Hardcoded patch for p4de.24xlarge, as our credentials doesn't have access
    # to the instance type.
    # Columns:
    # InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,
    # Price,SpotPrice,Region,AvailabilityZone
    records = []
    for zone in df[df['Region'] == region]['AvailabilityZone'].unique():
        records.append({
            'InstanceType': 'p4de.24xlarge',
            'AcceleratorName': 'A100-80GB',
            'AcceleratorCount': 8,
            'vCPUs': 96,
            'MemoryGiB': 1152,
            'GpuInfo':
                ('{\'Gpus\': [{\'Name\': \'A100-80GB\', \'Manufacturer\': '
                 '\'NVIDIA\', \'Count\': 8, \'MemoryInfo\': {\'SizeInMiB\': '
                 '81920}}], \'TotalGpuMemoryInMiB\': 655360}'),
            'AvailabilityZone': zone,
            'Region': region,
            'Price': pricing_df[pricing_df['InstanceType'] == 'p4de.24xlarge']
                     ['Price'].values[0],
            'SpotPrice': np.nan,
        })
    df = pd.concat([df, pd.DataFrame.from_records(records)])
    return df


def _get_instance_types_df(region: str) -> Union[str, 'pd.DataFrame']:
    try:
        # Fetch the zone info first to make sure the account has access to the
        # region.
        zone_df = _get_availability_zones(region)

        # Use ThreadPool instead of Pool because this function can be called
        # within a multiprocessing.Pool, and Pool cannot be nested.
        with mp_pool.ThreadPool() as pool:
            futures = [
                pool.apply_async(_get_instance_types, (region,)),
                pool.apply_async(_get_instance_type_offerings, (region,)),
                pool.apply_async(_get_pricing_table, (region,)),
                pool.apply_async(_get_spot_pricing_table, (region,))
            ]
            df, offering_df, pricing_df, spot_pricing_df = [
                future.get() for future in futures
            ]
        print(f'{region} Processing dataframes')

        def get_acc_info(row) -> Tuple[Optional[str], float]:
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
            try:
                return float(row['VCpuInfo']['DefaultVCpus'])
            except Exception as e:  # pylint: disable=broad-except
                print('Error occurred for row:', row)
                print('Error:', e)
                raise

        def get_memory_gib(row) -> float:
            if isinstance(row['MemoryInfo'], dict):
                return row['MemoryInfo']['SizeInMiB'] / 1024
            return float(row['Memory'].split(' GiB')[0])

        def get_additional_columns(row) -> pd.Series:
            acc_name, acc_count = get_acc_info(row)
            # AWS p3dn.24xlarge offers a different V100 GPU.
            # See https://aws.amazon.com/blogs/compute/optimizing-deep-learning-on-p3-and-p3dn-with-efa/ # pylint: disable=line-too-long
            if row['InstanceType'] == 'p3dn.24xlarge':
                acc_name = 'V100-32GB'
            if row['InstanceType'] == 'p4de.24xlarge':
                acc_name = 'A100-80GB'
                acc_count = 8
            if row['InstanceType'].startswith('trn1'):
                # Trainium instances does not have a field for information of
                # the accelerators. We need to infer the accelerator info from
                # the instance type name.
                # aws ec2 describe-instance-types --region us-east-1
                # https://aws.amazon.com/ec2/instance-types/trn1/
                acc_name = 'Trainium'
                find_num_in_name = re.search(r'(\d+)xlarge',
                                             row['InstanceType'])
                assert find_num_in_name is not None, row['InstanceType']
                num_in_name = find_num_in_name.group(1)
                acc_count = int(num_in_name) // 2
            if row['InstanceType'] == 'p5en.48xlarge':
                # TODO(andyl): Check if this workaround still needed after
                # v0.10.0 released. Currently, the acc_name returned by the
                # AWS API is 'NVIDIA', which is incorrect. See #4652.
                acc_name = 'H200'
                acc_count = 8
            if (row['InstanceType'].startswith('g6f') or
                    row['InstanceType'].startswith('gr6f')):
                # These instance actually have only fractional GPUs, but the API
                # returns Count: 1 under GpuInfo. We need to check the GPU
                # memory to get the actual fraction of the GPU.
                # See also Standard_NV{vcpu}ads_A10_v5 support on Azure.
                fraction = row['GpuInfo']['TotalGpuMemoryInMiB'] / L4_GPU_MEMORY
                acc_count = round(fraction, 3)
            if row['InstanceType'] == 'p5.4xlarge':
                acc_count = 1
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
        # An instance type may not be available in all zones. We need to
        # merge the zone info to the instance type info.
        df = df.merge(offering_df, on=['InstanceType'], how='inner')
        # Add the mapping from zone name to zone id, as the zone id is
        # the real identifier for a zone across different users.
        df = df.merge(zone_df, on=['AvailabilityZoneName'], how='inner')

        # Add spot price column, by joining the spot pricing table.
        df = df.merge(spot_pricing_df,
                      left_on=['InstanceType', 'AvailabilityZoneName'],
                      right_index=True,
                      how='left')

        # Extract vCPUs, memory, and accelerator info from the columns.
        df = pd.concat(
            [df, df.apply(get_additional_columns, axis='columns')],
            axis='columns')
        # patch the df for p4de.24xlarge
        if region in P4DE_REGIONS:
            df = _patch_p4de(region, df, pricing_df)
        if 'GpuInfo' not in df.columns:
            df['GpuInfo'] = np.nan
        df = df[USEFUL_COLUMNS]
    except Exception as e:  # pylint: disable=broad-except
        print(traceback.format_exc())
        print(f'{region} failed with {e}', file=sys.stderr)
        return region
    return df


def get_all_regions_instance_types_df(regions: Set[str]) -> 'pd.DataFrame':
    with mp_pool.Pool() as pool:
        df_or_regions = pool.map(_get_instance_types_df, regions)
    new_dfs = []
    for df_or_region in df_or_regions:
        if isinstance(df_or_region, str):
            print(f'{df_or_region} failed')
        else:
            new_dfs.append(df_or_region)

    df = pd.concat(new_dfs)
    df.sort_values(['InstanceType', 'Region', 'AvailabilityZone'], inplace=True)
    return df


# Fetch Images
# https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#Images:visibility=public-images;v=3;search=:64,:Ubuntu%2020,:Deep%20Learning%20AMI%20GPU%20PyTorch # pylint: disable=line-too-long
# Current AMIs (we have to use different PyTorch versions for different OS as Ubuntu 18.04
# does not have the latest PyTorch version):
# GPU:
# Deep Learning AMI GPU PyTorch 2.1.0 (Ubuntu 20.04) 20231103
#   Nvidia driver: 535.104.12, CUDA Version: 12.2
#
# Deep Learning AMI GPU PyTorch 1.10.0 (Ubuntu 18.04) 20221114
#   Nvidia driver: 510.47.03, CUDA Version: 11.6
#
# K80:
# Deep Learning AMI GPU PyTorch 1.10.0 (Ubuntu 20.04) 20211208
#   Nvidia driver: 470.57.02, CUDA Version: 11.4
#
# Deep Learning AMI GPU PyTorch 1.10.0 (Ubuntu 18.04) 20211208
#   Nvidia driver: 470.57.02, CUDA Version: 11.4
#
# Neuron (Inferentia / Trainium):
# https://aws.amazon.com/releasenotes/aws-deep-learning-ami-base-neuron-ubuntu-20-04/  # pylint: disable=line-too-long
# Deep Learning Base Neuron AMI (Ubuntu 20.04) 20240923
# TODO(tian): find out the driver version.
#   Neuron driver:
_GPU_DESC_UBUNTU_DATE = [
    ('gpu', 'AMI GPU PyTorch 2.1.0', '20.04', '20231103'),
    ('gpu', 'AMI GPU PyTorch 1.10.0', '18.04', '20221114'),
    ('k80', 'AMI GPU PyTorch 1.10.0', '20.04', '20211208'),
    ('k80', 'AMI GPU PyTorch 1.10.0', '18.04', '20211208'),
    ('neuron', 'Base Neuron AMI', '22.04', '20240923'),
]


def _fetch_image_id(region: str, description: str, ubuntu_version: str,
                    creation_date: str) -> Optional[str]:
    try:
        image = subprocess.check_output(f"""\
            aws ec2 describe-images --region {region} --owners amazon \\
                --filters 'Name=name,Values="Deep Learning {description} (Ubuntu {ubuntu_version}) {creation_date}"' \\
                    'Name=state,Values=available' --query 'Images[:1].ImageId' --output text
            """,
                                        shell=True)
    except subprocess.CalledProcessError as e:
        print(f'Failed {region}, {description}, {ubuntu_version}, '
              f'{creation_date}. Trying next date.')
        print(f'{type(e)}: {e}')
        image_id = None
    else:
        assert image is not None
        image_id = image.decode('utf-8').strip()
    return image_id


def _get_image_row(region: str, gpu: str, description: str, ubuntu_version: str,
                   date: str) -> Tuple[str, str, str, str, Optional[str], str]:
    print(f'Getting image for {region}, {description}, {ubuntu_version}, {gpu}')
    image_id = _fetch_image_id(region, description, ubuntu_version, date)
    if image_id is None:
        # not found
        print(f'Failed to find image for {region}, {description}, '
              f'{ubuntu_version}, {gpu}')
    tag = f'skypilot:{gpu}-ubuntu-{ubuntu_version.replace(".", "")}'
    return tag, region, 'ubuntu', ubuntu_version, image_id, date


def get_all_regions_images_df(regions: Set[str]) -> 'pd.DataFrame':
    image_metas = [
        (r, *i) for r, i in itertools.product(regions, _GPU_DESC_UBUNTU_DATE)
    ]
    with mp_pool.Pool() as pool:
        results = pool.starmap(_get_image_row, image_metas)
    result_df = pd.DataFrame(
        results,
        columns=['Tag', 'Region', 'OS', 'OSVersion', 'ImageId', 'CreationDate'])
    result_df.sort_values(['Tag', 'Region'], inplace=True)
    return result_df


def fetch_availability_zone_mappings() -> 'pd.DataFrame':
    """Fetch the availability zone mappings from ID to Name.

    Example output:
        AvailabilityZone  AvailabilityZoneName
        use1-az1          us-east-1b
        use1-az2          us-east-1a
    """
    regions = list(get_enabled_regions())

    errored_region_reasons = []

    def _get_availability_zones_with_error_handling(
            region: str) -> Optional[pd.DataFrame]:
        try:
            azs = _get_availability_zones(region)
        except exceptions.AWSAzFetchingError as e:
            errored_region_reasons.append(
                (region, e.reason))  # GIL means it's thread-safe.
            return None
        return azs

    # Use ThreadPool instead of Pool because this function can be called within
    # a Pool, and Pool cannot be nested.
    with mp_pool.ThreadPool() as pool:
        az_mappings = pool.map(_get_availability_zones_with_error_handling,
                               regions)
    # Remove the regions that the user does not have access to.
    az_mappings = [m for m in az_mappings if m is not None]
    errored_regions = collections.defaultdict(set)
    for region, reason in errored_region_reasons:
        errored_regions[reason].add(region)
    if errored_regions:
        # This could happen if (1) an AWS API glitch happens, (2) permission
        # error happens for specific availability zones. We print those zones to
        # make sure that those zone does not get lost silently.
        table = log_utils.create_table(['Regions', 'Reason'])
        for reason, region_set in errored_regions.items():
            reason_str = '\n'.join(textwrap.wrap(str(reason.message), 80))
            region_str = '\n'.join(
                textwrap.wrap(', '.join(region_set), 60,
                              break_on_hyphens=False))
            table.add_row([region_str, reason_str])
        if not az_mappings:
            raise RuntimeError('Failed to fetch availability zone mappings for '
                               f'all enabled regions.\n{table}')
        else:
            print('\rAWS: [WARNING] Missing availability zone mappings for the '
                  f'following enabled regions:\n{table}')
    az_mappings = pd.concat(az_mappings)
    return az_mappings


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--az-mappings',
        dest='az_mappings',
        action='store_true',
        help='Fetch the mapping from availability zone IDs to zone names.')
    parser.add_argument('--no-az-mappings',
                        dest='az_mappings',
                        action='store_false')
    parser.add_argument(
        '--check-all-regions-enabled-for-account',
        action='store_true',
        help=('Check that this account has enabled "all" global regions '
              'hardcoded in this script. Useful to ensure our automatic '
              'fetcher fetches the expected data.'))
    parser.set_defaults(az_mappings=True)
    args, _ = parser.parse_known_args()

    user_regions = get_enabled_regions()
    if args.check_all_regions_enabled_for_account and set(
            ALL_REGIONS) - user_regions:
        raise RuntimeError('The following regions are not enabled: '
                           f'{set(ALL_REGIONS) - user_regions}')

    def _check_regions_integrity(df: 'pd.DataFrame', name: str):
        # Check whether the fetched regions match the requested regions to
        # guard against network issues or glitches in the AWS API.
        fetched_regions = set(df['Region'].unique())
        if fetched_regions != user_regions:
            # This is a sanity check to make sure that the regions we
            # requested are the same as the ones we fetched.
            # The mismatch could happen for network issues or glitches
            # in the AWS API.
            diff = user_regions - fetched_regions
            raise RuntimeError(
                f'{name}: Fetched regions {fetched_regions} does not match '
                f'requested regions {user_regions}; Diff: {diff}')

    instance_df = get_all_regions_instance_types_df(user_regions)
    _check_regions_integrity(instance_df, 'instance types')

    os.makedirs('aws', exist_ok=True)
    instance_df.to_csv('aws/vms.csv', index=False)
    print('AWS Service Catalog saved to aws/vms.csv')

    # Disable refreshing images.csv as we are using skypilot custom AMIs
    # See sky/clouds/catalog/images/README.md for more details.
    # image_df = get_all_regions_images_df(user_regions)
    # _check_regions_integrity(image_df, 'images')

    # image_df.to_csv('aws/images.csv', index=False)
    # print('AWS Images saved to aws/images.csv')

    if args.az_mappings:
        az_mappings_df = fetch_availability_zone_mappings()
        az_mappings_df.to_csv('aws/az_mappings.csv', index=False)
        print('AWS Availability Zone mapping saved to aws/az_mappings.csv')
