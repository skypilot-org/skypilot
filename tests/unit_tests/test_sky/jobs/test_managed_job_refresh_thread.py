"""Unit tests for sky.jobs.managed_job_refresh_thread.

These tests cover the state machine of the leader-elected refresh thread,
not the full daemon loop:

* ``_lock_still_held`` dispatches correctly between ``PostgresLock``
  (probes the underlying PG session) and any other ``DistributedLock``
  (trusts the local ``is_locked`` flag).
* ``_suicide_on_lock_loss`` sends ``SIGTERM`` to the API server PID so
  K8s restarts the pod and the leader is re-elected on another replica.
* ``start_managed_job_refresh_daemon`` gates on consolidation mode,
  preserving the historical ``should_skip_managed_job_status_refresh``
  semantics now that the daemon no longer lives in
  ``INTERNAL_REQUEST_DAEMONS``.
"""
import signal
from unittest import mock

import pytest

from sky.jobs import managed_job_refresh_thread as mjrt
from sky.utils import locks


class TestLockStillHeld:
    """`_lock_still_held` dispatches on lock type."""

    def test_postgres_lock_session_alive(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        thread._lock.is_session_alive.return_value = True
        assert thread._lock_still_held() is True
        thread._lock.is_session_alive.assert_called_once_with()

    def test_postgres_lock_session_dead(self):
        """Silent PG conn loss: PostgresLock thinks acquired locally but
        ``is_session_alive`` says otherwise.  Probe must return False so
        the caller can SIGTERM the process."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        thread._lock.is_session_alive.return_value = False
        assert thread._lock_still_held() is False

    def test_non_postgres_lock_uses_is_locked(self):
        """Non-PG locks (e.g. FileLock in non-HA deployments) don't have
        a session concept; trust ``is_locked``."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.FileLock,
                                            instance=True,
                                            spec_set=True)
        thread._lock.is_locked.return_value = True
        assert thread._lock_still_held() is True
        thread._lock.is_locked.assert_called_once_with()
        # Must NOT call any PostgresLock-only method.
        assert not hasattr(thread._lock, 'is_session_alive') or \
            not thread._lock.is_session_alive.called


class TestSuicideOnLockLoss:
    """Lock-loss must SIGTERM the current process, not the thread."""

    def test_sends_sigterm_to_current_pid(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        with mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_lock_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)


class TestStart:
    """`start_managed_job_refresh_daemon` honors consolidation mode."""

    def test_skips_when_consolidation_mode_off(self):
        with mock.patch.object(mjrt.ManagedJobRefreshDaemonThread,
                               'start') as start_mock, \
                mock.patch(
                    'sky.jobs.utils.is_consolidation_mode',
                    return_value=False):
            mjrt.start_managed_job_refresh_daemon()
        start_mock.assert_not_called()

    def test_starts_when_consolidation_mode_on(self):
        with mock.patch.object(mjrt.ManagedJobRefreshDaemonThread,
                               'start') as start_mock, \
                mock.patch(
                    'sky.jobs.utils.is_consolidation_mode',
                    return_value=True):
            mjrt.start_managed_job_refresh_daemon()
        start_mock.assert_called_once_with()
