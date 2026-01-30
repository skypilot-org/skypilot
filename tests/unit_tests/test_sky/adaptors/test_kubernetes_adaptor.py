"""Tests for Kubernetes adaptor."""

import concurrent.futures
import gc
import os
import tempfile
import threading
import time
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
    api_client_mock = MagicMock()
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: api_client_mock)
    monkeypatch.setattr(
        kubernetes.kubernetes.client,
        ctor_name,
        lambda api_client=None: SimpleNamespace(api_client=api_client),
    )
    obj = api_func()
    del obj
    annotations.clear_request_level_cache()
    gc.collect()

    assert api_client_mock.close.call_count == 1


def test_api_client_cleanup(monkeypatch):
    """Verify ApiClient.close() is called on GC."""
    instances = []

    class FakeApiClient:

        def __init__(self):
            self.close = MagicMock()
            instances.append(self)

    # Mock _get_api_client to return a FakeApiClient instance
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: FakeApiClient())
    # Also mock the ApiClient class so isinstance checks work
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
    api_client_mock = MagicMock()
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: api_client_mock)

    class FakeWatch:

        def __init__(self, return_type=None):
            self._raw_return_type = return_type

    monkeypatch.setattr(kubernetes.kubernetes.watch, 'Watch', FakeWatch)

    w = kubernetes.watch()
    # Keep a handle to the underlying watch instance created by the API
    # so we can assert its _api_client.close() was called.
    underlying = w._client
    del w
    annotations.clear_request_level_cache()
    gc.collect()

    assert underlying._api_client.close.call_count == 1


def _create_test_kubeconfig(num_contexts):
    """Create a temporary kubeconfig with multiple contexts."""
    clusters = '\n'.join(f'- cluster:\n'
                         f'    server: https://cluster-{i}.example.com\n'
                         f'  name: cluster-{i}' for i in range(num_contexts))

    contexts = '\n'.join(f'- context:\n'
                         f'    cluster: cluster-{i}\n'
                         f'    user: user-{i}\n'
                         f'  name: context-{i}' for i in range(num_contexts))

    users = '\n'.join(f'- name: user-{i}\n'
                      f'  user: {{}}' for i in range(num_contexts))

    kubeconfig = (f'apiVersion: v1\n'
                  f'kind: Config\n'
                  f'clusters:\n'
                  f'{clusters}\n'
                  f'contexts:\n'
                  f'{contexts}\n'
                  f'current-context: context-0\n'
                  f'users:\n'
                  f'{users}\n')
    fd, path = tempfile.mkstemp(suffix='.yaml')
    os.write(fd, kubeconfig.encode())
    os.close(fd)
    return path


@pytest.mark.parametrize(
    'api_func',
    [
        kubernetes.core_api,
        kubernetes.storage_api,
        kubernetes.auth_api,
        kubernetes.networking_api,
        kubernetes.custom_objects_api,
        kubernetes.apps_api,
        kubernetes.batch_api,
        kubernetes.custom_resources_api,
    ],
)
def test_concurrent_context_isolation(monkeypatch, api_func):
    """Verify concurrent API calls with different contexts get isolated clients.

    This is a regression test for a race condition where the old implementation
    would:
    1. Call _load_config(context) which modified global
       kubernetes.client.configuration
    2. Create an API client that used that global config

    If two threads interleaved:
    - Thread A: _load_config('context-a')
    - Thread B: _load_config('context-b')  # overwrites global config
    - Thread A: CoreV1Api()  # incorrectly uses context-b!

    The fix uses new_client_from_config() which returns an ApiClient with an
    isolated Configuration object, avoiding global state.
    """
    num_contexts = 10
    iterations = 5
    contexts = [f'context-{i}' for i in range(num_contexts)]
    expected_hosts = {
        f'context-{i}': f'https://cluster-{i}.example.com'
        for i in range(num_contexts)
    }

    config_file = _create_test_kubeconfig(num_contexts)
    try:
        monkeypatch.setattr(kubernetes, '_get_config_file', lambda: config_file)

        original_get_api_client = kubernetes._get_api_client  # pylint: disable=protected-access

        def slow_get_api_client(context=None):
            assert (context is not None)
            client = original_get_api_client(context)
            time.sleep(0.001)
            return client

        monkeypatch.setattr(kubernetes, '_get_api_client', slow_get_api_client)

        for iteration in range(iterations):
            annotations.clear_request_level_cache()

            def get_api_for_context(ctx):
                api = api_func(ctx)
                # pylint: disable=protected-access
                return (ctx, api._client.api_client.configuration.host)

            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=num_contexts) as executor:
                futures = [
                    executor.submit(get_api_for_context, ctx)
                    for ctx in contexts
                ]
                results = [f.result() for f in futures]

            for requested_ctx, actual_host in results:
                expected_host = expected_hosts[requested_ctx]
                assert actual_host == expected_host, (
                    f'Iteration {iteration}: Host mismatch for '
                    f'{requested_ctx}: expected {expected_host}, '
                    f'got {actual_host}. Race condition detected.')
    finally:
        os.unlink(config_file)
