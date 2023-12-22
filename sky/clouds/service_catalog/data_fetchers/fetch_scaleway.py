"""A script that generates Scaleway catalog.

This script uses the Scaleway APIs to query the list and real-time prices
of the VM that got a GPU.
"""

import pandas as pd
import scaleway
from scaleway.instance.v1 import InstanceV1API


def fetch_pricing():
    res = []
    client = scaleway.Client.from_config_file_and_env()
    for zone in scaleway.ALL_ZONES:
        servers = (InstanceV1API(client).list_servers_types(
            zone=zone, per_page=100).servers)
        for key, server_type in servers.items():
            # We are not interested in nodes that do not have a GPU
            if not server_type.gpu:
                continue

            name_to_gpu_info = {
                'RENDER-S': {
                    'Gpus': [{
                        'Name': 'Tesla P100-PCIE-16GB',
                        'Manufacturer': 'NVIDIA',
                        'Count': 1,
                        'MemoryInfo': {
                            'SizeInMiB': 16384
                        }
                    }],
                    'TotalGpuMemoryInMiB': 16384
                },
                'GPU-3070-S': {
                    'Gpus': [{
                        'Name': 'NVIDIA GeForce RTX 3070',
                        'Manufacturer': 'NVIDIA',
                        'Count': 1,
                        'MemoryInfo': {
                            'SizeInMiB': 8192,
                        }
                    }],
                    'TotalGpuMemoryInMiB': 8192,
                },
                'H100-1-80G': {
                    'Gpus': [{
                        'Name': 'NVIDIA H100 PCIe',
                        'Manufacturer': 'NVIDIA',
                        'Count': 1,
                        'MemoryInfo': {
                            'SizeInMiB': 81559
                        }
                    }],
                    'TotalGpuMemoryInMiB': 81559,
                },
                'H100-2-80G': {
                    'Gpus': [{
                        'Name': 'NVIDIA H100 PCIe',
                        'Manufacturer': 'NVIDIA',
                        'Count': 2,
                        'MemoryInfo': {
                            'SizeInMiB': 81559
                        }
                    }, ],
                    'TotalGpuMemoryInMiB': 163118,
                },
            }

            name_to_accelerator_name = {
                'RENDER-S': 'P100',
                'GPU-3070-S': 'RTX 3070',
                'H100-1-80G': 'H100',
                'H100-2-80G': 'H100',
            }

            zone_to_region = {'fr-par-1': 'fr-par', 'fr-par-2': 'fr-par'}

            res.append({
                'AvailabilityZone': zone,
                'InstanceType': key,
                'vCPUs': server_type.ncpus,
                'MemoryGiB': server_type.ram // (1024 ** 3),
                'AcceleratorName': name_to_accelerator_name.get(key, 'Unknown'),
                'AcceleratorCount': server_type.gpu,
                'GPUInfo': name_to_gpu_info.get(key, 'Unknown'),
                'Region': zone_to_region[zone],
                'Price': server_type.hourly_price,
                'SpotPrice': None
            })
    df = pd.DataFrame(res)
    df.to_csv('scaleway/vms.csv', index=False)


if __name__ == '__main__':
    fetch_pricing()
