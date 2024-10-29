"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
import re
from typing import Dict, List, Optional, Tuple, Union

from sky import clouds as cloud_lib
from sky import sky_logging
from sky.clouds import Azure
from sky.clouds.service_catalog import common
from sky.utils import resources_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# This list should match the list of regions in
# skypilot image generation Packer script's replication_regions
# sky/clouds/service_catalog/images/skypilot-azure-cpu-ubuntu.pkr.hcl
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

# We will select from the following three instance families:
_DEFAULT_INSTANCE_FAMILY = [
    # The latest general-purpose instance family as of Mar. 2023.
    # CPU: Intel Ice Lake 8370C.
    # Memory: 4 GiB RAM per 1 vCPU;
    'Ds_v5',
    # The latest memory-optimized instance family as of Mar. 2023.
    # CPU: Intel Ice Lake 8370C.
    # Memory: 8 GiB RAM per 1 vCPU.
    'Es_v5',
    # The latest compute-optimized instance family as of Mar 2023.
    # CPU: Intel Ice Lake 8370C, Cascade Lake 8272CL, or Skylake 8168.
    # Memory: 2 GiB RAM per 1 vCPU.
    'Fs_v2'
]
_DEFAULT_NUM_VCPUS = 8
_DEFAULT_MEMORY_CPU_RATIO = 4


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.validate_region_zone_impl('azure', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    # Ref: https://azure.microsoft.com/en-us/support/legal/offer-details/
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def _get_instance_family(instance_type: str) -> str:
    if instance_type.startswith('Basic_A'):
        return 'basic_a'

    assert instance_type.startswith('Standard_')
    # Remove the 'Standard_' prefix.
    instance_type = instance_type[len('Standard_'):]
    # Remove the '_Promo' suffix if exists.
    if '_Promo' in instance_type:
        instance_type = instance_type[:-len('_Promo')]

    # TODO(woosuk): Use better regex.
    if '-' in instance_type:
        x = re.match(r'([A-Za-z]+)([0-9]+)(-)([0-9]+)(.*)', instance_type)
        assert x is not None, x
        instance_family = x.group(1) + '_' + x.group(5)
    else:
        x = re.match(r'([A-Za-z]+)([0-9]+)(.*)', instance_type)
        assert x is not None, x
        instance_family = x.group(1) + x.group(3)
    return instance_family


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None) -> Optional[str]:
    if cpus is None and memory is None:
        cpus = f'{_DEFAULT_NUM_VCPUS}+'
    if memory is None:
        memory_gb_or_ratio = f'{_DEFAULT_MEMORY_CPU_RATIO}x'
    else:
        memory_gb_or_ratio = memory
    df = _df[_df['InstanceType'].apply(_get_instance_family).isin(
        _DEFAULT_INSTANCE_FAMILY)]

    def _filter_disk_type(instance_type: str) -> bool:
        valid, _ = Azure.check_disk_tier(instance_type, disk_tier)
        return valid

    df = df.loc[df['InstanceType'].apply(_filter_disk_type)]
    return common.get_instance_type_for_cpus_mem_impl(df, cpus,
                                                      memory_gb_or_ratio)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """Filter the instance types based on resource requirements.

    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure does not support zones.')

    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(
        instance_type: str, use_spot: bool) -> List[cloud_lib.Region]:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


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


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag."""
    global _image_df
    image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    if image_id is None:
        # Refresh the image catalog and try again, if the image tag is not
        # found.
        logger.debug('Refreshing the image catalog and trying again.')
        _image_df = common.read_catalog('azure/images.csv',
                                        pull_frequency_hours=0)
        image_id = common.get_image_id_from_tag_impl(_image_df, tag, region)
    return image_id


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid."""
    # Azure images are not region-specific.
    del region  # Unused.
    return common.is_image_tag_valid_impl(_image_df, tag, None)
