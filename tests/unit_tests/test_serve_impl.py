"""Tests for sky/serve/server/impl.py.

Focused on `apply()` rejecting terminal-state rows so callers don't blindly
hit a dead controller HTTP listener and get an opaque ECONNREFUSED. This
also makes the user-visible failure mode "go run --purge" instead of "look
at the connection-refused traceback and figure it out."
"""
# pylint: disable=invalid-name,protected-access
from unittest import mock

import pytest

from sky import backends
from sky.serve import serve_state
from sky.serve.server import impl


def _backend_mock():
    """A mock that passes `isinstance(_, backends.CloudVmRayBackend)`."""
    return mock.MagicMock(spec=backends.CloudVmRayBackend)


class TestApplyRefusesTerminalStates:
    """`apply` should refuse to update a row that's in a terminal state.
    The previous behavior was to call `update()` regardless, which would
    POST to the (likely-dead) controller HTTP listener and surface a
    confusing ECONNREFUSED to the user."""

    def _service_record(self, status):
        return {
            'name': 'svc',
            'status': status,
            'controller_pid': 1234,
            'controller_port': 20001,
            'controller_ip': None,
            'pool': True,
        }

    def _common_patches(self, status):
        # Pretend the controller cluster is accessible (consolidation mode
        # is_controller_accessible is essentially a no-op anyway).
        return [
            mock.patch(
                'sky.serve.server.impl.serve_utils.get_service_filelock_path',
                return_value='/tmp/test_apply_lock'),
            mock.patch('sky.serve.server.impl.controller_utils.'
                       'get_controller_for_pool'),
            mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'is_controller_accessible',
                return_value=mock.Mock()),
            mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=_backend_mock()),
            mock.patch('sky.serve.server.impl._get_service_record',
                       return_value=self._service_record(status)),
        ]

    def _run_apply_with_status(self, status, pool):
        patches = self._common_patches(status)
        with mock.patch('sky.serve.server.impl.update') as mock_update, \
             mock.patch('sky.serve.server.impl.up') as mock_up:
            for p in patches:
                p.start()
            try:
                impl.apply(task=mock.Mock(),
                           workers=None,
                           service_name='svc',
                           pool=pool)
            finally:
                for p in patches:
                    p.stop()
            return mock_update, mock_up

    def test_refuses_shutting_down(self):
        # SHUTTING_DOWN gets a friendlier "wait for shutdown" message that
        # still mentions --purge as a fallback for stuck cleanups, so users
        # who just ran `down` and re-applied aren't pushed straight to purge.
        with pytest.raises(RuntimeError,
                           match='shutting down.*Wait for shutdown.*--purge'):
            self._run_apply_with_status(serve_state.ServiceStatus.SHUTTING_DOWN,
                                        pool=True)

    def test_refuses_failed_cleanup(self):
        with pytest.raises(RuntimeError, match='FAILED_CLEANUP'):
            self._run_apply_with_status(
                serve_state.ServiceStatus.FAILED_CLEANUP, pool=True)

    def test_refuses_controller_failed(self):
        with pytest.raises(RuntimeError, match='CONTROLLER_FAILED'):
            self._run_apply_with_status(
                serve_state.ServiceStatus.CONTROLLER_FAILED, pool=False)

    def test_error_message_includes_purge_hint_for_pool(self):
        with pytest.raises(RuntimeError,
                           match='sky jobs pool down svc --purge'):
            self._run_apply_with_status(serve_state.ServiceStatus.SHUTTING_DOWN,
                                        pool=True)

    def test_error_message_includes_purge_hint_for_serve(self):
        with pytest.raises(RuntimeError, match='sky serve down svc --purge'):
            self._run_apply_with_status(serve_state.ServiceStatus.SHUTTING_DOWN,
                                        pool=False)

    def test_ready_does_not_raise_and_calls_update(self):
        """Sanity check: healthy READY rows still go through to update()."""
        mock_update, mock_up = self._run_apply_with_status(
            serve_state.ServiceStatus.READY, pool=True)
        mock_update.assert_called_once()
        mock_up.assert_not_called()

    def test_no_existing_record_calls_up(self):
        """When no row exists, apply should fall through to up() (create new),
        not raise."""
        patches = [
            mock.patch(
                'sky.serve.server.impl.serve_utils.get_service_filelock_path',
                return_value='/tmp/test_apply_lock'),
            mock.patch('sky.serve.server.impl.controller_utils.'
                       'get_controller_for_pool'),
            mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'is_controller_accessible',
                return_value=mock.Mock()),
            mock.patch(
                'sky.serve.server.impl.backend_utils.'
                'get_backend_from_handle',
                return_value=_backend_mock()),
            mock.patch('sky.serve.server.impl._get_service_record',
                       return_value=None),
        ]
        with mock.patch('sky.serve.server.impl.update') as mock_update, \
             mock.patch('sky.serve.server.impl.up') as mock_up:
            for p in patches:
                p.start()
            try:
                impl.apply(task=mock.Mock(),
                           workers=None,
                           service_name='svc',
                           pool=True)
            finally:
                for p in patches:
                    p.stop()
        mock_up.assert_called_once()
        mock_update.assert_not_called()
