"""A script that generates the IBM Cloud catalog.

Usage:
    python fetch_ibm.py [-h] [--api-key API_KEY]
                             [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.ibm/credentials.yaml` to look for IBM API key (`iam_api_key`).
"""
import argparse
import csv
from datetime import datetime
import json
import os
from typing import Dict, List, Optional, Tuple

import requests
import yaml

TOKEN_ENDPOINT = 'https://iam.cloud.ibm.com/identity/token'
REGIONS_ENDPOINT = f'https://us-south.iaas.cloud.ibm.com/v1/regions?version={datetime.today().strftime("%Y-%m-%d")}&generation=2'  # pylint: disable=line-too-long
DEFAULT_IBM_CREDENTIALS_PATH = os.path.expanduser('~/.ibm/credentials.yaml')


def _fetch_token(api_key: Optional[str] = None,
                 api_key_path: Optional[str] = None,
                 ibm_token: Optional[str] = None) -> str:
    if ibm_token is None:
        if api_key is None:
            if api_key_path is None:
                api_key_path = DEFAULT_IBM_CREDENTIALS_PATH
            with open(api_key_path, mode='r', encoding='utf-8') as f:
                ibm_cred_yaml = yaml.safe_load(f)
                api_key = ibm_cred_yaml['iam_api_key']

        headers = {
            'Accept': 'application/json',
        }
        data = {
            'grant_type': 'urn:ibm:params:oauth:grant-type:apikey',
            'apikey': api_key,
        }
        response = requests.post(url=TOKEN_ENDPOINT, data=data, headers=headers)
        return response.json()['access_token']
    else:
        return ibm_token


def _fetch_regions(ibm_token: str) -> List[str]:
    headers = {
        'Authorization': f'Bearer {ibm_token}',
        'Accept': 'application/json',
    }
    response = requests.get(url=REGIONS_ENDPOINT, headers=headers)
    regions_json = response.json()

    regions = [r['name'] for r in regions_json['regions']]

    print(f'regions: {regions}')
    return regions


def _fetch_instance_profiles(regions: List[str],
                             ibm_token: str) -> Dict[str, Tuple[List, List]]:
    """Fetch instance profiles by region (map):
    {
        "region_name": (
            [list of available zones in the region],
            [list of available instance profiles in the region]
        )
    }
    """
    d = datetime.today().strftime('%Y-%m-%d')

    result = {}
    headers = {
        'Authorization': f'Bearer {ibm_token}',
        'Accept': 'application/json',
    }
    for r in regions:
        az_endpoint = f'https://{r}.iaas.cloud.ibm.com/v1/regions/{r}/zones?version={d}&generation=2'  # pylint: disable=line-too-long
        az_response = requests.get(url=az_endpoint, headers=headers)
        az_response_json = az_response.json()
        zones = [a['name'] for a in az_response_json['zones']]
        print(f'Fetching instance profiles for region {r}, zones {zones}')

        instances_endpoint = f'https://{r}.iaas.cloud.ibm.com/v1/instance/profiles?version={d}&generation=2'  # pylint: disable=line-too-long
        instance_response = requests.get(url=instances_endpoint,
                                         headers=headers)
        instance_response_json = instance_response.json()
        instance_profiles = instance_response_json['profiles']
        result[r] = (zones, instance_profiles)

    return result


def create_catalog(region_profile: Dict[str, Tuple[List, List]],
                   output_path: str) -> None:
    print(f'Create catalog file {output_path} based on the fetched profiles')
    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'GpuInfo', 'Price', 'SpotPrice', 'Region',
            'AvailabilityZone'
        ])

        for region, (zones, profiles) in region_profile.items():
            print(f'  adding region {region} instances')
            for profile in profiles:
                vm = profile['name']
                gpu: Optional[str] = None
                gpu_cnt: Optional[int] = None
                gpu_manufacturer: Optional[str] = None
                gpu_memory: Optional[int] = None
                if 'gpu_model' in profile:
                    gpu = profile['gpu_model']['values'][0]
                if 'gpu_count' in profile:
                    gpu_cnt = int(profile['gpu_count']['value'])
                if 'vcpu_count' in profile:
                    vcpus = int(profile['vcpu_count']['value'])
                if 'memory' in profile:
                    mem = int(profile['memory']['value'])
                if 'gpu_memory' in profile:
                    gpu_memory = int(profile['gpu_memory']['value'])
                if 'gpu_manufacturer' in profile:
                    gpu_manufacturer = profile['gpu_manufacturer']['values'][0]
                # TODO: How to fetch prices?
                #       The pricing API doesn't return prices for instance.profile. # pylint: disable=line-too-long
                #       https://cloud.ibm.com/docs/account?topic=account-getting-pricing-api # pylint: disable=line-too-long
                #       https://globalcatalog.cloud.ibm.com/api/v1?q=kind:instance.profile # pylint: disable=line-too-long
                #       https://globalcatalog.cloud.ibm.com/api/v1/gx2-16x128x1v100/plan # pylint: disable=line-too-long
                price = 0.0
                gpuinfo: Optional[str] = None
                gpu_memory_mb: Optional[int] = None
                gpu_memory_total_mb: Optional[int] = None
                if gpu_memory is not None:
                    gpu_memory_mb = gpu_memory * 1024
                    if gpu_cnt is not None:
                        gpu_memory_total_mb = gpu_memory_mb * gpu_cnt
                # gpuinfo: Optional[str] = None
                if gpu is not None:
                    gpuinfo_dict = {
                        'Gpus': [{
                            'Name': gpu,
                            'Manufacturer': gpu_manufacturer,
                            'Count': gpu_cnt,
                            'MemoryInfo': {
                                'SizeInMiB': gpu_memory_mb
                            },
                        }],
                        'TotalGpuMemoryInMiB': gpu_memory_total_mb
                    }
                    gpuinfo = json.dumps(gpuinfo_dict).replace('"', "'")  # pylint: disable=inconsistent-quotes,invalid-string-quote

                for zone in zones:
                    writer.writerow([
                        vm, gpu, gpu_cnt, vcpus, mem, gpuinfo, price, '',
                        region, zone
                    ])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='IBM API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing IBM Credentials.')
    args = parser.parse_args()
    os.makedirs('ibm', exist_ok=True)
    call_token = _fetch_token()
    call_regions = _fetch_regions(call_token)
    region_profiles_map = _fetch_instance_profiles(regions=call_regions,
                                                   ibm_token=call_token)
    create_catalog(region_profiles_map, 'ibm/vms.csv')
    print('IBM Cloud catalog saved to ibm/vms.csv')
