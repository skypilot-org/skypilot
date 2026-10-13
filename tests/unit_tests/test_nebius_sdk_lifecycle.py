"""Regression tests for SDK reuse and retirement across Nebius requests."""
# pylint: disable=protected-access
import asyncio
import concurrent.futures
from importlib import metadata
import gc
import os
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace
from unittest import mock
import weakref

from packaging import version
import pytest

from sky.adaptors import nebius
from sky.utils import annotations


def _fake_sdk(**kwargs):
    del kwargs
    closed = threading.Event()
    return SimpleNamespace(close=mock.AsyncMock(side_effect=closed.set),
                           closed=closed)


def _wait_closed(raw):
    # Finished SDK requests can participate in cycles. Only the separate handle
    # is collected; the raw SDK's renewal task would otherwise keep it alive.
    for _ in range(100):
        gc.collect()
        if raw.closed.wait(timeout=0.05):
            return
    pytest.fail('Retired SDK was not closed')


@pytest.fixture(name='sdk_factory')
def mock_sdk_factory(monkeypatch):
    """Use distinct fake clients so duplicate creation cannot go unnoticed."""
    nebius.clear_sdk_cache()
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'api.nebius.test')
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-a')
    factory = mock.Mock(side_effect=_fake_sdk)
    monkeypatch.setattr(nebius, 'nebius',
                        SimpleNamespace(sdk=SimpleNamespace(SDK=factory)))
    yield factory
    nebius.clear_sdk_cache()
    gc.collect()


@pytest.mark.parametrize('use_token', [False, True])
def test_reuses_sdk_across_requests(sdk_factory, monkeypatch, tmp_path,
                                    use_token):
    """Catch the original leak using APIs also present on unpatched master."""
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('fake credentials')
    cloud_config = {} if use_token else {
        'credentials_file_path': str(credentials)
    }
    monkeypatch.setattr(nebius.skypilot_config, 'get_workspace_cloud',
                        lambda cloud: cloud_config)
    monkeypatch.setattr(nebius, 'get_iam_token', lambda: 'token'
                        if use_token else None)
    for _ in range(10):
        nebius.sdk()
        annotations.clear_request_level_cache()
    assert sdk_factory.call_count == 1


def test_clear_waits_for_previous_users(sdk_factory):
    del sdk_factory
    first = nebius._sdk('token', None)
    raw = first._sdk_client
    nebius.clear_sdk_cache()
    raw.close.assert_not_awaited()
    assert nebius._sdk('token', None) is not first
    del first
    _wait_closed(raw)
    nebius.clear_sdk_cache()
    raw.close.assert_awaited_once()


def test_finalizer_during_first_loop_creation_does_not_deadlock():
    # Run in a fresh process so earlier tests cannot initialize the loop and
    # mask the bug. A regression must time out instead of hanging pytest.
    script = textwrap.dedent("""
        import asyncio
        import gc
        import threading
        from types import SimpleNamespace

        from sky.adaptors import nebius

        gc.disable()
        closed = threading.Event()

        async def close():
            closed.set()

        handle = nebius._SDKHandle(SimpleNamespace(close=close))
        handle.cycle = [handle]
        del handle
        original_new_event_loop = asyncio.new_event_loop

        def collect_then_create_loop():
            gc.collect()
            return original_new_event_loop()

        nebius.asyncio.new_event_loop = collect_then_create_loop
        nebius._get_event_loop()
        gc.collect()
        assert closed.wait(timeout=5), 'Retired SDK was not closed'
    """)
    result = subprocess.run([sys.executable, '-c', script],
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_concurrent_cache_misses_create_one_sdk(sdk_factory):
    barrier = threading.Barrier(8)

    def construct(**kwargs):
        # Release the GIL while constructing, as the real SDK does.
        time.sleep(0.05)
        return _fake_sdk(**kwargs)

    sdk_factory.side_effect = construct

    def get_client(_):
        barrier.wait(timeout=5)
        return nebius._sdk('token', None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(get_client, range(8)))
    assert sdk_factory.call_count == 1
    assert all(client is clients[0] for client in clients)


def test_token_rotation_waits_for_previous_users(sdk_factory):
    first = nebius._sdk('old-token', None)
    raw = first._sdk_client
    second = nebius._sdk('new-token', None)
    assert second is not first
    assert nebius._sdk('new-token', None) is second
    raw.close.assert_not_awaited()
    del first
    _wait_closed(raw)
    raw.close.assert_awaited_once()
    second.close.assert_not_awaited()
    assert sdk_factory.call_args.kwargs['credentials'] == 'new-token'


@pytest.mark.parametrize('replace_file', [False, True])
def test_credentials_file_rotation(sdk_factory, tmp_path, replace_file):
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('old credentials')
    first = nebius._sdk(None, str(credentials))
    raw = first._sdk_client
    if replace_file:
        replacement = tmp_path / 'replacement.json'
        replacement.write_text('new credentials')
        replacement.replace(credentials)
    else:
        credentials.write_text('updated credentials')
    second = nebius._sdk(None, str(credentials))
    assert second is not first
    assert nebius._sdk(None, str(credentials)) is second
    raw.close.assert_not_awaited()
    del first
    _wait_closed(raw)
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


def test_cache_separates_workspaces_and_domains(sdk_factory, monkeypatch,
                                                tmp_path):
    credentials = tmp_path / 'a.json'
    credentials.write_text('credentials')
    first = nebius._sdk(None, str(credentials))
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-b')
    second = nebius._sdk('token', None)
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'other.nebius.test')
    other_domain = nebius._sdk(None, str(credentials))
    assert len({id(c) for c in (first, second, other_domain)}) == 3
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'api.nebius.test')
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-a')
    assert nebius._sdk(None, str(credentials)) is first
    assert sdk_factory.call_count == 3


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
    first = nebius._sdk('token', None)._sdk_client
    monkeypatch.setattr(nebius, 'api_domain', lambda: 'other.nebius.test')
    second = nebius._sdk('token', None)._sdk_client
    first.close.side_effect = TimeoutError('shutdown timed out')
    warned = threading.Event()
    with mock.patch.object(nebius.logger,
                           'warning',
                           side_effect=lambda *a, **k: warned.set()):
        nebius.clear_sdk_cache()
        assert warned.wait(timeout=5)
        _wait_closed(second)
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()


def test_last_user_can_release_on_cleanup_loop(sdk_factory):
    del sdk_factory
    holders = [nebius._sdk('token', None)]
    raw = holders[0]._sdk_client
    nebius.clear_sdk_cache()

    async def release():
        holders.clear()

    # A blocking finalizer here would deadlock the loop that runs close().
    asyncio.run_coroutine_threadsafe(release(),
                                     nebius._get_event_loop()).result(timeout=5)
    _wait_closed(raw)


@pytest.mark.parametrize('start_with_token', [False, True])
def test_public_sdk_retires_previous_source(sdk_factory, monkeypatch, tmp_path,
                                            start_with_token):
    del sdk_factory
    credentials = tmp_path / 'credentials.json'
    credentials.write_text('credentials')
    token = 'token' if start_with_token else None
    monkeypatch.setattr(nebius, '_get_workspace_credentials_path', lambda: None)
    monkeypatch.setattr(nebius, '_get_default_credentials_path',
                        lambda: str(credentials))
    monkeypatch.setattr(nebius, 'get_iam_token', lambda: token)
    first = nebius.sdk()
    raw = first._sdk_client
    token = None if start_with_token else 'token'
    second = nebius.sdk()
    assert second is not first
    assert len(nebius._sdk_cache) == 1
    raw.close.assert_not_awaited()
    del first
    _wait_closed(raw)
    second.close.assert_not_awaited()


def test_source_switch_preserves_other_workspace(sdk_factory, monkeypatch,
                                                 tmp_path):
    del sdk_factory
    credentials = tmp_path / 'shared.json'
    credentials.write_text('credentials')
    raw = nebius._sdk(None, str(credentials))._sdk_client
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-b')
    assert nebius._sdk(None, str(credentials))._sdk_client is raw
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-a')
    nebius._sdk('token', None)
    raw.close.assert_not_awaited()
    monkeypatch.setattr(nebius.skypilot_config, 'get_active_workspace',
                        lambda: 'workspace-b')
    assert nebius._sdk(None, str(credentials))._sdk_client is raw
    nebius._sdk('token', None)
    _wait_closed(raw)
    assert len(nebius._sdk_cache) == 1


@pytest.mark.parametrize('retire', ['rotate', 'clear'])
def test_real_rpc_survives_retirement(sdk_factory, monkeypatch, retire):
    """Hold a real RPC open on localhost while retiring its SDK."""
    sdk_module = pytest.importorskip('nebius.sdk')
    if version.parse(metadata.version('nebius')) < version.parse('0.6.12'):
        pytest.skip('Requires a supported Nebius SDK')
    iam = pytest.importorskip('nebius.api.nebius.iam.v1')
    resolver = pytest.importorskip('nebius.base.resolver')
    grpc = pytest.importorskip('grpc')
    started = threading.Event()
    release = threading.Event()
    raw_clients = []

    def get_profile(request, context):
        del request, context
        started.set()
        assert release.wait(timeout=10)
        return b''  # An empty GetProfileResponse is valid protobuf.

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        server = grpc.server(pool)
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
            'nebius.iam.v1.ProfileService',
            {'Get': grpc.unary_unary_rpc_method_handler(get_profile)}),))
        port = server.add_insecure_port('127.0.0.1:0')
        server.start()

        def construct(**kwargs):
            raw = sdk_module.SDK(
                resolver=resolver.Constant(f'127.0.0.1:{port}'),
                options=[('nebius.insecure', True)],
                **kwargs)
            raw.closed = threading.Event()
            original_close = raw.close

            async def close():
                await original_close()
                raw.closed.set()

            monkeypatch.setattr(raw, 'close', close)
            raw_clients.append(raw)
            return raw

        sdk_factory.side_effect = construct

        def call():
            # Only the SDK request retains the handle, not a service/client
            # variable in this test. Also exercise SDK synchronous wait().
            request = iam.ProfileServiceClient(nebius._sdk(
                'old-token', None)).get(iam.GetProfileRequest(),
                                        timeout=10,
                                        auth_timeout=1,
                                        retries=0)
            return request.wait()

        try:
            future = pool.submit(call)
            assert started.wait(timeout=5)
            if retire == 'rotate':
                nebius._sdk('new-token', None)
            else:
                nebius.clear_sdk_cache()
            assert not raw_clients[0].closed.is_set()
            release.set()
            assert future.result(timeout=5) is not None
            _wait_closed(raw_clients[0])
        finally:
            release.set()
            nebius.clear_sdk_cache()
            server.stop(0).wait(timeout=5)
            for raw in raw_clients:
                _wait_closed(raw)


@pytest.mark.parametrize('consumer', ['request', 'operation'])
def test_request_and_operation_retain_handle(sdk_factory, consumer):
    """Generated SDK objects must carry the handle beyond service creation."""
    del sdk_factory
    pytest.importorskip('nebius.sdk')
    if version.parse(metadata.version('nebius')) < version.parse('0.6.12'):
        pytest.skip('Requires a supported Nebius SDK')
    compute = pytest.importorskip('nebius.api.nebius.compute.v1')
    common = pytest.importorskip('nebius.api.nebius.common.v1')
    operation = pytest.importorskip('nebius.aio.operation')
    handle = nebius._sdk('token', None)
    raw = handle._sdk_client
    # Constant channel creation for operation_service() asks for parent_id().
    raw.parent_id = lambda: None
    handle_ref = weakref.ref(handle)
    if consumer == 'request':
        service = compute.InstanceServiceClient(handle)
        operation_service = service.operation_service()
        retained = operation_service.get(
            common.GetOperationRequest(id='operation-fake'))
        del service, operation_service
    else:
        retained = operation.Operation(
            'nebius.compute.v1.InstanceService.Create', handle,
            common.Operation(id='operation-fake'))
    del handle
    nebius.clear_sdk_cache()
    assert handle_ref() is not None
    raw.close.assert_not_awaited()
    del retained
    _wait_closed(raw)
    assert handle_ref() is None
