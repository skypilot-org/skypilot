"""Tests for Azure utilities and provisioning config."""
from unittest import mock

import pytest

from sky import clouds
from sky import exceptions
from sky.adaptors import azure
from sky.clouds.utils import azure_utils
from sky.provision.azure.config import _remove_msi_resources_from_template
from sky.provision.azure.config import _remove_network_resources_from_template
from sky.provision.azure.config import _resolve_custom_managed_identity

_SIG_IMAGE_ID = ('/subscriptions/sub-123/resourceGroups/my-rg/providers/'
                 'Microsoft.Compute/galleries/my-gallery/images/my-image/'
                 'versions/1.0.3')


def test_validate_image_id():
    # Valid marketplace image ID
    azure_utils.validate_image_id('publisher:offer:sku:version')

    # Valid community image ID
    azure_utils.validate_image_id(
        '/CommunityGalleries/gallery-name/Images/image-name')

    # Valid private Shared Image Gallery image-version resource ID
    azure_utils.validate_image_id(_SIG_IMAGE_ID)

    # Invalid format (neither marketplace nor community)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            'CommunityGalleries/gallery-name/Images/image-name')

    # Invalid marketplace image ID (too few parts)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id('publisher:offer:sku')

    # A resource ID missing the version segment is not a valid SIG image ID.
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
            'Microsoft.Compute/galleries/my-gallery/images/my-image')


def test_parse_shared_image_gallery_id():
    parsed = azure_utils.parse_shared_image_gallery_id(_SIG_IMAGE_ID)
    assert parsed == {
        'subscription_id': 'sub-123',
        'resource_group': 'my-rg',
        'gallery_name': 'my-gallery',
        'image_name': 'my-image',
        'version': '1.0.3',
    }

    # Azure resource IDs are case-insensitive in their literal segments.
    assert azure_utils.parse_shared_image_gallery_id(
        _SIG_IMAGE_ID.replace('resourceGroups', 'resourcegroups')) is not None

    # Non-SIG image IDs return None rather than raising.
    assert azure_utils.parse_shared_image_gallery_id(
        'publisher:offer:sku:version') is None
    assert azure_utils.parse_shared_image_gallery_id(
        '/CommunityGalleries/gallery-name/Images/image-name') is None

    # A gallery image *definition* (no version segment) is not a bootable
    # image and must not be treated as one.
    assert azure_utils.parse_shared_image_gallery_id(
        '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
        'Microsoft.Compute/galleries/my-gallery/images/my-image') is None

    # An unrelated resource ID (managed identity) returns None.
    assert azure_utils.parse_shared_image_gallery_id(
        '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
        'Microsoft.ManagedIdentity/userAssignedIdentities/my-msi') is None


def _mock_compute_client(size_in_gb):
    client = mock.MagicMock()
    version = mock.MagicMock()
    version.storage_profile.os_disk_image.size_in_gb = size_in_gb
    client.gallery_image_versions.get.return_value = version
    return client


def test_get_shared_image_gallery_image_size():
    client = _mock_compute_client(size_in_gb=64)
    assert azure_utils.get_shared_image_gallery_image_size(
        client, 'my-rg', 'my-gallery', 'my-image', '1.0.3') == 64.0
    client.gallery_image_versions.get.assert_called_once_with(
        resource_group_name='my-rg',
        gallery_name='my-gallery',
        gallery_image_name='my-image',
        gallery_image_version_name='1.0.3')


def test_get_shared_image_gallery_image_size_missing_size():
    client = _mock_compute_client(size_in_gb=None)
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_get_shared_image_gallery_image_size_missing_storage_profile():
    client = mock.MagicMock()
    version = mock.MagicMock()
    version.storage_profile = None
    client.gallery_image_versions.get.return_value = version
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_get_shared_image_gallery_image_size_azure_error():
    client = mock.MagicMock()
    client.gallery_image_versions.get.side_effect = azure.exceptions(
    ).AzureError('boom')
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_azure_get_image_size_shared_gallery_uses_image_subscription():
    # The compute client must target the image's own subscription, which may
    # differ from the active project.
    with mock.patch.object(azure, 'get_client') as mock_get_client, \
            mock.patch.object(azure_utils,
                              'get_shared_image_gallery_image_size',
                              return_value=64.0) as mock_size:
        size = clouds.Azure.get_image_size(_SIG_IMAGE_ID, region=None)
    assert size == 64.0
    mock_get_client.assert_called_once_with('compute', 'sub-123')
    assert mock_size.call_args.args[1:] == ('my-rg', 'my-gallery', 'my-image',
                                            '1.0.3')


def test_azure_get_image_size_shared_gallery_falls_back_on_no_access():
    # If the image's subscription is not readable, fall back to 0.0 instead of
    # failing the launch.
    with mock.patch.object(azure, 'get_client'), \
            mock.patch.object(
                azure_utils, 'get_shared_image_gallery_image_size',
                side_effect=exceptions.ResourcesUnavailableError('no access')):
        size = clouds.Azure.get_image_size(_SIG_IMAGE_ID, region=None)
    assert size == 0.0


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
