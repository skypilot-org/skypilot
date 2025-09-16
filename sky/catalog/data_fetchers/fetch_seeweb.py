"""A script that generates the Seeweb catalog.

Usage:
    python fetch_seeweb.py [-h] [--api-key API_KEY]
    [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.seeweb_cloud/seeweb_keys` to look for Seeweb API key.
"""
import argparse
import configparser
import csv
import json
import os
from typing import Any, Dict, List, Optional

from sky.adaptors.seeweb import ecsapi

# GPU name mapping from Seeweb to SkyPilot canonical names
SEEWEB_GPU_NAME_TO_SKYPILOT_GPU_NAME = {
    'H200 141GB': 'H200',
    'RTX A6000 48GB': 'RTXA6000',
    'A100 80GB': 'A100',
    'L4 24GB': 'L4',
    'L40s 48GB': 'L40S',
    'H100 80GB': 'H100',
    'MI300X': 'MI300X',
    'A30': 'A30',
    'RTX 6000 24GB': 'RTX6000',
    'Tenstorrent Grayskull e75': 'GRAYSKULL-E75',
    'Tenstorrent Grayskull e150': 'GRAYSKULL-E150',
}

# GPU VRAM mapping in MB
VRAM = {
    'RTXA6000': 48384,  # 48GB
    'H200': 144384,  # 141GB
    'A100': 81920,  # 80GB
    'L4': 24576,  # 24GB
    'L40S': 49152,  # 48GB
    'H100': 81920,  # 80GB
    'MI300X': 192000,  # 192GB
    'A30': 24576,  # 24GB
    'RTX6000': 24576,  # 24GB
    'GRAYSKULL-E75': 8192,  # 8GB
    'GRAYSKULL-E150': 8192,  # 8GB
}


def is_tenstorrent_gpu_name(gpu_name: Optional[str]) -> bool:
    """Return True if the given GPU name refers to a Tenstorrent GPU.

    Detects by common identifiers present in normalized names (e.g., GRAYSKULL)
    or by the vendor name directly.
    """
    if not gpu_name:
        return False
    upper = str(gpu_name).upper()
    return 'TENSTORRENT' in upper or 'GRAYSKULL' in upper


def is_mi300x_gpu_name(gpu_name: Optional[str]) -> bool:
    """Return True if the given GPU name refers to AMD MI300X."""
    if not gpu_name:
        return False
    return 'MI300X' in str(gpu_name).upper()


def get_api_key(path: Optional[str] = None) -> str:
    """Get API key from config file or environment variable."""
    # Step 1: Try to get from config file
    if path is None:
        path = os.path.expanduser('~/.seeweb_cloud/seeweb_keys')
    else:
        path = os.path.expanduser(path)

    try:
        parser = configparser.ConfigParser()
        parser.read(path)
        return parser['DEFAULT']['api_key'].strip()
    except (KeyError, FileNotFoundError) as exc:
        # Step 2: Try environment variable
        api_key = os.environ.get('SEEWEB_API_KEY')
        if api_key:
            return api_key.strip()

        # If neither found, raise error
        raise ValueError(
            f'API key not found in {path} or ENV variable SEEWEB_API_KEY'
        ) from exc


def normalize_gpu_name(gpu_name: str) -> str:
    """Normalize GPU name from Seeweb API to SkyPilot canonical name."""
    if not gpu_name:
        return ''

    # Map to canonical name if available
    canonical_name = SEEWEB_GPU_NAME_TO_SKYPILOT_GPU_NAME.get(gpu_name)
    if canonical_name:
        return canonical_name

    # If not found in mapping, return original name
    print(f'Warning: GPU name "{gpu_name}" not found in mapping,'
          f'using original name')
    return gpu_name


def parse_plan_info(plan: Any) -> Dict[str, Any]:
    """Parse plan information from Seeweb API response."""
    # Handle both dictionary and object formats
    if hasattr(plan, 'name'):
        # Object format from API
        plan_name = getattr(plan, 'name', 'unknown')
        vcpus = int(getattr(plan, 'cpu', 0))

        # Handle memory conversion safely
        memory_mb = getattr(plan, 'ram', 0)
        try:
            memory_gb = int(
                memory_mb) / 1024 if memory_mb else 0  # Convert to GB
        except (ValueError, TypeError):
            memory_gb = 0

        # Handle price safely
        try:
            price = float(getattr(plan, 'hourly_price', 0.0))
        except (ValueError, TypeError):
            price = 0.0

        # Handle GPU info
        try:
            gpu_count = int(getattr(plan, 'gpu', 0))
        except (ValueError, TypeError):
            gpu_count = 0

        gpu_label = getattr(plan, 'gpu_label', None)

        # Determine GPU name - use gpu_label if available,
        # otherwise try to infer from plan name
        if gpu_label:
            gpu_name = normalize_gpu_name(gpu_label)  # Normalize the GPU name
        else:
            # Try to extract GPU name from plan name
            plan_name = getattr(plan, 'name', '')
            if 'GPU' in plan_name:
                # Extract GPU type from plan name (e.g., ECS1GPU11 -> GPU11)
                parts = plan_name.split('GPU')
                if len(parts) > 1:
                    gpu_name = 'GPU' + parts[1]
                else:
                    gpu_name = 'GPU'
            else:
                gpu_name = None

        # Get GPU VRAM from mapping using the normalized name
        gpu_vram_mb = VRAM.get(gpu_name, 0) if gpu_name else 0
    else:
        raise ValueError(f'Unsupported plan format: {type(plan)}')

    return {
        'plan_name': plan_name,
        'vcpus': vcpus,
        'memory_gb': memory_gb,
        'gpu_name': gpu_name,
        'gpu_count': gpu_count,
        'gpu_vram_mb': gpu_vram_mb,
        'price': price,
    }


def get_gpu_info(gpu_count: int, gpu_name: str, gpu_vram_mb: int = 0) -> str:
    """Generate GPU info JSON string compatible with SkyPilot."""
    if not gpu_name or gpu_count == 0:
        return ''

    # Determine manufacturer based on GPU name
    gpu_name_upper = str(gpu_name).upper()
    if 'MI300' in gpu_name_upper or gpu_name_upper == 'MI300X':
        manufacturer = 'AMD'
    elif 'GRAYSKULL' in gpu_name_upper:
        manufacturer = 'TENSTORRENT'
    else:
        manufacturer = 'NVIDIA'

    gpu_info = {
        'Gpus': [{
            'Name': gpu_name,
            'Manufacturer': manufacturer,
            'Count': float(gpu_count),
            'MemoryInfo': {
                'SizeInMiB': gpu_vram_mb
            },
        }],
        'TotalGpuMemoryInMiB': gpu_vram_mb * gpu_count if gpu_vram_mb else 0
    }

    return json.dumps(gpu_info).replace('"', '\'')


def fetch_seeweb_data(api_key: str) -> List[Dict]:
    """Fetch data from Seeweb API."""
    if ecsapi is None:
        raise ImportError('ecsapi not available')

    try:
        client = ecsapi.Api(token=api_key)

        print('Fetching plans from Seeweb API...')
        api_plans = client.fetch_plans()

        if not api_plans:
            raise ValueError('No plans returned from API')

        print(f'Successfully fetched {len(api_plans)} plans from API')
        plans = []

        for plan in api_plans:
            try:
                # Parse first so we can filter
                # Tenstorrent before extra API calls
                parsed = parse_plan_info(plan)

                if is_tenstorrent_gpu_name(parsed.get('gpu_name')):
                    print(f'Skipping Tenstorrent plan {plan.name}')
                    continue

                if is_mi300x_gpu_name(parsed.get('gpu_name')):
                    print(f'Skipping MI300X plan {plan.name}')
                    continue

                print(f'Fetching regions available for {plan.name}')
                regions_available = client.fetch_regions_available(plan.name)

                parsed.update({'regions_available': regions_available})
                plans.append(parsed)
            except Exception as e:  # pylint: disable=broad-except
                print(f'Error parsing plan {plan.name}: {e}')
                continue

        print(f'Successfully parsed {len(plans)} plans')
        return plans

    except Exception as e:  # pylint: disable=broad-except
        raise Exception(f'Error fetching data from Seeweb API: {e}') from e


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Seeweb catalog by fetching data from API."""
    plans = fetch_seeweb_data(api_key)

    # Create CSV catalog
    print(f'Writing catalog to {output_path}')
    with open(output_path, mode='w', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quotechar='"')
        writer.writerow([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice'
        ])

        for plan in plans:
            try:
                gpu_info_str = ''
                if plan['gpu_name'] and plan['gpu_count'] > 0:
                    gpu_info_str = get_gpu_info(plan['gpu_count'],
                                                plan['gpu_name'],
                                                plan.get('gpu_vram_mb', 0))

                # Handle regions - create a row for each available region
                regions_available = plan['regions_available']
                if isinstance(regions_available,
                              list) and len(regions_available) > 0:
                    # Create a row for each region
                    for region in regions_available:
                        writer.writerow([
                            plan['plan_name'],  # InstanceType
                            plan['gpu_name'],  # AcceleratorName (cleaned)
                            plan['gpu_count'] if plan['gpu_count'] > 0 else
                            '',  # AcceleratorCount
                            plan['vcpus'],  # vCPUs
                            plan['memory_gb'],  # MemoryGiB
                            plan['price'],  # Price
                            region,  # Region (single region per row)
                            gpu_info_str,  # GpuInfo
                            ''  # SpotPrice (Seeweb doesn't support spot)
                        ])
                else:
                    # No regions available, create a row with empty region
                    writer.writerow([
                        plan['plan_name'],  # InstanceType
                        plan['gpu_name'],  # AcceleratorName (cleaned)
                        plan['gpu_count']
                        if plan['gpu_count'] > 0 else '',  # AcceleratorCount
                        plan['vcpus'],  # vCPUs
                        plan['memory_gb'],  # MemoryGiB
                        plan['price'],  # Price
                        '',  # Region (empty)
                        gpu_info_str,  # GpuInfo
                        ''  # SpotPrice (Seeweb doesn't support spot)
                    ])
            except Exception as e:  # pylint: disable=broad-except
                print(f'Error processing plan {plan["plan_name"]}: {e}')
                continue

    print(f'Seeweb catalog saved to {output_path}')
    print(f'Created {len(plans)} instance types')


def main() -> None:
    """Main function to fetch and write Seeweb platform prices to a CSV file."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', help='Seeweb API key')
    parser.add_argument('--api-key-path',
                        help='Path to file containing Seeweb API key')
    args = parser.parse_args()

    # Get API key
    if args.api_key:
        api_key = args.api_key
    else:
        api_key = get_api_key(args.api_key_path)

    os.makedirs('seeweb', exist_ok=True)
    create_catalog(api_key, 'seeweb/vms.csv')
    print('Seeweb Service Catalog saved to seeweb/vms.csv')


if __name__ == '__main__':
    main()
