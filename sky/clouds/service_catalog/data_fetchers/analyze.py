"""Analyze the new catalog fetched with the original."""

from typing import List

import pandas as pd

from sky.clouds.service_catalog import common


def resource_diff(original_df: pd.DataFrame, new_df: pd.DataFrame,
                  check_tuple: List[str]) -> pd.DataFrame:
    """Returns the difference between two dataframes."""
    original_resources = original_df[check_tuple]
    new_resources = new_df[check_tuple]

    return new_resources.merge(
        original_resources, on=check_tuple, how='left',
        indicator=True)[lambda x: x['_merge'] == 'left_only'].sort_values(
            by=check_tuple)


CLOUD_CHECKS = {
    'aws': ['InstanceType', 'Region', 'AvailabilityZone'],
    'azure': ['InstanceType', 'Region'],
    'gcp': ['InstanceType', 'Region', 'AcceleratorName', 'AcceleratorCount']
}

table = {}

for cloud, current_check_tuple in CLOUD_CHECKS.items():
    result = {}
    print(f'=> Checking {cloud}')
    original_catalog_df = common.read_catalog(f'{cloud}.csv')
    new_catalog_df = pd.read_csv(f'{cloud}.csv')

    resource_diff_df = resource_diff(original_catalog_df, new_catalog_df,
                                     current_check_tuple)
    resource_diff_df.merge(new_catalog_df, on=current_check_tuple,
                           how='left').to_csv(f'{cloud}_diff.csv', index=False)

    result['#resources'] = len(resource_diff_df)

    check_price = current_check_tuple + ['Price']
    price_diff_df = resource_diff(original_catalog_df, new_catalog_df,
                                  check_price)
    result['#prices'] = len(price_diff_df)

    check_price = current_check_tuple + ['SpotPrice']
    spot_price_diff_df = resource_diff(original_catalog_df, new_catalog_df,
                                       check_price)
    result['#spot_prices'] = len(spot_price_diff_df)

    table[cloud] = result

summary = pd.DataFrame(table).T
summary.to_csv('diff_summary.csv')
print(summary)
