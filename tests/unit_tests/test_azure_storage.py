"""Unit tests for Azure blob storage account creation.

Regression test for https://github.com/skypilot-org/skypilot/issues/9775:
`_create_storage_account` must pass a typed ``StorageAccountCreateParameters``
model rather than a hand-built dict. azure-mgmt-storage 25.0.0 switched to a
TypeSpec serializer that serializes a dict verbatim and no longer remaps the
top-level ``encryption`` key to ``properties.encryption``, so a raw dict is
rejected by ARM. A typed model serializes to the correct body under both
msrest (<=24.x) and the TypeSpec encoder (25.x).
"""
import unittest.mock as mock

import pytest


def test_create_storage_account_passes_typed_model(monkeypatch):
    storage_models = pytest.importorskip('azure.mgmt.storage.models')
    from sky.data import storage as storage_lib

    # Build an AzureBlobStore without running its heavy __init__; we only
    # exercise _create_storage_account.
    store = storage_lib.AzureBlobStore.__new__(storage_lib.AzureBlobStore)
    store.region = 'eastus'
    store.storage_client = mock.MagicMock()

    captured = {}

    def fake_begin_create(resource_group_name, storage_account_name,
                          parameters):
        captured['parameters'] = parameters
        return mock.MagicMock()

    store.storage_client.storage_accounts.begin_create.side_effect = (
        fake_begin_create)

    # Short-circuit the post-create role-assignment retry loop.
    monkeypatch.setattr(storage_lib.azure, 'assign_storage_account_iam_role',
                        lambda *args, **kwargs: None)

    store._create_storage_account('my-rg', 'myaccount')

    params = captured['parameters']
    # Must be a typed model, not a raw dict.
    assert isinstance(params, storage_models.StorageAccountCreateParameters), (
        f'expected a typed model, got {type(params)}')

    # The model must serialize with `encryption` nested under `properties`,
    # never at the top level. msrest (<=24.x) exposes .serialize(); the
    # TypeSpec model (25.x) exposes .as_dict() -- both yield the wire body.
    body = params.serialize() if hasattr(params,
                                         'serialize') else params.as_dict()
    assert isinstance(body.get('properties'), dict), body
    assert 'encryption' in body['properties'], body
    assert 'encryption' not in body, body
