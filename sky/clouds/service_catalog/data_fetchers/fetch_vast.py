#!/usr/bin/env python
import json, re
from vastai_sdk import VastAI
import csv, sys

def create_instance_type(obj):
    stubify = lambda x: re.sub(r'\s', '_', x)
    return "{}x-{}-{}".format(obj['num_gpus'], stubify(obj['gpu_name']), obj['cpu_cores'])

def dot_get(d, key):
    for k in key.split("."):
       d = d[k]
    return d

# InstanceType and gpuInfo are basically just stubs
# so that the dictwriter is happy without weird
# code.
mapped_keys = (
    ('gpu_name', 'InstanceType'), 
    ('gpu_name', 'AcceleratorName'),
    ('num_gpus', 'AcceleratorCount'),
    ('cpu_cores', 'vCPUs'),
    ('gpu_total_ram', 'MemoryGiB'),
    ('search.totalHour', 'Price'),
    ('geolocation', 'Region'),
    ('gpu_name', 'GpuInfo'),
    ('search.totalHour', 'SpotPrice')
)
writer = csv.DictWriter(sys.stdout, fieldnames=[x[1] for x in mapped_keys])
writer.writeheader()

offerList = VastAI().search_offers(limit=10000)
for offer in offerList:
    entry = {}
    for ours, theirs in mapped_keys:
        field = dot_get(offer, ours)
        if 'Price' in theirs:
            field = "{:.2f}".format(field)
        entry[theirs] = field

    entry['InstanceType'] = create_instance_type(offer)

    # the documentation says
    # "{'gpus': [{'name': 'v100', 'manufacturer': 'nvidia', 'count': 8.0, 'memoryinfo': {'sizeinmib': 16384}}], 'totalgpumemoryinmib': 16384}",
    # we can do that.
    entry['MemoryGiB'] /= 1024
    entry['GpuInfo'] = json.dumps({'Gpus': [{'Name': offer['gpu_name'], 'Count': offer['num_gpus'], 'MemoryInfo': {'SizeInMiB': offer['gpu_total_ram']}}], 'TotalGpuMemoryInMiB': offer['gpu_total_ram']}).replace('"', "'")

    writer.writerow(entry)

