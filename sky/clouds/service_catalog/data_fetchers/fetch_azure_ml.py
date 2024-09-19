"""A script to fetch Azure ML pricing data.

Requires running fetch_azure.py first to get the pricing data.
"""
from multiprocessing import pool as mp_pool
import os
import typing
from typing import Dict, Set

from azure.ai import ml
from azure.ai.ml import entities

from sky.adaptors import azure
from sky.adaptors import common as adaptors_common
from sky.clouds.service_catalog import common
from sky.clouds.service_catalog.data_fetchers import fetch_azure

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

SUBSCRIPTION_ID = azure.get_subscription_id()

SINGLE_THREADED = False

az_df = common.read_catalog('azure/vms.csv')


def init_ml_client(region: str) -> ml.MLClient:
    resource_client = azure.get_client('resource', SUBSCRIPTION_ID)
    resource_group_name = f'az-ml-fetcher-{region}'
    workspace_name = f'az-ml-fetcher-{region}-ws'
    resource_client.resource_groups.create_or_update(resource_group_name,
                                                     {'location': region})
    ml_client: ml.MLClient = azure.get_client(
        'ml',
        SUBSCRIPTION_ID,
        resource_group=resource_group_name,
        workspace_name=workspace_name)
    try:
        ml_client.workspaces.get(workspace_name)
    except azure.exceptions().ResourceNotFoundError:
        print(f'Creating workspace {workspace_name} in {region}')
        ws = ml_client.workspaces.begin_create(
            entities.Workspace(name=workspace_name, location=region)).result()
        print(f'Created workspace {ws.name} in {ws.location}.')
    return ml_client


def get_supported_instance_type(region: str) -> Dict[str, bool]:
    ml_client = init_ml_client(region)
    supported_instance_types = {}
    for sz in ml_client.compute.list_sizes():
        if sz.supported_compute_types is None:
            continue
        if 'ComputeInstance' not in sz.supported_compute_types:
            continue
        supported_instance_types[sz.name] = sz.low_priority_capable
    return supported_instance_types


def get_instance_type_df(region: str) -> 'pd.DataFrame':
    supported_instance_type = get_supported_instance_type(region)
    df_filtered = az_df[az_df['Region'] == region].copy()
    df_filtered = df_filtered[df_filtered['InstanceType'].isin(
        supported_instance_type.keys())]

    def _get_spot_price(row):
        ins_type = row['InstanceType']
        assert ins_type in supported_instance_type, (
            f'Instance type {ins_type} not in supported_instance_type')
        if supported_instance_type[ins_type]:
            return row['SpotPrice']
        return None

    df_filtered['SpotPrice'] = df_filtered.apply(_get_spot_price, axis=1)

    supported_set = set(supported_instance_type.keys())
    df_set = set(az_df[az_df['Region'] == region]['InstanceType'])
    missing_instance_types = supported_set - df_set
    missing_str = ', '.join(missing_instance_types)
    if missing_instance_types:
        print(f'Missing instance types for {region}: {missing_str}')
    else:
        print(f'All supported instance types for {region} are in the catalog.')

    return df_filtered


def get_all_regions_instance_types_df(region_set: Set[str]) -> 'pd.DataFrame':
    if SINGLE_THREADED:
        dfs = [get_instance_type_df(region) for region in region_set]
    else:
        with mp_pool.Pool() as pool:
            dfs_result = pool.map_async(get_instance_type_df, region_set)
            dfs = dfs_result.get()
    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values(by='InstanceType').reset_index(drop=True)
    return df


if __name__ == '__main__':
    az_ml_parser = fetch_azure.get_arg_parser('Fetch Azure ML pricing data.')
    # TODO(tian): Support cleanup after fetching the data.
    az_ml_parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Cleanup the resource group and workspace after '
        'fetching the data.')
    args = az_ml_parser.parse_args()

    SINGLE_THREADED = args.single_threaded

    instance_df = get_all_regions_instance_types_df(
        fetch_azure.get_region_filter(args.all_regions, args.regions,
                                      args.exclude))
    os.makedirs('azure', exist_ok=True)
    instance_df.to_csv('azure/az_ml_vms.csv', index=False)
    print('Azure ML Service Catalog saved to azure/az_ml_vms.csv')
