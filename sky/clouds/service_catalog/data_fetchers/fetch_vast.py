"""A script that generates the Vast Cloud catalog. """

# pylint: disable=assignment-from-no-return
import csv
import json
import re
import sys
from typing import Any, Dict

from vastai_sdk import VastAI

_map = {
    'TeslaV100': 'V100',
    'TeslaT4': 'T4',
    'TeslaP100': 'P100',
    'QRTX6000': 'RTX6000',
    'QRTX8000': 'RTX8000'
}


def create_instance_type(obj: Dict[str, Any]) -> str:
    stubify = lambda x: re.sub(r'\s', '_', x)
    return '{}x-{}-{}'.format(obj['num_gpus'], stubify(obj['gpu_name']),
                              obj['cpu_cores'])


def dot_get(d: dict, key: str) -> Any:
    for k in key.split('.'):
        d = d[k]
    return d


if __name__ == '__main__':
    # InstanceType and gpuInfo are basically just stubs
    # so that the dictwriter is happy without weird
    # code.
    mapped_keys = (('gpu_name', 'InstanceType'), ('gpu_name',
                                                  'AcceleratorName'),
                   ('num_gpus', 'AcceleratorCount'), ('cpu_cores', 'vCPUs'),
                   ('gpu_total_ram', 'MemoryGiB'), ('gpu_name', 'GpuInfo'),
                   ('search.totalHour', 'Price'),
                   ('search.totalHour', 'SpotPrice'), ('geolocation', 'Region'))
    writer = csv.DictWriter(sys.stdout, fieldnames=[x[1] for x in mapped_keys])
    writer.writeheader()

    offerList = VastAI().search_offers(limit=10000)
    for offer in offerList:
        entry = {}
        for ours, theirs in mapped_keys:
            field = dot_get(offer, ours)
            if 'Price' in theirs:
                field = '{:.2f}'.format(field)
            entry[theirs] = field

        entry['InstanceType'] = create_instance_type(offer)

        # the documentation says
        # "{'gpus': [{
        #   'name': 'v100',
        #   'manufacturer': 'nvidia',
        #   'count': 8.0,
        #   'memoryinfo': {'sizeinmib': 16384}
        #   }],
        #   'totalgpumemoryinmib': 16384}",
        # we can do that.
        entry['MemoryGiB'] /= 1024

        gpu = re.sub('Ada', '-Ada', re.sub(r'\s', '', offer['gpu_name']))
        gpu = re.sub(r'(Ti|PCIE|SXM4|SXM|NVL)$', '', gpu)
        gpu = re.sub(r'(RTX\d0\d0)(S|D)$', r'\1', gpu)

        if gpu in _map:
            gpu = _map[gpu]

        entry['AcceleratorName'] = gpu
        entry['GpuInfo'] = json.dumps({
            'Gpus': [{
                'Name': gpu,
                'Count': offer['num_gpus'],
                'MemoryInfo': {
                    'SizeInMiB': offer['gpu_total_ram']
                }
            }],
            'TotalGpuMemoryInMiB': offer['gpu_total_ram']
        }).replace('"', '\'')

        writer.writerow(entry)
