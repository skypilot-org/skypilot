"""Utilies for Azure"""

import typing

from sky import exceptions
from sky.adaptors import azure
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from azure.mgmt import compute as azure_compute
    from azure.mgmt.compute import models as azure_compute_models


def validate_image_id(image_id: str):
    """Check if the image ID has a valid format.

    Raises:
        ValueError: If the image ID is invalid.
    """
    image_id_colon_splitted = image_id.split(':')
    image_id_slash_splitted = image_id.split('/')
    if len(image_id_slash_splitted) != 5 and len(image_id_colon_splitted) != 4:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Invalid image id for Azure: {image_id}. Expected format: \n'
                '* Marketplace image ID: <publisher>:<offer>:<sku>:<version>\n'
                '* Community image ID: '
                '/CommunityGalleries/<gallery-name>/Images/<image-name>')
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
