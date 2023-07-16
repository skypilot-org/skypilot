"""Common utilities for service catalog."""
import ast
import hashlib
import os
import time
from typing import Dict, List, NamedTuple, Optional, Tuple

import difflib
import filelock
import requests
import pandas as pd

from sky import sky_logging
from sky.clouds import cloud as cloud_lib
from sky.clouds.service_catalog import constants
from sky.utils import log_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_CATALOG_DIR = os.path.join(constants.LOCAL_CATALOG_DIR,
                            constants.CATALOG_SCHEMA_VERSION)
os.makedirs(_CATALOG_DIR, exist_ok=True)


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
    accelerator_count: int
    cpu_count: Optional[float]
    device_memory: Optional[float]
    memory: Optional[float]
    price: float
    spot_price: float
    region: str


def get_catalog_path(filename: str) -> str:
    return os.path.join(_CATALOG_DIR, filename)


def read_catalog(filename: str,
                 pull_frequency_hours: Optional[int] = None) -> pd.DataFrame:
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
            pull_frequency_hours > 0), pull_frequency_hours
    catalog_path = get_catalog_path(filename)
    cloud = cloud_lib.CLOUD_REGISTRY.from_str(os.path.dirname(filename))

    meta_path = os.path.join(_CATALOG_DIR, '.meta', filename)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    # Atomic check, to avoid conflicts with other processes.
    # TODO(mraheja): remove pylint disabling when filelock version updated
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(meta_path + '.lock'):

        def _need_update() -> bool:
            if not os.path.exists(catalog_path):
                return True
            if pull_frequency_hours is None:
                return False
            # Check the md5 of the file to see if it has changed.
            with open(catalog_path, 'rb') as f:
                file_md5 = hashlib.md5(f.read()).hexdigest()
            md5_filepath = meta_path + '.md5'
            if os.path.exists(md5_filepath):
                with open(md5_filepath, 'r') as f:
                    last_md5 = f.read()
                if file_md5 != last_md5:
                    # Do not update the file if the user modified it.
                    return False

            last_update = os.path.getmtime(catalog_path)
            return last_update + pull_frequency_hours * 3600 < time.time()

        if _need_update():
            url = f'{constants.HOSTED_CATALOG_DIR_URL}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'  # pylint: disable=line-too-long
            update_frequency_str = ''
            if pull_frequency_hours is not None:
                update_frequency_str = f' (every {pull_frequency_hours} hours)'
            with log_utils.safe_rich_status(
                (f'Updating {cloud} catalog: '
                 f'{filename}'
                 f'{update_frequency_str}')) as status:
                try:
                    r = requests.get(url)
                    r.raise_for_status()
                except requests.exceptions.RequestException as e:
                    ux_utils.console_newline()
                    status.stop()
                    error_str = (f'Failed to fetch {cloud} catalog '
                                 f'{filename}. ')
                    if os.path.exists(catalog_path):
                        logger.warning(
                            f'{error_str}Using cached catalog files.')
                        # Update catalog file modification time.
                        os.utime(catalog_path, None)  # Sets to current time
                    else:
                        logger.error(
                            f'{error_str}Please check your internet connection.'
                        )
                        with ux_utils.print_exception_no_traceback():
                            raise e
                else:
                    # Download successful, save the catalog to a local file.
                    os.makedirs(os.path.dirname(catalog_path), exist_ok=True)
                    with open(catalog_path, 'w') as f:
                        f.write(r.text)
                    with open(meta_path + '.md5', 'w') as f:
                        f.write(hashlib.md5(r.text.encode()).hexdigest())

    try:
        df = pd.read_csv(catalog_path)
    except Exception as e:  # pylint: disable=broad-except
        # As users can manually modify the catalog, read_csv can fail.
        logger.error(f'Failed to read {catalog_path}. '
                     'To fix: delete the csv file and try again.')
        with ux_utils.print_exception_no_traceback():
            raise e
    return df


def _get_instance_type(
    df: pd.DataFrame,
    instance_type: str,
    region: Optional[str],
    zone: Optional[str] = None,
) -> pd.DataFrame:
    idx = df['InstanceType'] == instance_type
    if region is not None:
        idx &= df['Region'].str.lower() == region.lower()
    if zone is not None:
        # NOTE: For Azure instances, zone must be None.
        idx &= df['AvailabilityZone'] == zone
    return df[idx]


def instance_type_exists_impl(df: pd.DataFrame, instance_type: str) -> bool:
    """Returns True if the instance type is valid."""
    return instance_type in df['InstanceType'].unique()


def validate_region_zone_impl(
        cloud_name: str, df: pd.DataFrame, region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validates whether region and zone exist in the catalog."""

    def _get_candidate_str(loc: str, all_loc: List[str]) -> str:
        candidate_loc = difflib.get_close_matches(loc, all_loc, n=5, cutoff=0.9)
        candidate_loc = sorted(candidate_loc)
        candidate_strs = ''
        if len(candidate_loc) > 0:
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
        if len(filter_df) == 0:
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid region {region!r}')
                candidate_strs = _get_candidate_str(
                    region.lower(), df['Region'].str.lower().unique())
                if not candidate_strs:
                    error_msg += _get_all_supported_regions_str()
                    raise ValueError(error_msg)
                error_msg += candidate_strs
                raise ValueError(error_msg)
        validated_region = filter_df['Region'].unique()[0]

    if zone is not None:
        maybe_region_df = filter_df
        filter_df = filter_df[filter_df['AvailabilityZone'] == zone]
        if len(filter_df) == 0:
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
    df: pd.DataFrame,
    instance_type: str,
    use_spot: bool,
    region: Optional[str],
    zone: Optional[str],
) -> float:
    """Returns the hourly price of a VM instance in the given region and zone.

    Refer to get_hourly_cost in service_catalog/__init__.py for the docstring.
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

    cheapest_idx = df[price_str].idxmin()
    cheapest = df.loc[cheapest_idx]
    return cheapest[price_str]


def _get_value(value):
    if pd.isna(value):
        return None
    return float(value)


def get_vcpus_mem_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Tuple[Optional[float], Optional[float]]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
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


def _filter_with_cpus(df: pd.DataFrame, cpus: Optional[str]) -> pd.DataFrame:
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


def _filter_with_mem(df: pd.DataFrame,
                     memory_gb_or_ratio: Optional[str]) -> pd.DataFrame:
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


def _filter_region_zone(df: pd.DataFrame, region: Optional[str],
                        zone: Optional[str]) -> pd.DataFrame:
    if region is not None:
        df = df[df['Region'].str.lower() == region.lower()]
    if zone is not None:
        df = df[df['AvailabilityZone'].str.lower() == zone.lower()]
    return df


def get_instance_type_for_cpus_mem_impl(
        df: pd.DataFrame, cpus: Optional[str],
        memory_gb_or_ratio: Optional[str]) -> Optional[str]:
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
    """
    df = _filter_with_cpus(df, cpus)
    df = _filter_with_mem(df, memory_gb_or_ratio)
    if df.empty:
        return None
    # Sort by the price.
    df = df.sort_values(by=['Price'], ascending=True)
    return df['InstanceType'].iloc[0]


def get_accelerators_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Optional[Dict[str, int]]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    row = df.iloc[0]
    acc_name, acc_count = row['AcceleratorName'], row['AcceleratorCount']
    if pd.isnull(acc_name):
        return None
    return {acc_name: int(acc_count)}


def get_instance_type_for_accelerator_impl(
    df: pd.DataFrame,
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    result = df[(df['AcceleratorName'].str.fullmatch(acc_name, case=False)) &
                (df['AcceleratorCount'] == acc_count)]
    result = _filter_region_zone(result, region, zone)
    if len(result) == 0:
        fuzzy_result = df[
            (df['AcceleratorName'].str.contains(acc_name, case=False)) &
            (df['AcceleratorCount'] >= acc_count)]
        fuzzy_result = _filter_region_zone(fuzzy_result, region, zone)
        fuzzy_result = fuzzy_result.sort_values('Price', ascending=True)
        fuzzy_result = fuzzy_result[['AcceleratorName',
                                     'AcceleratorCount']].drop_duplicates()
        fuzzy_candidate_list = []
        if len(fuzzy_result) > 0:
            for _, row in fuzzy_result.iterrows():
                fuzzy_candidate_list.append(f'{row["AcceleratorName"]}:'
                                            f'{int(row["AcceleratorCount"])}')
        return (None, fuzzy_candidate_list)

    result = _filter_with_cpus(result, cpus)
    result = _filter_with_mem(result, memory)
    result = _filter_region_zone(result, region, zone)
    if len(result) == 0:
        return ([], [])

    # Current strategy: choose the cheapest instance
    price_str = 'SpotPrice' if use_spot else 'Price'
    result = result.sort_values(price_str, ascending=True)
    instance_types = list(result['InstanceType'].drop_duplicates())
    return (instance_types, [])


def list_accelerators_impl(
    cloud: str,
    df: pd.DataFrame,
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
) -> Dict[str, List[InstanceTypeInfo]]:
    """Lists accelerators offered in a cloud service catalog.

    `name_filter` is a regular expression used to filter accelerator names
    using pandas.Series.str.contains.

    Returns a mapping from the canonical names of accelerators to a list of
    instance types offered by this cloud.
    """
    if gpus_only:
        df = df[~df['GpuInfo'].isna()]
    df = df.copy()  # avoid column assignment warning

    try:
        gpu_info_df = df['GpuInfo'].apply(ast.literal_eval)
        df['DeviceMemoryGiB'] = gpu_info_df.apply(
            lambda row: row['Gpus'][0]['MemoryInfo']['SizeInMiB']) / 1024.0
    except ValueError:
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
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
    if quantity_filter is not None:
        df = df[df['AcceleratorCount'] == quantity_filter]
    grouped = df.groupby('AcceleratorName')

    def make_list_from_df(rows):
        # Only keep the lowest prices across regions.
        rows = rows.groupby([
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB'
        ],
                            dropna=False).aggregate(min).reset_index()
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
        ret.sort(key=lambda info: (info.accelerator_count, info.cpu_count
                                   if info.cpu_count is not None else 0))
        return ret

    return {k: make_list_from_df(v) for k, v in grouped}


def get_region_zones(df: pd.DataFrame,
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


def _accelerator_in_region(df: pd.DataFrame, acc_name: str, acc_count: int,
                           region: str) -> bool:
    """Returns True if the accelerator is in the region."""
    return len(df[(df['AcceleratorName'] == acc_name) &
                  (df['AcceleratorCount'] == acc_count) &
                  (df['Region'].str.lower() == region.lower())]) > 0


def _accelerator_in_zone(df: pd.DataFrame, acc_name: str, acc_count: int,
                         zone: str) -> bool:
    """Returns True if the accelerator is in the zone."""
    return len(df[(df['AcceleratorName'] == acc_name) &
                  (df['AcceleratorCount'] == acc_count) &
                  (df['AvailabilityZone'] == zone)]) > 0


def accelerator_in_region_or_zone_impl(
    df: pd.DataFrame,
    accelerator_name: str,
    acc_count: int,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> bool:
    """Returns True if the accelerator is in the region or zone."""
    assert region is not None or zone is not None, (
        'Both region and zone are None.')
    if zone is None:
        assert region is not None
        return _accelerator_in_region(df, accelerator_name, acc_count, region)
    else:
        return _accelerator_in_zone(df, accelerator_name, acc_count, zone)


# Images
def get_image_id_from_tag_impl(df: pd.DataFrame, tag: str,
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
    if len(df) == 0:
        return None
    image_id = df['ImageId'].iloc[0]
    if pd.isna(image_id):
        return None
    return image_id


def is_image_tag_valid_impl(df: pd.DataFrame, tag: str,
                            region: Optional[str]) -> bool:
    """Returns True if the image tag is valid."""
    df = df[df['Tag'] == tag]
    df = _filter_region_zone(df, region, zone=None)
    df = df.dropna(subset=['ImageId'])
    return len(df) > 0
