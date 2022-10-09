import copy
from typing import Tuple
import pandas as pd

from sky.clouds.service_catalog import common


def resource_diff(original_df: pd.DataFrame, new_df: pd.DataFrame, check_tuple: Tuple[str]) -> pd.DataFrame:
    """Returns the difference between two dataframes."""
    original_resources = original_df[check_tuple]
    new_resources = new_df[check_tuple]
    
    return new_resources.merge(original_resources, on=check_tuple, how='left', indicator=True)[lambda x: x['_merge'] == 'left_only'].sort_values(by=check_tuple)
    

CLOUD_CHECKS = {
    'aws': ['InstanceType', 'Region', 'AvailabilityZone'],
    'azure': ['InstanceType', 'Region'],
    'gcp': ['InstanceType', 'Region', 'AcceleratorName', 'AcceleratorCount']}



for cloud in CLOUD_CHECKS:
    print(f'=> Checking {cloud}')
    original_df = common.read_catalog(f'{cloud}.csv')
    new_df = pd.read_csv(f'{cloud}.csv')

    current_check_tuple = CLOUD_CHECKS[cloud]
    
    diff_df = resource_diff(original_df, new_df, current_check_tuple)
    diff_df.merge(new_df, on=current_check_tuple, how='left').to_csv(f'{cloud}_diff.csv', index=False)
    print(f'New resources in {cloud}: {len(diff_df)}')

    check_price = current_check_tuple + ['Price']
    diff_df = resource_diff(original_df, new_df, check_price)
    print(f'New prices in {cloud}: {len(diff_df)}')

    check_price = current_check_tuple + ['SpotPrice']
    diff_df = resource_diff(original_df, new_df, check_price)
    print(f'New spot prices in {cloud}: {len(diff_df)}')







