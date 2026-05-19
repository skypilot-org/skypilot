"""Unit tests for Storage.construct() region propagation and Azure SKU.

Covers the fix for https://github.com/skypilot-org/skypilot/issues/8504,
where a user-specified ``store: azure`` would always create the underlying
storage account in ``eastus`` (and as GRS) regardless of the cluster's
region.
"""
from unittest import mock

import pytest

from sky.data import storage as storage_lib


@pytest.fixture(autouse=True)
def _patch_validate_storage_spec():
    """Skip the validation that requires real cloud credentials."""
    with mock.patch.object(storage_lib.Storage,
                           '_validate_storage_spec',
                           return_value=None):
        yield


@pytest.fixture(autouse=True)
def _patch_global_user_state():
    """Pretend there's no pre-existing handle so construct() takes the
    'create new stores' branch.
    """
    with mock.patch.object(storage_lib.global_user_state,
                           'get_handle_from_storage_name',
                           return_value=None):
        yield


def _make_storage(stores):
    return storage_lib.Storage(name='sky-test-bucket',
                               source='/bin',
                               stores=stores,
                               mode=storage_lib.StorageMode.MOUNT)


class TestConstructRegionForwarding:
    """Ensure ``construct()`` forwards the task's preferred region to
    ``add_store`` only when the store type matches the task's cloud.
    """

    def test_explicit_azure_store_inherits_task_region(self):
        storage_obj = _make_storage(stores=[storage_lib.StoreType.AZURE])

        with mock.patch.object(storage_lib.Storage,
                               'add_store') as add_store_mock:
            storage_obj.construct(
                preferred_store_type=storage_lib.StoreType.AZURE,
                preferred_region='westus2')

        add_store_mock.assert_called_once_with(storage_lib.StoreType.AZURE,
                                               region='westus2')

    def test_explicit_gcs_store_with_azure_task_does_not_get_azure_region(self):
        # Mismatched clouds: don't pass an Azure region to a GCS bucket.
        storage_obj = _make_storage(stores=[storage_lib.StoreType.GCS])

        with mock.patch.object(storage_lib.Storage,
                               'add_store') as add_store_mock:
            storage_obj.construct(
                preferred_store_type=storage_lib.StoreType.AZURE,
                preferred_region='westus2')

        add_store_mock.assert_called_once_with(storage_lib.StoreType.GCS,
                                               region=None)

    def test_construct_without_preference_preserves_legacy_behavior(self):
        # Backwards compat: callers that don't pass hints should behave as
        # before (region=None at the add_store layer).
        storage_obj = _make_storage(stores=[storage_lib.StoreType.AZURE])

        with mock.patch.object(storage_lib.Storage,
                               'add_store') as add_store_mock:
            storage_obj.construct()

        add_store_mock.assert_called_once_with(storage_lib.StoreType.AZURE,
                                               region=None)

    def test_construct_is_idempotent_with_or_without_hints(self):
        storage_obj = _make_storage(stores=[storage_lib.StoreType.AZURE])

        with mock.patch.object(storage_lib.Storage,
                               'add_store') as add_store_mock:
            storage_obj.construct(
                preferred_store_type=storage_lib.StoreType.AZURE,
                preferred_region='westus2')
            # Second call must short-circuit (already constructed).
            storage_obj.construct(
                preferred_store_type=storage_lib.StoreType.AZURE,
                preferred_region='eastus')

        assert add_store_mock.call_count == 1


class TestAzureStorageAccountSku:
    """The Azure storage account default SKU should be locally-redundant
    (Standard_LRS), not geo-redundant (Standard_GRS).
    """

    def test_create_storage_account_uses_lrs_sku(self):
        store = storage_lib.AzureBlobStore.__new__(storage_lib.AzureBlobStore)
        store.region = 'westus2'
        store.storage_client = mock.MagicMock()
        # The IAM role assignment path is exercised after begin_create; stub
        # the azure adaptor to make that no-op.
        creation_response = mock.MagicMock()
        creation_response.id = ('/subscriptions/x/resourceGroups/rg/providers/'
                                'Microsoft.Storage/storageAccounts/sa')
        store.storage_client.storage_accounts.begin_create.return_value.\
            result.return_value = creation_response

        with mock.patch('sky.adaptors.azure.assign_storage_account_iam_role'):
            store._create_storage_account(  # pylint: disable=protected-access
                resource_group_name='rg',
                storage_account_name='sa')

        store.storage_client.storage_accounts.begin_create.\
            assert_called_once()
        _, args, _ = (
            store.storage_client.storage_accounts.begin_create.mock_calls[0])
        # begin_create(resource_group_name, storage_account_name, params)
        params = args[2]
        assert params['sku']['name'] == 'Standard_LRS'
        assert params['location'] == 'westus2'
