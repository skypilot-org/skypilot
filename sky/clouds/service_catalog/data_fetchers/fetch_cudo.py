"""A script that generates the Cudo Compute catalog.

Usage:
    python fetch_cudo_cloud.py
"""

import json
import os

import cudo_compute

import sky.provision.cudo.cudo_utils as utils

VMS_CSV = 'cudo/vms.csv'


def cudo_api():
    configuration = cudo_compute.Configuration()
    configuration.host = 'https://rest.compute.cudo.org'
    client = cudo_compute.ApiClient(configuration)
    return cudo_compute.VirtualMachinesApi(client)


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


def machine_types(gpu_model, mem_gib, vcpu_count, gpu_count):
    try:
        api = cudo_api()
        types = api.list_vm_machine_types(mem_gib,
                                          vcpu_count,
                                          gpu=gpu_count,
                                          gpu_model=gpu_model)
        return types.to_dict()
    except cudo_compute.rest.ApiException as e:
        raise e


def update_prices():
    rows = []
    for spec in utils.machine_specs:
        mts = machine_types('', spec['mem'], spec['vcpu'], spec['gpu'])
        for hc in mts['host_configs']:
            if not utils.gpu_exists(hc['gpu_model']):
                continue
            accelerator_name = utils.cudo_gpu_to_skypilot_gpu(hc['gpu_model'])
            row = {
                'instance_type': get_instance_type(hc['machine_type'],
                                                   spec['vcpu'], spec['mem'],
                                                   spec['gpu']),
                'accelerator_name': accelerator_name,
                'accelerator_count': str(spec['gpu']) + '.0',
                'vcpus': str(spec['vcpu']),
                'memory_gib': str(spec['mem']),
                'price': hc['total_price_hr']['value'],
                'region': hc['data_center_id'],
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
