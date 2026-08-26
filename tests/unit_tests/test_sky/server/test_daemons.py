"""Tests for sky.server.daemons."""
import os
import sys
import tempfile
from unittest import mock

import pytest

from sky import skypilot_config
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server.requests import request_names
from sky.server.requests import requests
from sky.server.requests.requests import ScheduleType


def _mock_get_nested(max_bytes):
    """Return a patched get_nested that overrides daemon_log_max_bytes."""
    original = skypilot_config.get_nested

    def patched(keys, default=None):
        if keys == ('api_server', 'daemon_log_max_bytes'):
            return max_bytes
        return original(keys, default)

    return patched


def test_autodown_reconciler_daemon_is_registered_hidden_and_short():
    matching = [
        daemon for daemon in daemons.INTERNAL_REQUEST_DAEMONS
        if daemon.name is request_names.RequestName.REQUEST_DAEMON_AUTODOWN
    ]

    assert len(matching) == 1
    daemon = matching[0]
    assert daemon.id == 'autodown-reconciler-daemon'
    assert daemon.id.endswith('-daemon')
    assert daemon.name in daemons.HIDDEN_REQUEST_NAMES
    assert requests.build_internal_daemon_request(
        daemon).schedule_type is ScheduleType.SHORT


def test_successful_autodown_reconciler_event_sleeps_configured_interval(
        monkeypatch):
    reconcile = mock.Mock()
    sleep = mock.Mock()
    monkeypatch.setattr('sky.server.autodown.reconcile_autodown_intents',
                        reconcile)
    monkeypatch.setattr(daemons.time, 'sleep', sleep)

    def get_nested(keys, default=None):
        assert keys == ('daemons', 'autodown-reconciler-daemon',
                        'interval_seconds')
        assert default == (
            server_constants.AUTODOWN_RECONCILER_DAEMON_INTERVAL_SECONDS)
        return 7

    monkeypatch.setattr(skypilot_config, 'get_nested', get_nested)

    daemons.autodown_reconciliation_event()

    reconcile.assert_called_once_with()
    sleep.assert_called_once_with(7)


def test_runpod_catalog_refresh_daemon_is_optional_and_short():
    """Register RunPod refresh as a short internal daemon when configured."""
    matching = [
        daemon for daemon in daemons.INTERNAL_REQUEST_DAEMONS
        if daemon.name is (
            request_names.RequestName.REQUEST_DAEMON_RUNPOD_CATALOG_REFRESH)
    ]

    assert len(matching) == 1
    assert matching[0].id == 'runpod-catalog-refresh-daemon'
    assert matching[0].name in daemons.HIDDEN_REQUEST_NAMES
    assert requests.build_internal_daemon_request(
        matching[0]).schedule_type is ScheduleType.SHORT


def test_runpod_catalog_refresh_event_refreshes_and_sleeps(monkeypatch):
    """Refresh the catalog once and honor the configured daemon interval."""
    refresh = mock.Mock()
    sleep = mock.Mock()
    monkeypatch.setattr('sky.catalog.runpod_refresh.refresh_catalog', refresh)
    monkeypatch.setattr(daemons.time, 'sleep', sleep)

    def get_nested(keys, default=None):
        assert keys == ('daemons', 'runpod-catalog-refresh-daemon',
                        'interval_seconds')
        assert default == server_constants.RUNPOD_CATALOG_REFRESH_DAEMON_INTERVAL_SECONDS
        return 17

    monkeypatch.setattr(skypilot_config, 'get_nested', get_nested)

    daemons.refresh_runpod_catalog_event()

    refresh.assert_called_once_with()
    sleep.assert_called_once_with(17)


def test_runpod_catalog_refresh_daemon_skips_without_credentials(monkeypatch):
    """Do not schedule RunPod refresh when neither credential source exists."""
    monkeypatch.delenv('RUNPOD_API_KEY', raising=False)
    monkeypatch.setattr(daemons.os.path, 'isfile', lambda _path: False)

    assert daemons.should_skip_runpod_catalog_refresh()


class TestDaemonLogRotation:
    """Tests for daemon log rotation."""

    def _redirect_stdout_stderr(self, fd: int):
        """Redirect stdout and stderr to the given fd via dup2."""
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())

    def test_rotates_when_exceeds_threshold(self, monkeypatch):
        """Log is backed up to .log.1 and truncated when exceeding threshold."""
        threshold = 1024  # 1 KB for testing
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                # Open with O_APPEND to mimic executor.py behavior.
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data exceeding the threshold.
                data = b'x' * (threshold + 100)
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()
                assert os.fstat(sys.stdout.fileno()).st_size == threshold + 100

                # Rotation should happen.
                daemons._rotate_daemon_log(tmp_path)
                assert os.fstat(sys.stdout.fileno()).st_size == 0

                # Backup should contain the original data.
                with open(backup_path, 'rb') as check:
                    assert check.read() == data

                # Writes after rotation should start from position 0
                # (no sparse hole).
                msg = b'hello after rotation\n'
                os.write(sys.stdout.fileno(), msg)
                sys.stdout.flush()
                assert os.fstat(sys.stdout.fileno()).st_size == len(msg)

                # Verify the content on disk.
                with open(tmp_path, 'rb') as check:
                    assert check.read() == msg

                os.close(append_fd)
        finally:
            # Restore original stdout/stderr.
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)
            if os.path.exists(backup_path):
                os.unlink(backup_path)

    def test_old_backup_replaced_on_next_rotation(self, monkeypatch):
        """Old .log.1 backup is replaced on subsequent rotation."""
        threshold = 1024
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # First rotation.
                first_data = b'A' * (threshold + 100)
                os.write(sys.stdout.fileno(), first_data)
                sys.stdout.flush()
                daemons._rotate_daemon_log(tmp_path)
                with open(backup_path, 'rb') as check:
                    assert check.read() == first_data

                # Write new data exceeding threshold again.
                second_data = b'B' * (threshold + 200)
                os.write(sys.stdout.fileno(), second_data)
                sys.stdout.flush()
                daemons._rotate_daemon_log(tmp_path)

                # Backup should now contain second data, not first.
                with open(backup_path, 'rb') as check:
                    assert check.read() == second_data

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)
            if os.path.exists(backup_path):
                os.unlink(backup_path)

    def test_no_rotation_when_under_threshold(self, monkeypatch):
        """No rotation or backup when log size is under threshold."""
        threshold = 1024
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data under the threshold.
                data = b'x' * (threshold - 100)
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

                daemons._rotate_daemon_log(tmp_path)

                # File should not be truncated.
                assert os.fstat(sys.stdout.fileno()).st_size == threshold - 100
                # No backup should be created.
                assert not os.path.exists(backup_path)

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)

    def test_rotation_disabled_when_max_bytes_zero(self, monkeypatch):
        """Rotation is disabled when max_bytes is set to 0."""
        monkeypatch.setattr(skypilot_config, 'get_nested', _mock_get_nested(0))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data that would normally exceed a threshold.
                data = b'x' * 2048
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

                daemons._rotate_daemon_log(tmp_path)

                # File should not be truncated.
                assert os.fstat(sys.stdout.fileno()).st_size == 2048
                # No backup should be created.
                assert not os.path.exists(backup_path)

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)


class TestConsolidationEventInstancePersistence:
    """The consolidation-mode refresh daemons must reuse a single
    SkyletEvent instance across iterations.

    Background: SkyletEvent.run() throttles its expensive `_run()`
    callback via a per-instance counter `_n` that accumulates across
    calls. The outer InternalRequestDaemon loop re-invokes the
    daemon's event_fn repeatedly; if the event is freshly instantiated
    on every call, `_n` resets to 0, run() only advances it to 1,
    `_n == 0` never re-fires, and the throttled work
    (update_service_status / managed-job status refresh) is silently
    skipped forever.

    These tests assert the instance is created once and reused so the
    counter persists. With the bug, the constructor is invoked on
    every iteration; with the fix, exactly once."""

    # Attribute names the fix introduces. Use getattr/setattr so that if
    # the fix is reverted the fixture still runs cleanly; the actual test
    # assertions (call_count) then become the failure signal.
    _EVENT_ATTRS = ('_pool_status_update_event', '_serve_status_update_event')

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        # Pre-populate the consolidation locks with mocks that look
        # already-locked so the daemon never tries to acquire a real
        # advisory lock during the test.
        fake_lock = mock.MagicMock()
        fake_lock.is_locked.return_value = True
        prior_locks = (daemons._pool_consolidation_mode_lock,
                       daemons._serve_consolidation_mode_lock)
        prior_events = {
            name: getattr(daemons, name, None) for name in self._EVENT_ATTRS
        }
        daemons._pool_consolidation_mode_lock = fake_lock
        daemons._serve_consolidation_mode_lock = fake_lock
        for name in self._EVENT_ATTRS:
            setattr(daemons, name, None)
        yield
        (daemons._pool_consolidation_mode_lock,
         daemons._serve_consolidation_mode_lock) = prior_locks
        for name, value in prior_events.items():
            setattr(daemons, name, value)

    def test_serve_event_instance_is_reused_across_iterations(self):
        with mock.patch(
                'sky.serve.serve_utils.ha_recovery_for_consolidation_mode'), \
             mock.patch('sky.skylet.events.ServiceUpdateEvent') as mock_event, \
             mock.patch.object(daemons.time, 'sleep'):
            for _ in range(3):
                daemons._serve_status_refresh_event(pool=False)
            # Without the fix this would be 3 (one ctor per iteration).
            # With the fix the cached instance is reused.
            mock_event.assert_called_once_with(pool=False)
            # run() is invoked on the SAME instance once per iteration,
            # so its internal `_n` counter accumulates as designed.
            assert mock_event.return_value.run.call_count == 3

    def test_pool_event_instance_is_reused_across_iterations(self):
        with mock.patch(
                'sky.serve.serve_utils.ha_recovery_for_consolidation_mode'), \
             mock.patch('sky.skylet.events.ServiceUpdateEvent') as mock_event, \
             mock.patch.object(daemons.time, 'sleep'):
            for _ in range(3):
                daemons._serve_status_refresh_event(pool=True)
            mock_event.assert_called_once_with(pool=True)
            assert mock_event.return_value.run.call_count == 3

    def test_serve_and_pool_use_independent_instances(self):
        # Serve (pool=False) and pool (pool=True) must each get their own
        # cached event — otherwise pool=True daemon iterations would call
        # run() on the pool=False event (or vice versa).
        with mock.patch(
                'sky.serve.serve_utils.ha_recovery_for_consolidation_mode'), \
             mock.patch('sky.skylet.events.ServiceUpdateEvent') as mock_event, \
             mock.patch.object(daemons.time, 'sleep'):
            daemons._serve_status_refresh_event(pool=False)
            daemons._serve_status_refresh_event(pool=True)
            daemons._serve_status_refresh_event(pool=False)
            daemons._serve_status_refresh_event(pool=True)
            # Exactly two ctor calls: one per pool flag.
            assert mock_event.call_count == 2
            assert mock.call(pool=False) in mock_event.call_args_list
            assert mock.call(pool=True) in mock_event.call_args_list
