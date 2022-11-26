"""Common utilities for service catalog."""
import os
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import difflib
import requests
import pandas as pd

from sky import sky_logging
from sky.backends import backend_utils
from sky.clouds import cloud as cloud_lib
from sky.clouds.service_catalog import constants
from sky.skylet import constants as sky_constants
from sky.utils import common_utils
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
    - memory: Instance memory in GiB.
    - price: Regular instance price per hour (cheapest across all regions).
    - spot_price: Spot instance price per hour (cheapest across all regions).
    """
    cloud: str
    instance_type: Optional[str]
    accelerator_name: str
    accelerator_count: int
    cpu_count: Optional[float]
    memory: Optional[float]
    price: float
    spot_price: float


def get_catalog_path(filename: str) -> str:
    return os.path.join(_CATALOG_DIR, filename)


def _get_filtered_cache_path(filename: str, area_filter: List[str]) -> str:
    """Returns the cache path for the filtered catalog.

    Args:
        filename: The filename of the catalog.
        area_filter: The area filter.

    Returns:
        The cache path.
    """
    assert filename.endswith('.csv'), filename
    new_name = filename.split('.csv')[0] + '.filtered.'
    cached_catalog_path = get_catalog_path(new_name)
    cached_catalog_path += f'{"_".join(area_filter)}.csv'
    return cached_catalog_path


def _read_catalog_config() -> Optional[Dict[str, Dict[str, str]]]:
    """Reads the config file and extract the catalog config."""
    # TODO(zhwu): Add schema validation.
    config_path = os.path.expanduser(sky_constants.CONFIG_PATH)
    if not os.path.exists(config_path):
        return None
    config = common_utils.read_yaml(config_path)
    if config is None:
        return None
    return config.get('catalog')


def get_preferred_areas_from_config(
        cloud: 'cloud_lib.Cloud') -> Optional[List[str]]:
    """Gets the preferred areas from the config file.

    If not specified, returns the default areas for the cloud.
    """
    default_areas = cloud.default_areas()
    config = _read_catalog_config()
    if config is None:
        return default_areas
    config = {k.lower(): v for k, v in config.items()}
    cloud_str = str(cloud).lower()
    if cloud_str not in config:
        return default_areas
    preferred_areas = config[cloud_str].get('preferred_areas')
    if preferred_areas is None:
        return default_areas
    logger.warning(f'Setting preferred areas are EXPERIMENTAL and '
                   f'may break existing clusters. ({cloud}: {preferred_areas})')
    if isinstance(preferred_areas, str):
        if preferred_areas == 'all':
            preferred_areas = None
        else:
            preferred_areas = [preferred_areas]
    elif preferred_areas is not None and not isinstance(preferred_areas, list):
        raise ValueError(
            f'Invalid preferred_areas for {cloud}: {preferred_areas}')
    return preferred_areas


def read_catalog(
    filename: str,
    area_filter_fn: Optional[Callable[[pd.DataFrame, List[str]],
                                      pd.DataFrame]] = None
) -> pd.DataFrame:
    """Reads the catalog from a local CSV file and filter it by area.

    If the catalog file does not exist, download the up-to-date catalog that
    matches the schema version.
    """
    assert filename.endswith('.csv'), 'The catalog file must be a CSV file.'
    catalog_path = get_catalog_path(filename)
    cloud = cloud_lib.CLOUD_REGISTRY.from_str(os.path.dirname(filename))

    if area_filter_fn is not None:
        preferred_areas = get_preferred_areas_from_config(cloud)
        if preferred_areas is not None:
            cached_catalog_path = _get_filtered_cache_path(
                filename, preferred_areas)
            try:
                return pd.read_csv(cached_catalog_path)
            except Exception:  # pylint: disable=broad-except
                # Failed to load the cache regenerate the cache.
                pass

    if not os.path.exists(catalog_path):
        url = f'{constants.HOSTED_CATALOG_DIR_URL}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'  # pylint: disable=line-too-long
        with backend_utils.safe_console_status(
                f'Downloading {cloud} catalog...'):
            try:
                r = requests.get(url)
                r.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.error(f'Failed to download {cloud} catalog:')
                with ux_utils.print_exception_no_traceback():
                    raise e
        # Save the catalog to a local file.
        os.makedirs(os.path.dirname(catalog_path), exist_ok=True)
        with open(catalog_path, 'w') as f:
            f.write(r.text)
        logger.info(f'A new {cloud} catalog has been downloaded to '
                    f'{catalog_path}')

    try:
        df = pd.read_csv(catalog_path)
    except Exception as e:  # pylint: disable=broad-except
        # As users can manually modify the catalog, read_csv can fail.
        logger.error(f'Failed to read {catalog_path}. '
                     'To fix: delete the csv file and try again.')
        with ux_utils.print_exception_no_traceback():
            raise e

    if area_filter_fn is not None and preferred_areas is not None:
        df = area_filter_fn(df, preferred_areas)
        df.to_csv(cached_catalog_path, index=False)
    return df


def _get_instance_type(
    df: pd.DataFrame,
    instance_type: str,
    region: Optional[str],
) -> pd.DataFrame:
    idx = df['InstanceType'] == instance_type
    if region is not None:
        idx &= df['Region'] == region
    return df[idx]


def instance_type_exists_impl(df: pd.DataFrame, instance_type: str) -> bool:
    """Returns True if the instance type is valid."""
    return instance_type in df['InstanceType'].unique()


def validate_region_zone_impl(
        df: pd.DataFrame, region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validates whether region and zone exist in the catalog."""

    def _get_candidate_str(loc: str, all_loc: List[str]) -> List[str]:
        candidate_loc = difflib.get_close_matches(loc, all_loc, n=5, cutoff=0.9)
        candidate_loc = sorted(candidate_loc)
        candidate_strs = ''
        if len(candidate_loc) > 0:
            candidate_strs = ', '.join(candidate_loc)
            candidate_strs = f'\nDid you mean one of these: {candidate_strs!r}?'
        return candidate_strs

    validated_region, validated_zone = region, zone

    filter_df = df
    if region is not None:
        filter_df = filter_df[filter_df['Region'] == region]
        if len(filter_df) == 0:
            with ux_utils.print_exception_no_traceback():
                error_msg = (f'Invalid region {region!r}')
                error_msg += _get_candidate_str(region, df['Region'].unique())
                raise ValueError(error_msg)

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
    region: Optional[str],
    use_spot: bool = False,
) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _get_instance_type(df, instance_type, region)
    assert pd.isnull(
        df['Price'].iloc[0]) is False, (f'Missing price for "{instance_type}, '
                                        f'Spot: {use_spot}" in the catalog.')
    # TODO(zhwu): We should handle the price difference among different regions.
    price_str = 'SpotPrice' if use_spot else 'Price'
    assert region is None or len(set(df[price_str])) == 1, df

    cheapest_idx = df[price_str].idxmin()

    cheapest = df.loc[cheapest_idx]
    return cheapest[price_str]


def get_vcpus_from_instance_type_impl(
    df: pd.DataFrame,
    instance_type: str,
) -> Optional[float]:
    df = _get_instance_type(df, instance_type, None)
    if len(df) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'No instance type {instance_type} found.')
    assert len(set(df['vCPUs'])) == 1, ('Cannot determine the number of vCPUs '
                                        f'of the instance type {instance_type}.'
                                        f'\n{df}')
    vcpus = df['vCPUs'].iloc[0]
    if pd.isna(vcpus):
        return None
    return float(vcpus)


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
) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    result = df[(df['AcceleratorName'].str.fullmatch(acc_name, case=False)) &
                (df['AcceleratorCount'] == acc_count)]
    if len(result) == 0:
        fuzzy_result = df[
            (df['AcceleratorName'].str.contains(acc_name, case=False)) &
            (df['AcceleratorCount'] >= acc_count)]
        fuzzy_result = fuzzy_result.sort_values('Price', ascending=True)
        fuzzy_result = fuzzy_result[['AcceleratorName',
                                     'AcceleratorCount']].drop_duplicates()
        fuzzy_candidate_list = []
        if len(fuzzy_result) > 0:
            for _, row in fuzzy_result.iterrows():
                fuzzy_candidate_list.append(f'{row["AcceleratorName"]}:'
                                            f'{int(row["AcceleratorCount"])}')
        return (None, fuzzy_candidate_list)
    # Current strategy: choose the cheapest instance
    result = result.sort_values('Price', ascending=True)
    instance_types = list(result['InstanceType'].drop_duplicates())
    return (instance_types, [])


def list_accelerators_impl(
    cloud: str,
    df: pd.DataFrame,
    gpus_only: bool,
    name_filter: Optional[str],
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
    df = df[[
        'InstanceType',
        'AcceleratorName',
        'AcceleratorCount',
        'vCPUs',
        'MemoryGiB',
        'Price',
        'SpotPrice',
    ]].dropna(subset=['AcceleratorName']).drop_duplicates()
    if name_filter is not None:
        df = df[df['AcceleratorName'].str.contains(name_filter,
                                                   case=case_sensitive,
                                                   regex=True)]
    df['AcceleratorCount'] = df['AcceleratorCount'].astype(int)
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
                row['MemoryGiB'],
                row['Price'],
                row['SpotPrice'],
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
    df = df.dropna(subset=[price_str]).sort_values(price_str)
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
                  (df['Region'] == region)]) > 0


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
    if region is not None:
        df = df[df['Region'] == region]
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
    if region is not None:
        df = df[df['Region'] == region]
    df = df.dropna(subset=['ImageId'])
    return len(df) > 0
