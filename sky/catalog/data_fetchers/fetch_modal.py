"""Fetch Modal prices and write the Modal catalog (modal/vms.csv).

Modal has no pricing API. Prices come from https://modal.com/pricing and the
region multipliers from https://modal.com/docs/guide/region-selection. GPU
memory, GPU counts per container and region names are not on those pages, so
they are kept below; keep them in sync with https://modal.com/docs/guide/gpu
and the region list on the region-selection page.

Modal sizes CPU and memory per container, so SkyPilot builds Modal instance
types on the fly (e.g. `32CPU--128GB--H100:2`). Each row therefore carries the
per-hour rates used to price them, already scaled for the row's region:
CpuCoreHourlyPrice (per Modal core, which is 2 vCPUs), MemoryGiBHourlyPrice
and AcceleratorHourlyPrice (per GPU). CPU-only rows use Modal's
non-preemptible rates, since SkyPilot runs CPU-only clusters non-preemptible.
The rows themselves are fixed shapes with 4 vCPUs and 16 GiB, used when no
CPU or memory is requested.

Usage (from the catalog directory, e.g. ~/.sky/catalogs/v8):
    python -m sky.catalog.data_fetchers.fetch_modal
"""

import argparse
import csv
import html.parser
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

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

GPU_COUNTS = {
    'B300': (1, 2, 4, 8),
    'B200': (1, 2, 4, 8),
    'H200': (1, 2, 4, 8),
    'H100': (1, 2, 4, 8),
    'RTX-PRO-6000': (1,),
    'A100-80GB': (1, 2, 4, 8),
    'A100-40GB': (1, 2, 4, 8),
    'A100': (1, 2, 4, 8),
    'L40S': (1, 2, 4, 8),
    'A10': (1, 2, 4),
    'L4': (1, 2, 4, 8),
    'T4': (1, 2, 4, 8),
}

# Fixed shapes used when no CPU or memory is requested.
DEFAULT_VCPUS = 4
DEFAULT_MEMORY_GIB = 16
CPU_INSTANCE_TYPE = 'modal-cpu-4x-16gb'

COLUMNS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'Price',
    'SpotPrice',
    'Region',
    'GpuInfo',
    'CpuCoreHourlyPrice',
    'MemoryGiBHourlyPrice',
    'AcceleratorHourlyPrice',
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
                  'GPU_NAMES, GPU_MEMORY_GIB and GPU_COUNTS.')
            continue
        for name in GPU_NAMES[modal_name]:
            prices[name] = price
    missing = sorted(set(GPU_COUNTS) - set(prices))
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


def _gpu_info(gpu_name: str, gpu_count: int) -> str:
    gpu_memory_mib = GPU_MEMORY_GIB[gpu_name] * 1024
    return repr({
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': gpu_count,
            'MemoryInfo': {
                'SizeInMiB': gpu_memory_mib,
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory_mib * gpu_count,
    })


def create_catalog(prices: Dict[str, float],
                   region_multipliers: Dict[str, float]) -> List[Dict]:
    """Catalog rows (see the module docstring) from parsed prices."""
    rows = []
    cores = DEFAULT_VCPUS / 2

    def add_rows(instance_type: str, gpu: Optional[Tuple[str, int]],
                 cpu_rate: float, memory_rate: float, gpu_rate: float) -> None:
        for region, multiplier in region_multipliers.items():
            cpu_hourly = cpu_rate * 3600 * multiplier
            memory_hourly = memory_rate * 3600 * multiplier
            gpu_hourly = gpu_rate * 3600 * multiplier
            gpu_count = gpu[1] if gpu else 0
            rows.append({
                'InstanceType': instance_type,
                'AcceleratorName': gpu[0] if gpu else None,
                'AcceleratorCount': float(gpu_count) if gpu else None,
                'vCPUs': float(DEFAULT_VCPUS),
                'MemoryGiB': float(DEFAULT_MEMORY_GIB),
                'Price': round(
                    cores * cpu_hourly + DEFAULT_MEMORY_GIB * memory_hourly +
                    gpu_count * gpu_hourly, 8),
                'SpotPrice': None,
                'Region': region,
                'GpuInfo': _gpu_info(*gpu) if gpu else None,
                'CpuCoreHourlyPrice': round(cpu_hourly, 8),
                'MemoryGiBHourlyPrice': round(memory_hourly, 8),
                'AcceleratorHourlyPrice': round(gpu_hourly, 8) if gpu else None,
            })

    add_rows(CPU_INSTANCE_TYPE, None, prices['nonpreemptible_cpu'],
             prices['nonpreemptible_memory'], 0.0)
    for gpu_name, counts in GPU_COUNTS.items():
        for gpu_count in counts:
            add_rows(f'modal-{gpu_name.lower()}-{gpu_count}x',
                     (gpu_name, gpu_count), prices['cpu'], prices['memory'],
                     prices[gpu_name])
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
