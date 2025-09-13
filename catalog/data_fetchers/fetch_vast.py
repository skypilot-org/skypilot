"""A script that generates the Vast Cloud catalog. """

#
# Due to the design of the sdk, pylint has a false
# positive for the functions.
#
# pylint: disable=assignment-from-no-return
import collections
import csv
import json
import math
import os
import re
from typing import Any, Dict, List

from sky.adaptors import vast

_map = {
    'TeslaV100': 'V100',
    'TeslaT4': 'T4',
    'TeslaP100': 'P100',
    'QRTX6000': 'RTX6000',
    'QRTX8000': 'RTX8000'
}


def create_instance_type(obj: Dict[str, Any]) -> str:
    stubify = lambda x: re.sub(r'\s', '_', x)
    return '{}x-{}-{}-{}'.format(obj['num_gpus'], stubify(obj['gpu_name']),
                                 obj['cpu_cores'], obj['cpu_ram'])


def dot_get(d: dict, key: str) -> Any:
    for k in key.split('.'):
        d = d[k]
    return d


if __name__ == '__main__':
    seen = set()
    # InstanceList is the buffered list to emit to
    # the CSV
    csvList = []

    # InstanceType and gpuInfo are basically just stubs
    # so that the dictwriter is happy without weird
    # code.
    mapped_keys = (('gpu_name', 'InstanceType'), ('gpu_name',
                                                  'AcceleratorName'),
                   ('num_gpus', 'AcceleratorCount'), ('cpu_cores', 'vCPUs'),
                   ('cpu_ram', 'MemoryGiB'), ('gpu_name', 'GpuInfo'),
                   ('search.totalHour', 'Price'), ('min_bid', 'SpotPrice'),
                   ('geolocation', 'Region'))

    # Vast has a wide variety of machines, some of
    # which will have less diskspace and network
    # bandwidth than others.
    #
    # The machine normally have high specificity
    # in the vast catalog - this is fairly unique
    # to Vast and can make bucketing them into
    # instance types difficult.
    #
    # The flags
    #
    #   * georegion consolidates geographic areas
    #
    #   * chunked rounds down specifications (such
    #     as 1025GB to 1024GB disk) in order to
    #     make machine specifications look more
    #     consistent
    #
    #   * inet_down makes sure that only machines
    #     with "reasonable" downlink speed are
    #     considered
    #
    #   * disk_space sets a lower limit of how
    #     much space is availble to be allocated
    #     in order to ensure that machines with
    #     small disk pools aren't listed
    #
    offerList = vast.vast().search_offers(
        query=('georegion = true chunked = true '
               'inet_down >= 100 disk_space >= 80'),
        limit=10000)

    priceMap: Dict[str, List] = collections.defaultdict(list)
    for offer in offerList:
        entry = {}
        for ours, theirs in mapped_keys:
            field = dot_get(offer, ours)
            entry[theirs] = field

        instance_type = create_instance_type(offer)
        entry['InstanceType'] = instance_type

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

        priceMap[instance_type].append(entry)

    for instanceList in priceMap.values():
        priceList = sorted([x['Price'] for x in instanceList])
        index = math.ceil(0.5 * len(priceList)) - 1
        priceTarget = priceList[index]
        toList: List = []
        for instance in instanceList:
            if instance['Price'] <= priceTarget:
                instance['Price'] = '{:.2f}'.format(priceTarget)
                toList.append(instance)

        maxBid = max([x.get('SpotPrice') for x in toList])
        for instance in toList:
            stub = f'{instance["InstanceType"]} {instance["Region"][-2:]}'
            if stub in seen:
                printstub = f'{stub}#print'
                if printstub not in seen:
                    instance['SpotPrice'] = f'{maxBid:.2f}'
                    csvList.append(instance)
                    seen.add(printstub)
            else:
                seen.add(stub)

    os.makedirs('vast', exist_ok=True)
    with open('vast/vms.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[x[1] for x in mapped_keys])
        writer.writeheader()

        for instance in csvList:
            writer.writerow(instance)
