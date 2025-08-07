"""A script that generates the Seeweb catalog.

Usage:
    python fetch_seeweb.py [-h] [--api-key API_KEY] [--api-key-path API_KEY_PATH]

If neither --api-key nor --api-key-path are provided, this script will parse
`~/.seeweb_cloud/seeweb_keys` to look for Seeweb API key.
"""
import argparse
import configparser
import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

DEFAULT_SEEWEB_KEYS_PATH = os.path.expanduser('~/.seeweb_cloud/seeweb_keys')

# Known GPU types to memory mapping (in MiB)
# Updated with actual Seeweb offerings from catalog
GPU_TO_MEMORY = {
    'RTX-6000-24GB': 24576,  # 24GB
    'RTX-A6000-48GB': 49152,  # 48GB
    'A30': 24576,  # 24GB
    'A100-80GB': 81920,  # 80GB
    'L4-24GB': 24576,  # 24GB
    'L40s-48GB': 49152,  # 48GB
    'MI300X': 131072,  # 128GB (8x16GB)
    'GENERAL': None  # No GPU
}

# Fallback catalog data if API fails
# Updated with actual Seeweb offerings from catalog
FALLBACK_PLANS = [
    # CPU-only instances
    {
        'plan_name': 'eCS1',
        'vcpus': 1,
        'memory_gb': 1,
        'price': 0.019,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS2',
        'vcpus': 2,
        'memory_gb': 2,
        'price': 0.032,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS3',
        'vcpus': 4,
        'memory_gb': 4,
        'price': 0.048,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS4',
        'vcpus': 4,
        'memory_gb': 8,
        'price': 0.063,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS5',
        'vcpus': 8,
        'memory_gb': 16,
        'price': 0.127,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS6',
        'vcpus': 12,
        'memory_gb': 24,
        'price': 0.152,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'ECS7',
        'vcpus': 16,
        'memory_gb': 32,
        'price': 0.31,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'ECS8',
        'vcpus': 16,
        'memory_gb': 64,
        'price': 0.63,
        'gpu_name': None,
        'gpu_count': 0
    },

    # High Memory instances
    {
        'plan_name': 'eCS1HM',
        'vcpus': 4,
        'memory_gb': 64,
        'price': 0.217,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS2HM',
        'vcpus': 8,
        'memory_gb': 128,
        'price': 0.434,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS3HM',
        'vcpus': 16,
        'memory_gb': 256,
        'price': 0.719,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS4HM',
        'vcpus': 32,
        'memory_gb': 512,
        'price': 1.338,
        'gpu_name': None,
        'gpu_count': 0
    },
    {
        'plan_name': 'eCS5HM',
        'vcpus': 32,
        'memory_gb': 960,
        'price': 2.537,
        'gpu_name': None,
        'gpu_count': 0
    },

    # GPU instances - RTX-6000-24GB
    {
        'plan_name': 'ECS1GPU',
        'vcpus': 8,
        'memory_gb': 32,
        'price': 0.29,
        'gpu_name': 'RTX-6000-24GB',
        'gpu_count': 1
    },

    # GPU instances - A30
    {
        'plan_name': 'ECS1GPU11',
        'vcpus': 8,
        'memory_gb': 32,
        'price': 0.29,
        'gpu_name': 'A30',
        'gpu_count': 1
    },

    # GPU instances - RTX-A6000-48GB
    {
        'plan_name': 'ECS1GPU2',
        'vcpus': 8,
        'memory_gb': 32,
        'price': 0.74,
        'gpu_name': 'RTX-A6000-48GB',
        'gpu_count': 1
    },
    {
        'plan_name': 'ECS2GPU2',
        'vcpus': 16,
        'memory_gb': 64,
        'price': 1.48,
        'gpu_name': 'RTX-A6000-48GB',
        'gpu_count': 2
    },
    {
        'plan_name': 'ECS3GPU2',
        'vcpus': 32,
        'memory_gb': 128,
        'price': 2.96,
        'gpu_name': 'RTX-A6000-48GB',
        'gpu_count': 4
    },

    # GPU instances - A100-80GB
    {
        'plan_name': 'ECS1GPU3',
        'vcpus': 16,
        'memory_gb': 120,
        'price': 1.89,
        'gpu_name': 'A100-80GB',
        'gpu_count': 1
    },
    {
        'plan_name': 'ECS2GPU3',
        'vcpus': 32,
        'memory_gb': 240,
        'price': 3.78,
        'gpu_name': 'A100-80GB',
        'gpu_count': 2
    },
    {
        'plan_name': 'ECS3GPU3',
        'vcpus': 64,
        'memory_gb': 480,
        'price': 7.56,
        'gpu_name': 'A100-80GB',
        'gpu_count': 4
    },

    # GPU instances - L4-24GB
    {
        'plan_name': 'ECS1GPU6',
        'vcpus': 4,
        'memory_gb': 32,
        'price': 0.38,
        'gpu_name': 'L4-24GB',
        'gpu_count': 1
    },
    {
        'plan_name': 'ECS2GPU6',
        'vcpus': 8,
        'memory_gb': 64,
        'price': 0.76,
        'gpu_name': 'L4-24GB',
        'gpu_count': 2
    },
    {
        'plan_name': 'ECS3GPU6',
        'vcpus': 16,
        'memory_gb': 128,
        'price': 1.52,
        'gpu_name': 'L4-24GB',
        'gpu_count': 4
    },

    # GPU instances - L40s-48GB
    {
        'plan_name': 'ECS1GPU7',
        'vcpus': 8,
        'memory_gb': 32,
        'price': 0.85,
        'gpu_name': 'L40s-48GB',
        'gpu_count': 1
    },
    {
        'plan_name': 'ECS2GPU7',
        'vcpus': 16,
        'memory_gb': 64,
        'price': 1.7,
        'gpu_name': 'L40s-48GB',
        'gpu_count': 2
    },

    # GPU instances - MI300X (AMD)
    {
        'plan_name': 'ECS8GPU9',
        'vcpus': 255,
        'memory_gb': 2048,
        'price': 12.8,
        'gpu_name': 'MI300X',
        'gpu_count': 8
    },
]

FALLBACK_REGIONS = ['it-fr2', 'it-mi2', 'ch-lug1', 'bg-sof1']


def get_api_key(api_key_path: Optional[str] = None) -> str:
    """Get Seeweb API key from file or environment."""
    if api_key_path:
        path = os.path.expanduser(api_key_path)
    else:
        path = DEFAULT_SEEWEB_KEYS_PATH

    if not os.path.exists(path):
        raise FileNotFoundError(f'Seeweb API key file not found: {path}')

    parser = configparser.ConfigParser()
    parser.read(path)

    try:
        return parser['DEFAULT']['api_key'].strip()
    except KeyError:
        raise ValueError(f'Missing api_key in {path}')


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

        # Determine GPU name
        gpu_name = None
        if gpu_count > 0 and gpu_label:
            gpu_name = str(gpu_label)
        elif gpu_count > 0:
            # Try to extract from plan name
            plan_name_upper = plan_name.upper()
            for gpu_type in GPU_TO_MEMORY.keys():
                if gpu_type in plan_name_upper:
                    gpu_name = gpu_type
                    break
    else:
        # Dictionary format (fallback data)
        plan_name = plan.get('name', plan.get('plan_name', 'unknown'))
        vcpus = plan.get('vcpu', plan.get('vcpus', 0))
        memory_gb = plan.get('ram', plan.get('memory_gb', 0))
        price = plan.get('price_hour', plan.get('price', 0.0))
        gpu_name = plan.get('gpu', plan.get('gpu_name'))
        gpu_count = plan.get('gpu_count', 0)

    return {
        'plan_name': plan_name,
        'vcpus': vcpus,
        'memory_gb': memory_gb,
        'gpu_name': gpu_name,
        'gpu_count': gpu_count,
        'price': price,
    }


def clean_gpu_name(gpu_name: str) -> str:
    """Clean GPU name by replacing spaces with hyphens for SkyPilot compatibility."""
    if not gpu_name or pd.isna(gpu_name):
        return ''
    return str(gpu_name).replace(' ', '-')


def get_gpu_info(gpu_count: int, gpu_name: str) -> str:
    """Generate GPU info JSON string compatible with SkyPilot."""
    if not gpu_name or gpu_count == 0:
        return ''

    # Clean GPU name by replacing spaces with hyphens
    clean_name = clean_gpu_name(gpu_name)

    gpu_memory = GPU_TO_MEMORY.get(gpu_name,
                                   16384)  # Default to 16GB if unknown

    gpu_info = {
        'Gpus': [{
            'Name': clean_name,  # Use cleaned name
            'Manufacturer': 'NVIDIA',  # Assuming NVIDIA for now
            'Count': float(gpu_count),
            'MemoryInfo': {
                'SizeInMiB': gpu_memory
            },
        }],
        'TotalGpuMemoryInMiB': gpu_memory * gpu_count if gpu_memory else 0
    }

    return json.dumps(gpu_info).replace('"', "'")


def fetch_seeweb_data(api_key: str) -> List[Dict]:
    """Fetch plans and regions from Seeweb API, with fallback to default data."""
    plans = FALLBACK_PLANS.copy()
    regions = FALLBACK_REGIONS.copy()

    try:
        import ecsapi
        client = ecsapi.Api(token=api_key)

        # Try to fetch plans - this is more complex and may fail
        try:
            print("Fetching plans from Seeweb API...")
            # Try simple plans first
            api_plans = client.fetch_plans()
            print(f"API plans: {[p.name for p in api_plans if int(p.gpu) > 0]}")
            if api_plans and len(api_plans) > 0:
                print(f"Successfully fetched {len(api_plans)} plans from API")
                plans = []
                for plan in api_plans:
                    # Skip plans that end with 'W' (Windows plans)
                    if plan.name.endswith('W'):
                        print(f"Skipping Windows plan: {plan.name}")
                        continue

                    print(f"Fetching regions available for {plan.name}")
                    regions_available = client.fetch_regions_available(
                        plan.name)
                    print(f"Regions available: {len(regions_available)}")
                    try:
                        parsed = parse_plan_info(plan)
                        parsed.update({'regions_available': regions_available})
                        print(f"Parsed plan: {parsed}")
                        plans.append(parsed)
                    except Exception as e:
                        print(f"Error parsing plan {plan}: {e}")
                        raise e
                        continue
                print(
                    f"Successfully parsed {len(plans)} plans (excluding Windows plans)"
                )
        except Exception as e:
            raise e
            print(f"Error fetching plans from API: {e}")
            print(f"Using fallback plans: {[p['plan_name'] for p in plans]}")

    except ImportError:
        print("ecsapi not available, using fallback data")
    except Exception as e:
        raise e
        print(f"Error initializing Seeweb client: {e}")
        print("Using fallback data")

    return plans


def create_catalog(api_key: str, output_path: str) -> None:
    """Create Seeweb catalog by fetching data from API."""
    plans = fetch_seeweb_data(api_key)

    # Create CSV catalog
    print(f"Writing catalog to {output_path}")
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
                                                plan['gpu_name'])

                # Create entry for each region
                for region in plan.get('regions_available', []):
                    print(f"Writing plan {plan['plan_name']} to {region}")
                    # Clean GPU name for AcceleratorName field
                    clean_gpu_name_value = clean_gpu_name(
                        plan['gpu_name']) if plan['gpu_name'] else ''
                    writer.writerow([
                        plan['plan_name'],  # InstanceType
                        clean_gpu_name_value,  # AcceleratorName (cleaned)
                        plan['gpu_count']
                        if plan['gpu_count'] > 0 else '',  # AcceleratorCount
                        plan['vcpus'],  # vCPUs
                        plan['memory_gb'],  # MemoryGiB  
                        plan['price'],  # Price
                        region,  # Region
                        gpu_info_str,  # GpuInfo
                        ''  # SpotPrice (Seeweb doesn't support spot)
                    ])
            except Exception as e:
                print(f"Error processing plan {plan}: {e}")
                continue

    print(f"Seeweb catalog saved to {output_path}")
    print(f"Created {len(plans)} instance types")


def main():
    parser = argparse.ArgumentParser(description='Generate Seeweb catalog')
    parser.add_argument('--api-key', help='Seeweb API key')
    parser.add_argument('--api-key-path',
                        help='Path to file containing Seeweb API key')
    parser.add_argument('--fallback-only',
                        action='store_true',
                        help='Skip API calls and use only fallback data')

    args = parser.parse_args()

    # Get API key
    api_key = None
    if not args.fallback_only:
        try:
            if args.api_key:
                api_key = args.api_key
            else:
                api_key = get_api_key(args.api_key_path)
        except Exception as e:
            print(f"Error getting API key: {e}")
            print("Using fallback data only")

    # Create catalog using standard pattern
    os.makedirs('seeweb', exist_ok=True)
    create_catalog(api_key, 'seeweb/vms.csv')
    print('Seeweb Service Catalog saved to seeweb/vms.csv')


if __name__ == '__main__':
    main()
