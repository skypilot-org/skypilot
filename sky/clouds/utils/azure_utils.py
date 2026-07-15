"""Utilies for Azure"""

import threading
import time
import typing

from sky import exceptions
from sky.adaptors import azure
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from azure.mgmt import compute as azure_compute
    from azure.mgmt.compute import models as azure_compute_models


def parse_shared_image_gallery_id(
        image_id: str) -> typing.Optional[typing.Dict[str, str]]:
    """Parses a private Shared Image Gallery image-version resource ID.

    Uses Azure's own resource-ID parser so the full ARM grammar is handled
    (case-insensitive segments, escaping) rather than hand-rolled matching.

    Args:
        image_id: An Azure resource ID of the form
            ``/subscriptions/<sub>/resourceGroups/<rg>/providers/
            Microsoft.Compute/galleries/<gallery>/images/<image>/versions/
            <version>``.

    Returns:
        A dict with ``subscription_id``, ``resource_group``, ``gallery_name``,
        ``image_name`` and ``version`` keys, or None if ``image_id`` is not a
        Shared Image Gallery image-version resource ID.
    """
    # pylint: disable=import-outside-toplevel
    from azure.mgmt.core.tools import is_valid_resource_id
    from azure.mgmt.core.tools import parse_resource_id
    if not is_valid_resource_id(image_id):
        return None
    parsed = parse_resource_id(image_id)
    if (parsed.get('namespace', '').lower() != 'microsoft.compute' or
            parsed.get('type', '').lower() != 'galleries' or
            parsed.get('child_type_1', '').lower() != 'images' or
            parsed.get('child_type_2', '').lower() != 'versions'):
        return None
    subscription_id = parsed.get('subscription')
    resource_group = parsed.get('resource_group')
    gallery_name = parsed.get('name')
    image_name = parsed.get('child_name_1')
    version = parsed.get('child_name_2')
    # A syntactically valid ARM id can match the gallery image-version path yet
    # omit a scope segment (e.g. no ``resourceGroups``), leaving a component
    # unset. Treat that as "not a SIG id" so ``validate_image_id`` raises a
    # clean ValueError instead of a KeyError here.
    if not all(
        (subscription_id, resource_group, gallery_name, image_name, version)):
        return None
    return {
        'subscription_id': subscription_id,
        'resource_group': resource_group,
        'gallery_name': gallery_name,
        'image_name': image_name,
        'version': version,
    }


def validate_image_id(image_id: str):
    """Check if the image ID has a valid format.

    Raises:
        ValueError: If the image ID is invalid.
    """
    image_id_colon_splitted = image_id.split(':')
    image_id_slash_splitted = image_id.split('/')
    is_shared_gallery_image = parse_shared_image_gallery_id(
        image_id) is not None
    if (len(image_id_slash_splitted) != 5 and
            len(image_id_colon_splitted) != 4 and not is_shared_gallery_image):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Invalid image id for Azure: {image_id}. Expected format: \n'
                '* Marketplace image ID: <publisher>:<offer>:<sku>:<version>\n'
                '* Community image ID: '
                '/CommunityGalleries/<gallery-name>/Images/<image-name>\n'
                '* Shared Image Gallery image version resource ID: '
                '/subscriptions/<subscription-id>/resourceGroups/'
                '<resource-group>/providers/Microsoft.Compute/galleries/'
                '<gallery>/images/<image>/versions/<version>')
    if len(image_id_slash_splitted) == 5:
        _, gallery_type, _, image_type, _ = image_id.split('/')
        if gallery_type != 'CommunityGalleries' or image_type != 'Images':
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid community image id for Azure: {image_id}.\n'
                    'Expected format: '
                    '/CommunityGalleries/<gallery-name>/Images/<image-name>')


def get_community_image(
        compute_client: 'azure_compute.ComputeManagementClient', image_id: str,
        region: str) -> 'azure_compute_models.CommunityGalleryImage':
    """Get community image from cloud.

    Args:
        image_id: /CommunityGalleries/<gallery-name>/Images/<image-name>
    Raises:
        ResourcesUnavailableError
    """
    try:
        _, _, gallery_name, _, image_name = image_id.split('/')
        return compute_client.community_gallery_images.get(
            location=region,
            public_gallery_name=gallery_name,
            gallery_image_name=image_name)
    except azure.exceptions().AzureError as e:
        raise exceptions.ResourcesUnavailableError(
            f'Community image {image_id} does not exist in region {region}.'
        ) from e


def get_community_image_size(
        compute_client: 'azure_compute.ComputeManagementClient',
        gallery_name: str, image_name: str, region: str) -> float:
    """Get the size of the community image from cloud.

    Args:
        image_id: /CommunityGalleries/<gallery-name>/Images/<image-name>
    Raises:
        ResourcesUnavailableError
    """
    try:
        image_versions = compute_client.community_gallery_image_versions.list(
            location=region,
            public_gallery_name=gallery_name,
            gallery_image_name=image_name,
        )
        image_versions = list(image_versions)
        if not image_versions:
            raise exceptions.ResourcesUnavailableError(
                f'No versions available for Azure community image {image_name}')
        latest_version = image_versions[-1].name

        image_details = compute_client.community_gallery_image_versions.get(
            location=region,
            public_gallery_name=gallery_name,
            gallery_image_name=image_name,
            gallery_image_version_name=latest_version)
        return image_details.storage_profile.os_disk_image.disk_size_gb
    except azure.exceptions().AzureError as e:
        raise exceptions.ResourcesUnavailableError(
            f'Failed to get community image size: {e}.') from e


def get_shared_image_gallery_image_size(
        compute_client: 'azure_compute.ComputeManagementClient',
        resource_group: str, gallery_name: str, image_name: str,
        version: str) -> float:
    """Get the OS disk size of a private Shared Image Gallery image version.

    The image's gallery may live in a different subscription than the cluster,
    so ``compute_client`` must already target the image's subscription.

    Args:
        resource_group: Resource group holding the gallery.
        gallery_name: Shared Image Gallery name.
        image_name: Image definition name.
        version: Image version name.

    Raises:
        ResourcesUnavailableError: If the image version cannot be read, e.g.
            the caller's credentials lack access to the image's subscription,
            or the version does not expose an OS disk size.
    """
    try:
        image_details = compute_client.gallery_image_versions.get(
            resource_group_name=resource_group,
            gallery_name=gallery_name,
            gallery_image_name=image_name,
            gallery_image_version_name=version)
        storage_profile = image_details.storage_profile
        os_disk_image = (storage_profile.os_disk_image
                         if storage_profile is not None else None)
        if os_disk_image is None or os_disk_image.size_in_gb is None:
            raise exceptions.ResourcesUnavailableError(
                f'OS disk size unavailable for Azure Shared Image Gallery '
                f'image {image_name} version {version}.')
        return float(os_disk_image.size_in_gb)
    except azure.exceptions().AzureError as e:
        raise exceptions.ResourcesUnavailableError(
            f'Failed to get Shared Image Gallery image size: {e}.') from e


class RegionQuotaCapacity(typing.NamedTuple):
    """One region's quota picture for the current subscription.

    ``family_headroom`` maps a normalized family name (lowercase, spaces
    stripped, since the Usage API emits names like ``standard NDASv4_A100
    Family`` with stray spaces) to remaining vCPUs (limit - current).
    ``family_by_sku`` keeps the Resource SKUs API's original casing;
    normalize with :func:`_normalize_family` when joining against
    ``family_headroom``.
    """
    family_headroom: typing.Dict[str, int]
    total_vcpu_headroom: typing.Optional[int]
    restricted_skus: typing.FrozenSet[str]
    family_by_sku: typing.Dict[str, str]


class _TimedQuotaSnapshot(typing.NamedTuple):
    fetched_at: float
    capacity: RegionQuotaCapacity


# Quota moves as VMs come and go, but the two Azure API round-trips per
# region take seconds; a short TTL turns a multi-SKU, multi-region
# failover into at most one fetch per region.
_QUOTA_SNAPSHOT_TTL_SECONDS = 300.0
# A snapshot older than this cannot conclusively block a region: quota
# frees up when clusters come down, so a stale "no headroom" reading is
# re-verified against a fresh snapshot before the region is condemned.
# (A stale "yes" is harmless - provisioning proceeds and fails over
# naturally.)
_QUOTA_BLOCK_MAX_AGE_SECONDS = 30.0
_quota_snapshot_lock = threading.Lock()
_quota_snapshots: typing.Dict[str, _TimedQuotaSnapshot] = {}


def _normalize_family(name: str) -> str:
    """Normalizes a family name for matching across the two Azure APIs."""
    return name.replace(' ', '').lower()


def _fetch_region_quota_capacity(region: str) -> RegionQuotaCapacity:
    """Fetches the region's quota picture (Usage + Resource SKUs APIs)."""
    compute_client = azure.get_client('compute', azure.get_subscription_id())

    family_headroom: typing.Dict[str, int] = {}
    total_vcpu_headroom: typing.Optional[int] = None
    for usage in compute_client.usage.list(region):
        # Malformed rows (missing or non-numeric limits) are skipped
        # instead of failing the whole region fetch over one bad entry.
        try:
            headroom = int(float(usage.limit)) - int(float(usage.current_value))
        except (TypeError, ValueError):
            continue
        name = _normalize_family(usage.name.value)
        if name == 'cores':
            total_vcpu_headroom = headroom
        else:
            family_headroom[name] = headroom

    restricted: typing.Set[str] = set()
    family_by_sku: typing.Dict[str, str] = {}
    for sku in compute_client.resource_skus.list(
            filter=f'location eq {region!r}'):
        if sku.resource_type != 'virtualMachines':
            continue
        family_by_sku[sku.name] = sku.family or ''
        for restriction in sku.restrictions or []:
            if (restriction.reason_code == 'NotAvailableForSubscription' and
                    restriction.type == 'Location'):
                restricted.add(sku.name)

    return RegionQuotaCapacity(family_headroom=family_headroom,
                               total_vcpu_headroom=total_vcpu_headroom,
                               restricted_skus=frozenset(restricted),
                               family_by_sku=family_by_sku)


def _fetch_and_cache_region_quota(region: str) -> RegionQuotaCapacity:
    # Fetch outside the lock so slow Azure calls do not serialize other
    # regions; stamp the time after the fetch so a slow fetch does not
    # shorten the snapshot's effective TTL.
    capacity = _fetch_region_quota_capacity(region)
    with _quota_snapshot_lock:
        _quota_snapshots[region] = _TimedQuotaSnapshot(
            fetched_at=time.monotonic(), capacity=capacity)
    return capacity


def _get_region_quota_capacity(
        region: str) -> typing.Tuple[RegionQuotaCapacity, float]:
    """Returns a TTL-cached quota snapshot for ``region`` and its age."""
    with _quota_snapshot_lock:
        cached = _quota_snapshots.get(region)
        if cached is not None:
            age = time.monotonic() - cached.fetched_at
            if age < _QUOTA_SNAPSHOT_TTL_SECONDS:
                return cached.capacity, age
    return _fetch_and_cache_region_quota(region), 0.0


def _instance_type_vcpus(instance_type: str) -> int:
    """vCPUs the instance type consumes, from the catalog (0 = unknown)."""
    # pylint: disable=import-outside-toplevel
    from sky import catalog
    try:
        vcpus, _ = catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                            clouds='azure')
    except ValueError:
        # Unknown instance type: callers fall back to a nonzero-quota check.
        return 0
    return int(vcpus) if vcpus else 0


def _has_quota_headroom(instance_type: str, capacity: RegionQuotaCapacity,
                        use_spot: bool) -> bool:
    """Evaluates one snapshot; True unless it conclusively blocks."""
    if instance_type in capacity.restricted_skus:
        return False
    if instance_type not in capacity.family_by_sku:
        # The SKU is not sold in this region at all.
        return False
    # An empty family name is inconclusive: the family-quota gate below
    # is skipped, but the regional-total gate still applies.
    family = _normalize_family(capacity.family_by_sku[instance_type])

    needed = _instance_type_vcpus(instance_type)
    if needed <= 0:
        # Size unknown: only the conclusive zero-quota case can block.
        needed = 1

    if use_spot:
        spot_headroom = capacity.family_headroom.get('lowprioritycores')
        return spot_headroom is None or spot_headroom >= needed

    if family:
        family_headroom = capacity.family_headroom.get(family)
        if family_headroom is not None and family_headroom < needed:
            return False
    if (capacity.total_vcpu_headroom is not None and
            capacity.total_vcpu_headroom < needed):
        return False
    return True


def check_quota_available(instance_type: str, region: str,
                          use_spot: bool) -> bool:
    """Checks whether ``instance_type`` has launchable quota in ``region``.

    Mirrors the AWS/GCP contract: returns False only on a conclusive no
    (the SKU is restricted for the subscription in the region, the SKU is
    not sold there, or a fresh reading of the vCPU family / regional-total
    headroom is smaller than the SKU's vCPUs); returns True on any doubt
    (unknown SKU size, missing family row) so a provisionable region is
    never preemptively skipped. Azure SDK failures propagate to the call
    site, which logs them and treats the check as inconclusive.

    Headroom is a live reading, so only a *fresh* snapshot may block: a
    cached "no headroom" verdict is re-verified against a fresh fetch
    first (quota may have been freed since the snapshot was taken).

    Args:
        instance_type: Azure SKU name (e.g. ``Standard_NC40ads_H100_v5``).
        region: Azure region name (e.g. ``southcentralus``).
        use_spot: Whether the check is for a spot instance; gated on the
            region-wide low-priority vCPU bucket instead of the on-demand
            family quota.

    Returns:
        False if the region conclusively cannot provision the instance
        type, True otherwise.
    """
    capacity, age = _get_region_quota_capacity(region)
    if _has_quota_headroom(instance_type, capacity, use_spot):
        return True
    if age <= _QUOTA_BLOCK_MAX_AGE_SECONDS:
        return False
    return _has_quota_headroom(instance_type,
                               _fetch_and_cache_region_quota(region), use_spot)
