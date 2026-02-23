import pytest

from sky.clouds.utils import azure_utils
from sky.provision.azure.config import _remove_network_resources_from_template


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


class TestRemoveNetworkResourcesFromTemplate:
    """Tests for _remove_network_resources_from_template."""

    def test_removes_vnet(self):
        """VNet resource is removed, NSG is kept."""
        template = _make_arm_template()
        _remove_network_resources_from_template(template)
        resource_types = [r['type'] for r in template['resources']]
        assert 'Microsoft.Network/virtualNetworks' not in resource_types
        assert 'Microsoft.Network/networkSecurityGroups' in resource_types
        assert 'Microsoft.ManagedIdentity/userAssignedIdentities' \
            in resource_types
        assert 'subnet' not in template['outputs']
        assert 'nsg' in template['outputs']
        assert 'msi' in template['outputs']


def _make_arm_template():
    """Create a minimal ARM template for testing."""
    return {
        'resources': [
            {
                'type': 'Microsoft.ManagedIdentity/userAssignedIdentities'
            },
            {
                'type': 'Microsoft.Authorization/roleAssignments'
            },
            {
                'type': 'Microsoft.Network/networkSecurityGroups'
            },
            {
                'type': 'Microsoft.Network/virtualNetworks'
            },
        ],
        'outputs': {
            'subnet': {
                'type': 'string',
                'value': 'subnet-id'
            },
            'nsg': {
                'type': 'string',
                'value': 'nsg-id'
            },
            'msi': {
                'type': 'string',
                'value': 'msi-id'
            },
        }
    }
