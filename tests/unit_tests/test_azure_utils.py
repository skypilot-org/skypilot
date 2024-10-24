import pytest

from sky.clouds.utils import azure_utils


def test_validate_image_id():
    # Valid marketplace image ID
    azure_utils.validate_image_id("publisher:offer:sku:version")

    # Valid community image ID
    azure_utils.validate_image_id(
        "/CommunityGalleries/gallery-name/Images/image-name")

    # Invalid format (neither marketplace nor community)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            "CommunityGalleries/gallery-name/Images/image-name")

    # Invalid marketplace image ID (too few parts)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id("publisher:offer:sku")
