"""Cudo Compute instance search helper for SkyPilot."""

import csv
import json

from sky.clouds.service_catalog.common import get_catalog_path
import sky.provision.cudo.cudo_wrapper as cudo_wrapper

VMS_CSV = 'cudo/vms.csv'

cudo_gpu_model = {
    'NVIDIA V100': 'V100',
    'NVIDIA A40': 'A40',
    'RTX 3080': '3080',
    'RTX A4000': 'A4000',
    'RTX A4500': 'A4500',
    'RTX A5000': 'A5000',
    'RTX A6000': 'A6000',
}

cudo_gpu_mem = {
    '3080': 12,
    'A40': 48,
    'A4000': 16,
    'A4500': 20,
    'A5000': 24,
    'A6000': 48,
    'V100': 16,
}

machine_specs = [
    # Low
    {
        'vcpu': 2,
        'mem': 4,
        'gpu': 1,
    },
    {
        'vcpu': 4,
        'mem': 8,
        'gpu': 1,
    },
    {
        'vcpu': 8,
        'mem': 16,
        'gpu': 2,
    },
    {
        'vcpu': 16,
        'mem': 32,
        'gpu': 2,
    },
    {
        'vcpu': 32,
        'mem': 64,
        'gpu': 4,
    },
    {
        'vcpu': 64,
        'mem': 128,
        'gpu': 8,
    },
    # Mid
    {
        'vcpu': 96,
        'mem': 192,
        'gpu': 8
    },
    {
        'vcpu': 48,
        'mem': 96,
        'gpu': 4
    },
    {
        'vcpu': 24,
        'mem': 48,
        'gpu': 2
    },
    {
        'vcpu': 12,
        'mem': 24,
        'gpu': 1
    },
    # Hi
    {
        'vcpu': 96,
        'mem': 192,
        'gpu': 4
    },
    {
        'vcpu': 48,
        'mem': 96,
        'gpu': 2
    },
    {
        'vcpu': 24,
        'mem': 48,
        'gpu': 1
    },
]


def get_spec_from_instance(instance_type, data_center_id):
    path = get_catalog_path(VMS_CSV)
    spec = []
    with open(path, mode='r') as file:
        csv_reader = csv.reader(file)
        for row in csv_reader:
            if row and row[0] == instance_type and row[6] == data_center_id:
                spec = row
                break
    return {
        'gpu_model': spec[1],
        'vcpu_count': spec[3],
        'mem_gb': spec[4],
        'gpu_count': spec[2],
        'machine_type': spec[0].split('_')[0]
    }


def cudo_gpu_to_skypilot_gpu(model):
    if model in cudo_gpu_model:
        return cudo_gpu_model[model]
    else:
        return model.replace('RTX ', '')


def skypilot_gpu_to_cudo_gpu(model):
    for key, value in cudo_gpu_model.items():
        if value == model:
            return key
    return model


def gpu_exists(model):
    if model in cudo_gpu_model:
        return True
    return False


def get_gpu_info(count, model):
    mem = cudo_gpu_mem[model]
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
    gpu_types = cudo_wrapper.gpu_types()
    for gpu in gpu_types:
        for spec in machine_specs:
            if not gpu_exists(gpu):
                break
            accelerator_name = cudo_gpu_to_skypilot_gpu(gpu)

            mts = cudo_wrapper.machine_types(gpu, spec['mem'], spec['vcpu'],
                                             spec['gpu'])
            for hc in mts['host_configs']:
                row = {
                    'instance_type': get_instance_type(hc['machine_type'],
                                                       spec['gpu'],
                                                       spec['vcpu'],
                                                       spec['mem']),
                    'accelerator_name': accelerator_name,
                    'accelerator_count': str(spec['gpu']) + '.0',
                    'vcpus': str(spec['vcpu']),
                    'memory_gib': str(spec['mem']),
                    'price': hc['total_price_hr']['value'],
                    'region': hc['data_center_id'],
                    'gpu_info': get_gpu_info(spec['gpu'], accelerator_name),
                    'spot_price': hc['total_price_hr']['value'],
                }
                rows.append(row)
    path = get_catalog_path(VMS_CSV)
    with open(path, 'w') as file:  # I assume the path is made on install
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
                row['spot_price'],
            ]
            file.write(','.join(data) + '\n')
