"""Tests for Azure utilities and provisioning config."""
import pytest

from sky.clouds.utils import azure_utils
from sky.provision.azure.config import _remove_msi_resources_from_template
from sky.provision.azure.config import _remove_network_resources_from_template
from sky.provision.azure.config import _resolve_custom_managed_identity


def test_validate_image_id():
    # Valid marketplace image ID
    azure_utils.validate_image_id('publisher:offer:sku:version')

    # Valid community image ID
    azure_utils.validate_image_id(
        '/CommunityGalleries/gallery-name/Images/image-name')

    # Invalid format (neither marketplace nor community)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            'CommunityGalleries/gallery-name/Images/image-name')

    # Invalid marketplace image ID (too few parts)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id('publisher:offer:sku')


class TestResolveCustomManagedIdentity:
    """Tests for _resolve_custom_managed_identity."""

    def test_none_remote_identity(self):
        """Returns None when remote_identity is None."""
        assert _resolve_custom_managed_identity(None, 'sub-id', 'rg') is None

    def test_local_credentials(self):
        """Returns None for LOCAL_CREDENTIALS."""
        assert _resolve_custom_managed_identity('LOCAL_CREDENTIALS', 'sub-id',
                                                'rg') is None

    def test_service_account(self):
        """Returns None for SERVICE_ACCOUNT."""
        assert _resolve_custom_managed_identity('SERVICE_ACCOUNT', 'sub-id',
                                                'rg') is None

    def test_no_upload(self):
        """Returns None for NO_UPLOAD."""
        assert _resolve_custom_managed_identity('NO_UPLOAD', 'sub-id',
                                                'rg') is None

    def test_custom_msi_name(self):
        """Custom MSI name is resolved to full resource ID."""
        result = _resolve_custom_managed_identity('my-custom-msi', 'sub-123',
                                                  'my-rg')
        expected = ('/subscriptions/sub-123'
                    '/resourceGroups/my-rg'
                    '/providers/Microsoft.ManagedIdentity'
                    '/userAssignedIdentities/my-custom-msi')
        assert result == expected

    def test_full_resource_id(self):
        """Full resource ID is used directly."""
        full_id = ('/subscriptions/sub-456/resourceGroups/other-rg'
                   '/providers/Microsoft.ManagedIdentity'
                   '/userAssignedIdentities/existing-msi')
        result = _resolve_custom_managed_identity(full_id, 'sub-123', 'my-rg')
        assert result == full_id


class TestRemoveMsiResourcesFromTemplate:
    """Tests for _remove_msi_resources_from_template."""

    def test_removes_msi_and_role_assignment(self):
        """MSI and role assignment resources are removed."""
        template = _make_arm_template()
        _remove_msi_resources_from_template(template)
        resource_types = [r['type'] for r in template['resources']]
        assert 'Microsoft.ManagedIdentity/userAssignedIdentities' \
            not in resource_types
        assert 'Microsoft.Authorization/roleAssignments' \
            not in resource_types
        assert 'Microsoft.Network/networkSecurityGroups' in resource_types
        assert 'Microsoft.Network/virtualNetworks' in resource_types
        assert 'msi' not in template['outputs']
        assert 'subnet' in template['outputs']
        assert 'nsg' in template['outputs']


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
