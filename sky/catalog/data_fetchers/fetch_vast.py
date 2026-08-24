"""A script that generates the Vast Cloud catalog. """

#
# Due to the design of the sdk, pylint has a false
# positive for the functions.
#
# pylint: disable=assignment-from-no-return
import argparse
import collections
import csv
import json
import math
import os
import re
from typing import Any, Dict, List, Set, Tuple

from sky.adaptors import vast

_map = {
    'TeslaV100': 'V100',
    'TeslaT4': 'T4',
    'TeslaP100': 'P100',
    'QRTX6000': 'RTX6000',
    'QRTX8000': 'RTX8000'
}

_MAPPED_KEYS = (
    ('gpu_name', 'InstanceType'),
    ('gpu_name', 'AcceleratorName'),
    ('num_gpus', 'AcceleratorCount'),
    ('cpu_cores', 'vCPUs'),
    ('cpu_ram', 'MemoryGiB'),
    ('gpu_name', 'GpuInfo'),
    ('search.totalHour', 'Price'),
    ('min_bid', 'SpotPrice'),
    ('geolocation', 'Region'),
    ('hosting_type', 'HostingType'),
)


def create_instance_type(obj: Dict[str, Any]) -> str:
    stubify = lambda x: re.sub(r'\s', '_', x)
    return '{}x-{}-{}-{}'.format(obj['num_gpus'], stubify(obj['gpu_name']),
                                 obj['cpu_cores'], obj['cpu_ram'])


def dot_get(d: dict, key: str) -> Any:
    for k in key.split('.'):
        d = d[k]
    return d


def fetch_vast_catalog() -> List[Dict[str, Any]]:
    """Fetch and normalize the current Vast offers into catalog rows."""
    seen: Set[Tuple[str, str, str]] = set()
    # InstanceList is the buffered list to emit to
    # the CSV.
    csv_list = []

    # InstanceType and gpuInfo are basically just stubs
    # so that the dictwriter is happy without weird
    # code.
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
    offer_list = vast.vast().search_offers(
        query=('georegion = true chunked = true '
               'inet_down >= 100 disk_space >= 80'),
        limit=10000)

    price_map: Dict[str, List] = collections.defaultdict(list)
    for offer in offer_list:
        entry = {}
        for ours, theirs in _MAPPED_KEYS:
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

        price_map[instance_type].append(entry)

    for instance_list in price_map.values():
        price_list = sorted([x['Price'] for x in instance_list])
        index = math.ceil(0.5 * len(price_list)) - 1
        price_target = price_list[index]
        to_list: List = []
        for instance in instance_list:
            if instance['Price'] <= price_target:
                instance['Price'] = '{:.2f}'.format(price_target)
                to_list.append(instance)

        max_bid = max([x.get('SpotPrice') for x in to_list])
        for instance in to_list:
            hosting_type = instance.get('HostingType', 0)
            raw_region = instance['Region']
            try:
                country_code = vast.extract_country_code(raw_region)
            except ValueError:
                geographic_key = f'raw:{str(raw_region).strip().casefold()}'
            else:
                geographic_key = country_code or 'any'
            deduplication_key = (instance['InstanceType'], geographic_key,
                                 str(hosting_type))
            if deduplication_key in seen:
                continue
            instance['SpotPrice'] = f'{max_bid:.2f}'
            csv_list.append(instance)
            seen.add(deduplication_key)

    return csv_list


def save_catalog(instances: List[Dict[str, Any]], output_file: str) -> None:
    """Save previously fetched Vast catalog rows to a CSV file."""
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile,
                                fieldnames=[x[1] for x in _MAPPED_KEYS])
        writer.writeheader()

        for instance in instances:
            writer.writerow(instance)


def main() -> None:
    """Generate the Vast CSV used by hosted catalog publishing jobs."""
    parser = argparse.ArgumentParser(
        description='Update Vast catalog for SkyPilot')
    parser.add_argument('--output', default='vast/vms.csv')
    args = parser.parse_args()
    save_catalog(fetch_vast_catalog(), args.output)


if __name__ == '__main__':
    main()
