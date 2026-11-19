"""A script that generates the UpCloud catalog.

Usage:
    python fetch_upcloud.py [--api-token TOKEN]
        [--output PATH]

Without --api-token, the token is read from the UPCLOUD_TOKEN environment
variable. The catalog is written to
~/.sky/catalogs/v8/upcloud/vms.csv by default.
"""
import argparse
import csv
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from sky.provision.upcloud import utils as upcloud_utils

logger = logging.getLogger(__name__)

# SkyPilot canonical GPU names and VRAM (MiB).
UPCLOUD_GPU_NAME_TO_SKYPILOT = {
    'L40S': 'L40S',
    'L40s': 'L40S',
    'L4': 'L4',
    'H100': 'H100',
    'B200': 'B200',
    'NVIDIA B200': 'B200',
    'B300': 'B300',
    'NVIDIA B300 SXM6 AC': 'B300',
    'RTXPRO6000': 'RTXPRO6000',
    'RTX PRO 6000': 'RTXPRO6000',
    'NVIDIA RTX PRO 6000 Blackwell Server Edition': 'RTXPRO6000',
}

VRAM_MIB = {
    'L40S': 46068,
    'L4': 23034,
    'H100': 81920,
    'B200': 184320,  # 180 GB
    'B300': 294912,  # 288 GB
    'RTXPRO6000': 98304,  # 96 GB
}

CSV_HEADER = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'Price', 'Region', 'GpuInfo', 'SpotPrice'
]

# Zones that offer GPU plans. CPU plans are listed in every public zone.
GPU_ZONES = ['fi-hel2']


def _get(token: str,
         path: str,
         params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fetch an UpCloud API resource and return its JSON-shaped dict.

    Kept as a path-based helper so unit tests can mock catalog API responses.
    """
    client = upcloud_utils.UpCloudClient(token)
    return client.get(path, params=params)


def normalize_gpu_name(gpu_model: Optional[str]) -> str:
    """Map an UpCloud gpu_model string to a SkyPilot accelerator name."""
    if not gpu_model:
        return ''
    stripped = gpu_model.strip()
    if stripped in UPCLOUD_GPU_NAME_TO_SKYPILOT:
        return UPCLOUD_GPU_NAME_TO_SKYPILOT[stripped]
    upper = re.sub(r'[^A-Z0-9]', '', stripped.upper())
    for key, canonical in sorted(UPCLOUD_GPU_NAME_TO_SKYPILOT.items(),
                                 key=lambda kv: -len(kv[0])):
        compact_key = re.sub(r'[^A-Z0-9]', '', key.upper())
        if compact_key and compact_key in upper:
            return canonical
    logger.warning(f'GPU model "{gpu_model}" not in mapping, using as-is')
    return stripped


def get_gpu_info(gpu_count: int, gpu_name: str) -> str:
    """Generate GPU info JSON string compatible with SkyPilot."""
    if not gpu_name or gpu_count <= 0:
        return ''
    vram = VRAM_MIB.get(gpu_name, 0)
    gpu_info = {
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': 'NVIDIA',
            'Count': float(gpu_count),
            'MemoryInfo': {
                'SizeInMiB': vram
            },
        }],
        'TotalGpuMemoryInMiB': vram * gpu_count if vram else 0,
    }
    return json.dumps(gpu_info).replace('"', '\'')


def _list_plans(token: str) -> List[Dict[str, Any]]:
    data = _get(token, '/plan')
    plans = data.get('plans', {}).get('plan', [])
    if not isinstance(plans, list):
        return []
    return plans


def _list_zones(token: str) -> List[str]:
    """Return the ids of the public UpCloud zones."""
    zones = _get(token, '/zone').get('zones', {}).get('zone', [])
    return [
        str(zone['id'])
        for zone in zones
        if zone.get('id') and str(zone.get('public', 'yes')).lower() == 'yes'
    ]


def _price_map(token: str, zones: List[str]) -> Dict[str, Dict[str, float]]:
    """Return hourly USD prices by zone and plan for the given zones.

    The API reports prices in USD cents per hour.
    """
    # The API accepts `zone` multiple times; requests repeats list params.
    data = _get(token, '/price', params={'zone': zones})
    prices = data.get('prices', {})
    currency = str(prices.get('currency', '')).upper()
    if currency != 'USD':
        raise ValueError(
            f'UpCloud prices are in {currency or "an unknown currency"}, '
            'expected USD; refusing to build the catalog.')
    by_zone: Dict[str, Dict[str, float]] = {}
    for zone_entry in prices.get('zone', []):
        if not isinstance(zone_entry, dict):
            continue
        zone_name = zone_entry.get('name') or zone_entry.get('id')
        plan_prices: Dict[str, float] = {}
        for key, value in zone_entry.items():
            if not isinstance(value, dict) or 'price' not in value:
                continue
            if not key.startswith('server_plan_'):
                continue
            plan_name = key[len('server_plan_'):]
            try:
                plan_prices[plan_name] = round(float(value['price']) / 100, 6)
            except (TypeError, ValueError):
                continue
        if zone_name and plan_prices:
            by_zone[str(zone_name)] = plan_prices
    return by_zone


def _priced_zones(
        name: str, zones: List[str],
        price_by_zone: Dict[str, Dict[str, float]]) -> List[Tuple[str, float]]:
    return [(zone, price_by_zone[zone][name])
            for zone in zones
            if name in price_by_zone.get(zone, {})]


def fetch_upcloud_rows(token: str) -> List[List[Any]]:
    """Fetch plans, prices, and zones and return catalog CSV rows.

    Spot plans (GPU-SPOT-*) are folded into the SpotPrice column of the
    matching on-demand plan; the provisioner maps use_spot back to the spot
    plan name.
    """
    plans = _list_plans(token)
    zones = _list_zones(token)
    price_by_zone = _price_map(token, zones)
    logger.info(f'Fetched {len(plans)} plans, {len(zones)} zones')

    current = [
        plan for plan in plans
        if isinstance(plan, dict) and plan.get('name') and
        str(plan.get('current_offering', 'yes')).lower() != 'no'
    ]
    names = {str(plan['name']) for plan in current}
    for name in sorted(names):
        if (upcloud_utils.is_spot_plan(name) and
                upcloud_utils.on_demand_plan_name(name) not in names):
            logger.info(f'Skipping spot plan {name}: no on-demand counterpart')

    rows: List[List[Any]] = []
    for plan in current:
        name = str(plan['name'])
        if upcloud_utils.is_spot_plan(name):
            continue
        try:
            vcpus = int(plan.get('core_number') or 0)
            memory_gb = float(plan.get('memory_amount') or 0) / 1024.0
        except (TypeError, ValueError):
            continue
        try:
            gpu_count = int(plan.get('gpu_amount') or 0)
        except (TypeError, ValueError):
            gpu_count = 0
        gpu_name = normalize_gpu_name(
            plan.get('gpu_model')) if gpu_count else ''
        gpu_info = get_gpu_info(gpu_count, gpu_name) if gpu_count else ''

        spot_name = (upcloud_utils.spot_plan_name(name)
                     if name.upper().startswith(upcloud_utils.GPU_PLAN_PREFIX)
                     else None)
        has_spot = spot_name in names
        priced_zones = _priced_zones(name, GPU_ZONES if gpu_count else zones,
                                     price_by_zone)
        if not priced_zones:
            logger.info(f'Skipping plan {name}: no zone prices found')
            continue

        for zone, hourly_price in priced_zones:
            spot_price: Any = ''
            spot_units = (price_by_zone.get(zone, {}).get(spot_name)
                          if spot_name else None)
            if has_spot and spot_units is not None:
                spot_price = spot_units
            rows.append([
                name,
                gpu_name,
                gpu_count if gpu_count else '',
                vcpus,
                memory_gb,
                hourly_price,
                zone,
                gpu_info,
                spot_price,
            ])
    # Sorted, so the scheduled catalog update only commits real changes.
    rows.sort(key=lambda row: (row[0], row[6]))
    return rows


def create_catalog(token: str, output_path: str) -> None:
    """Write the UpCloud catalog CSV from the UpCloud API."""
    rows = fetch_upcloud_rows(token)
    if not rows:
        raise RuntimeError('No UpCloud rows were fetched; refusing to '
                           'overwrite the catalog with an empty file.')
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f,
                            delimiter=',',
                            quotechar='"',
                            lineterminator='\n')
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    logger.info(f'Wrote {len(rows)} rows to {output_path}')


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-token', help='UpCloud API token')
    parser.add_argument('--output',
                        default='~/.sky/catalogs/v8/upcloud/vms.csv',
                        help='Output CSV path')
    args = parser.parse_args()
    token = args.api_token or os.environ.get('UPCLOUD_TOKEN')
    if not token:
        raise SystemExit('Set UPCLOUD_TOKEN or pass --api-token.')
    create_catalog(token, os.path.expanduser(args.output))


if __name__ == '__main__':
    main()
