"""SSH Catalog.

This catalog inherits from the Kubernetes catalog as SSH cloud is a wrapper
around Kubernetes that uses SSH-specific contexts.
"""
import typing
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.catalog import CloudFilter
from sky.catalog import common
from sky.catalog import kubernetes_catalog
from sky.clouds import ssh

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    from sky.adaptors import common as adaptors_common
    pd = adaptors_common.LazyImport('pandas')

_PULL_FREQUENCY_HOURS = 7

# Reuse the Kubernetes images catalog for SSH cloud.
# We keep pull_frequency_hours so we can remotely update the default image paths
_image_df = common.read_catalog('kubernetes/images.csv',
                                pull_frequency_hours=_PULL_FREQUENCY_HOURS)


def get_image_id_from_tag(tag: str, region: Optional[str]) -> Optional[str]:
    """Returns the image id from the tag.

    Delegates to Kubernetes catalog implementation.
    """
    return kubernetes_catalog.get_image_id_from_tag(tag, region)


def is_image_tag_valid(tag: str, region: Optional[str]) -> bool:
    """Returns whether the image tag is valid.

    Delegates to Kubernetes catalog implementation.
    """
    return kubernetes_catalog.is_image_tag_valid(tag, region)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """List accelerators in SSH-based Kubernetes clusters.

    Delegates to the Kubernetes _list_accelerators function but restricts to
    SSH contexts.
    """
    return _list_accelerators(gpus_only,
                              name_filter,
                              region_filter,
                              quantity_filter,
                              case_sensitive,
                              all_regions,
                              require_price,
                              realtime=False)[0]


def list_accelerators_realtime(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str,
                                                                          int]]:
    """List accelerators in SSH Node Pools with real-time information.

    Delegates to the Kubernetes _list_accelerators function but restricts to
    SSH contexts.
    """
    return _list_accelerators(gpus_only,
                              name_filter,
                              region_filter,
                              quantity_filter,
                              case_sensitive,
                              all_regions,
                              require_price,
                              realtime=True)


def _list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
    realtime: bool = False
) -> Tuple[Dict[str, List[common.InstanceTypeInfo]], Dict[str, int], Dict[str,
                                                                          int]]:
    """List accelerators in SSH-based Kubernetes clusters.

    Lists the SSH Node Pool contexts allowed by the active workspace through
    the Kubernetes catalog, each pool with its own entries, like the table
    output of ``sky gpus list --infra ssh``. If region_filter is specified,
    only that SSH context is listed, and nothing if it is not an allowed SSH
    context.
    """
    # Every allowed SSH context is listed regardless of all_regions: see
    # kubernetes_catalog.list_accelerators_in_contexts().
    del all_regions, require_price  # Unused.

    # If a specific region is requested, ensure it's an SSH context
    if region_filter is not None and not region_filter.startswith('ssh-'):
        return {}, {}, {}

    # Get SSH contexts
    ssh_contexts = ssh.SSH.existing_allowed_contexts()

    if region_filter is not None:
        # If the requested SSH context is not allowed, return empty results
        contexts = [region_filter] if region_filter in ssh_contexts else []
    else:
        contexts = ssh_contexts

    result = kubernetes_catalog.list_accelerators_in_contexts(
        contexts,
        gpus_only=gpus_only,
        name_filter=name_filter,
        quantity_filter=quantity_filter,
        case_sensitive=case_sensitive,
        realtime=realtime)
    if not realtime:
        return result[0], {}, {}
    return result


def validate_region_zone(
        region_name: Optional[str],
        zone_name: Optional[str],
        clouds: CloudFilter = None) -> Tuple[Optional[str], Optional[str]]:
    """Validates the region and zone for SSH cloud.

    Delegates to the Kubernetes catalog implementation but ensures
    the region is a valid SSH context.
    """
    # Delegate to Kubernetes implementation
    region, zone = kubernetes_catalog.validate_region_zone(
        region_name, zone_name, clouds)

    # Get SSH contexts
    ssh_contexts = ssh.SSH.existing_allowed_contexts()

    # If a region is specified, ensure it's in the list of SSH contexts
    if region is not None and region not in ssh_contexts:
        return None, None

    return region, zone
