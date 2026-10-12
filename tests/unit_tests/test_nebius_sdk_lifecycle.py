"""Regression tests for SDK reuse and cleanup across Nebius requests."""
# pylint: disable=protected-access
import asyncio
import concurrent.futures
import os
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from sky.adaptors import nebius
from sky.utils import annotations


@pytest.fixture(name='sdk_factory')
def mock_sdk_factory(monkeypatch):
    """Use distinct fake clients so duplicate creation cannot go unnoticed."""
    monkeypatch.setattr(nebius, '_sdk_cache', {})
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'api.nebius.test')
    factory = mock.Mock(
        side_effect=lambda **kwargs: SimpleNamespace(close=mock.AsyncMock()))
    monkeypatch.setattr(nebius, 'nebius',
                        SimpleNamespace(sdk=SimpleNamespace(SDK=factory)))
    yield factory
    nebius.clear_sdk_cache()


@pytest.mark.parametrize('use_token', [False, True])
def test_reuses_sdk_across_requests(sdk_factory, tmp_path, use_token):
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('fake credentials')
    args = ('token', None) if use_token else (None, str(credentials))
    clients = []
    for _ in range(10):
        clients.append(nebius._sdk(*args))
        annotations.clear_request_level_cache()
    assert sdk_factory.call_count == 1
    assert all(client is clients[0] for client in clients)
    clients[0].close.assert_not_awaited()
    nebius.clear_sdk_cache()
    clients[0].close.assert_awaited_once()
    nebius.clear_sdk_cache()
    clients[0].close.assert_awaited_once()
    assert nebius._sdk(*args) is not clients[0]


def test_concurrent_cache_misses_create_one_sdk(sdk_factory):
    barrier = threading.Barrier(8)

    def construct(**kwargs):
        del kwargs
        # Release the GIL while constructing, as the real SDK does.
        time.sleep(0.05)
        return SimpleNamespace(close=mock.AsyncMock())

    sdk_factory.side_effect = construct

    def get_client(_):
        barrier.wait(timeout=5)
        return nebius._sdk('token', None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(get_client, range(8)))
    assert sdk_factory.call_count == 1
    assert all(client is clients[0] for client in clients)


def test_token_rotation_closes_old_sdk(sdk_factory):
    first = nebius._sdk('old-token', None)
    second = nebius._sdk('new-token', None)
    assert second is not first
    assert nebius._sdk('new-token', None) is second
    first.close.assert_awaited_once()
    second.close.assert_not_awaited()
    assert sdk_factory.call_args.kwargs['credentials'] == 'new-token'


@pytest.mark.parametrize('replace_file', [False, True])
def test_credentials_file_rotation(sdk_factory, tmp_path, replace_file):
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('old credentials')
    first = nebius._sdk(None, str(credentials))
    if replace_file:
        replacement = tmp_path / 'replacement.json'
        replacement.write_text('new credentials')
        replacement.replace(credentials)
    else:
        credentials.write_text('updated credentials')
    second = nebius._sdk(None, str(credentials))
    assert second is not first
    assert nebius._sdk(None, str(credentials)) is second
    first.close.assert_awaited_once()
    second.close.assert_not_awaited()
    assert sdk_factory.call_count == 2


def test_deleted_credentials_are_not_reused(sdk_factory, tmp_path):
    del sdk_factory
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('credentials')
    nebius._sdk(None, str(credentials))
    credentials.unlink()
    with pytest.raises(FileNotFoundError):
        nebius._sdk(None, str(credentials))


def test_cache_separates_sources_and_domains(sdk_factory, monkeypatch,
                                             tmp_path):
    files = [tmp_path / 'a.json', tmp_path / 'b.json']
    for path in files:
        path.write_text('credentials')
    first = nebius._sdk(None, str(files[0]))
    second = nebius._sdk(None, str(files[1]))
    token = nebius._sdk('token', None)
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'other.nebius.test')
    other_domain = nebius._sdk(None, str(files[0]))
    assert len({id(c) for c in (first, second, token, other_domain)}) == 4
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'api.nebius.test')
    assert nebius._sdk(None, str(files[0])) is first
    assert sdk_factory.call_count == 4
    nebius.clear_sdk_cache()
    for client in (first, second, token, other_domain):
        client.close.assert_awaited_once()


def test_cache_normalizes_credential_paths(sdk_factory, monkeypatch, tmp_path):
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('credentials')
    monkeypatch.chdir(tmp_path)
    first = nebius._sdk(None, 'credentials.json')
    assert nebius._sdk(None, str(credentials)) is first
    assert sdk_factory.call_count == 1
    assert sdk_factory.call_args.kwargs['credentials_file_name'] == os.fspath(
        credentials)


def test_failed_replacement_keeps_previous_client(sdk_factory):
    first = nebius._sdk('old-token', None)
    sdk_factory.side_effect = ValueError('invalid credentials')
    with pytest.raises(ValueError, match='invalid credentials'):
        nebius._sdk('new-token', None)
    assert nebius._sdk('old-token', None) is first
    first.close.assert_not_awaited()


def test_clear_attempts_all_clients_after_close_failure(sdk_factory,
                                                        monkeypatch):
    del sdk_factory
    first = nebius._sdk('token', None)
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'other.nebius.test')
    second = nebius._sdk('token', None)
    first.close.side_effect = TimeoutError('shutdown timed out')
    with mock.patch.object(nebius.logger, 'warning') as warning:
        nebius.clear_sdk_cache()
    warning.assert_called_once()
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    assert nebius._sdk('token', None) is not second


def test_rotation_and_clear_from_async_caller(sdk_factory):
    del sdk_factory

    async def use_clients():
        first = nebius._sdk('old-token', None)
        second = nebius._sdk('new-token', None)
        first.close.assert_awaited_once()
        nebius.clear_sdk_cache()
        second.close.assert_awaited_once()

    asyncio.run(use_clients())
