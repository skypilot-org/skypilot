"""Dynamic local service catalog for OpenStack."""

from collections.abc import Mapping
import csv
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import typing
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

from sky.adaptors import common as adaptors_common
from sky.adaptors import openstack as openstack_adaptor
from sky.catalog import common

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')

_CLOUD = 'openstack'
_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 4
_CATALOG_COLUMNS = (
    'InstanceType',
    'FlavorId',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Price',
    'SpotPrice',
    'Region',
    'AvailabilityZone',
    'RootDiskGiB',
)


class _CatalogContext(NamedTuple):
    cloud: str
    project_id: str
    region: str


_context_lock = threading.RLock()
_active_context: Optional[_CatalogContext] = None
_active_catalog_path: Optional[str] = None
_active_context_signature: Optional[Tuple[int, int, int, int]] = None
_active_catalog_signature: Optional[Tuple[int, int, int, int]] = None
_df: Optional['pd.DataFrame'] = None


def _active_context_path() -> str:
    return common.get_catalog_path(os.path.join(_CLOUD, 'active-context.json'))


def _normalize_required(value: Any, description: str) -> str:
    if value is None:
        raise ValueError(f'OpenStack {description} could not be determined.')
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f'OpenStack {description} could not be determined.')
    return normalized


def _normalize_optional(value: Any, description: str) -> Optional[str]:
    if value is None:
        return None
    return _normalize_required(value, description)


def _context(cloud: Any, project_id: Any, region: Any) -> _CatalogContext:
    return _CatalogContext(
        cloud=_normalize_required(cloud, 'cloud profile'),
        project_id=_normalize_required(project_id, 'project ID'),
        region=_normalize_required(region, 'region'),
    )


def get_catalog_path(cloud: str,
                     project_id: str,
                     region: str,
                     availability_zone: Optional[str] = None) -> str:
    """Returns the isolated local catalog path for an OpenStack project."""
    del availability_zone  # Zones are rows within a per-region catalog.
    catalog_context = _context(cloud, project_id, region)
    cache_key = '\0'.join(catalog_context).encode('utf-8')
    digest = hashlib.sha256(cache_key).hexdigest()[:16]
    profile = re.sub(r'[^A-Za-z0-9_.-]+', '-',
                     catalog_context.cloud).strip('-') or 'cloud'
    filename = os.path.join(_CLOUD, f'{profile}-{digest}.csv')
    return common.get_catalog_path(filename)


def set_catalog_context(cloud: str,
                        project_id: str,
                        region: str,
                        availability_zone: Optional[str] = None) -> str:
    """Selects a refreshed catalog and invalidates the in-memory cache."""
    del availability_zone  # Zones are selected by service-catalog filters.
    catalog_context = _context(cloud, project_id, region)
    catalog_path = get_catalog_path(*catalog_context)
    global _active_context, _active_catalog_path, _active_context_signature
    global _active_catalog_signature, _df
    with _context_lock:
        _active_context = catalog_context
        _active_catalog_path = catalog_path
        _df = None
        _write_active_context_atomically(catalog_context)
        _active_context_signature = _file_signature(_active_context_path())
        _active_catalog_signature = None
    return catalog_path


def _file_signature(path: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _write_active_context_atomically(context: _CatalogContext) -> None:
    path = _active_context_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',
                                         encoding='utf-8',
                                         dir=directory,
                                         prefix='openstack-context-',
                                         suffix='.tmp',
                                         delete=False) as file:
            temporary_path = file.name
            json.dump(context._asdict(), file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _restore_active_context() -> bool:
    path = _active_context_path()
    try:
        with open(path, 'r', encoding='utf-8') as file:
            raw_context = json.load(file)
        context = _context(raw_context['cloud'], raw_context['project_id'],
                           raw_context['region'])
    except FileNotFoundError:
        return False
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError('OpenStack catalog context is invalid. Run '
                           '`sky check openstack` again.') from exc

    catalog_path = get_catalog_path(*context)
    if not os.path.exists(catalog_path):
        raise RuntimeError('OpenStack catalog is not initialized. '
                           'Run `sky check openstack` first.')

    global _active_context, _active_catalog_path, _active_context_signature
    global _active_catalog_signature, _df
    _active_context = context
    _active_catalog_path = catalog_path
    _active_context_signature = _file_signature(path)
    _active_catalog_signature = None
    _df = None
    return True


def _get_field(resource: Any, *names: str) -> Any:
    for name in names:
        if isinstance(resource, Mapping) and name in resource:
            return resource[name]
        value = getattr(resource, name, None)
        if value is not None:
            return value
    return None


def _is_available_zone(zone: Any) -> bool:
    state = _get_field(zone, 'state', 'zone_state')
    available = _get_field(state, 'available')
    if isinstance(available, str):
        return available.strip().lower() not in ('false', '0', 'no')
    return available is not False


def _available_zone_names(connection: Any) -> List[str]:
    zones = set()
    for zone in connection.compute.availability_zones():
        if not _is_available_zone(zone):
            continue
        name = _get_field(zone, 'name', 'zone_name')
        if name is not None:
            zones.add(_normalize_required(name, 'availability zone'))
    zones = {zone for zone in zones if zone.lower() != 'internal'}
    if not zones:
        raise ValueError('OpenStack availability zone could not be determined.')
    return sorted(zones)


def _number(value: Any,
            description: str,
            flavor_name: str,
            *,
            allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f'OpenStack flavor {flavor_name!r} has invalid {description}: '
            f'{value!r}.') from None
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError(
            f'OpenStack flavor {flavor_name!r} has invalid {description}: '
            f'{value!r}.')
    return number


def _flavor_rows(connection: Any, region: str,
                 zones: List[str]) -> List[Dict[str, Any]]:
    rows = []
    for flavor in connection.compute.flavors(details=True):
        name = _normalize_required(_get_field(flavor, 'name'), 'flavor name')
        flavor_id = _normalize_required(_get_field(flavor, 'id'), 'flavor ID')
        vcpus = _number(_get_field(flavor, 'vcpus'), 'vCPU count', name)
        memory_mib = _number(_get_field(flavor, 'ram'), 'RAM size', name)
        root_disk_gib = _number(_get_field(flavor, 'disk'),
                                'root disk size',
                                name,
                                allow_zero=True)
        for zone in zones:
            rows.append({
                'InstanceType': name,
                'FlavorId': flavor_id,
                'AcceleratorName': None,
                'AcceleratorCount': None,
                'vCPUs': vcpus,
                'MemoryGiB': memory_mib / 1024.0,
                'GpuInfo': None,
                # OpenStack has no standard pricing API. Zero is an internal
                # placeholder; OpenStack is only considered when explicit.
                'Price': 0.0,
                'SpotPrice': None,
                'Region': region,
                'AvailabilityZone': zone,
                'RootDiskGiB': root_disk_gib,
            })
    return sorted(
        rows,
        key=lambda row:
        (row['InstanceType'], row['AvailabilityZone'], row['FlavorId']))


def _write_catalog_atomically(path: str, rows: List[Dict[str, Any]]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',
                                         encoding='utf-8',
                                         newline='',
                                         dir=directory,
                                         prefix='openstack-',
                                         suffix='.tmp',
                                         delete=False) as file:
            temporary_path = file.name
            writer = csv.DictWriter(file, fieldnames=_CATALOG_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def refresh_catalog(cloud: str,
                    project_id: Optional[str] = None,
                    region: Optional[str] = None,
                    availability_zone: Optional[str] = None,
                    connection: Optional[Any] = None) -> str:
    """Fetches Nova flavors and atomically refreshes the local catalog."""
    cloud = _normalize_required(cloud, 'cloud profile')
    region = _normalize_optional(region, 'region')
    requested_zone = _normalize_optional(availability_zone, 'availability zone')
    if connection is None:
        connection = openstack_adaptor.get_connection(cloud, region)

    if project_id is None:
        project_id = getattr(connection, 'current_project_id', None)
    project_id = _normalize_required(project_id, 'project ID')
    if region is None:
        connection_config = getattr(connection, 'config', None)
        region = _get_field(connection_config, 'region_name')
    region = _normalize_required(region, 'region')

    zones = _available_zone_names(connection)
    if requested_zone is not None and requested_zone not in zones:
        raise ValueError(f'OpenStack availability zone {requested_zone!r} is '
                         'not available.')
    rows = _flavor_rows(connection, region, zones)
    path = get_catalog_path(cloud, project_id, region)
    _write_catalog_atomically(path, rows)
    set_catalog_context(cloud, project_id, region)
    return path


def _get_df() -> 'pd.DataFrame':
    global _active_catalog_signature, _df
    with _context_lock:
        context_signature = _file_signature(_active_context_path())
        if context_signature is None:
            raise RuntimeError('OpenStack catalog is not initialized. '
                               'Run `sky check openstack` first.')
        if (_active_catalog_path is None or
                context_signature != _active_context_signature):
            _restore_active_context()
        catalog_path = _active_catalog_path
        if catalog_path is None:
            raise RuntimeError('OpenStack catalog is not initialized. '
                               'Run `sky check openstack` first.')
        catalog_signature = _file_signature(catalog_path)
        if catalog_signature is None:
            raise RuntimeError('OpenStack catalog is not initialized. '
                               'Run `sky check openstack` first.')
        if _df is None or catalog_signature != _active_catalog_signature:
            _df = pd.read_csv(catalog_path)
            _active_catalog_signature = catalog_signature
        return _df


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    del tag, region
    # The first OpenStack integration accepts raw Glance image IDs or names.
    return False


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl(_CLOUD, _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    assert not use_spot, 'OpenStack does not support spot instances.'
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[Any] = None,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    use_spot: bool = False,
    max_hourly_cost: Optional[float] = None,
    min_disk_size: Optional[float] = None,
) -> Optional[str]:
    del disk_tier, local_disk
    assert not use_spot, 'OpenStack does not support spot instances.'
    if max_hourly_cost is not None:
        # OpenStack has no standard price API, so a cost ceiling cannot be
        # evaluated safely.
        return None
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    if memory is None:
        memory = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    selection_df = _get_df().sort_values(
        by=['vCPUs', 'MemoryGiB', 'RootDiskGiB', 'InstanceType']).copy()
    if min_disk_size is not None:
        selection_df = selection_df[
            selection_df['RootDiskGiB'] >= min_disk_size]
    # Price is unknown for every flavor. Use a resource-size rank only for
    # deterministic smallest-satisfying selection inside the common filter.
    selection_df['Price'] = range(len(selection_df))
    return common.get_instance_type_for_cpus_mem_impl(selection_df, cpus,
                                                      memory, region, zone,
                                                      use_spot, None)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    if not instance_type_exists(instance_type):
        raise ValueError(f'No instance type {instance_type} found.')
    return None


def get_arch_from_instance_type(instance_type: str) -> Optional[str]:
    return common.get_arch_from_instance_type_impl(_get_df(), instance_type)


def get_local_disk_from_instance_type(instance_type: str) -> Optional[str]:
    return common.get_local_disk_from_instance_type_impl(
        _get_df(), instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: Union[int, float],
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    del (acc_name, acc_count, cpus, memory, use_spot, local_disk, region, zone,
         max_hourly_cost)
    return None, []


def _region_zone_rows(df: 'pd.DataFrame') -> 'pd.DataFrame':
    columns = ['Region', 'AvailabilityZone', 'Price', 'SpotPrice']
    return df[columns].drop_duplicates()


def regions() -> List['cloud.Region']:
    return common.get_region_zones(_region_zone_rows(_get_df()), use_spot=False)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _get_df()
    df = df[df['InstanceType'] == instance_type]
    return common.get_region_zones(_region_zone_rows(df), use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    del (gpus_only, name_filter, region_filter, quantity_filter, case_sensitive,
         all_regions, require_price)
    return {}


def get_root_disk_size(instance_type: str) -> float:
    df = _get_df()
    df = df[df['InstanceType'] == instance_type]
    if df.empty:
        raise ValueError(f'No instance type {instance_type} found.')
    disk_sizes = df['RootDiskGiB'].dropna().unique()
    if len(disk_sizes) != 1:
        raise ValueError('Cannot determine the root disk size of OpenStack '
                         f'flavor {instance_type!r}.')
    return float(disk_sizes[0])


def check_disk_size(instance_type: str, disk_size: float) -> None:
    root_disk_size = get_root_disk_size(instance_type)
    if disk_size > root_disk_size:
        raise ValueError(
            f'OpenStack flavor {instance_type} has a {root_disk_size:g} GiB '
            f'root disk, smaller than requested disk size {disk_size:g} GiB.')
