"""Fetch Modal prices and write the Modal catalog (modal/vms.csv).

Modal has no pricing API. Prices come from https://modal.com/pricing and the
region multipliers from https://modal.com/docs/guide/region-selection. GPU
memory, the maximum GPU count per container and region names are not on those
pages, so they are kept below; keep them in sync with
https://modal.com/docs/guide/gpu and the region list on the region-selection
page.

Modal sizes CPU and memory per container, so there are no instance types to
list. Like Kubernetes, SkyPilot builds them on the fly (e.g.
`32CPU--128GB--H100:2`) and prices them from this table. It uses the standard
catalog columns, GCP-style, with two kinds of rows per region, already scaled
for the region's price multiplier:
- accelerator rows (no `InstanceType`), one per GPU type and count Modal
  offers per container, as in gcp/vms.csv: `Price` is the hourly price of
  that many GPUs, `GpuInfo` carries the GPU memory;
- two rate rows, `InstanceType` `cpu-core` (`vCPUs` 2: one Modal physical
  core) and `memory-gib` (`MemoryGiB` 1): `Price` is the hourly
  non-preemptible rate, which CPU-only clusters pay since SkyPilot runs them
  non-preemptible, and `SpotPrice` the list rate, which is what Modal's
  (preemptible) GPU containers pay for their CPU and memory.

Usage (from the catalog directory, e.g. ~/.sky/catalogs/v8):
    python -m sky.catalog.data_fetchers.fetch_modal
"""

import argparse
import csv
import html.parser
import os
import re
import sys
from typing import Dict, List

import requests

PRICING_URL = 'https://modal.com/pricing'
REGION_SELECTION_URL = 'https://modal.com/docs/guide/region-selection'

AUTO_REGION = 'auto'
BROAD_REGIONS = ('us', 'eu', 'ap', 'uk', 'ca', 'me', 'sa', 'af', 'mx')
NARROW_REGIONS = (
    'us-east',
    'us-central',
    'us-south',
    'us-west',
    'eu-west',
    'eu-north',
    'eu-south',
    'ap-northeast',
    'ap-southeast',
    'ap-south',
    'ap-melbourne',
    'jp',
    'au',
)

# Name on modal.com/pricing (after "Nvidia ") -> SkyPilot accelerator names.
# Modal's `A100` GPU argument is the 40 GB card.
GPU_NAMES = {
    'B300': ('B300',),
    'B200': ('B200',),
    'H200 SXM': ('H200',),
    'H100 SXM5': ('H100',),
    'RTX PRO 6000': ('RTX-PRO-6000',),
    'A100, 80 GB': ('A100-80GB',),
    'A100, 40 GB': ('A100-40GB', 'A100'),
    'L40S': ('L40S',),
    'A10': ('A10',),
    'L4': ('L4',),
    'T4': ('T4',),
}

GPU_MEMORY_GIB = {
    'B300': 288,
    'B200': 180,
    'H200': 141,
    'H100': 80,
    'RTX-PRO-6000': 96,
    'A100-80GB': 80,
    'A100-40GB': 40,
    'A100': 40,
    'L40S': 48,
    'A10': 24,
    'L4': 24,
    'T4': 16,
}

# Most GPUs of a type per container; any count up to it may be requested.
# https://modal.com/docs/guide/gpu lists up to 8 for these types and up to 4
# for A10; RTX PRO 6000 is not in that list, so it stays at 1.
GPU_MAX_COUNTS = {
    'B300': 8,
    'B200': 8,
    'H200': 8,
    'H100': 8,
    'RTX-PRO-6000': 1,
    'A100-80GB': 8,
    'A100-40GB': 8,
    'A100': 8,
    'L40S': 8,
    'A10': 4,
    'L4': 8,
    'T4': 8,
}

# Keep in sync with modal_catalog.py.
CPU_RESOURCE = 'cpu-core'
MEMORY_RESOURCE = 'memory-gib'
# A Modal physical core is two vCPUs.
VCPUS_PER_CORE = 2

# The standard catalog columns, as in gcp/vms.csv.
COLUMNS = [
    'InstanceType',
    'vCPUs',
    'MemoryGiB',
    'AcceleratorName',
    'AcceleratorCount',
    'GpuInfo',
    'Region',
    'AvailabilityZone',
    'Price',
    'SpotPrice',
]

_PRICE = r'\$\s*([0-9]*\.?[0-9]+)'


class ModalPricingError(Exception):
    """A Modal page no longer has the expected layout."""


class _TextExtractor(html.parser.HTMLParser):
    """Visible page text, skipping scripts, styles and aria-hidden subtrees.

    modal.com draws each price twice: as screen-reader text and as animated,
    aria-hidden digits. Only the screen-reader copy is kept.
    """

    _VOID_TAGS = frozenset({
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'source', 'track', 'wbr'
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._VOID_TAGS:
            return
        if self._hidden_depth:
            self._hidden_depth += 1
        elif (tag in ('script', 'style') or
              dict(attrs).get('aria-hidden') == 'true'):
            self._hidden_depth = 1

    def handle_startendtag(self, tag, attrs):
        del tag, attrs  # Self-closing tags hold no text.

    def handle_endtag(self, tag):
        if tag not in self._VOID_TAGS and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth:
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r'\s+', ' ', ' '.join(self._parts)).strip()


def page_text(page_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(page_html)
    parser.close()
    return parser.text()


def _search(pattern: str, text: str, what: str) -> float:
    match = re.search(pattern, text)
    if match is None:
        raise ModalPricingError(f'Could not find the {what} on {PRICING_URL}.')
    return float(match.group(1))


def parse_pricing_page(page_html: str) -> Dict[str, float]:
    """Per-second prices from modal.com/pricing.

    Returns {SkyPilot GPU name: per-GPU price} plus the keys `cpu`, `memory`,
    `nonpreemptible_cpu` and `nonpreemptible_memory` (per core / per GiB).
    """
    text = page_text(page_html)
    start = text.find('GPU Tasks')
    if start < 0:
        raise ModalPricingError(f'No "GPU Tasks" section on {PRICING_URL}.')
    text = text[start:]

    prices: Dict[str, float] = {}
    gpu_section = text[:text.find(' CPU ')]
    for match in re.finditer(r'Nvidia (.+?) ' + _PRICE + r' / sec',
                             gpu_section):
        modal_name, price = match.group(1).strip(), float(match.group(2))
        if modal_name not in GPU_NAMES:
            print(f'Skipping unknown Modal GPU {modal_name!r}: add it to '
                  'GPU_NAMES, GPU_MEMORY_GIB and GPU_MAX_COUNTS.')
            continue
        for name in GPU_NAMES[modal_name]:
            prices[name] = price
    missing = sorted(set(GPU_MAX_COUNTS) - set(prices))
    if missing:
        raise ModalPricingError(
            f'No price for {", ".join(missing)} on {PRICING_URL}.')

    prices['cpu'] = _search(r'\bCPU ' + _PRICE + r' / core / sec', text,
                            'CPU price')
    prices['memory'] = _search(r'\bMemory ' + _PRICE + r' / GiB / sec', text,
                               'memory price')
    prices['nonpreemptible_cpu'] = _search(
        r'CPU \(non-preemptible\).*?' + _PRICE + r' / core / sec', text,
        'non-preemptible CPU price')
    prices['nonpreemptible_memory'] = _search(
        r'Memory \(non-preemptible\) ' + _PRICE + r' / GiB / sec', text,
        'non-preemptible memory price')
    return prices


def parse_region_multipliers(page_html: str) -> Dict[str, float]:
    """{region: price multiplier} from Modal's region-selection guide."""
    text = page_text(page_html)
    multipliers = {}
    for kind in ('Broad', 'Narrow'):
        match = re.search(kind + r' \(e\.g\. [\w-]+ \) ([0-9.]+)x', text)
        if match is None:
            raise ModalPricingError(f'No {kind.lower()} region multiplier on '
                                    f'{REGION_SELECTION_URL}.')
        multipliers[kind] = float(match.group(1))
    return {
        AUTO_REGION: 1.0,
        **{region: multipliers['Broad'] for region in BROAD_REGIONS},
        **{region: multipliers['Narrow'] for region in NARROW_REGIONS},
    }


def _hourly(per_second: float, multiplier: float) -> float:
    return round(per_second * 3600 * multiplier, 8)


def _rate_row(resource: str, region: str, multiplier: float, per_second: float,
              nonpreemptible_per_second: float, vcpus: int,
              memory_gib: int) -> Dict:
    """A `cpu-core` or `memory-gib` row: per-unit hourly rates."""
    return {
        'InstanceType': resource,
        'vCPUs': vcpus,
        'MemoryGiB': memory_gib,
        'AcceleratorName': None,
        'AcceleratorCount': None,
        'GpuInfo': None,
        'Region': region,
        'AvailabilityZone': None,
        'Price': _hourly(nonpreemptible_per_second, multiplier),
        'SpotPrice': _hourly(per_second, multiplier),
    }


def gpu_info(gpu_name: str, count: int) -> str:
    """The `GpuInfo` column: the standard dict, as a string."""
    memory_mib = GPU_MEMORY_GIB[gpu_name] * 1024
    return str({
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': count,
            'MemoryInfo': {
                'SizeInMiB': memory_mib
            },
        }],
        'TotalGpuMemoryInMiB': memory_mib * count,
    })


def _gpu_row(gpu_name: str, count: int, region: str, multiplier: float,
             per_second: float) -> Dict:
    """An accelerator row, as in gcp/vms.csv: no instance type, priced for
    `count` GPUs. Modal has no non-preemptible GPUs, so no SpotPrice."""
    return {
        'InstanceType': None,
        'vCPUs': None,
        'MemoryGiB': None,
        'AcceleratorName': gpu_name,
        'AcceleratorCount': count,
        'GpuInfo': gpu_info(gpu_name, count),
        'Region': region,
        'AvailabilityZone': None,
        'Price': _hourly(per_second * count, multiplier),
        'SpotPrice': None,
    }


def create_catalog(prices: Dict[str, float],
                   region_multipliers: Dict[str, float]) -> List[Dict]:
    """Catalog rows (see the module docstring) from parsed per-second prices."""
    rows = []
    for region, multiplier in region_multipliers.items():
        rows.append(
            _rate_row(CPU_RESOURCE,
                      region,
                      multiplier,
                      prices['cpu'],
                      prices['nonpreemptible_cpu'],
                      vcpus=VCPUS_PER_CORE,
                      memory_gib=0))
        rows.append(
            _rate_row(MEMORY_RESOURCE,
                      region,
                      multiplier,
                      prices['memory'],
                      prices['nonpreemptible_memory'],
                      vcpus=0,
                      memory_gib=1))
        for gpu_name, max_count in GPU_MAX_COUNTS.items():
            rows.extend(
                _gpu_row(gpu_name, count, region, multiplier, prices[gpu_name])
                for count in range(1, max_count + 1))
    return rows


def _get(url: str) -> str:
    response = requests.get(url,
                            headers={'User-Agent': 'SkyPilot catalog fetcher'},
                            timeout=60)
    response.raise_for_status()
    return response.text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',
                        default=os.path.join('modal', 'vms.csv'),
                        help='Where to write the catalog.')
    args = parser.parse_args()

    prices = parse_pricing_page(_get(PRICING_URL))
    multipliers = parse_region_multipliers(_get(REGION_SELECTION_URL))
    rows = create_catalog(prices, multipliers)

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f'Wrote {len(rows)} Modal catalog rows to {args.output}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
