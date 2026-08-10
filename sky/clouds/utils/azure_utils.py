"""Utilies for Azure"""

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
