"""Tests for sky.serve.runner registry and _DefaultServiceStatusRunner.

Covers the strategy/registry pattern that lets plugins swap out the
default codegen+subprocess status fetcher for an in-process one when
the controller is consolidated into the API server.
"""
# pylint: disable=invalid-name,protected-access
import contextlib
from unittest import mock

import pytest

from sky import backends
from sky import exceptions
from sky.serve import runner as serve_runner
from sky.serve.server import impl


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure each test starts with no plugin runner registered."""
    serve_runner.reset_for_testing()
    yield
    serve_runner.reset_for_testing()


def _handle_mock(grpc_enabled: bool = True):
    h = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
    h.is_grpc_enabled_with_flag = grpc_enabled
    return h


def _backend_mock():
    return mock.MagicMock(spec=backends.CloudVmRayBackend)


class TestRegistry:

    def test_current_returns_default_when_unregistered(self):
        runner = serve_runner.current()
        assert isinstance(runner, impl._DefaultServiceStatusRunner)

    def test_current_caches_default(self):
        a = serve_runner.current()
        b = serve_runner.current()
        assert a is b

    def test_register_replaces_default(self):
        custom = mock.Mock()
        serve_runner.register(custom)
        assert serve_runner.current() is custom

    def test_register_last_wins(self):
        first = mock.Mock()
        second = mock.Mock()
        serve_runner.register(first)
        serve_runner.register(second)
        assert serve_runner.current() is second


class TestDefaultRunnerRpcPath:
    """gRPC path: ``is_grpc_enabled_with_flag`` true and RpcRunner returns."""

    def test_rpc_success_no_legacy_fallback(self):
        runner = impl._DefaultServiceStatusRunner()
        handle = _handle_mock(grpc_enabled=True)
        expected = [{'name': 'p1', 'status': 'READY'}]
        with mock.patch(
                'sky.serve.server.impl.serve_rpc_utils.RpcRunner.'
                'get_service_status',
                return_value=expected) as rpc, \
             mock.patch(
                'sky.serve.server.impl.serve_utils.ServeCodeGen.'
                'get_service_status') as codegen, \
             mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle') as get_backend:
            result = runner.get_service_status(handle=handle,
                                               service_names=['p1'],
                                               pool=True)
        assert result == expected
        rpc.assert_called_once_with(handle, ['p1'], True)
        codegen.assert_not_called()
        # RPC path must not even materialize a backend.
        get_backend.assert_not_called()

    def test_rpc_not_implemented_falls_back_to_legacy(self):
        runner = impl._DefaultServiceStatusRunner()
        handle = _handle_mock(grpc_enabled=True)
        backend = _backend_mock()
        backend.run_on_head.return_value = (0, b'PAYLOAD', '')
        legacy_records = [{'name': 'p2'}]
        with mock.patch(
                'sky.serve.server.impl.serve_rpc_utils.RpcRunner.'
                'get_service_status',
                side_effect=exceptions.SkyletMethodNotImplementedError(
                    'old skylet')), \
             mock.patch(
                'sky.serve.server.impl.serve_utils.ServeCodeGen.'
                'get_service_status',
                return_value='CODE') as codegen, \
             mock.patch(
                'sky.serve.server.impl.serve_utils.load_service_status',
                return_value=legacy_records) as load, \
             mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=backend):
            result = runner.get_service_status(handle=handle,
                                               service_names=None,
                                               pool=False)
        assert result == legacy_records
        codegen.assert_called_once_with(None, pool=False)
        backend.run_on_head.assert_called_once()
        load.assert_called_once_with(b'PAYLOAD')


class TestDefaultRunnerLegacyPath:
    """Direct legacy path: ``is_grpc_enabled_with_flag`` false."""

    def test_legacy_when_grpc_disabled(self):
        runner = impl._DefaultServiceStatusRunner()
        handle = _handle_mock(grpc_enabled=False)
        backend = _backend_mock()
        backend.run_on_head.return_value = (0, b'PAYLOAD', '')
        with mock.patch(
                'sky.serve.server.impl.serve_rpc_utils.RpcRunner.'
                'get_service_status') as rpc, \
             mock.patch(
                'sky.serve.server.impl.serve_utils.ServeCodeGen.'
                'get_service_status',
                return_value='CODE'), \
             mock.patch(
                'sky.serve.server.impl.serve_utils.load_service_status',
                return_value=[{'name': 'p3'}]), \
             mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=backend):
            result = runner.get_service_status(handle=handle,
                                               service_names=['p3'],
                                               pool=True)
        rpc.assert_not_called()
        backend.run_on_head.assert_called_once()
        assert result == [{'name': 'p3'}]

    def test_legacy_command_error_surfaces_as_runtimeerror(self):
        runner = impl._DefaultServiceStatusRunner()
        handle = _handle_mock(grpc_enabled=False)
        backend = _backend_mock()
        backend.run_on_head.return_value = (1, b'', 'boom')
        with mock.patch(
                'sky.serve.server.impl.serve_utils.ServeCodeGen.'
                'get_service_status',
                return_value='CODE'), \
             mock.patch(
                'sky.serve.server.impl.subprocess_utils.handle_returncode',
                side_effect=exceptions.CommandError(returncode=1,
                                                    command='CODE',
                                                    error_msg='boom failed',
                                                    detailed_reason=None)), \
             mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=backend):
            with pytest.raises(RuntimeError, match='boom failed'):
                runner.get_service_status(handle=handle,
                                          service_names=None,
                                          pool=True)


class TestStatusDelegatesToRunner:
    """`status()` entry point passes the right args to the registered runner."""

    def _common_patches(self, handle=None, get_backend_return=None):
        if handle is None:
            handle = _handle_mock(grpc_enabled=True)
        return [
            mock.patch('sky.serve.server.impl.backend_utils.'
                       'check_network_connection'),
            mock.patch(
                'sky.serve.server.impl.controller_utils.get_controller_for_pool'
            ),
            mock.patch(
                'sky.serve.server.impl.backend_utils.is_controller_accessible',
                return_value=handle),
            mock.patch(
                'sky.serve.server.impl.backend_utils.get_backend_from_handle',
                return_value=get_backend_return or _backend_mock()),
        ]

    def test_calls_registered_runner_with_normalized_args(self):
        captured = {}

        def fake_get(*, handle, service_names, pool):
            captured['handle'] = handle
            captured['service_names'] = service_names
            captured['pool'] = pool
            # Pool path: skip the endpoint-augmentation loop.
            return []

        serve_runner.register(mock.Mock(get_service_status=fake_get))

        with contextlib.ExitStack() as stack:
            for p in self._common_patches():
                stack.enter_context(p)
            impl.status(service_names='single', pool=True)

        # service_names should be normalized from str -> [str].
        assert captured['service_names'] == ['single']
        assert captured['pool'] is True

    def test_rpc_then_legacy_fallback_end_to_end_via_status(self):
        """status() -> default runner -> RPC raises NotImplemented -> legacy.

        This is the operationally important path (old skylets) and the
        only one that exercises the full status() + default-runner chain
        without registering a mock runner.
        """
        handle = _handle_mock(grpc_enabled=True)
        backend = _backend_mock()
        backend.run_on_head.return_value = (0, b'PAYLOAD', '')
        legacy_records = [{
            'name': 'svc',
            'load_balancer_port': None,
            'tls_encrypted': False,
        }]
        with contextlib.ExitStack() as stack:
            for p in self._common_patches(handle=handle,
                                          get_backend_return=backend):
                stack.enter_context(p)
            rpc = stack.enter_context(
                mock.patch(
                    'sky.serve.server.impl.serve_rpc_utils.RpcRunner.'
                    'get_service_status',
                    side_effect=exceptions.SkyletMethodNotImplementedError(
                        'old skylet')))
            codegen = stack.enter_context(
                mock.patch(
                    'sky.serve.server.impl.serve_utils.ServeCodeGen.'
                    'get_service_status',
                    return_value='CODE'))
            stack.enter_context(
                mock.patch(
                    'sky.serve.server.impl.serve_utils.load_service_status',
                    return_value=legacy_records))
            result = impl.status(pool=False)

        rpc.assert_called_once()
        codegen.assert_called_once()
        backend.run_on_head.assert_called_once()
        # Endpoint augmentation runs (serve path, load_balancer_port=None).
        assert result == [{
            'name': 'svc',
            'load_balancer_port': None,
            'tls_encrypted': False,
            'endpoint': None,
        }]

    def test_returns_runner_output(self):
        records = [{
            'name': 'svc',
            'load_balancer_port': None,
            'tls_encrypted': False,
        }]
        runner = mock.Mock()
        runner.get_service_status.return_value = records
        serve_runner.register(runner)

        with contextlib.ExitStack() as stack:
            for p in self._common_patches():
                stack.enter_context(p)
            result = impl.status(pool=False)

        # serve path with no load_balancer_port → endpoint stays None
        assert result == [{
            'name': 'svc',
            'load_balancer_port': None,
            'tls_encrypted': False,
            'endpoint': None,
        }]
