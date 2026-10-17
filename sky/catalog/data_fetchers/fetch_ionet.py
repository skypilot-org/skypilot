"""A script that generates the io.net VMaaS catalog.

Usage:
    python -m sky.catalog.data_fetchers.fetch_ionet [-h] [--api-key API_KEY]
        [--api-key-path API_KEY_PATH] [--output OUTPUT]

If neither --api-key nor --api-key-path is provided, the API key is read from
the IONET_API_KEY environment variable or `~/.ionet/api_key`. The catalog is
written to `ionet/vms.csv` unless --output is given.
"""
import argparse
import csv
import io
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from sky.provision.ionet import utils as ionet_utils

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_PATH = 'ionet/vms.csv'

COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB',
    'StorageGiB', 'Price', 'SpotPrice', 'Region', 'GpuInfo', 'HardwareId',
    'GpusPerVm'
]


def _to_float(value: Any) -> Optional[float]:
    """Returns a finite float, or None for a missing value."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'Expected a number, got {value!r}')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'Expected a finite number, got {value!r}')
    return result


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f'{value:g}'


def canonicalize_gpu_name(name: str, vram_gib: Optional[float]) -> str:
    """Maps an io.net GPU name to SkyPilot's canonical accelerator name.

    For example, 'NVIDIA GeForce RTX 4090' -> 'RTX4090',
    'NVIDIA A100 80GB PCIe' -> 'A100-80GB', 'H100 SXM5' -> 'H100' and
    'RTX 6000 Ada' -> 'RTX6000-Ada'.
    """
    gpu = re.sub(r'[\s_-]+', ' ', name).strip()
    gpu = re.sub(r'^(NVIDIA|AMD)\s+', '', gpu, flags=re.IGNORECASE)
    gpu = re.sub(r'^(GeForce|Tesla)\s+', '', gpu, flags=re.IGNORECASE)
    # Memory size and form factor are not part of SkyPilot's names; the
    # memory is recorded in GpuInfo instead.
    gpu = re.sub(r'\b\d+\s*GB\b', '', gpu, flags=re.IGNORECASE)
    gpu = re.sub(r'\b(SXM\d?|PCIe|HBM\d?e?)\b', '', gpu, flags=re.IGNORECASE)
    gpu = re.sub(r'\s+', ' ', gpu).strip()
    # SkyPilot has no separator in RTX names, e.g. RTX4090 and RTXPRO6000.
    gpu = re.sub(r'^RTX PRO ', 'RTXPRO', gpu, flags=re.IGNORECASE)
    gpu = re.sub(r'^RTX ', 'RTX', gpu, flags=re.IGNORECASE)
    gpu = '-'.join('Ada' if token.lower() == 'ada' else token.upper()
                   for token in gpu.split(' '))
    # A100 and V100 are the only names that encode the memory size.
    if gpu == 'A100' and vram_gib is not None and vram_gib >= 80:
        gpu = 'A100-80GB'
    elif gpu == 'V100' and vram_gib is not None and vram_gib >= 32:
        gpu = 'V100-32GB'
    return gpu


def _make_instance_type(accelerator: str, count: int,
                        vram_gib: Optional[float]) -> str:
    """Returns a readable instance type, e.g. 1x_H100_80GB."""
    name = accelerator.replace('-', '_')
    instance_type = f'{count}x_{name}'
    if vram_gib is not None and not accelerator.endswith('GB'):
        instance_type += f'_{_format_number(vram_gib)}GB'
    return instance_type


def _make_gpu_info(accelerator: str, count: int,
                   vram_gib: Optional[float]) -> str:
    vram_mib = None if vram_gib is None else int(vram_gib * 1024)
    gpu_info = {
        'Gpus': [{
            'Name': accelerator,
            'Manufacturer': 'AMD' if accelerator.startswith('MI') else 'NVIDIA',
            'Count': float(count),
            'MemoryInfo': {
                'SizeInMiB': vram_mib
            },
        }],
        'TotalGpuMemoryInMiB': None if vram_mib is None else vram_mib * count,
    }
    # list_accelerators parses GpuInfo with ast.literal_eval, so write a
    # Python literal (None for unknown memory) rather than JSON.
    return repr(gpu_info)


def _parse_hardware_id(value: Any) -> Union[int, str]:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f'Invalid deploy_id {value!r}')
    value = str(value).strip()
    if not value:
        raise ValueError('Empty deploy_id')
    return int(value) if value.isdigit() else value


def _parse_offer(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parses one hardware offer, or returns None for sold-out offers."""
    if item.get('sold_out'):
        return None
    region = str(item['location']).strip().upper()
    name = str(item['name']).strip()
    count = int(item['num_cards'])
    price = _to_float(item['price'])
    if not region or not name or count < 1 or price is None or price < 0:
        raise ValueError('Missing location, name, num_cards or price')
    vram_gib = _to_float(item.get('vram_per_card'))
    accelerator = canonicalize_gpu_name(name, vram_gib)
    return {
        'InstanceType': _make_instance_type(accelerator, count, vram_gib),
        'AcceleratorName': accelerator,
        'AcceleratorCount': float(count),
        'vCPUs': _to_float(item.get('vcpu')),
        'MemoryGiB': _to_float(item.get('memory')),
        'StorageGiB': _to_float(item.get('storage')),
        'Price': price,
        'SpotPrice': None,
        'Region': region,
        'GpuInfo': _make_gpu_info(accelerator, count, vram_gib),
        'HardwareId': _parse_hardware_id(item['deploy_id']),
        'GpusPerVm': count,
    }


def parse_hardware(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Converts a VMaaS /hardware response into catalog rows.

    This is the only function that knows the response shape:
    `{'data': {'hardware': [offer, ...]}}`, where each offer has the fields
    id, name, num_cards, deploy_id, location, sold_out, price (USD per VM
    hour), vram_per_card, vcpu, memory and storage (sizes in GB).

    Sold-out and malformed offers are skipped. Instance types are unique per
    region; when two offers collide, the cheaper one is kept. If an instance
    type has different vCPUs or memory in different regions, its name gets a
    vCPU/memory suffix, as SkyPilot requires one shape per instance type.
    """
    data = response.get('data') if isinstance(response, dict) else None
    items = data.get('hardware') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError('io.net hardware response has no data.hardware list.')

    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in items:
        offer_id = item.get('id') if isinstance(item, dict) else None
        try:
            row = _parse_offer(item)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            logger.warning(f'Skipping malformed io.net offer {offer_id!r}: '
                           f'{e}')
            continue
        if row is None:
            continue
        key = (row['InstanceType'], row['Region'])
        existing = rows.get(key)
        if existing is not None:
            kept, dropped = sorted([existing, row], key=lambda r: r['Price'])
            kept_id, dropped_id = kept['HardwareId'], dropped['HardwareId']
            logger.info(f'io.net offers collide on {key[0]} in {key[1]}: '
                        f'keeping hardware {kept_id!r} over {dropped_id!r}, '
                        'which costs more.')
            row = kept
        rows[key] = row

    shapes: Dict[str, set] = {}
    for row in rows.values():
        shapes.setdefault(row['InstanceType'], set()).add(
            (row['vCPUs'], row['MemoryGiB']))
    for row in rows.values():
        if len(shapes[row['InstanceType']]) > 1:
            vcpus = ('unknown'
                     if row['vCPUs'] is None else _format_number(row['vCPUs']))
            memory = ('unknown' if row['MemoryGiB'] is None else _format_number(
                row['MemoryGiB']))
            row['InstanceType'] += f'_{vcpus}vCPU_{memory}GiB'
    return sorted(rows.values(), key=lambda r: (r['InstanceType'], r['Region']))


def create_catalog(api_key: str, output_path: str) -> None:
    """Fetches io.net hardware offers and writes the catalog CSV."""
    client = ionet_utils.IONetClient(api_key=api_key)
    rows = parse_hardware(client.get_hardware())
    # Keep the previous catalog so that a sold-out inventory does not wipe
    # out every io.net offer.
    if not rows and os.path.exists(output_path):
        logger.warning('No io.net offer is available. Keeping the existing '
                       f'catalog at {output_path}.')
        return
    with io.StringIO() as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as out:
            out.write(f.getvalue())
    logger.info(f'Wrote {len(rows)} io.net offers to {output_path}')


def get_api_key(cmdline_args: argparse.Namespace) -> str:
    """Gets the io.net API key from the command line, env var or file."""
    if cmdline_args.api_key:
        return cmdline_args.api_key
    if cmdline_args.api_key_path is not None:
        with open(os.path.expanduser(cmdline_args.api_key_path),
                  mode='r',
                  encoding='utf-8') as f:
            return f.read().strip()
    # Reads IONET_API_KEY, then ~/.ionet/api_key.
    return ionet_utils.get_api_key()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='io.net API key.')
    parser.add_argument('--api-key-path',
                        help='path of file containing the io.net API key.')
    parser.add_argument('--output',
                        default=DEFAULT_OUTPUT_PATH,
                        help='path of the catalog CSV to write.')
    args = parser.parse_args()
    create_catalog(get_api_key(args), os.path.expanduser(args.output))
    print(f'io.net catalog saved to {args.output}')


if __name__ == '__main__':
    main()
