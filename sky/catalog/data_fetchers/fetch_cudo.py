"""A script that generates the Cudo Compute catalog.

Usage:
    python fetch_cudo_cloud.py
"""

import json
import os

import cudo_compute

from sky.provision.cudo import cudo_utils as utils

VMS_CSV = 'cudo/vms.csv'


def get_gpu_info(count, model):
    mem = utils.cudo_gpu_mem[model]
    # pylint: disable=line-too-long
    # {'Name': 'A4000', 'Manufacturer': 'NVIDIA', 'Count': 1.0, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 16384}"
    info = {
        'Gpus': [{
            'Name': model,
            'Manufacturer': 'NVIDIA',
            'Count': str(count) + '.0',
            'MemoryInfo': {
                'SizeInMiB': 1024 * mem
            }
        }],
        'TotalGpuMemoryInMiB': 1024 * mem * count
    }
    # pylint: disable=invalid-string-quote
    return '"' + json.dumps(info).replace('"', "'") + '"'


def get_instance_type(machine_type, vcpu, mem, gpu):
    return machine_type + '_' + str(gpu) + 'x' + str(vcpu) + 'v' + str(
        mem) + 'gb'


def update_prices():
    rows = []

    api = cudo_compute.cudo_api.virtual_machines()
    all_types = api.list_vm_machine_types2()
    all_machine_types = all_types.to_dict()['machine_types']

    for spec in utils.machine_specs:
        for machine_type in all_machine_types:
            if (machine_type['min_vcpu'] <= spec['vcpu'] and
                    machine_type['min_memory_gib'] <= spec['mem'] and
                    utils.gpu_exists(machine_type['gpu_model'])):

                accelerator_name = utils.cudo_gpu_to_skypilot_gpu(
                    machine_type['gpu_model'])

                # Calculate total price per hour based on the given spec
                vcpu_price = float(
                    machine_type['vcpu_price_hr']['value']) * spec['vcpu']
                memory_price = float(
                    machine_type['memory_gib_price_hr']['value']) * spec['mem']
                gpu_price = float(
                    machine_type['gpu_price_hr']['value']) * spec['gpu']
                # Note: Not including storage and IPv4 prices
                # for now as they may be optional
                total_price = vcpu_price + memory_price + gpu_price

                row = {
                    'instance_type': get_instance_type(
                        machine_type['machine_type'], spec['vcpu'], spec['mem'],
                        spec['gpu']),
                    'accelerator_name': accelerator_name,
                    'accelerator_count': str(spec['gpu']) + '.0',
                    'vcpus': str(spec['vcpu']),
                    'memory_gib': str(spec['mem']),
                    'price': str(total_price),
                    'region': machine_type['data_center_id'],
                    'gpu_info': get_gpu_info(spec['gpu'], accelerator_name),
                }
                rows.append(row)
    path = VMS_CSV
    with open(path, 'w', encoding='utf-8') as file:
        file.write(
            # pylint: disable=line-too-long
            'InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,Price,Region,GpuInfo,SpotPrice\n'
        )
        for row in rows:
            data = [
                row['instance_type'],
                row['accelerator_name'],
                row['accelerator_count'],
                row['vcpus'],
                row['memory_gib'],
                row['price'],
                row['region'],
                row['gpu_info'],
                '',
            ]
            file.write(','.join(data) + '\n')


if __name__ == '__main__':
    os.makedirs('cudo', exist_ok=True)
    update_prices()
    print('Cudo Compute catalog saved to cudo/vms.csv')
