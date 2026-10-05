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
from sky.provision.vast import utils as vast_utils

_map = {
    'TeslaV100': 'V100',
    'TeslaT4': 'T4',
    'TeslaP100': 'P100',
    'QRTX6000': 'RTX6000',
    'QRTX8000': 'RTX8000',
    # RTX PRO 6000 variants: after whitespace strip + suffix regex, these
    # still carry 'PRO' (the regex expects RTX\d0\d0, but the GPU name
    # is 'RTX PRO 6000 S' → 'RTXPRO6000S' which doesn't match). The
    # platform's accelerator token for the 96GB-class Blackwell part is
    # RTXPRO6000 (matching the Karpenter/EC2 naming for g4-standard-48+).
    'RTXPRO6000S': 'RTXPRO6000',
    'RTXPRO6000D': 'RTXPRO6000',
    'RTXPRO6000MaxQ': 'RTXPRO6000',
    'RTXPRO6000Max-Q': 'RTXPRO6000',
    'RTXPRO6000WS': 'RTXPRO6000',
    # 'RTX 6000D' is the China-market Blackwell RTX PRO 6000 SKU, the same
    # uRun rtx6000 class. Without this entry the suffix rule below strips
    # the D and files it under RTX6000 next to the 24GB Turing Quadro RTX
    # 6000 ('Q RTX 6000' -> QRTX6000 -> RTX6000), so no single accelerator
    # covered the Blackwell family (measured 2026-09-30: the only KVM offer
    # flipped from a 6000D to a PRO 6000 Max-Q within hours).
    'RTX6000D': 'RTXPRO6000',
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
                   ('geolocation', 'Region'), ('hosting_type', 'HostingType'))

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
    # THE CATALOG MUST SHOW ONLY WHAT THE LANE CAN RENT. The launch path
    # (sky/provision/vast/utils.py) refuses anything except Secure-Cloud
    # (datacenter + hosting_type >= 1) on-demand KVM VMs (vms_enabled;
    # `vm` is not a filterable key), so a catalog that lists container or
    # interruptible or community offers lets the QUOTE admit candidates
    # the LAUNCH must refuse — quotes and placement must agree (the
    # 2026-09-29 Australia container rows were exactly this mismatch).
    # no_default=True + SEARCH_BASE_TERMS: the SDK's implicit verified=true
    # default hides Secure-Cloud offers (see vast_utils.SEARCH_BASE_TERMS);
    # the catalog and the launch search must share one base filter.
    offerList = vast.vast().search_offers(
        query=(f'{vast_utils.SEARCH_BASE_TERMS} '
               'georegion = true chunked = true '
               'inet_down >= 100 disk_space >= 80 '
               'type = ondemand vms_enabled = true '
               'datacenter = true hosting_type >= 1'),
        limit=10000,
        no_default=True)

    # With georegion = true the API's geolocation is a full
    # "Country, CC, GEO" string. Region MUST be the trailing georegion
    # token (EU/NA/...) alone: the controller's residency filter
    # exact-matches Region against the budget policy's allowedRegions
    # tokens, and its launch path reads region[-2:] — a full-string
    # Region matched NEITHER (measured 2026-09-30: the live claim
    # refused "region Czechia, CZ, EU not in budget.allowedRegions
    # [NA, EU, ...]" against a policy that DID list EU).
    for offer in offerList:
        geo = str(offer.get('geolocation') or '').strip()
        if geo:
            offer['geolocation'] = geo.split(',')[-1].strip()

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
        if gpu not in _map:
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
            hosting_type = instance.get('HostingType', 0)
            stub = (f'{instance["InstanceType"]} '
                    f'{instance["Region"][-2:]} {hosting_type}')
            if stub in seen:
                # DUPLICATE stub: update the spot price to the max bid
                # across the group. Only the FIRST row for this stub is
                # in csvList; subsequent ones update spot price in place.
                printstub = f'{stub}#print'
                if printstub not in seen:
                    instance['SpotPrice'] = f'{maxBid:.2f}'
                    csvList.append(instance)
                    seen.add(printstub)
            else:
                # FIRST occurrence of this (InstanceType, Region,
                # HostingType) stub: EMIT the row. The original code
                # only added it to `seen` without appending — every
                # unique instance type was silently dropped, producing
                # a header-only CSV even with live offers. Vast
                # instance types encode CPU/RAM, so most marketplace
                # offers are unique; the two-offer dedup test exposed
                # this as the root cause of the empty catalog (the
                # second occurrence was needed to emit the first stub's
                # row).
                seen.add(stub)
                csvList.append(instance)

    os.makedirs('vast', exist_ok=True)
    with open('vast/vms.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[x[1] for x in mapped_keys])
        writer.writeheader()

        for instance in csvList:
            writer.writerow(instance)
