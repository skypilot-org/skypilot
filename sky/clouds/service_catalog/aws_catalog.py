"""AWS Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for AWS.
"""
import glob
import hashlib
import os
import threading
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.clouds import aws
from sky.clouds.service_catalog import common
from sky.clouds.service_catalog import config
from sky.clouds.service_catalog.data_fetchers import fetch_aws
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)

# We will select from the following three instance families:
_DEFAULT_INSTANCE_FAMILY = [
    # This is the latest general-purpose instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8375C.
    # Memory: 4 GiB RAM per 1 vCPU;
    'm6i',
    # This is the latest memory-optimized instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8375C
    # Memory: 8 GiB RAM per 1 vCPU;
    'r6i',
    # This is the latest compute-optimized instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8375C
    # Memory: 2 GiB RAM per 1 vCPU;
    'c6i',
]
_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 4

# Keep it synced with the frequency in
# skypilot-catalog/.github/workflows/update-aws-catalog.yml
_PULL_FREQUENCY_HOURS = 7

# The main catalog dataframe.
#   - _default_df: default non-account-specific catalog
#     The AvailabilityZone column is a zone ID (e.g. use1-az1).
#   - _user_df: account-specific catalog (i.e., regions that the account
#     doesn't have enabled are dropped; AZ mapping is applied, etc.)
#     Creating this requires AWS credentials. It is created at most once
#     (and cached) per a process' lifetime.
#     The AvailabilityZone column is a zone name (e.g. us-east-1a).
# `_apply_az_mapping_lock` protects reading/writing `_user_df`.
_default_df = common.read_catalog('aws/vms.csv',
                                  pull_frequency_hours=_PULL_FREQUENCY_HOURS)
_user_df = None
_apply_az_mapping_lock = threading.Lock()

_image_df = common.read_catalog('aws/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_quotas_df = common.read_catalog('aws/instance_quota_mapping.csv',
                                 pull_frequency_hours=_PULL_FREQUENCY_HOURS)


def _get_az_mappings(aws_user_hash: str) -> Optional['pd.DataFrame']:
    filename = f'aws/az_mappings-{aws_user_hash}.csv'
    az_mapping_path = common.get_catalog_path(filename)
    az_mapping_md5_path = common.get_catalog_path(f'.meta/{filename}.md5')
    if not os.path.exists(az_mapping_path):
        az_mappings = None
        if aws_user_hash != 'default':
            # Fetch az mapping from AWS.
            with rich_utils.safe_status(
                    ux_utils.spinner_message('AWS: Fetching availability '
                                             'zones mapping')):
                az_mappings = fetch_aws.fetch_availability_zone_mappings()
        else:
            return None
        az_mappings.to_csv(az_mapping_path, index=False)
        # Write md5 of the az_mapping file to a file so we can check it for
        # any changes when uploading to the controller
        with open(az_mapping_path, 'r', encoding='utf-8') as f:
            az_mapping_hash = hashlib.md5(f.read().encode()).hexdigest()
        with open(az_mapping_md5_path, 'w', encoding='utf-8') as f:
            f.write(az_mapping_hash)
    else:
        az_mappings = pd.read_csv(az_mapping_path)
    return az_mappings


def _fetch_and_apply_az_mapping(df: common.LazyDataFrame) -> 'pd.DataFrame':
    """Maps zone IDs (use1-az1) to zone names (us-east-1x).

    The upper-level functions that use the availability zone information
    should be able to handle the case where the zone name is not correct,
    due to the credentials not being configured.

    Such mappings are account-specific and determined by AWS. We fetch the
    mappings from AWS, which requires AWS credentials. If the user does not
    have AWS credentials configured, we use original zone id. It is ok to
    use the default mapping because the user will not be able to provision
    instances with those wrong availablity zones due to the lack of
    credentials.

    The mappings will also serve to remove from 'df' the regions that are
    not supported by the user account.

    Returns:
        A dataframe with column 'AvailabilityZone' that's correctly replaced
        with the zone name (e.g. us-east-1a).
    """
    try:
        user_identity_list = aws.AWS.get_active_user_identity()
        assert user_identity_list, user_identity_list
        user_identity = user_identity_list[0]
        aws_user_hash = hashlib.md5(user_identity.encode()).hexdigest()[:8]
    except exceptions.CloudUserIdentityError:
        glob_name = common.get_catalog_path('aws/az_mappings-*.csv')
        # Find the most recent file that matches the glob.
        # We check the existing files because the user could remove the
        # credentials after a cluster is created. Using the latest mapping
        # file is better than using the default mapping file because the
        # former is more likely to be correct.
        glob_files = glob.glob(glob_name)
        if glob_files:
            glob_files.sort(key=os.path.getmtime)
            aws_user_hash = os.path.basename(glob_files[-1]).split('-')[1]
            # aws_user_hash can be set to `default` if the user never
            # configured AWS credentials.
            aws_user_hash = aws_user_hash.split('.')[0]
        else:
            aws_user_hash = 'default'
        logger.debug(
            'Failed to get AWS user identity. Using the latest mapping '
            f'file for user {aws_user_hash!r}.')

    az_mappings = _get_az_mappings(aws_user_hash)
    if az_mappings is None:
        # Returning the original dataframe directly, as no cloud
        # identity can be fetched which suggests there are no
        # credentials.
        return df
    # Use inner join to drop rows with unknown AZ IDs, which are likely
    # because the user does not have access to that Region. Otherwise,
    # there will be rows with NaN in the AvailabilityZone column.
    df = df.merge(az_mappings, on=['AvailabilityZone'], how='inner')
    df = df.drop(columns=['AvailabilityZone']).rename(
        columns={'AvailabilityZoneName': 'AvailabilityZone'})
    return df


def _get_df() -> 'pd.DataFrame':
    global _user_df
    with _apply_az_mapping_lock:
        if _user_df is None:
            try:
                _user_df = _fetch_and_apply_az_mapping(_default_df)
            except (RuntimeError, ImportError) as e:
                if config.get_use_default_catalog_if_failed():
                    logger.warning('Failed to fetch availability zone mapping. '
                                   f'{common_utils.format_exception(e)}')
                    return _default_df
                else:
                    raise
    return _user_df


def get_quota_code(instance_type: str, use_spot: bool) -> Optional[str]:
    """Get the quota code based on `instance_type` and `use_spot`.

    The quota code is fetched from `_quotas_df` based on the instance type
    specified, and will then be utilized in a botocore API command in order
    to check its quota.
    """

    if use_spot:
        spot_header = 'SpotInstanceCode'
    else:
        spot_header = 'OnDemandInstanceCode'
    try:
        quota_code = _quotas_df.loc[_quotas_df['InstanceType'] == instance_type,
                                    spot_header].values[0]
        return quota_code

    except IndexError:
        return None


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    return common.validate_region_zone_impl('aws', _get_df(), region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot,
                                       region, zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_get_df(),
                                                        instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None) -> Optional[str]:
    del disk_tier  # unused
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'

    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory
    instance_type_prefix = tuple(
        f'{family}.' for family in _DEFAULT_INSTANCE_FAMILY)
    df = _get_df()
    df = df[df['InstanceType'].str.startswith(instance_type_prefix)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(
        _get_df(), instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators/cpus/memory with sorted prices and a list of candidates with
    fuzzy search.
    """
    return common.get_instance_type_for_accelerator_impl(df=_get_df(),
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _get_df()
    df = df[df['InstanceType'] == instance_type]
    region_list = common.get_region_zones(df, use_spot)
    # Hack: Enforce US regions are always tried first:
    #   [US regions sorted by price] + [non-US regions sorted by price]
    us_region_list = []
    other_region_list = []
    for region in region_list:
        if region.name.startswith('us-'):
            us_region_list.append(region)
        else:
            other_region_list.append(region)
    return us_region_list + other_region_list


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in AWS offering accelerators."""
    del require_price  # Unused.
    return common.list_accelerators_impl('AWS', _get_df(), gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive,
                                         all_regions)


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    global _image_df

    image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    if image_id is None:
        # Refresh the image catalog and try again, if the image tag is not
        # found.
        logger.debug('Refreshing the image catalog and trying again.')
        _image_df = common.read_catalog('aws/images.csv',
                                        pull_frequency_hours=0)
        image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    return image_id


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    return common.is_image_tag_valid_impl(_image_df, tag, region)
