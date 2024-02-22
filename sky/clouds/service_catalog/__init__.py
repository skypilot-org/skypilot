"""Service catalog."""
import collections
import importlib
import typing
from typing import Dict, List, Optional, Set, Tuple, Union

from sky.clouds.service_catalog.config import fallback_to_default_catalog
from sky.clouds.service_catalog.constants import CATALOG_SCHEMA_VERSION
from sky.clouds.service_catalog.constants import HOSTED_CATALOG_DIR_URL
from sky.clouds.service_catalog.constants import LOCAL_CATALOG_DIR
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud
    from sky.clouds.service_catalog import common

CloudFilter = Optional[Union[List[str], str]]
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vsphere', 'cudo', 'fluidstack')


def _map_clouds_catalog(clouds: CloudFilter, method_name: str, *args, **kwargs):
    if clouds is None:
        clouds = list(ALL_CLOUDS)

        # TODO(hemil): Remove this once the common service catalog
        # functions are refactored from clouds/kubernetes.py to
        # kubernetes_catalog.py
        if method_name != 'list_accelerators':
            clouds.remove('kubernetes')
    single = isinstance(clouds, str)
    if single:
        clouds = [clouds]  # type: ignore

    results = []
    for cloud in clouds:
        try:
            cloud_module = importlib.import_module(
                f'sky.clouds.service_catalog.{cloud}_catalog')
        except ModuleNotFoundError:
            raise ValueError(
                'Cannot find module "sky.clouds.service_catalog'
                f'.{cloud}_catalog" for cloud "{cloud}".') from None
        try:
            method = getattr(cloud_module, method_name)
        except AttributeError:
            raise AttributeError(
                f'Module "{cloud}_catalog" does not '
                f'implement the "{method_name}" method') from None
        results.append(method(*args, **kwargs))
    if single:
        return results[0]
    return results


@fallback_to_default_catalog
def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    clouds: CloudFilter = None,
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> 'Dict[str, List[common.InstanceTypeInfo]]':
    """List the names of all accelerators offered by Sky.

    This will include all accelerators offered by Sky, including those
    that may not be available in the user's account.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of instance type offerings. See usage in cli.py.
    """
    results = _map_clouds_catalog(clouds, 'list_accelerators', gpus_only,
                                  name_filter, region_filter, quantity_filter,
                                  case_sensitive, all_regions, require_price)
    if not isinstance(results, list):
        results = [results]
    ret: Dict[str,
              List['common.InstanceTypeInfo']] = collections.defaultdict(list)
    for result in results:
        for gpu, items in result.items():
            ret[gpu] += items
    return dict(ret)


def list_accelerator_counts(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    clouds: CloudFilter = None,
) -> Dict[str, List[int]]:
    """List all accelerators offered by Sky and available counts.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of available counts. See usage in cli.py.
    """
    results = _map_clouds_catalog(clouds,
                                  'list_accelerators',
                                  gpus_only,
                                  name_filter,
                                  region_filter,
                                  quantity_filter,
                                  all_regions=False,
                                  require_price=False)
    if not isinstance(results, list):
        results = [results]
    accelerator_counts: Dict[str, Set[int]] = collections.defaultdict(set)
    for result in results:
        for gpu, items in result.items():
            for item in items:
                accelerator_counts[gpu].add(item.accelerator_count)
    ret: Dict[str, List[int]] = {}
    for gpu, counts in accelerator_counts.items():
        ret[gpu] = sorted(counts)
    return ret


def instance_type_exists(instance_type: str,
                         clouds: CloudFilter = None) -> bool:
    """Check the existence of a instance type."""
    return _map_clouds_catalog(clouds, 'instance_type_exists', instance_type)


def validate_region_zone(
        region_name: Optional[str],
        zone_name: Optional[str],
        clouds: CloudFilter = None) -> Tuple[Optional[str], Optional[str]]:
    """Returns the zone by name."""
    return _map_clouds_catalog(clouds, 'validate_region_zone', region_name,
                               zone_name)


def regions(clouds: CloudFilter = None) -> 'List[cloud.Region]':
    """Returns the list of regions in a Cloud's catalog.
    Each Region object contains a list of Zones, if available.
    """
    return _map_clouds_catalog(clouds, 'regions')


def get_region_zones_for_instance_type(
        instance_type: str,
        use_spot: bool,
        clouds: CloudFilter = None) -> 'List[cloud.Region]':
    """Returns a list of regions for a given instance type."""
    return _map_clouds_catalog(clouds, 'get_region_zones_for_instance_type',
                               instance_type, use_spot)


def get_hourly_cost(instance_type: str,
                    use_spot: bool,
                    region: Optional[str],
                    zone: Optional[str],
                    clouds: CloudFilter = None) -> float:
    """Returns the hourly price of a VM instance in the given region and zone.

    * If (region, zone) == (None, None), return the cheapest hourly price among
        all regions and zones.
    * If (region, zone) == (str, None), return the cheapest hourly price among
        all the zones in the given region.
    * If (region, zone) == (None, str), return the hourly price of the instance
        type in the zone.
    * If (region, zone) == (str, str), zone must be in the region, and the
        function returns the hourly price of the instance type in the zone.
    """
    return _map_clouds_catalog(clouds, 'get_hourly_cost', instance_type,
                               use_spot, region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str,
        clouds: CloudFilter = None) -> Tuple[Optional[float], Optional[float]]:
    """Returns the number of virtual CPUs from a instance type."""
    return _map_clouds_catalog(clouds, 'get_vcpus_mem_from_instance_type',
                               instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              clouds: CloudFilter = None) -> Optional[str]:
    """Returns the cloud's default instance type for given #vCPUs and memory.

    For example, if cpus='4', this method returns the default instance type
    with 4 vCPUs.  If cpus='4+', this method returns the default instance
    type with 4 or more vCPUs.

    If memory_gb_or_ratio is not specified, this method returns the General
    Purpose instance type with the given number of vCPUs. If memory_gb_or_ratio
    is specified, this method returns the cheapest instance type that meets
    the given CPU and memory requirement.
    """
    return _map_clouds_catalog(clouds, 'get_default_instance_type', cpus,
                               memory, disk_tier)


def get_accelerators_from_instance_type(
        instance_type: str,
        clouds: CloudFilter = None) -> Optional[Dict[str, int]]:
    """Returns the accelerators from a instance type."""
    return _map_clouds_catalog(clouds, 'get_accelerators_from_instance_type',
                               instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    clouds: CloudFilter = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return _map_clouds_catalog(clouds, 'get_instance_type_for_accelerator',
                               acc_name, acc_count, cpus, memory, use_spot,
                               region, zone)


def get_accelerator_hourly_cost(
    acc_name: str,
    acc_count: int,
    use_spot: bool,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    clouds: CloudFilter = None,
) -> float:
    """Returns the hourly price of the accelerator in the given region and zone.

    * If (region, zone) == (None, None), return the cheapest hourly price among
        all regions and zones.
    * If (region, zone) == (str, None), return the cheapest hourly price among
        all the zones in the given region.
    * If (region, zone) == (None, str), return the hourly price of the
        accelerator in the zone.
    * If (region, zone) == (str, str), zone must be in the region, and the
        function returns the hourly price of the accelerator in the zone.
    """
    return _map_clouds_catalog(clouds,
                               'get_accelerator_hourly_cost',
                               acc_name,
                               acc_count,
                               use_spot=use_spot,
                               region=region,
                               zone=zone)


def get_region_zones_for_accelerators(
        acc_name: str,
        acc_count: int,
        use_spot: bool,
        clouds: CloudFilter = None) -> 'List[cloud.Region]':
    """Returns a list of regions for a given accelerators."""
    return _map_clouds_catalog(clouds, 'get_region_zones_for_accelerators',
                               acc_name, acc_count, use_spot)


def check_accelerator_attachable_to_host(instance_type: str,
                                         accelerators: Optional[Dict[str, int]],
                                         zone: Optional[str] = None,
                                         clouds: CloudFilter = None) -> None:
    """GCP only: Check if the accelerators can be attached to the host VM.

    Specifically, this function checks the max CPU count and memory of the host
    that the accelerators can be attached to. It is invoked by the optimizer,
    so sky exec will not execute this function.
    """
    _map_clouds_catalog(clouds, 'check_accelerator_attachable_to_host',
                        instance_type, accelerators, zone)


def get_common_gpus() -> List[str]:
    """Returns a list of commonly used GPU names."""
    return [
        'A10',
        'A10G',
        'A100',
        'A100-80GB',
        'H100',
        'K80',
        'L4',
        'M60',
        'P100',
        'T4',
        'V100',
        'V100-32GB',
    ]


def get_tpus() -> List[str]:
    """Returns a list of TPU names."""
    # TODO(wei-lin): refactor below hard-coded list.
    return [
        'tpu-v2-8', 'tpu-v2-32', 'tpu-v2-128', 'tpu-v2-256', 'tpu-v2-512',
        'tpu-v3-8', 'tpu-v3-32', 'tpu-v3-64', 'tpu-v3-128', 'tpu-v3-256',
        'tpu-v3-512', 'tpu-v3-1024', 'tpu-v3-2048'
    ]


def get_image_id_from_tag(tag: str,
                          region: Optional[str] = None,
                          clouds: CloudFilter = None) -> str:
    """Returns the image ID from the tag."""
    return _map_clouds_catalog(clouds, 'get_image_id_from_tag', tag, region)


def is_image_tag_valid(tag: str,
                       region: Optional[str],
                       clouds: CloudFilter = None) -> bool:
    """Validates the image tag."""
    return _map_clouds_catalog(clouds, 'is_image_tag_valid', tag, region)


__all__ = [
    'list_accelerators',
    'list_accelerator_counts',
    'get_region_zones_for_instance_type',
    'get_hourly_cost',
    'get_accelerators_from_instance_type',
    'get_instance_type_for_accelerator',
    'get_accelerator_hourly_cost',
    'get_region_zones_for_accelerators',
    'get_common_gpus',
    'get_tpus',
    # Images
    'get_image_id_from_tag',
    'is_image_tag_valid',
    # Configuration
    'fallback_to_default_catalog',
    # Constants
    'HOSTED_CATALOG_DIR_URL',
    'CATALOG_SCHEMA_VERSION',
    'LOCAL_CATALOG_DIR',
]
