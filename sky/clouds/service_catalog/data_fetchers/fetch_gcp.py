"""A script that generates Google Cloud GPU catalog.

Google Cloud does not have an API for querying TPU/GPU offerings, so we crawl
the information from GCP websites.
"""

import re

from lxml import html
import pandas as pd
import requests

GCP_URL = 'https://cloud.google.com'
GCP_VM_PRICING_URL = 'https://cloud.google.com/compute/vm-instance-pricing'
GCP_GPU_PRICING_URL = 'https://cloud.google.com/compute/gpus-pricing'
GCP_GPU_ZONES_URL = 'https://cloud.google.com/compute/docs/gpus/gpu-regions-zones'

NOT_AVAILABLE_STR = 'Not available in this region'

# Supported GPU types and counts.
GPU_TYPES_TO_COUNTS = {
    'A100': [1, 2, 4, 8, 16],
    'T4': [1, 2, 4],
    'P4': [1, 2, 4],
    'V100': [1, 2, 4, 8],
    'P100': [1, 2, 4],
    'K80': [1, 2, 4, 8],
}

# FIXME(woosuk): This URL can change.
A2_PRICING_URL = '/compute/vm-instance-pricing_500ae19db0b58b862da0bc662dafc1b90ed3365a4e002c244f84fa5cae0da2fc.frame'
A2_INSTANCE_TYPES = {
    'a2-highgpu-1g': {'vCPU': 12, 'MemoryGiB': 85},
    'a2-highgpu-2g': {'vCPU': 24, 'MemoryGiB': 170},
    'a2-highgpu-4g': {'vCPU': 48, 'MemoryGiB': 340},
    'a2-highgpu-8g': {'vCPU': 96, 'MemoryGiB': 680},
    'a2-megagpu-16g': {'vCPU': 96, 'MemoryGiB': 1360},
}

# Source: https://cloud.google.com/compute/docs/gpus/gpu-regions-zones
NO_A100_16G_ZONES = ['asia-northeast3-a', 'asia-northeast3-b', 'us-west4-b']

# For the TPU catalog, we rely on the hard-coded CSV files.
TPU_DATA_DIR = './tpu_data/'

# Source: https://cloud.google.com/tpu/docs/regions-zones
TPU_ZONES = TPU_DATA_DIR + 'zones.csv'
# Source: https://cloud.google.com/tpu/pricing
# NOTE: The CSV file does not completely align with the data in the website.
# The differences are:
# 1. We added us-east1 for TPU Research Cloud.
# 2. We deleted TPU v3 pods in us-central1, because we found that GCP is not
#    actually supporting them in the region.
# 3. We used estimated prices for on-demand tpu-v3-{64,...,2048} as their
#    prices are not publicly available.
# 4. For preemptible TPUs whose prices are not publicly available, we applied
#    70% off discount on the on-demand prices because every known preemptible
#    TPU price follows this pricing rule.
TPU_PRICING = TPU_DATA_DIR + 'pricing.csv'

COLUMNS = [
    'InstanceType',  # None for accelerators
    'AcceleratorName',
    'AcceleratorCount',
    'MemoryGiB',  # 0 for accelerators
    'GpuInfo',  # Same as AcceleratorName
    'Price',
    'SpotPrice',
    'Region',
    'AvailabilityZone',
]


def get_iframe_sources(url):
    page = requests.get(url)
    tree = html.fromstring(page.content)
    return tree.xpath('//iframe/@src')


def get_regions(doc):
    # Get the dictionary of regions.
    # E.g., 'kr': 'asia-northeast3'
    regions = doc.xpath('//md-option')
    regions = {
        region.attrib['value']: re.search(r'\((.*?)\)',region.text).group(1)
        for region in regions
    }
    return regions

# TODO(woosuk): parallelize this function using Ray.
# Currently, 'HTML parser error : Tag md-option invalid' is raised
# when the function is parallelized by Ray.
def get_vm_price_table(url):
    page = requests.get(url)
    doc = html.fromstring(page.content)
    regions = get_regions(doc)

    # Get the table.
    rows = doc.xpath('//tr')
    headers = rows.pop(0).getchildren()
    headers = [header.text_content() for header in headers]

    # Create the dataframe.
    table = []
    for region, full_name in regions.items():
        for row in rows:
            new_row = [full_name]
            cells = row.getchildren()
            if not cells:
                continue

            for cell in cells:
                # Remove duplicated header (in "M1 machine types").
                if 'Machine type' in cell.text_content():
                    break
                # Remove footer.
                if 'Custom machine type' in cell.text_content():
                    break

                if 'cloud-pricer' not in cell.attrib:
                    # This cell only contains a plain text.
                    text = cell.text_content()
                    # Remove Skylake related text.
                    if 'Skylake Platform only' in text:
                        text = text.replace('Skylake Platform only', '')
                    new_row.append(text.strip())
                elif 'default' in cell.attrib:
                    # This cell only contains a plain text.
                    new_row.append(cell.attrib['default'])
                else:
                    # This cell contains the region-wise price information.
                    key = region + '-hourly'
                    if key in cell.attrib:
                        new_row.append(cell.attrib[key])
                    else:
                        new_row.append(NOT_AVAILABLE_STR)
            else:
                table.append(new_row)
    df = pd.DataFrame(table, columns=['Region'] + headers)

    # Standardize the column names.
    column_remapping = {
        # InstanceType
        'Machine type': 'InstanceType',
        # vCPU
        'Virtual CPUs': 'vCPU',
        'vCPUs': 'vCPU',
        # MemoryGiB
        'Memory': 'MemoryGiB',
        'Memory(GB)': 'MemoryGiB',
        # Price
        'On-demand price': 'Price',
        'On-demand price (USD)': 'Price',
        'Price (USD)': 'Price',
        'On Demand List Price': 'Price',
        'Evaluative price (USD)': 'Price',
        # SpotPrice
        'Spot price*': 'SpotPrice',
        'Spot price* (USD)': 'SpotPrice',
        'Spot price*(USD)': 'SpotPrice',
        'Spot price (USD)': 'SpotPrice',
        ' Spot price* (USD)': 'SpotPrice',
    }
    df.rename(columns=column_remapping, inplace=True)

    def parse_memory(memory_str):
        if 'GB' in memory_str:
            return float(memory_str.replace('GB', ''))
        else:
            return float(memory_str)

    pattern = re.compile(r'\$?(.*?)\s?/')
    def parse_price(price_str):
        if NOT_AVAILABLE_STR in price_str:
            return None
        try:
            price = float(price_str[1:])
        except ValueError:
            price = float(re.search(pattern, price_str).group(1))
        return price

    # Parse the prices.
    df['Price'] = df['Price'].apply(parse_price)
    df['SpotPrice'] = df['SpotPrice'].apply(parse_price)

    # Remove unsupported regions.
    df = df[~df['Price'].isna()]
    df = df[~df['SpotPrice'].isna()]

    # E.g., m2-ultramem instances will be skipped because their spot prices
    # are not available.
    if df.empty:
        return None

    instance_type = None
    if 'InstanceType' in df.columns:
        # Price table for specific instance types.
        instance_type = df['InstanceType'].iloc[0]
        if instance_type == 'a2-highgpu-1g':
            # The A2 price table includes the GPU cost.
            return None

        # Price table for specific VM types.
        df = df[['InstanceType', 'vCPU', 'MemoryGiB', 'Region', 'Price', 'SpotPrice']]
        # vCPU
        df['vCPU'] = df['vCPU'].astype(float)
        # MemoryGiB
        df['MemoryGiB'] = df['MemoryGiB'].apply(parse_memory)

        df.drop(columns=['vCPU'], inplace=True)
        df['AcceleratorName'] = None
        df['AcceleratorCount'] = None
        df['GpuInfo'] = None
        df['AvailabilityZone'] = None
    else:
        # Others (e.g., pricing rule table).
        # Currently, we do not use this table.
        df = df[['Item', 'Region', 'Price', 'SpotPrice']]
    return df


def get_gpu_price_table(url):
    page = requests.get(url)
    doc = html.fromstring(page.content)
    regions = get_regions(doc)

    # Get the table.
    rows = doc.xpath('//tr')
    headers = rows.pop(0).getchildren()
    headers = [header.text_content() for header in headers]

    # Create the dataframe.
    table = []
    row_span = []
    for region, full_name in regions.items():
        i = 0
        while i < len(rows):
            row = rows[i]
            new_row = [full_name]
            cells = row.getchildren()

            first_cell = cells[0]
            # Do not include NVIDIA workstations.
            if 'virtual workstation' in first_cell.text_content().lower():
                break

            row_span = int(first_cell.attrib['rowspan'])
            i += row_span

            for cell in cells:
                if 'cloud-pricer' not in cell.attrib:
                    # This cell only contains a plain text.
                    text = cell.text_content()
                    new_row.append(text.strip())
                else:
                    # This cell contains the region-wise price information.
                    key = region + '-hourly'
                    if key in cell.attrib:
                        new_row.append(cell.attrib[key])
                    else:
                        new_row.append(NOT_AVAILABLE_STR)
            table.append(new_row)
    df = pd.DataFrame(table, columns=['Region'] + headers)

    # Standardize the column names.
    column_remapping = {
        'Model': 'AcceleratorName',
        'GPU price (USD)': 'Price',
        'Spot price* (USD)': 'SpotPrice',
    }
    df.rename(columns=column_remapping, inplace=True)

    df = df[['AcceleratorName', 'Region', 'Price', 'SpotPrice']]
    # Fix GPU names (i.e., remove NVIDIA prefix).
    df['AcceleratorName'] = df['AcceleratorName'].apply(lambda x: x.replace('NVIDIA ', ''))
    # Add GPU counts.
    df['AcceleratorCount'] = df['AcceleratorName'].apply(lambda x: GPU_TYPES_TO_COUNTS[x])

    # Parse the prices.
    pattern = re.compile(r'\$?(.*?)\s?per GPU')
    def parse_price(price_str):
        if NOT_AVAILABLE_STR in price_str:
            return None
        try:
            price = float(price_str[1:])
        except ValueError:
            price = float(re.search(pattern, price_str).group(1))
        return price

    df['Price'] = df['Price'].apply(parse_price)
    df['SpotPrice'] = df['SpotPrice'].apply(parse_price)

    # Remove unsupported regions.
    df = df[~df['Price'].isna()]
    df = df[~df['SpotPrice'].isna()]
    return df


def get_gpu_zones(url):
    page = requests.get(url)
    df = pd.read_html(page.text.replace('<br>', '\n'))[0]
    column_remapping = {
        'GPU platforms': 'AcceleratorName',
        'Zones': 'AvailabilityZone',
    }
    df.rename(columns=column_remapping, inplace=True)
    df = df[['AvailabilityZone', 'AcceleratorName']]

    # Remove zones that do not support any GPU.
    df = df[~df['AcceleratorName'].isna()]

    # Explode Availability Zone.
    df['AvailabilityZone'] = df['AvailabilityZone'].str.split(' ')
    df = df.explode('AvailabilityZone', ignore_index=True)

    # Remove "(except a2-megagpu-16g)"
    # The exceptional zones will be handled manually.
    df['AcceleratorName'] = df['AcceleratorName'].apply(lambda x: x.replace(' (except a2-megagpu-16g)', ''))
    return df


def get_gpu_df():
    """Generates the GCP service catalog for GPUs."""
    gpu_price_table_url = get_iframe_sources(GCP_GPU_PRICING_URL)
    assert len(gpu_price_table_url) == 1
    gpu_pricing = get_gpu_price_table(GCP_URL + gpu_price_table_url[0])
    gpu_zones = get_gpu_zones(GCP_GPU_ZONES_URL)

    # Remove zones not in the pricing data.
    zone_to_region = lambda x: x[:-2]
    gpu_zones['Region'] = gpu_zones['AvailabilityZone'].apply(zone_to_region)
    supported_regions = gpu_pricing['Region'].unique()
    gpu_zones = gpu_zones[gpu_zones['Region'].isin(supported_regions)]

    # Explode GPU types.
    gpu_zones['AcceleratorName'] = gpu_zones['AcceleratorName'].apply(
        lambda x: x.split(', '))
    gpu_zones = gpu_zones.explode(column='AcceleratorName', ignore_index=True)

    # Merge the two dataframes.
    gpu_df = pd.merge(gpu_zones, gpu_pricing, on=['AcceleratorName', 'Region'])

    # Explode GPU counts.
    gpu_df = gpu_df.explode(column='AcceleratorCount', ignore_index=True)
    gpu_df['AcceleratorCount'] = gpu_df['AcceleratorCount'].astype(int)

    # Calculate the on-demand and spot prices.
    gpu_df['Price'] = gpu_df['AcceleratorCount'] * gpu_df['Price']
    gpu_df['SpotPrice'] = gpu_df['AcceleratorCount'] * gpu_df['SpotPrice']

    # Consider the zones that do not have 16xA100 machines.
    gpu_df = gpu_df[~(gpu_df['AvailabilityZone'].isin(NO_A100_16G_ZONES) &
                      (gpu_df['AcceleratorName'] == 'A100') &
                      (gpu_df['AcceleratorCount'] == 16))]

    # Add columns for the service catalog.
    gpu_df['InstanceType'] = None
    gpu_df['GpuInfo'] = gpu_df['AcceleratorName']
    gpu_df['MemoryGiB'] = 0
    return gpu_df


def get_tpu_df():
    """Generates the GCP service catalog for TPUs."""
    tpu_zones = pd.read_csv(TPU_ZONES)
    tpu_pricing = pd.read_csv(TPU_PRICING)

    # Rename the columns.
    tpu_zones = tpu_zones.rename(columns={
        'TPU type': 'AcceleratorName',
        'Zones': 'AvailabilityZone',
    })
    tpu_pricing = tpu_pricing.rename(columns={
        'TPU type': 'AcceleratorName',
        'Spot price': 'SpotPrice',
    })

    # Explode Zones.
    tpu_zones['AvailabilityZone'] = tpu_zones['AvailabilityZone'].apply(
        lambda x: x.split(', '))
    tpu_zones = tpu_zones.explode(column='AvailabilityZone', ignore_index=True)
    zone_to_region = lambda x: x[:-2]
    tpu_zones['Region'] = tpu_zones['AvailabilityZone'].apply(zone_to_region)

    # Merge the two dataframes.
    tpu_df = pd.merge(tpu_zones, tpu_pricing, on=['AcceleratorName', 'Region'])
    tpu_df['AcceleratorCount'] = 1

    # Add columns for the service catalog.
    tpu_df['InstanceType'] = None
    tpu_df['GpuInfo'] = tpu_df['AcceleratorName']
    tpu_df['MemoryGiB'] = 0
    return tpu_df


def get_a2_df():
    a2_pricing = get_vm_price_table(GCP_URL + A2_PRICING_URL)
    cpu_pricing = a2_pricing[a2_pricing['Item'] == 'Predefined vCPUs']
    memory_pricing = a2_pricing[a2_pricing['Item'] == 'Predefined Memory']

    table = []
    for region in a2_pricing['Region'].unique():
        per_cpu_price = cpu_pricing[cpu_pricing['Region'] == region]['Price'].values[0]
        per_cpu_spot_price = cpu_pricing[cpu_pricing['Region'] == region]['SpotPrice'].values[0]
        per_memory_price = memory_pricing[memory_pricing['Region'] == region]['Price'].values[0]
        per_memory_spot_price = memory_pricing[memory_pricing['Region'] == region]['SpotPrice'].values[0]

        for instance_type, spec in A2_INSTANCE_TYPES.items():
            cpu = spec['vCPU']
            memory = spec['MemoryGiB']
            price = per_cpu_price * cpu + per_memory_price * memory
            spot_price = per_cpu_spot_price * cpu + per_memory_spot_price * memory
            table.append([instance_type, memory, price, spot_price, region])
    a2_df = pd.DataFrame(table, columns=['InstanceType', 'MemoryGiB', 'Price', 'SpotPrice', 'Region'])

    a2_df['AcceleratorName'] = None
    a2_df['AcceleratorCount'] = None
    a2_df['GpuInfo'] = None
    a2_df['AvailabilityZone'] = None
    return a2_df


def get_vm_df():
    """Generates the GCP service catalog for host VMs."""
    vm_price_table_urls = get_iframe_sources(GCP_VM_PRICING_URL)
    # Skip the table for "Suspended VM instances".
    vm_price_table_urls = vm_price_table_urls[:-1]
    vm_dfs = [get_vm_price_table(GCP_URL + url) for url in vm_price_table_urls]
    vm_dfs = [df for df in vm_dfs if df is not None and 'InstanceType' in df.columns]

    # Handle A2 instance types separately.
    a2_df = get_a2_df()
    vm_df = pd.concat(vm_dfs + [a2_df])
    return vm_df


if __name__ == '__main__':
    vm_df = get_vm_df()
    gpu_df = get_gpu_df()
    tpu_df = get_tpu_df()
    catalog_df = pd.concat([vm_df, gpu_df, tpu_df])

    # Reorder the columns.
    catalog_df = catalog_df[COLUMNS]

    catalog_df.to_csv('gcp.csv', index=False)
    print('GCP Service Catalog saved to gcp.csv')
