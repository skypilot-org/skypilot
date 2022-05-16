"""Service catalog."""
import collections
import importlib
import typing
from typing import Dict, List, Optional, Tuple, Union

if typing.TYPE_CHECKING:
    from sky.clouds import cloud
    from sky.clouds.service_catalog import common

CloudFilter = Optional[Union[List[str], str]]
_ALL_CLOUDS = ('aws', 'azure', 'gcp')


def _map_clouds_catalog(clouds: CloudFilter, method_name, *args, **kwargs):
    if clouds is None:
        clouds = list(_ALL_CLOUDS)
    single = isinstance(clouds, str)
    if single:
        clouds = [clouds]

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


def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    clouds: CloudFilter = None,
) -> 'Dict[str, List[common.InstanceTypeInfo]]':
    """List the names of all accelerators offered by Sky.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of instance type offerings. See usage in cli.py.
    """
    results = _map_clouds_catalog(clouds, 'list_accelerators', gpus_only,
                                  name_filter)
    if not isinstance(results, list):
        results = [results]
    ret = collections.defaultdict(list)
    for result in results:
        for gpu, items in result.items():
            ret[gpu] += items
    return dict(ret)


def list_accelerator_counts(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    clouds: CloudFilter = None,
) -> Dict[str, List[int]]:
    """List all accelerators offered by Sky and available counts.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of available counts. See usage in cli.py.
    """
    results = _map_clouds_catalog(clouds, 'list_accelerators', gpus_only,
                                  name_filter)
    if not isinstance(results, list):
        results = [results]
    ret = collections.defaultdict(set)
    for result in results:
        for gpu, items in result.items():
            for item in items:
                ret[gpu].add(item.accelerator_count)
    for gpu, counts in ret.items():
        ret[gpu] = sorted(counts)
    return ret


def instance_type_exists(instance_type: str,
                         clouds: CloudFilter = None) -> bool:
    """Check the existence of a instance type."""
    return _map_clouds_catalog(clouds, 'instance_type_exists', instance_type)


def region_exists(region_name: str, clouds: CloudFilter = None) -> bool:
    """Returns the region by name."""
    return _map_clouds_catalog(clouds, 'region_exists', region_name)


def get_region_zones_for_instance_type(
        instance_type: str,
        use_spot: bool,
        clouds: CloudFilter = None) -> 'List[cloud.Region]':
    """Returns a list of regions for a given instance type."""
    return _map_clouds_catalog(clouds, 'get_region_zones_for_instance_type',
                               instance_type, use_spot)


def get_hourly_cost(instance_type: str,
                    region: Optional[str],
                    use_spot: bool,
                    clouds: CloudFilter = None):
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return _map_clouds_catalog(clouds, 'get_hourly_cost', instance_type, region,
                               use_spot)


def get_accelerators_from_instance_type(
        instance_type: str,
        clouds: CloudFilter = None) -> Optional[Dict[str, int]]:
    """Returns the accelerators from a instance type."""
    return _map_clouds_catalog(clouds, 'get_accelerators_from_instance_type',
                               instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    clouds: CloudFilter = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    return _map_clouds_catalog(clouds, 'get_instance_type_for_accelerator',
                               acc_name, acc_count)


def get_accelerator_hourly_cost(
    acc_name: str,
    acc_count: int,
    use_spot: bool,
    clouds: CloudFilter = None,
) -> float:
    """Returns the hourly cost with the accelerators."""
    return _map_clouds_catalog(clouds, 'get_accelerator_hourly_cost', acc_name,
                               acc_count, use_spot=use_spot)


def get_region_zones_for_accelerators(
        acc_name: str,
        acc_count: int,
        use_spot: bool,
        clouds: CloudFilter = None) -> 'List[cloud.Region]':
    """Returns a list of regions for a given accelerators."""
    return _map_clouds_catalog(clouds, 'get_region_zones_for_accelerators',
                               acc_name, acc_count, use_spot)


def get_common_gpus() -> List[str]:
    """Returns a list of commonly used GPU names."""
    return [
        'V100', 'V100-32GB', 'A100', 'A100-80GB', 'P100', 'K80', 'T4', 'M60'
    ]


def get_tpus() -> List[str]:
    """Returns a list of TPU names."""
    return ['tpu-v2-8', 'tpu-v2-32', 'tpu-v2-128', 'tpu-v3-8']


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
]
