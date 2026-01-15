"""A script that queries Nebius API to get instance types and pricing info.

This script takes about 1 minute to finish.
"""
import csv
from dataclasses import dataclass
import decimal
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from sky.adaptors import nebius
from sky.adaptors.nebius import billing
from sky.adaptors.nebius import compute
from sky.adaptors.nebius import iam
from sky.adaptors.nebius import nebius_common

logger = logging.getLogger(__name__)

TIMEOUT = 10
PARENT_ID_TEMPLATE = 'project-{}public-images'
ACCELERATOR_MANUFACTURER = 'NVIDIA'


@dataclass
class PresetInfo:
    """Represents information about a specific compute preset,
    including its pricing.

    Attributes:
        region (str): The geographical region where the preset is available.
        fullname (str): The full name of the preset, a combination of platform
            and preset name.
        name (str): The name of the preset.
        platform_name (str): The name of the platform the preset belongs to.
        gpu (int): The number of GPUs in the preset.
        vcpu (int): The number of virtual CPUs in the preset.
        gpu_memory_gibibytes (int): size of gpu memory in GiB.
        memory_gib (int): The amount of memory in GiB in the preset.
        accelerator_manufacturer (str | None): The manufacturer of the
            accelerator (e.g., "NVIDIA"), or None if no accelerator.
        accelerator_name (str | None): The name of the accelerator
            (e.g., "H100"), or None if no accelerator.
        price_hourly (decimal.Decimal): The hourly price of the preset.
        spot_price (decimal.Decimal): The spot (preemptible) price
            of the preset.
    """

    region: str
    fullname: str
    name: str
    platform_name: str
    gpu: int
    vcpu: int
    gpu_memory_gibibytes: int
    memory_gib: int
    accelerator_manufacturer: Optional[str]
    accelerator_name: Optional[str]
    price_hourly: decimal.Decimal
    spot_price: decimal.Decimal


def _format_decimal(value: decimal.Decimal) -> str:
    """Formats a decimal value to a string with at least two decimal places,
    removing trailing zeros and ensuring a two-digit decimal part.

    Args:
        value (decimal.Decimal): The decimal value to format.

    Returns:
        str: The formatted string representation of the decimal.
    """
    formatted_value = f'{value:f}'
    integer_part, decimal_part = formatted_value.split(
        '.') if '.' in formatted_value else (formatted_value, '')
    if len(decimal_part) < 2:
        decimal_part += '0' * (2 - len(decimal_part))

    return f'{integer_part}.{decimal_part}'


def _estimate_platforms(platforms: List[Any], parent_id: str,
                        region: str) -> List[PresetInfo]:
    """Collects specifications for all presets on the given platforms to form a
    batch price request. It then sends the request and processes the responses
    to create a list of PresetInfo objects.

    Args:
        platforms (List[Platform]): A List of compute platforms to estimate
        prices for.
        parent_id (str): The parent ID used for resource metadata
        in the estimate request.
        region (str): The region associated with the platforms.

    Returns:
        List[PresetInfo]: A list of PresetInfo objects containing details and
        estimated prices for each preset.
    """

    calculator_service = billing().CalculatorServiceClient(nebius.sdk())
    futures = []

    for platform in platforms:
        platform_name = platform.metadata.name

        for preset in platform.spec.presets:
            # Form the specification for the price request
            estimate_spec = billing().ResourceSpec(
                compute_instance_spec=compute().CreateInstanceRequest(
                    metadata=nebius_common().ResourceMetadata(
                        parent_id=parent_id,),
                    spec=compute().InstanceSpec(
                        resources=compute().ResourcesSpec(
                            platform=platform_name,
                            preset=preset.name,
                        )),
                ))
            price_request = billing().EstimateBatchRequest(
                resource_specs=[estimate_spec])

            # Form the specification for the spot price request
            spot_estimate_spec = billing().ResourceSpec(
                compute_instance_spec=compute().CreateInstanceRequest(
                    metadata=nebius_common().ResourceMetadata(
                        parent_id=parent_id,),
                    spec=compute().InstanceSpec(
                        resources=compute().ResourcesSpec(
                            platform=platform_name,
                            preset=preset.name,
                        ),
                        preemptible=compute().PreemptibleSpec(priority=1),
                    ),
                ))
            spot_price_request = billing().EstimateBatchRequest(
                resource_specs=[spot_estimate_spec])

            # Start future for each preset
            futures.append((
                platform,
                preset,
                calculator_service.estimate_batch(price_request,
                                                  timeout=TIMEOUT),
                calculator_service.estimate_batch(spot_price_request,
                                                  timeout=TIMEOUT),
            ))

    # wait all futures to complete and collect results
    result = []
    for platform, preset, future, future_spot in futures:
        platform_name = platform.metadata.name
        result.append(
            PresetInfo(
                region=region,
                fullname=f'{platform_name}_{preset.name}',
                name=preset.name,
                platform_name=platform_name,
                gpu=preset.resources.gpu_count or 0,
                vcpu=preset.resources.vcpu_count,
                gpu_memory_gibibytes=platform.spec.gpu_memory_gibibytes,
                memory_gib=preset.resources.memory_gibibytes,
                accelerator_manufacturer=ACCELERATOR_MANUFACTURER
                if platform_name.startswith('gpu-') else '',
                accelerator_name=platform_name.split('-')[1].upper()
                if platform_name.startswith('gpu-') else '',
                price_hourly=decimal.Decimal(
                    future.wait().hourly_cost.general.total.cost),
                spot_price=decimal.Decimal(
                    future_spot.wait().hourly_cost.general.total.cost),
            ))

    return result


def _write_preset_prices(presets: List[PresetInfo], output_file: str) -> None:
    """Writes the provided preset information to a CSV file.

    Args:
        presets (List[PresetInfo]): A list of PresetInfo objects to write.
        output_file (str): The path to the output CSV file.
    """
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    # Set up the CSV writer to output to stdout
    with open(output_file, 'w', encoding='utf-8') as out:
        header = [
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'vCPUs',
            'MemoryGiB',
            'Price',
            'Region',
            'GpuInfo',
            'SpotPrice',
        ]
        writer = csv.DictWriter(out, fieldnames=header)
        writer.writeheader()
        # logger.info(presets)
        for preset in sorted(presets,
                             key=lambda x:
                             (bool(x.gpu), x.region, x.platform_name, x.vcpu)):
            gpu_info = ''
            if preset.gpu > 0 and preset.accelerator_name:
                vram = preset.gpu_memory_gibibytes * 1024
                gpu_info_dict = {
                    'Gpus': [{
                        'Name': preset.accelerator_name,
                        'Manufacturer': preset.accelerator_manufacturer,
                        'Count': preset.gpu,
                        'MemoryInfo': {
                            'SizeInMiB': vram
                        },
                    }],
                    'TotalGpuMemoryInMiB': vram * preset.gpu,
                }
                gpu_info = json.dumps(gpu_info_dict).replace('"', '\'')

            writer.writerow({
                'InstanceType': preset.fullname,
                'AcceleratorName': preset.accelerator_name,
                'AcceleratorCount': preset.gpu,
                'vCPUs': preset.vcpu,
                'MemoryGiB': preset.memory_gib,
                'Price': _format_decimal(preset.price_hourly),
                'Region': preset.region,
                'GpuInfo': gpu_info,
                'SpotPrice': _format_decimal(preset.spot_price)
                             if preset.spot_price else '',
            })


def _fetch_platforms_for_project(project_id: str) -> List[Any]:
    """Fetches all available compute platforms for a given project.

    Args:
        project_id (str): The ID of the project to fetch platforms from.

    Returns:
        List[ComputePlatform]: A list of ComputePlatform objects available
        in the project.
    """
    platform_service = compute().PlatformServiceClient(nebius.sdk())

    platform_request = compute().ListPlatformsRequest(page_size=999,
                                                      parent_id=project_id)
    platform_response = platform_service.list(platform_request,
                                              timeout=TIMEOUT).wait()

    return platform_response.items


def _get_regions_map() -> Dict[str, str]:
    """Maps region codes to their full names by iterating through tenants and
     projects.

    Returns:
        dict[str, str]: A dictionary where keys are region codes (e.g., "e00")
                        and values are full region names (e.g., "eu-north1").
    """
    result = {}
    response = iam().TenantServiceClient(nebius.sdk()).list(
        iam().ListTenantsRequest(), timeout=TIMEOUT).wait()

    for tenant in response.items:
        projects = (iam().ProjectServiceClient(nebius.sdk()).list(
            iam().ListProjectsRequest(parent_id=tenant.metadata.id),
            timeout=TIMEOUT).wait())

        for project in projects.items:
            match = re.match(r'^project-([a-z0-9]{3})', project.metadata.id)
            if match is None:
                logger.error('Could not parse project id %s',
                             project.metadata.id)
                continue
            result[match.group(1)] = project.status.region

    return result


def _get_all_platform_prices() -> List[PresetInfo]:
    """Orchestrates fetching specifications and prices for all platforms across
     all regions.

    This function first retrieves a map of region codes to full names, then
    iterates through each region, fetches available platforms for
    the corresponding project ID, and finally estimates prices for all presets
    on those platforms.

    Returns:
        List[PresetInfo]: A consolidated list of PresetInfo objects for all
                        platforms and presets across all regions.
    """

    # Get regions codes to names
    regions_map = _get_regions_map()

    presets = []

    for region_code in sorted(regions_map.keys()):
        project_id = PARENT_ID_TEMPLATE.format(region_code)
        region = regions_map[region_code]
        logger.info('Processing region: %s (project: %s)...', region,
                    project_id)

        platforms = _fetch_platforms_for_project(project_id)
        if not platforms:
            logger.warning('No platforms found in region %s', region)
            continue

        presets.extend(
            _estimate_platforms(platforms=platforms,
                                parent_id=project_id,
                                region=region))

    return presets


def main() -> None:
    """Main function to fetch and write Nebius platform prices to a CSV file.

    It initializes the SDK, fetches all platform prices, and then writes them
    to the specified CSV file.
    """

    output_file = 'nebius/vms.csv'

    # Fetch presets and estimate
    presets = _get_all_platform_prices()

    # Write CSV
    _write_preset_prices(presets, output_file)

    logger.info('Done!')


if __name__ == '__main__':
    main()
