"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
CPU candidates follow Azure capabilities and native failover.
"""
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sky import clouds as cloud_lib
from sky import sky_logging
from sky.adaptors import azure
from sky.catalog import common
from sky.clouds import Azure
from sky.utils import annotations
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

# This list should match the list of regions in
# skypilot image generation Packer script's replication_regions
# sky/clouds/catalog/images/skypilot-azure-cpu-ubuntu.pkr.hcl
COMMUNITY_IMAGE_AVAILABLE_REGIONS = {
    'centralus',
    'eastus',
    'eastus2',
    'northcentralus',
    'southcentralus',
    'westcentralus',
    'westus',
    'westus2',
    'westus3',
}

# The frequency of pulling the latest catalog from the cloud provider.
# Though the catalog update is manual in our skypilot-catalog repo, we
# still want to pull the latest catalog periodically to make sure the
# user is using the latest catalog.
_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('azure/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_image_df = common.read_catalog('azure/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

# CPU eligibility comes from live Azure SKU capabilities.
_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 4


# Query the native SKU catalog once per subscription and request.
@annotations.lru_cache(scope='request', maxsize=64)
def _get_resource_skus(subscription_id: str,
                       region: Optional[str]) -> Tuple[Any, ...]:
    client = azure.get_client('compute', subscription_id)
    kwargs = {} if region is None else {'filter': f'location eq \'{region}\''}
    return tuple(sku for sku in client.resource_skus.list(**kwargs)
                 if sku.resource_type == 'virtualMachines')


def _sku_zones(sku: Any, region: str) -> Optional[List[str]]:
    """None means unavailable; an empty list means a regional offering."""
    region = region.lower()
    if region not in {location.lower() for location in sku.locations or []}:
        return None
    zones: Set[str] = set()
    for info in sku.location_info or []:
        if info.location.lower() == region:
            zones.update(info.zones or [])
    has_zones = bool(zones)
    for restriction in sku.restrictions or []:
        info = restriction.restriction_info
        locations = (getattr(info, 'locations', None) or
                     getattr(restriction, 'values', None) or
                     getattr(restriction, 'values_property', None) or [])
        if locations and region not in {loc.lower() for loc in locations}:
            continue
        if restriction.type == 'Location':
            return None
        if restriction.type == 'Zone':
            zones.difference_update(getattr(info, 'zones', None) or [])
    if has_zones and not zones:
        # Exhausted zonal offerings must not become regional-only offerings.
        return None
    return sorted(zones)


def get_instance_type_zones(instance_type: str,
                            region: str) -> Optional[List[str]]:
    zones: Set[str] = set()
    offered = False
    for sku in _get_resource_skus(azure.get_subscription_id(), region):
        if sku.name == instance_type:
            available = _sku_zones(sku, region)
            if available is not None:
                offered = True
                zones.update(available)
    return sorted(zones) if offered else None


def get_instance_type_capabilities(instance_type: str,
                                   region: str) -> Dict[str, str]:
    for sku in _get_resource_skus(azure.get_subscription_id(), region):
        if sku.name == instance_type and _sku_zones(sku, region) is not None:
            return {cap.name: cap.value for cap in sku.capabilities or []}
    return {}


def get_cpu_instance_types(region: Optional[str]) -> Set[str]:
    """CPU SKUs compatible with Azure's default x64 images."""
    result = set()
    for sku in _get_resource_skus(azure.get_subscription_id(), region):
        locations = [region] if region is not None else sku.locations or []
        if not any(_sku_zones(sku, loc) is not None for loc in locations):
            continue
        caps = {cap.name: cap.value for cap in sku.capabilities or []}
        if caps.get('CpuArchitectureType', '').lower() != 'x64':
            continue
        if float(caps.get('GPUs', '0')) != 0:
            continue
        result.add(sku.name)
    return result


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    # Azure's numeric zone labels are region-relative.
    if zone is not None and region is None:
        raise ValueError('Azure requires a region when specifying a zone.')
    region, _ = common.validate_region_zone_impl('azure', _df, region, None)
    if zone is not None:
        assert region is not None
        zone = str(zone)
        zones: Set[str] = set()
        for sku in _get_resource_skus(azure.get_subscription_id(), region):
            zones.update(_sku_zones(sku, region) or [])
        if zone not in zones:
            raise ValueError(
                f'Invalid Azure zone {zone!r} in region {region!r} '
                'for the current subscription.')
    return region, zone


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    # Azure prices are regional, including zonal VMs.
    del zone
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       None)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_instance_types_for_cpus_mem(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> List[str]:
    """Expose every compatible CPU SKU to native failover."""
    del local_disk
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    if memory is None:
        memory = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    # Share the catalog filters to preserve CPU/memory constraint syntax.
    # pylint: disable=protected-access
    df = common._filter_region_zone(_df, region, None)
    df = common._filter_with_cpus(df, cpus)
    df = common._filter_with_mem(df, memory)
    # pylint: enable=protected-access
    df = df[df['InstanceType'].isin(get_cpu_instance_types(region))]
    if zone is not None:
        region, zone = validate_region_zone(region, zone)
        assert region is not None
        df = df.loc[df['InstanceType'].apply(
            lambda name: zone in (get_instance_type_zones(name, region) or []))]
    df = df.loc[df['InstanceType'].apply(
        lambda name: Azure.check_disk_tier(name, disk_tier)[0])]
    price = 'SpotPrice' if use_spot else 'Price'
    df = df.dropna(subset=[price])
    if max_hourly_cost is not None:
        df = df[df[price] <= max_hourly_cost]
    return df.sort_values(price)['InstanceType'].drop_duplicates().tolist()


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    instances = get_instance_types_for_cpus_mem(cpus, memory, disk_tier,
                                                local_disk, region, zone,
                                                use_spot, max_hourly_cost)
    return instances[0] if instances else None


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None
) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    del local_disk  # unused
    # Filter native zone offerings before regional pricing.
    df = _df
    if zone is not None:
        region, zone = validate_region_zone(region, zone)
        assert region is not None
        df = df.loc[df['InstanceType'].apply(
            lambda name: zone in (get_instance_type_zones(name, region) or []))]
    return common.get_instance_type_for_accelerator_impl(
        df=df,
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=None,  # the price catalog has regional rows.
        max_hourly_cost=max_hourly_cost)


def get_region_zones_for_instance_type(
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None) -> List[cloud_lib.Region]:
    # Join regional prices to subscription-specific offerings.
    df = _df[_df['InstanceType'] == instance_type]
    if region is not None:
        df = df[df['Region'] == region]
    skus = [
        sku for sku in _get_resource_skus(azure.get_subscription_id(), region)
        if sku.name == instance_type
    ]
    result = []
    for candidate in common.get_region_zones(df, use_spot):
        offered = False
        zones: Set[str] = set()
        for sku in skus:
            available = _sku_zones(sku, candidate.name)
            if available is not None:
                offered = True
                zones.update(available)
        if offered:
            if zones:
                candidate.set_zones([cloud_lib.Zone(z) for z in sorted(zones)])
            result.append(candidate)
    return result


def get_gen_version_from_instance_type(instance_type: str) -> Optional[int]:
    return _df[_df['InstanceType'] == instance_type]['Generation'].iloc[0]


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Azure offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('Azure', _df, gpus_only, name_filter,
                                         region_filter, quantity_filter,
                                         case_sensitive, all_regions)


def get_image_id_from_tag(tag: str,
                          region: Optional[str],
                          use_base_image: bool = False) -> Optional[str]:
    """Returns the image id from the tag."""
    global _image_df
    column = 'BaseImageId' if use_base_image else 'ImageId'
    image_id = common.get_image_id_from_tag_impl(
        _image_df.assign(ImageId=_image_df[column]), tag, region)
    if image_id is None:
        # Refresh the image catalog and try again, if the image tag is not
        # found.
        logger.debug('Refreshing the image catalog and trying again.')
        _image_df = common.read_catalog('azure/images.csv',
                                        pull_frequency_hours=0)
        image_id = common.get_image_id_from_tag_impl(
            _image_df.assign(ImageId=_image_df[column]), tag, region)
    return image_id


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    # Azure images are not region-specific.
    del region  # Unused.
    return common.is_image_tag_valid_impl(_image_df, tag, None)
