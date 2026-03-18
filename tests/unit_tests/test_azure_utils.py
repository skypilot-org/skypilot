"""Tests for Azure utilities and provisioning config."""
# pylint: disable=import-outside-toplevel,protected-access,unused-argument
import os
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import azure
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


class TestIsNonCliCredential:
    """Tests for azure.is_non_cli_credential."""

    def test_true_when_client_id_set(self):
        """Returns True when AZURE_CLIENT_ID is set."""
        with mock.patch.dict(os.environ, {'AZURE_CLIENT_ID': 'test-id'}):
            assert azure.is_non_cli_credential() is True

    def test_false_when_not_set(self):
        """Returns False when AZURE_CLIENT_ID is not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            assert azure.is_non_cli_credential() is False


class TestGetSubscriptionId:
    """Tests for azure.get_subscription_id with env var support."""

    def _clear_cache(self):
        azure.get_subscription_id.__wrapped__.cache_clear()

    def test_from_env(self):
        """Returns AZURE_SUBSCRIPTION_ID when set."""
        self._clear_cache()
        with mock.patch.dict(os.environ,
                             {'AZURE_SUBSCRIPTION_ID': 'env-sub-123'}):
            assert azure.get_subscription_id() == 'env-sub-123'
        self._clear_cache()

    def test_fallback_to_cli(self):
        """Falls back to CLI profile when env var not set."""
        self._clear_cache()
        env = os.environ.copy()
        env.pop('AZURE_SUBSCRIPTION_ID', None)
        with mock.patch.dict(os.environ, env, clear=True):
            mock_profile = mock.MagicMock()
            mock_profile.get_subscription_id.return_value = 'cli-sub-456'
            with mock.patch('azure.common.credentials.get_cli_profile',
                            return_value=mock_profile):
                assert azure.get_subscription_id() == 'cli-sub-456'
        self._clear_cache()


class TestGetCurrentAccountUser:
    """Tests for azure.get_current_account_user with SP support."""

    def test_sp_returns_client_id(self):
        """Returns AZURE_CLIENT_ID when SP env vars are set."""
        with mock.patch.dict(os.environ, {'AZURE_CLIENT_ID': 'sp-client-id'}):
            assert azure.get_current_account_user() == 'sp-client-id'

    def test_cli_fallback(self):
        """Falls back to CLI profile when no SP env vars."""
        with mock.patch.dict(os.environ, {}, clear=True):
            mock_profile = mock.MagicMock()
            mock_profile.get_current_account_user.return_value = 'user@test.com'
            with mock.patch('azure.common.credentials.get_cli_profile',
                            return_value=mock_profile):
                assert azure.get_current_account_user() == 'user@test.com'


class TestGetCredential:
    """Tests for azure.get_credential hybrid approach."""

    @mock.patch('os.path.isfile', return_value=True)
    def test_cli_fast_path(self, mock_isfile):
        """When token cache exists, returns AzureCliCredential."""
        mock_identity = mock.MagicMock()
        with mock.patch.dict('sys.modules', {'azure.identity': mock_identity}):
            from azure import identity
            identity.AzureCliCredential(process_timeout=30)
            mock_identity.AzureCliCredential.assert_called_with(
                process_timeout=30)

    @mock.patch('os.path.isfile', return_value=False)
    def test_non_cli_default_credential(self, mock_isfile):
        """When no token cache, returns DefaultAzureCredential."""
        mock_identity = mock.MagicMock()
        with mock.patch.dict('sys.modules', {'azure.identity': mock_identity}):
            from azure import identity
            identity.DefaultAzureCredential(exclude_cli_credential=True,
                                            exclude_powershell_credential=True)
            mock_identity.DefaultAzureCredential.assert_called_with(
                exclude_cli_credential=True, exclude_powershell_credential=True)


class TestCheckCredentialsSP:
    """Tests for Azure._check_credentials with SP auth."""

    @mock.patch.dict(os.environ, {'AZURE_CLIENT_ID': 'test-sp-id'})
    @mock.patch('sky.adaptors.common.can_import_modules', return_value=True)
    @mock.patch(
        'sky.clouds.azure.Azure.get_active_user_identity',
        return_value=['test-sp-id [subscription_id=sub-123]'],
    )
    def test_sp_skips_cli_checks(self, mock_identity, mock_imports):
        """SP env vars set: skips CLI file/version checks, succeeds."""
        from sky.clouds.azure import Azure
        ok, msg = Azure._check_credentials()
        assert ok is True
        assert msg is None

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('sky.adaptors.common.can_import_modules', return_value=True)
    @mock.patch('os.path.isfile', return_value=True)
    @mock.patch('sky.clouds.azure._run_output', return_value='az 2.50.0')
    @mock.patch(
        'sky.clouds.azure.Azure.get_active_user_identity',
        return_value=['user@test.com [subscription_id=sub-123]'],
    )
    def test_cli_flow_unchanged(self, mock_identity, mock_run, mock_isfile,
                                mock_imports):
        """No SP env vars: uses existing CLI flow."""
        from sky.clouds.azure import Azure
        ok, msg = Azure._check_credentials()
        assert ok is True
        assert msg is None


class TestCredentialFileMounts:
    """Tests for Azure.get_credential_file_mounts with SP support."""

    def test_empty_for_sp(self):
        """Returns {} when AZURE_CLIENT_ID is set."""
        from sky.clouds.azure import Azure
        instance = Azure()
        with mock.patch.dict(os.environ, {'AZURE_CLIENT_ID': 'test-sp-id'}):
            result = instance.get_credential_file_mounts()
            assert result == {}

    def test_cli_mounts_files(self):
        """Returns standard credential files when no SP env vars."""
        from sky.clouds.azure import Azure
        instance = Azure()
        with mock.patch.dict(os.environ, {}, clear=True):
            result = instance.get_credential_file_mounts()
            assert len(result) == 4
            assert '~/.azure/msal_token_cache.json' in result


class TestGetUserIdentitiesSP:
    """Tests for Azure.get_user_identities with SP support."""

    @mock.patch.dict(os.environ, {
        'AZURE_CLIENT_ID': 'sp-client-id',
        'AZURE_SUBSCRIPTION_ID': 'sub-789',
    })
    def test_sp_returns_client_id_identity(self):
        """Returns client ID based identity for SP auth."""
        from sky.clouds.azure import Azure
        Azure.get_user_identities.cache_clear()
        azure.get_subscription_id.__wrapped__.cache_clear()
        result = Azure.get_user_identities()
        assert result == [['sp-client-id [subscription_id=sub-789]']]
        Azure.get_user_identities.cache_clear()
        azure.get_subscription_id.__wrapped__.cache_clear()


class TestAssignStorageRoleSPGraphFailure:
    """Tests for assign_storage_account_iam_role SP Graph API failure."""

    @mock.patch.dict(os.environ, {
        'AZURE_CLIENT_ID': 'sp-client-id',
        'AZURE_SUBSCRIPTION_ID': 'sub-123',
    })
    @mock.patch('sky.adaptors.azure.get_client')
    @mock.patch('sky.adaptors.azure.get_subscription_id',
                return_value='sub-123')
    def test_graph_failure_raises_actionable_error(self, mock_sub_id,
                                                   mock_get_client):
        """SP mode + Graph API fails -> raises StorageBucketCreateError."""
        mock_graph_client = mock.MagicMock()
        mock_auth_client = mock.MagicMock()

        def side_effect(name, *args, **kwargs):
            if name == 'graph':
                return mock_graph_client
            if name == 'authorization':
                return mock_auth_client
            return mock.MagicMock()

        mock_get_client.side_effect = side_effect

        # Make the Graph API call raise an exception
        import asyncio
        future = asyncio.Future()
        future.set_exception(
            PermissionError('Insufficient privileges for Graph API'))
        mock_graph_client.service_principals.get.return_value = future

        storage_account_id = ('/subscriptions/sub-123/storageAccounts/test')
        with pytest.raises(exceptions.StorageBucketCreateError,
                           match='Graph API permissions'):
            azure.assign_storage_account_iam_role(
                storage_account_name='teststorage',
                storage_account_id=storage_account_id,
            )
