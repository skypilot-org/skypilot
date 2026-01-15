"""Tests for Kubernetes adaptor."""

import gc
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sky.adaptors import kubernetes
from sky.utils import annotations


@pytest.mark.parametrize(
    'ctor_name, api_func',
    [
        ('CoreV1Api', kubernetes.core_api),
        ('StorageV1Api', kubernetes.storage_api),
        ('RbacAuthorizationV1Api', kubernetes.auth_api),
        ('NetworkingV1Api', kubernetes.networking_api),
        ('CustomObjectsApi', kubernetes.custom_objects_api),
        ('AppsV1Api', kubernetes.apps_api),
        ('BatchV1Api', kubernetes.batch_api),
        ('CustomObjectsApi', kubernetes.custom_resources_api),
    ],
)
def test_typed_clients_cleanup(monkeypatch, ctor_name, api_func):
    """Verify typed client api_client.close() is called on GC."""
    monkeypatch.setattr(kubernetes, '_load_config', lambda context=None: None)
    api_client_mock = MagicMock()
    monkeypatch.setattr(
        kubernetes.kubernetes.client,
        ctor_name,
        lambda: SimpleNamespace(api_client=api_client_mock),
    )
    obj = api_func()
    del obj
    annotations.clear_request_level_cache()
    gc.collect()

    assert api_client_mock.close.call_count == 1


def test_api_client_cleanup(monkeypatch):
    """Verify ApiClient.close() is called on GC."""
    monkeypatch.setattr(kubernetes, '_load_config', lambda context=None: None)

    instances = []

    class FakeApiClient:

        def __init__(self):
            self.close = MagicMock()
            instances.append(self)

    monkeypatch.setattr(kubernetes.kubernetes.client, 'ApiClient',
                        FakeApiClient)

    client = kubernetes.api_client()
    del client
    annotations.clear_request_level_cache()
    gc.collect()

    assert len(instances) == 1
    assert instances[0].close.call_count == 1


def test_watch_cleanup(monkeypatch):
    """Verify Watch.stop() and underlying api_client.close() are called."""
    monkeypatch.setattr(kubernetes, '_load_config', lambda context=None: None)
    api_client_mock = MagicMock()

    class FakeWatch:

        def __init__(self):
            self._api_client = api_client_mock()

    monkeypatch.setattr(kubernetes.kubernetes.watch, 'Watch', FakeWatch)

    w = kubernetes.watch()
    # Keep a handle to the underlying watch instance created by the API
    # so we can assert its _api_client.close() was called.
    underlying = w._client
    del w
    annotations.clear_request_level_cache()
    gc.collect()

    assert underlying._api_client.close.call_count == 1
