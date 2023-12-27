"""A script that queries RunPod API to get instance types and pricing information."""
import csv
import json
import os
from typing import Any, Callable, Dict, List, Optional, Union

import runpod

SECURE_CLOUD_REGIONS = [
    'CA',
    'CZ',
    'NL',
    'RO',
    'SE',
    'IS',
    'NO',
    'US'
]

COMMUNITY_CLOUD_REGIONS = [
    'AR',
    'AT',
    'BG',
    'CA',
    'CZ',
    'ES',
    'FR',
    'GB',
    'HR',
    'IL',
    'NL',
    'PT',
    'RO',
    'SE',
    'SK',
    'US',
    'ZA'
]

ID_TO_VCPU_NUM = {
    'NVIDIA A100 80GB PCIe': 8,
    'NVIDIA A100-SXM4-80GB': 16,
    'NVIDIA A30': 8,
    'NVIDIA A40': 9,
    'NVIDIA GeForce RTX 3070': 4,
    'NVIDIA GeForce RTX 3080': 7,
    'NVIDIA GeForce RTX 3080 Ti': 14,
    'NVIDIA GeForce RTX 3090': 16,
    'NVIDIA GeForce RTX 3090 Ti': 7,
    'NVIDIA GeForce RTX 4070 Ti': 12,
    'NVIDIA GeForce RTX 4080': 16,
    'NVIDIA GeForce RTX 4090': 16,
    'NVIDIA H100 80GB HBM3': 26, # The displayed name is H100 80GB SXM5
    'NVIDIA H100 PCIe': 16,
    'NVIDIA L4': 4, # Need update
    'NVIDIA L40': 16,
    'NVIDIA RTX 4000 Ada Generation': 9,
    'NVIDIA RTX 4000 SFF Ada Generation': 7,
    'NVIDIA RTX 5000 Ada Generation': 16,
    'NVIDIA RTX 6000 Ada Generation': 14,
    'NVIDIA RTX A2000': 9,
    'NVIDIA RTX A4000': 6,
    'NVIDIA RTX A4500': 12,
    'NVIDIA RTX A5000': 6,
    'NVIDIA RTX A6000': 4,
    'Tesla V100-FHHL-16GB': 6,
    'Tesla V100-PCIE-16GB': 7,
    'Tesla V100-SXM2-16GB': 10,
    'Tesla V100-SXM2-32GB': 20
}


def get_instance_entry(gpu:Dict[str,Any],
                       size: int, region: str,
                       id_to_name: Dict[str,str],
                       cloud_type: str) -> Dict[str, Any]:
    price_key = 'securePrice' if cloud_type == 'SECURE' else 'communityPrice'
    spot_price_key = ('secureSpotPrice' 
                      if cloud_type == 'SECURE' else 'communitySpotPrice')

    instance_entry = {
        'InstanceType': f'{size}x_{id_to_name[gpu["id"]]}_{cloud_type}',
        'vCPUs': float(size * ID_TO_VCPU_NUM[gpu['id']]),
        'MemoryGiB': float(size * gpu['memoryInGb']),
        'AcceleratorName': id_to_name[gpu['id']],
        'AcceleratorCount': float(size),
        'GpuInfo': '',
        'Region': region,
        'Price': float(size * gpu[price_key]),
        'SpotPrice': (float(size * gpu[spot_price_key])
                      if gpu[spot_price_key] else None)
    }
    return instance_entry

def get_instance_types() -> List[Dict[str,Any]]:
    # Obtain the list of GPUs
    gpus = runpod.get_gpus()

    # Contains a mapping between internally used instance id and the name
    # displayed on the frontend for every instance types available at RunPod
    id_to_name = {gpu['id'] : gpu['displayName'].replace(' ', '-')
                  for gpu in gpus}

    # Availabe instance types differ between Secure Cloud and Community Cloud
    community_cloud_gpu_ids = []
    secure_cloud_gpu_ids = []
    for gpu in gpus:
        gpu = runpod.get_gpu(gpu['id'], 1)
        if gpu['communityCloud']:
            community_cloud_gpu_ids.append(gpu['id'])
        if gpu['secureCloud']:
            secure_cloud_gpu_ids.append(gpu['id'])

    instance_data = []
    for gpu_id in id_to_name.keys():
        for size in [1, 2, 4, 8]:
            gpu = runpod.get_gpu(gpu_id, size)
            if gpu['secureCloud']:
                for region in SECURE_CLOUD_REGIONS:
                    secure_cloud_entry = get_instance_entry(gpu, size,
                                                            region,
                                                            id_to_name,
                                                            'SECURE')
                    instance_data.append(secure_cloud_entry)
            if gpu['communityCloud']:
                for region in COMMUNITY_CLOUD_REGIONS:
                    community_cloud_entry = get_instance_entry(gpu, size,
                                                               region,
                                                               id_to_name,
                                                               'COMMUNITY')
                    instance_data.append(community_cloud_entry)

    sort_columns = ['AcceleratorName', 'AcceleratorCount', 'InstanceType',
                    'Region']
    sorted_instance_data = sorted(instance_data,
                                  key=lambda x: tuple(x[col]
                                                      for col in sort_columns))
    return sorted_instance_data

def create_catalog(output_path: str) -> None:
    instance_data = get_instance_types()
    with open(output_path, 'w', encoding='UTF-8') as file:
        writer = csv.DictWriter(file, fieldnames=instance_data[0].keys())
        writer.writeheader()
        writer.writerows(instance_data)    

if __name__ == '__main__':
    os.makedirs('runpod', exist_ok=True)
    create_catalog('runpod/vms.csv')
    print('RunPod catalog saved to runpod/vms.csv')
