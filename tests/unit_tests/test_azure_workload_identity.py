"""Tests for per-workspace Azure identity (workload identity federation)."""
# pylint: disable=redefined-outer-name
# pylint: disable=protected-access
from unittest import mock

import pytest

from sky import skypilot_config
from sky.adaptors import azure
from sky.clouds import azure as azure_cloud
from sky.provision.azure import instance
from sky.utils import common_utils
from sky.utils import schemas

_WS_A = {
    'subscription_id': 'sub-a',
    'tenant_id': 'tenant-a',
    'client_id': 'client-a',
    'resource_group_vm': 'rg-a',
    'remote_identity': ('/subscriptions/sub-a/resourceGroups/rg-a/providers/'
                        'Microsoft.ManagedIdentity/userAssignedIdentities/'
                        'node'),
}
_WS_B = dict(_WS_A,
             subscription_id='sub-b',
             tenant_id='tenant-b',
             client_id='client-b')


@pytest.fixture
def workspaces(monkeypatch, tmp_path):
    """Two workspaces with their own Azure identity, and one without."""
    token_file = tmp_path / 'token'
    token_file.write_text('projected-token')
    monkeypatch.setenv(azure.WORKLOAD_IDENTITY_TOKEN_FILE_ENV_VAR,
                       str(token_file))
    config = {
        'workspaces': {
            'ws-a': {
                'azure': _WS_A
            },
            'ws-b': {
                'azure': _WS_B
            },
            'ws-cli': {},
        }
    }

    def _workspace_cloud(cloud, workspace=None):
        ws = workspace or skypilot_config.get_active_workspace()
        return config['workspaces'][ws].get(cloud, {})

    monkeypatch.setattr(skypilot_config, 'get_workspace_cloud',
                        _workspace_cloud)
    azure.get_workload_identity_credential.cache_clear()
    azure.get_client.cache_clear()
    yield config, str(token_file)
    azure.get_workload_identity_credential.cache_clear()
    azure.get_client.cache_clear()


def test_workspace_azure_schema_accepts_identity_and_refuses_others():
    config = {'workspaces': {'w': {'azure': _WS_A}}}
    common_utils.validate_schema(config, schemas.get_config_schema(), '')
    with pytest.raises(ValueError):
        common_utils.validate_schema(
            {'workspaces': {
                'w': {
                    'azure': {
                        'client_secret': 'x'
                    }
                }
            }}, schemas.get_config_schema(), '')


def test_clients_use_the_workspace_identity_and_are_cached_per_identity(
        workspaces):
    _, token_file = workspaces
    made = []

    class _FakeCredential:

        def __init__(self, *, tenant_id, client_id, token_file_path):
            made.append((tenant_id, client_id, token_file_path))

    with mock.patch('azure.identity.WorkloadIdentityCredential',
                    _FakeCredential), \
            mock.patch('azure.identity.AzureCliCredential',
                       side_effect=AssertionError('CLI credential used')):
        with skypilot_config.local_active_workspace_ctx('ws-a'):
            c1 = azure.get_client('compute', 'sub-a')
            assert azure.get_client('compute', 'sub-a') is c1
            assert azure.get_client('compute', 'sub-x') is not c1
        with skypilot_config.local_active_workspace_ctx('ws-b'):
            assert azure.get_client('compute', 'sub-a') is not c1
    assert sorted(set(made)) == [('tenant-a', 'client-a', token_file),
                                 ('tenant-b', 'client-b', token_file)]


def test_subscription_and_identity_come_from_the_workspace(workspaces):
    del workspaces
    with skypilot_config.local_active_workspace_ctx('ws-a'):
        assert azure_cloud.Azure.get_project_id() == 'sub-a'
        assert azure.get_subscription_id() == 'sub-a'
        assert azure_cloud.Azure.get_user_identities() == [[
            'client-a [tenant_id=tenant-a] [subscription_id=sub-a]'
        ]]
    with skypilot_config.local_active_workspace_ctx('ws-b'):
        assert azure_cloud.Azure.get_project_id() == 'sub-b'


def test_no_azure_credential_files_are_uploaded_for_workload_identity(
        workspaces):
    del workspaces
    with skypilot_config.local_active_workspace_ctx('ws-a'):
        assert azure_cloud.Azure().get_credential_file_mounts() == {}
    with skypilot_config.local_active_workspace_ctx('ws-cli'):
        assert '~/.azure/msal_token_cache.json' in (
            azure_cloud.Azure().get_credential_file_mounts())


def test_check_credentials_names_a_missing_token_file(workspaces, monkeypatch):
    del workspaces
    monkeypatch.delenv(azure.WORKLOAD_IDENTITY_TOKEN_FILE_ENV_VAR)
    with skypilot_config.local_active_workspace_ctx('ws-a'):
        ok, reason = azure_cloud.Azure._check_credentials()
    assert not ok
    assert azure.WORKLOAD_IDENTITY_TOKEN_FILE_ENV_VAR in reason


def test_half_an_identity_is_refused(monkeypatch):
    monkeypatch.setattr(skypilot_config,
                        'get_workspace_cloud',
                        lambda cloud, workspace=None: {'tenant_id': 't'})
    with pytest.raises(ValueError, match='client_id'):
        azure.get_workspace_identity()


@pytest.mark.parametrize('cli_state', [True, False])
def test_without_a_workspace_identity(workspaces, monkeypatch, tmp_path,
                                      cli_state):
    """The az CLI login if there is one, else the machine's identity."""
    del workspaces
    monkeypatch.setenv('HOME', str(tmp_path))
    if cli_state:
        (tmp_path / '.azure').mkdir()
    with mock.patch('azure.identity.AzureCliCredential') as cli, \
            mock.patch('azure.identity.ManagedIdentityCredential') as msi:
        with skypilot_config.local_active_workspace_ctx('ws-cli'):
            azure.get_client('compute', 'sub-a')
    assert cli.called == cli_state
    assert msi.called != cli_state
    # The head node must not grow a ~/.azure from an autostop.
    assert (tmp_path / '.azure').exists() == cli_state


def test_teardown_waits_for_the_vnet_before_deleting_its_nsg():
    """An NSG still held by the VNet's subnet cannot be deleted."""
    order = []

    def deleter(what):

        def _delete(**kwargs):
            del kwargs
            order.append(f'{what} delete')
            return mock.Mock(result=lambda: order.append(f'{what} done'))

        return _delete

    network = mock.Mock()
    network.virtual_networks.begin_delete = deleter('vnet')
    network.network_security_groups.begin_delete = deleter('nsg')
    resource = mock.Mock()
    resource.resources.list_by_resource_group.return_value = [
        mock.Mock(type='Microsoft.Network/virtualNetworks', name='x'),
        mock.Mock(type='Microsoft.Network/networkSecurityGroups', name='x'),
    ]
    for r, n in zip(resource.resources.list_by_resource_group.return_value,
                    ('sky-c-1-vnet', 'sky-c-1-nsg')):
        r.name = n
    clients = {'network': network, 'resource': resource}
    with mock.patch.object(
            azure,
            'get_client',
            lambda name, sub=None: clients.get(name, mock.Mock())):
        instance.delete_vm_and_attached_resources('sub', 'rg', 'sky-c')
    assert order.index('vnet done') < order.index('nsg delete')
