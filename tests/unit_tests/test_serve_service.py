"""Tests for sky/serve/service.py.

Focused on the helpers added for HA leader-aware routing:

- _wait_for_controller_ready: must block until the uvicorn subprocess is
  actually accepting connections. Used to gate the DB flip in the recovery
  path so that clients never route to a pod whose listener isn't bound yet.
- _orphan_exit: must NOT call _cleanup. The whole point of this exit path
  is that another instance has already taken over the row, so any cleanup
  here would race with the new owner's replica state writes.
- _cleanup: must NOT delete version_specs. Deleting them on failure leaves
  the `services` row invisible to JOIN-based queries and breaks
  status / down --purge.
"""
import socket
import threading
import time
from unittest import mock

import pytest

from sky.serve import service


def _bind_socket_async(host, port, delay):
    """Helper: bind to host:port after `delay` seconds, then keep the socket
    open until the test thread sets a stop event."""
    stop_event = threading.Event()

    def run():
        time.sleep(delay)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            s.listen(1)
            stop_event.wait()
        finally:
            s.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, stop_event


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestWaitForControllerReady:

    def test_returns_when_listener_already_up(self):
        """Listener is up before we even start polling — fast path."""
        port = _free_port()
        thread, stop = _bind_socket_async('127.0.0.1', port, delay=0)
        try:
            time.sleep(0.1)  # let bind complete
            start = time.time()
            service._wait_for_controller_ready('127.0.0.1', port, timeout=5)
            assert time.time() - start < 1.0
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_polls_until_listener_comes_up(self):
        """Listener comes up partway through — verifies polling actually
        retries instead of returning the first ECONNREFUSED."""
        port = _free_port()
        # Bind 0.5s after we start waiting — well within timeout.
        thread, stop = _bind_socket_async('127.0.0.1', port, delay=0.5)
        try:
            start = time.time()
            service._wait_for_controller_ready('127.0.0.1', port, timeout=5)
            elapsed = time.time() - start
            # Should take at least the bind delay, but well under the timeout.
            assert 0.4 < elapsed < 4.0
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_raises_on_timeout(self):
        """Listener never comes up — must raise RuntimeError, not block
        forever (would otherwise leave the daemon stuck with the old DB row
        intact, blocking subsequent recoveries)."""
        port = _free_port()
        # Don't bind anything.
        with pytest.raises(RuntimeError, match='did not become ready'):
            service._wait_for_controller_ready('127.0.0.1', port, timeout=1)

    def test_treats_zero_zero_as_loopback(self):
        """Controller may be configured to bind 0.0.0.0 (k8s mode); we must
        probe via 127.0.0.1, not literally connect to 0.0.0.0 (which is not
        always a valid connect target on macOS)."""
        port = _free_port()
        thread, stop = _bind_socket_async('127.0.0.1', port, delay=0)
        try:
            time.sleep(0.1)
            service._wait_for_controller_ready('0.0.0.0', port, timeout=5)
        finally:
            stop.set()
            thread.join(timeout=2)


class TestOrphanExit:
    """Critical contract: _orphan_exit must NOT touch any DB state. It only
    kills our forked subprocesses and calls os._exit(0). This is what
    distinguishes orphan exit from normal cleanup — the new owner is now
    responsible for replica state, version cleanup, services row deletion,
    etc."""

    def test_calls_os_exit_zero(self):
        with mock.patch('os._exit') as mock_exit, \
             mock.patch('sky.serve.service.subprocess_utils.'
                        'kill_children_processes'):
            # Pass two mock processes so the function has something to kill.
            ctrl = mock.Mock(pid=11111)
            lb = mock.Mock(pid=22222)
            service._orphan_exit(ctrl, lb)
            mock_exit.assert_called_once_with(0)

    def test_does_not_call_cleanup(self):
        with mock.patch('os._exit'), \
             mock.patch('sky.serve.service.subprocess_utils.'
                        'kill_children_processes'), \
             mock.patch('sky.serve.service._cleanup') as mock_cleanup, \
             mock.patch('sky.serve.service.serve_state.'
                        'remove_service') as mock_remove, \
             mock.patch('sky.serve.service.serve_state.'
                        'remove_replica') as mock_remove_replica:
            ctrl = mock.Mock(pid=11111)
            service._orphan_exit(ctrl, None)
            mock_cleanup.assert_not_called()
            mock_remove.assert_not_called()
            mock_remove_replica.assert_not_called()

    def test_kills_only_provided_subprocesses(self):
        with mock.patch('os._exit'), \
             mock.patch('sky.serve.service.subprocess_utils.'
                        'kill_children_processes') as mock_kill:
            ctrl = mock.Mock(pid=11111)
            lb = mock.Mock(pid=22222)
            service._orphan_exit(ctrl, lb)
            _, kwargs = mock_kill.call_args
            # Both pids included.
            assert sorted(kwargs['parent_pids']) == [11111, 22222]
            assert kwargs['force'] is True

    def test_handles_none_subprocesses(self):
        """Both subprocesses may be None if we crashed before spawning."""
        with mock.patch('os._exit') as mock_exit, \
             mock.patch('sky.serve.service.subprocess_utils.'
                        'kill_children_processes') as mock_kill:
            service._orphan_exit(None, None)
            mock_kill.assert_not_called()
            mock_exit.assert_called_once_with(0)

    def test_swallows_kill_failure(self):
        """If kill_children_processes raises (e.g. pid already gone), we
        still must os._exit. Otherwise an exception leaves the orphan loop
        running."""
        with mock.patch('os._exit') as mock_exit, \
             mock.patch('sky.serve.service.subprocess_utils.'
                        'kill_children_processes',
                        side_effect=OSError('no such process')):
            ctrl = mock.Mock(pid=11111)
            service._orphan_exit(ctrl, None)
            mock_exit.assert_called_once_with(0)


class TestCleanupBlocksHaRecoveryButKeepsVersionSpecs:
    """`_cleanup` must:

    1. Delete `serve_ha_recovery_script` up front. Once the user has issued
       `pool down`, HA recovery must NOT respawn the controller — the SIGNAL
       file is unlinked on first read in `_handle_signal`, so a recovered
       controller can't "resume cleanup", it just silently brings the pool
       back to life and starts replacing replicas. Deleting the recovery
       script is the only thing that stops the daemon from reviving a
       service the user explicitly downed.

    2. Keep `version_specs` intact. `get_service_from_name` uses an INNER
       JOIN with `version_specs`, so deleting the version rows during a
       _cleanup that may still fail makes the resulting FAILED_CLEANUP row
       invisible to status queries AND to `--purge` — the only escape would
       be raw SQL DELETE. The success path in `_start` removes both
       atomically via `remove_service_completely`; failure leaves the row
       findable so the user can recover with `--purge`.
    """

    def _patch_common(self):
        # Replicas: empty → skip the terminate-thread loop.
        return [
            mock.patch('sky.serve.service.serve_state.get_replica_infos',
                       return_value=[]),
            mock.patch(
                'sky.serve.service.global_user_state.'
                'get_cluster_names_start_with',
                return_value=[]),
            mock.patch('sky.serve.service.serve_state.get_service_versions',
                       return_value=[1, 2]),
            mock.patch('sky.serve.service.serve_state.get_yaml_content',
                       return_value='dummy: yaml'),
        ]

    def test_recovery_script_removed_on_storage_success(self):
        patches = self._patch_common()
        with mock.patch('sky.serve.service.cleanup_storage',
                        return_value=True), \
             mock.patch(
                 'sky.serve.service.serve_state.delete_all_versions'
             ) as mock_delete_versions, \
             mock.patch(
                 'sky.serve.service.serve_state.remove_ha_recovery_script'
             ) as mock_remove_recovery:
            for p in patches:
                p.start()
            try:
                failed = service._cleanup('svc', pool=False)
            finally:
                for p in patches:
                    p.stop()
            assert failed is False
            # version_specs must NOT be touched by _cleanup (success path
            # in _start handles it via remove_service_completely).
            mock_delete_versions.assert_not_called()
            # recovery_script MUST be removed up front to block HA daemon.
            mock_remove_recovery.assert_called_once_with('svc')

    def test_recovery_script_removed_even_when_cleanup_fails(self):
        """Even when storage cleanup fails (we'll end up in FAILED_CLEANUP),
        the recovery script must already be gone — otherwise HA daemon
        respawns the controller into normal-pool mode (signal file is
        already unlinked) and the pool comes back to life."""
        patches = self._patch_common()
        with mock.patch('sky.serve.service.cleanup_storage',
                        return_value=False), \
             mock.patch(
                 'sky.serve.service.serve_state.delete_all_versions'
             ) as mock_delete_versions, \
             mock.patch(
                 'sky.serve.service.serve_state.remove_ha_recovery_script'
             ) as mock_remove_recovery:
            for p in patches:
                p.start()
            try:
                failed = service._cleanup('svc', pool=False)
            finally:
                for p in patches:
                    p.stop()
            assert failed is True
            # version_specs preserved → row still findable via JOIN, --purge
            # can clear it.
            mock_delete_versions.assert_not_called()
            # recovery_script gone → HA daemon won't respawn.
            mock_remove_recovery.assert_called_once_with('svc')


class TestCleanupStorageStaleBucket:
    """When a storage's bucket has already been deleted (e.g. by an earlier
    cleanup pass that succeeded for the bucket but crashed before remove_
    service committed), re-running `cleanup_storage` must NOT mark the
    cleanup as failed — the bucket already being gone IS the cleanup target
    state.

    Without this, FAILED_CLEANUP becomes a self-perpetuating loop:
    `ha_recovery_for_consolidation_mode` respawns the controller, which
    re-reads the same yaml and crashes on the same stale storage entry,
    re-entering FAILED_CLEANUP forever (observed live as a pool flipping
    between FAILED_CLEANUP and NO_REPLICA every time the recovery daemon
    ticked).
    """

    def test_returns_success_when_bucket_already_gone(self):
        from sky import exceptions as sky_exc

        stale_storage = mock.MagicMock()
        stale_storage.construct.side_effect = sky_exc.StorageBucketGetError(
            'Attempted to use a non-existent bucket as a source: s3://gone')

        live_storage = mock.MagicMock()
        # construct() returns normally → storage stays in storage_mounts.

        mock_task = mock.MagicMock()
        mock_task.storage_mounts = {
            '/stale': stale_storage,
            '/live': live_storage,
        }
        mock_task.file_mounts = None

        mock_backend = mock.MagicMock()

        with mock.patch('sky.serve.service.task_lib.Task.from_yaml_str',
                        return_value=mock_task), \
             mock.patch(
                 'sky.serve.service.cloud_vm_ray_backend.CloudVmRayBackend',
                 return_value=mock_backend):
            result = service.cleanup_storage('dummy: yaml')

        assert result is True, (
            'a bucket that is already gone is the cleanup target state, '
            'must not be reported as failure')
        # Stale entry dropped before teardown so backend doesn't retry it.
        assert '/stale' not in mock_task.storage_mounts
        assert '/live' in mock_task.storage_mounts
        mock_backend.teardown_ephemeral_storage.assert_called_once_with(
            mock_task)
        live_storage.construct.assert_called_once()

    def test_returns_failure_for_other_construct_errors(self):
        """Non-bucket-missing construct errors still fail cleanup — we
        don't want to silently swallow real bugs like expired creds."""
        broken_storage = mock.MagicMock()
        broken_storage.construct.side_effect = RuntimeError(
            'credential expired')

        mock_task = mock.MagicMock()
        mock_task.storage_mounts = {'/x': broken_storage}
        mock_task.file_mounts = None

        mock_backend = mock.MagicMock()

        with mock.patch('sky.serve.service.task_lib.Task.from_yaml_str',
                        return_value=mock_task), \
             mock.patch(
                 'sky.serve.service.cloud_vm_ray_backend.CloudVmRayBackend',
                 return_value=mock_backend):
            result = service.cleanup_storage('dummy: yaml')

        assert result is False, (
            'unexpected construct errors must propagate as cleanup failure')
        # The broader except block aborted before reaching teardown.
        mock_backend.teardown_ephemeral_storage.assert_not_called()
