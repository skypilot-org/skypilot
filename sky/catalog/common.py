"""Common utilities for service catalog."""
import ast
import difflib
import hashlib
import os
import tempfile
import time
import typing
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple, Union

import filelock

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.clouds import cloud as cloud_lib
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import pandas as pd
    import requests
else:
    pd = adaptors_common.LazyImport('pandas')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

_ABSOLUTE_VERSIONED_CATALOG_DIR = os.path.join(
    os.path.expanduser(constants.CATALOG_DIR), constants.CATALOG_SCHEMA_VERSION)
os.makedirs(_ABSOLUTE_VERSIONED_CATALOG_DIR, exist_ok=True)


class InstanceTypeInfo(NamedTuple):
    """Instance type information.

    - cloud: Cloud name.
    - instance_type: String that can be used in YAML to specify this instance
      type. E.g. `p3.2xlarge`.
    - accelerator_name: Canonical name of the accelerator. E.g. `V100`.
    - accelerator_count: Number of accelerators offered by this instance type.
    - cpu_count: Number of vCPUs offered by this instance type.
    - device_memory: Device memory in GiB.
    - memory: Instance memory in GiB.
    - price: Regular instance price per hour (cheapest across all regions).
    - spot_price: Spot instance price per hour (cheapest across all regions).
    - region: Region where this instance type belongs to.
    """
    cloud: str
    instance_type: Optional[str]
    accelerator_name: str
    accelerator_count: float
    cpu_count: Optional[float]
    device_memory: Optional[float]
    memory: Optional[float]
    price: float
    spot_price: float
    region: str


def get_catalog_path(filename: str) -> str:
    catalog_path = os.path.join(_ABSOLUTE_VERSIONED_CATALOG_DIR, filename)
    os.makedirs(os.path.dirname(catalog_path), exist_ok=True)
    return catalog_path


def is_catalog_modified(filename: str) -> bool:
    # Check the md5 of the file to see if it has changed.
    catalog_path = get_catalog_path(filename)
    meta_path = os.path.join(_ABSOLUTE_VERSIONED_CATALOG_DIR, '.meta', filename)
    md5_filepath = meta_path + '.md5'
    if os.path.exists(md5_filepath):
        file_md5 = common_utils.hash_file(catalog_path, 'md5').hexdigest()
        with open(md5_filepath, 'r', encoding='utf-8') as f:
            last_md5 = f.read()
        return file_md5 != last_md5
    else:
        # If the md5 file does not exist, it means the catalog was manually
        # populated instead of being fetched from the cloud.
        return True


def get_modified_catalog_file_mounts() -> Dict[str, str]:
    """Returns a dict of catalogs which have been modified locally.

    The dictionary maps the remote catalog path (relative) to the local path of
    the modified catalog (absolute). Can be used directly as file_mounts for a
    Task.

    Used to determine which catalogs to upload to the controllers when they
    are provisioned.
    """

    def _get_modified_catalogs() -> List[str]:
        """Returns a list of modified catalogs relative to the catalog dir."""
        modified_catalogs = []
        for cloud_name in constants.ALL_CLOUDS:
            cloud_catalog_dir = os.path.join(_ABSOLUTE_VERSIONED_CATALOG_DIR,
                                             cloud_name)
            if not os.path.exists(cloud_catalog_dir):
                continue
            # Iterate over all csvs cloud's catalog directory
            for file in os.listdir(cloud_catalog_dir):
                if file.endswith('.csv'):
                    filename = os.path.join(cloud_name,
                                            file)  # e.g., aws/vms.csv
                    if is_catalog_modified(filename):
                        modified_catalogs.append(filename)
        return modified_catalogs

    modified_catalog_list = _get_modified_catalogs()
    modified_catalog_path_map = {}  # Map of remote: local catalog paths
    for catalog in modified_catalog_list:
        # Use relative paths for remote to handle varying usernames on the cloud
        remote_path = os.path.join(constants.CATALOG_DIR,
                                   constants.CATALOG_SCHEMA_VERSION, catalog)
        local_path = os.path.expanduser(remote_path)
        modified_catalog_path_map[remote_path] = local_path
    return modified_catalog_path_map


class LazyDataFrame:
    """A lazy data frame that updates and reads the catalog on demand.

    We don't need to load the catalog for every SkyPilot call, and this class
    allows us to load the catalog only when needed.

    Use update_if_stale_func to pass in a function that decides whether to
    update the catalog on disk, updates it if needed, and returns
    a bool indicating whether the update was done.
    """

    def __init__(self, filename: str, update_if_stale_func: Callable[[], bool]):
        self._filename = filename
        self._df: Optional['pd.DataFrame'] = None
        self._update_if_stale_func = update_if_stale_func

    @annotations.lru_cache(scope='request')
    def _load_df(self) -> 'pd.DataFrame':
        if self._update_if_stale_func() or self._df is None:
            try:
                self._df = pd.read_csv(self._filename)
            except Exception as e:  # pylint: disable=broad-except
                # As users can manually modify the catalog, read_csv can fail.
                logger.error(f'Failed to read {self._filename}. '
                             'To fix: delete the csv file and try again.')
                with ux_utils.print_exception_no_traceback():
                    raise e
        return self._df

    def __getattr__(self, name: str):
        return getattr(self._load_df(), name)

    def __getitem__(self, key):
        # Delegate the indexing operation to the underlying DataFrame
        return self._load_df()[key]

    def __setitem__(self, key, value):
        # Delegate the set operation to the underlying DataFrame
        self._load_df()[key] = value


def read_catalog(filename: str,
                 pull_frequency_hours: Optional[int] = None) -> LazyDataFrame:
    """Reads the catalog from a local CSV file.

    If the file does not exist, download the up-to-date catalog that matches
    the schema version.
    If `pull_frequency_hours` is not None: pull the latest catalog with
    possibly updated prices, if the local catalog file is older than
    `pull_frequency_hours` and no changes to the local catalog file are
    made after the last pull.
    """
    assert filename.endswith('.csv'), 'The catalog file must be a CSV file.'
    assert (pull_frequency_hours is None or
            pull_frequency_hours >= 0), pull_frequency_hours
    catalog_path = get_catalog_path(filename)
    cloud = os.path.dirname(filename)
    if cloud != 'common':
        cloud = str(registry.CLOUD_REGISTRY.from_str(cloud))

    meta_path = os.path.join(_ABSOLUTE_VERSIONED_CATALOG_DIR, '.meta', filename)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    def _need_update() -> bool:
        if not os.path.exists(catalog_path):
            return True
        if pull_frequency_hours is None:
            return False
        if is_catalog_modified(filename):
            # If the catalog is modified by a user manually, we should
            # avoid overwriting the catalog by fetching from GitHub.
            return False

        last_update = os.path.getmtime(catalog_path)
        return last_update + pull_frequency_hours * 3600 < time.time()

    def _update_catalog():
        # Fast path: Exit early to avoid lock contention.
        if not _need_update():
            return False

        # Atomic check, to avoid conflicts with other processes.
        with filelock.FileLock(meta_path + '.lock'):
            # Double check after acquiring the lock.
            if not _need_update():
                return False

            url = f'{constants.HOSTED_CATALOG_DIR_URL}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'  # pylint: disable=line-too-long
            url_fallback = f'{constants.HOSTED_CATALOG_DIR_URL_S3_MIRROR}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'  # pylint: disable=line-too-long
            headers = {'User-Agent': 'SkyPilot/0.7'}
            update_frequency_str = ''
            if pull_frequency_hours is not None:
                update_frequency_str = (
                    f' (every {pull_frequency_hours} hours)')
            with rich_utils.safe_status(
                    ux_utils.spinner_message(
                        f'Updating {cloud} catalog: {filename}') +
                    f'{update_frequency_str}'):
                try:
                    r = requests.get(url=url, headers=headers)
                    if r.status_code == 429:
                        # fallback to s3 mirror, github introduced rate
                        # limit after 2025-05, see
                        # https://github.com/skypilot-org/skypilot/issues/5438
                        # for more details
                        r = requests.get(url=url_fallback, headers=headers)
                    r.raise_for_status()
                except requests.exceptions.RequestException as e:
                    error_str = (f'Failed to fetch {cloud} catalog '
                                 f'{filename}. ')
                    if os.path.exists(catalog_path):
                        logger.warning(
                            f'{error_str}Using cached catalog files.')
                        # Update catalog file modification time.
                        os.utime(catalog_path, None)  # Sets to current time
                    else:
                        logger.error(f'{error_str}Please check your internet '
                                     'connection.')
                        with ux_utils.print_exception_no_traceback():
                            raise e
                else:
                    # Download successful, save the catalog to a local file.
                    # Use atomic write (write to temp file, then rename) to
                    # avoid race conditions when multiple processes read/write
                    # the catalog file concurrently during parallel test
                    # execution.
                    os.makedirs(os.path.dirname(catalog_path), exist_ok=True)
                    with tempfile.NamedTemporaryFile(
                            mode='w',
                            dir=os.path.dirname(catalog_path),
                            delete=False,
                            encoding='utf-8') as f:
                        f.write(r.text)
                        tmp_path = f.name
                    os.rename(tmp_path, catalog_path)
                    with open(meta_path + '.md5', 'w', encoding='utf-8') as f:
                        f.write(hashlib.md5(r.text.encode()).hexdigest())
            logger.debug(f'Updated {cloud} catalog {filename}.')
        return True

    return LazyDataFrame(catalog_path, update_if_stale_func=_update_catalog)


def _get_instance_type(
    df: 'pd.DataFrame',
    instance_type: str,
    region: Optional[str],
    zone: Optional[str] = None,
) -> 'pd.DataFrame':
    idx = df['InstanceType'] == instance_type
    if region is not None:
        idx &= df['Region'].str.lower() == region.lower()
    if zone is not None:
        # NOTE: For Azure instances, zone must be None.
        idx &= df['AvailabilityZone'] == zone
    return df[idx]


def instance_type_exists_impl(df: 'pd.DataFrame', instance_type: str) -> bool:
    """Returns True if the instance type is valid."""
    return instance_type in df['InstanceType'].unique()


def validate_region_zone_impl(
        cloud_name: str, df: 'pd.DataFrame', region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validates whether region and zone exist in the catalog.

    Returns:
        A tuple of region and zone, if validated.

    Raises:
        ValueError: If region or zone is invalid or not supported.
    """

    def _get_candidate_str(loc: str, all_loc: List[str]) -> str:
        candidate_loc = difflib.get_close_matches(loc, all_loc, n=5, cutoff=0.9)
        candidate_loc = sorted(candidate_loc)
        candidate_strs = ''
        if candidate_loc:
            candidate_strs = ', '.join(candidate_loc)
            candidate_strs = f'\nDid you mean one of these: {candidate_strs!r}?'

        return candidate_strs

    def _get_all_supported_regions_str() -> str:
        all_regions: List[str] = sorted(
            df['Region'].str.lower().unique().tolist())
        return (f'\nList of supported {cloud_name} regions: '
                f'{", ".join(all_regions)!r}')

    validated_region, validated_zone = region, zone

    filter_df = df
    if region is not None:
        filter_df = _filter_region_zone(filter_df, region, zone=None)
        if filter_df.empty:
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid region {region!r}')
                candidate_strs = _get_candidate_str(
                    region.lower(), df['Region'].str.lower().unique())
                if not candidate_strs:
                    if cloud_name in ('azure', 'gcp'):
                        faq_msg = (
                            '\nIf a region is not included in the following '
                            'list, please check the FAQ docs for how to fetch '
                            'its catalog info.\nhttps://docs.skypilot.co'
                            '/en/latest/reference/faq.html#advanced-how-to-'
                            'make-skypilot-use-all-global-regions')
                        error_msg += faq_msg + _get_all_supported_regions_str()
                    else:
                        error_msg += _get_all_supported_regions_str()
                    raise ValueError(error_msg)
                error_msg += candidate_strs
                raise ValueError(error_msg)
        validated_region = filter_df['Region'].unique()[0]

    if zone is not None:
        maybe_region_df = filter_df
        filter_df = filter_df[filter_df['AvailabilityZone'] == zone]
        if filter_df.empty:
            region_str = f' for region {region!r}' if region else ''
            df = maybe_region_df if region else df
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid zone {zone!r}{region_str}')
                error_msg += _get_candidate_str(
                    zone, maybe_region_df['AvailabilityZone'].unique())
                raise ValueError(error_msg)
        region_df = filter_df['Region'].unique()
        assert len(region_df) == 1, 'Zone should be unique across regions.'
        validated_region = region_df[0]
    return validated_region, validated_zone


def get_hourly_cost_impl(
    df: 'pd.DataFrame',
    instance_type: str,
    use_spot: bool,
    region: Optional[str],
    zone: Optional[str],
) -> float:
    """Returns the hourly price of a VM instance in the given region and zone.

    Refer to get_hourly_cost in catalog/__init__.py for the docstring.
    """
    df = _get_instance_type(df, instance_type, region, zone)
    if df.empty:
        if zone is None:
            if region is None:
                region_or_zone = 'all regions'
            else:
                region_or_zone = f'region {region!r}'
        else:
            region_or_zone = f'zone {zone!r}'
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Instance type {instance_type!r} not found '
                             f'in {region_or_zone}.')

    # If the zone is specified, only one row should be found by the query.
    assert zone is None or len(df) == 1, df
    if use_spot:
        price_str = 'SpotPrice'
    else:
        price_str = 'Price'
        # For AWS/Azure/GCP on-demand instances, the price is the same across
        # all the zones in the same region.
        assert region is None or len(set(df[price_str])) == 1, df

    if pd.isna(df[price_str]).all():
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No {price_str} found for instance type '
                             f'{instance_type!r}.')
    cheapest_idx = df[price_str].idxmin()
    cheapest = df.loc[cheapest_idx]
    return float(cheapest[price_str])


def _get_value(value):
    if pd.isna(value):
        return None
    return float(value)


def get_vcpus_mem_from_instance_type_impl(
    df: 'pd.DataFrame',
    instance_type: str,
) -> Tuple[Optional[float], Optional[float]]:
    df = _get_instance_type(df, instance_type, None)
    if df.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    assert len(set(df['vCPUs'])) == 1, ('Cannot determine the number of vCPUs '
                                        f'of the instance type {instance_type}.'
                                        f'\n{df}')
    assert len(set(
        df['MemoryGiB'])) == 1, ('Cannot determine the memory size '
                                 f'of the instance type {instance_type}.'
                                 f'\n{df}')

    vcpus = df['vCPUs'].iloc[0]
    mem = df['MemoryGiB'].iloc[0]

    return _get_value(vcpus), _get_value(mem)


def _filter_with_cpus(df: 'pd.DataFrame',
                      cpus: Optional[str]) -> 'pd.DataFrame':
    if cpus is None:
        return df

    # The following code is redundant with the code in resources.py::_set_cpus()
    # but we add it here for safety.
    if cpus.endswith('+'):
        num_cpus_str = cpus[:-1]
    else:
        num_cpus_str = cpus
    try:
        num_cpus = float(num_cpus_str)
    except ValueError:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'The "cpus" field should be either a number or '
                             f'a string "<number>+". Found: {cpus!r}') from None

    if cpus.endswith('+'):
        return df[df['vCPUs'] >= num_cpus]
    else:
        return df[df['vCPUs'] == num_cpus]


def _filter_with_mem(df: 'pd.DataFrame',
                     memory_gb_or_ratio: Optional[str]) -> 'pd.DataFrame':
    if memory_gb_or_ratio is None:
        return df

    # The following code is partially redundant with the code in
    # resources.py::_set_memory() but we add it here for safety.
    if memory_gb_or_ratio.endswith(('+', 'x')):
        memory_gb_str = memory_gb_or_ratio[:-1]
    else:
        memory_gb_str = memory_gb_or_ratio
    try:
        memory = float(memory_gb_str)
    except ValueError:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'The "memory" field should be either a number or '
                             'a string "<number>+" or "<number>x". Found: '
                             f'{memory_gb_or_ratio!r}') from None
    if memory_gb_or_ratio.endswith('+'):
        return df[df['MemoryGiB'] >= memory]
    elif memory_gb_or_ratio.endswith('x'):
        return df[df['MemoryGiB'] >= df['vCPUs'] * memory]
    else:
        return df[df['MemoryGiB'] == memory]


def filter_with_local_disk(df: 'pd.DataFrame',
                           local_disk: Optional[str]) -> 'pd.DataFrame':
    if local_disk is None:
        return df

    local_disk = local_disk.lower()
    mode, size, at_least = resources_utils.parse_local_disk_str(local_disk)

    # Disk Type
    if mode not in ('nvme', 'ssd'):
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Local disk should be either nvme or ssd. '
                             f'Got {local_disk}.')
    df = df[df['LocalDiskType'] == 'ssd']  # SSD is always required.
    if mode == 'nvme':
        df = df[df['NVMeSupported'] == True]  # pylint: disable=singleton-comparison

    # Disk Size
    total_disk = df['LocalDiskSize'].fillna(0) * df['LocalDiskCount'].fillna(0)

    if at_least:
        df = df[total_disk >= size]
    else:
        df = df[abs(total_disk - size) < 1.0]

    return df


def _filter_region_zone(df: 'pd.DataFrame', region: Optional[str],
                        zone: Optional[str]) -> 'pd.DataFrame':
    if region is not None:
        df = df[df['Region'].str.lower() == region.lower()]
    if zone is not None:
        df = df[df['AvailabilityZone'].str.lower() == zone.lower()]
    return df


def get_instance_type_for_cpus_mem_impl(
        df: 'pd.DataFrame',
        cpus: Optional[str],
        memory_gb_or_ratio: Optional[str],
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Optional[str]:
    """Returns the cheapest instance type that satisfies the requirements.

    Args:
        df: The catalog cloud catalog data frame.
        cpus: The number of vCPUs. Can be a number or a string "<number>+". If
            the string ends with "+", then the returned instance type should
            have at least the given number of vCPUs.
        memory_gb_or_ratio: The memory size in GB. Can be a number or a string
            "<number>+" or "<number>x". If the string ends with "+", then the
            returned instance type should have at least the given memory size.
            If the string ends with "x", then the returned instance type should
            have at least the given number of vCPUs times the given ratio.
        region: The region to filter by.
        zone: The zone to filter by.
    """
    df = _filter_region_zone(df, region, zone)
    df = _filter_with_cpus(df, cpus)
    df = _filter_with_mem(df, memory_gb_or_ratio)
    if df.empty:
        return None
    # Sort by the price.
    df = df.sort_values(by=['Price'], ascending=True)
    return df['InstanceType'].iloc[0]


def get_accelerators_from_instance_type_impl(
    df: 'pd.DataFrame',
    instance_type: str,
) -> Optional[Dict[str, Union[int, float]]]:
    df = _get_instance_type(df, instance_type, None)
    if df.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        return None

    def _convert(value):
        if int(value) == value:
            return int(value)
        return float(value)

    return {acc_name: _convert(acc_count)}


def get_arch_from_instance_type_impl(
    df: 'pd.DataFrame',
    instance_type: str,
) -> Optional[str]:
    df = _get_instance_type(df, instance_type, None)
    if df.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]
    if 'Arch' not in row:
        return None
    arch = row['Arch']
    if pd.isnull(arch):
        return None

    return arch


def get_local_disk_from_instance_type_impl(df: 'pd.DataFrame',
                                           instance_type: str) -> Optional[str]:
    df = _get_instance_type(df, instance_type, None)
    if df.empty:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]

    if 'LocalDiskType' not in row or pd.isna(row['LocalDiskType']):
        return None

    mode = row['LocalDiskType']
    if mode != 'ssd':
        return None  # We don't support HDDs right now.

    if pd.isna(row.get('LocalDiskSize')) or pd.isna(row.get('LocalDiskCount')):
        # Should we raise error instead?
        return None

    total_size = float(row['LocalDiskSize']) * float(row['LocalDiskCount'])

    nvme_supported = row.get('NVMeSupported', False)
    if pd.isna(nvme_supported):
        nvme_supported = False
    if nvme_supported:
        mode = 'nvme'

    return f'{mode}:{int(total_size)}'


def get_instance_type_for_accelerator_impl(
    df: 'pd.DataFrame',
    acc_name: str,
    acc_count: Union[int, float],
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    result = df[(df['AcceleratorName'].str.fullmatch(acc_name, case=False)) &
                (abs(df['AcceleratorCount'] - acc_count) <= 0.01)]
    result = _filter_region_zone(result, region, zone)
    if result.empty:
        fuzzy_result = df[
            (df['AcceleratorName'].str.contains(acc_name, case=False)) &
            (df['AcceleratorCount'] >= acc_count)]
        fuzzy_result = _filter_region_zone(fuzzy_result, region, zone)
        fuzzy_result = fuzzy_result.sort_values('Price', ascending=True)
        fuzzy_result = fuzzy_result[['AcceleratorName',
                                     'AcceleratorCount']].drop_duplicates()
        fuzzy_candidate_list = []
        if not fuzzy_result.empty:
            for _, row in fuzzy_result.iterrows():
                acc_cnt = float(row['AcceleratorCount'])
                acc_count_display = (int(acc_cnt) if acc_cnt.is_integer() else
                                     f'{acc_cnt:.2f}')
                fuzzy_candidate_list.append(f'{row["AcceleratorName"]}:'
                                            f'{acc_count_display}')
        return (None, fuzzy_candidate_list)

    result = _filter_with_cpus(result, cpus)
    result = _filter_with_mem(result, memory)
    result = _filter_region_zone(result, region, zone)
    if result.empty:
        return ([], [])

    # Current strategy: choose the cheapest instance
    price_str = 'SpotPrice' if use_spot else 'Price'
    if pd.isna(result[price_str]).all():
        return ([], [])
    result = result.sort_values(price_str, ascending=True)
    instance_types = list(result['InstanceType'].drop_duplicates())
    return (instance_types, [])


def list_accelerators_impl(
        cloud: str,
        df: 'pd.DataFrame',
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[InstanceTypeInfo]]:
    """Lists accelerators offered in a cloud service catalog.

    `name_filter` is a regular expression used to filter accelerator names
    using pandas.Series.str.contains.

    Returns a mapping from the canonical names of accelerators to a list of
    instance types offered by this cloud.
    """
    del require_price  # Unused.
    if gpus_only:
        df = df[~df['GpuInfo'].isna()]
    df = df.copy()  # avoid column assignment warning

    try:
        gpu_info_df = df['GpuInfo'].apply(ast.literal_eval)
        df['DeviceMemoryGiB'] = gpu_info_df.apply(
            lambda row: row['Gpus'][0]['MemoryInfo']['SizeInMiB']) / 1024.0
    except (ValueError, SyntaxError):
        # TODO(zongheng,woosuk): GCP/Azure catalogs do not have well-formed
        # GpuInfo fields. So the above will throw:
        #  ValueError: malformed node or string: <_ast.Name object at ..>
        df['DeviceMemoryGiB'] = None

    df = df[[
        'InstanceType',
        'AcceleratorName',
        'AcceleratorCount',
        'vCPUs',
        'DeviceMemoryGiB',  # device memory
        'MemoryGiB',  # host memory
        'Price',
        'SpotPrice',
        'Region',
    ]].dropna(subset=['AcceleratorName']).drop_duplicates()

    if name_filter is not None:
        df = df[df['AcceleratorName'].str.contains(name_filter,
                                                   case=case_sensitive,
                                                   regex=True)]
    if region_filter is not None:
        df = df[df['Region'].str.contains(region_filter,
                                          case=case_sensitive,
                                          regex=True)]
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(float)
    if quantity_filter is not None:
        df = df[df['AcceleratorCount'] == quantity_filter]
    grouped = df.groupby('AcceleratorName')

    def make_list_from_df(rows):

        sort_key = ['Price', 'SpotPrice']
        subset = [
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB'
        ]
        if all_regions:
            sort_key.append('Region')
            subset.append('Region')

        rows = rows.sort_values(by=sort_key).drop_duplicates(subset=subset,
                                                             keep='first')
        ret = rows.apply(
            lambda row: InstanceTypeInfo(
                cloud,
                row['InstanceType'],
                row['AcceleratorName'],
                row['AcceleratorCount'],
                row['vCPUs'],
                row['DeviceMemoryGiB'],
                row['MemoryGiB'],
                row['Price'],
                row['SpotPrice'],
                row['Region'],
            ),
            axis='columns',
        ).tolist()
        # Sort by price and region as well.
        ret.sort(
            key=lambda info: (info.accelerator_count, info.instance_type, info.
                              cpu_count if not pd.isna(info.cpu_count) else 0,
                              info.price, info.spot_price, info.region))
        return ret

    return {k: make_list_from_df(v) for k, v in grouped}


def get_region_zones(df: 'pd.DataFrame',
                     use_spot: bool) -> List[cloud_lib.Region]:
    """Returns a list of regions/zones from a dataframe."""
    price_str = 'SpotPrice' if use_spot else 'Price'
    sort_keys = [price_str, 'Region']
    if 'AvailabilityZone' in df.columns:
        sort_keys.append('AvailabilityZone')
    # If NaN appears in any of the sort keys, drop the row, as that means
    # errors in the data.
    df = df.dropna(subset=sort_keys).sort_values(sort_keys)
    regions = [cloud_lib.Region(region) for region in df['Region'].unique()]
    if 'AvailabilityZone' in df.columns:
        zones_in_region = df.groupby('Region')['AvailabilityZone'].apply(
            lambda x: [cloud_lib.Zone(zone) for zone in x])
        for region in regions:
            region.set_zones(zones_in_region[region.name])
    return regions


# Images
def get_image_id_from_tag_impl(df: 'pd.DataFrame', tag: str,
                               region: Optional[str]) -> Optional[str]:
    """Returns the image ID for the given tag and region.

    If region is None, there must be only one image with the given tag.

    Returns None if a region (or globally if region is None) does not have
    an image that matches the tag.
    """
    df = df[df['Tag'] == tag]
    df = _filter_region_zone(df, region, zone=None)
    assert len(df) <= 1, ('Multiple images found for tag '
                          f'{tag} in region {region}')
    if df.empty:
        return None
    image_id = df['ImageId'].iloc[0]
    if pd.isna(image_id):
        return None
    return image_id


def is_image_tag_valid_impl(df: 'pd.DataFrame', tag: str,
                            region: Optional[str]) -> bool:
    """Returns True if the image tag is valid."""
    df = df[df['Tag'] == tag]
    df = _filter_region_zone(df, region, zone=None)
    df = df.dropna(subset=['ImageId'])
    return not df.empty
