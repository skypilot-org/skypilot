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

    def test_non_postgres_lock_returns_true(self):
        """Non-PG locks (FileLock in non-HA) have no session concept;
        _lock_still_held returns True unconditionally."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.FileLock,
                                            instance=True,
                                            spec_set=True)
        assert thread._lock_still_held() is True


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


class TestOuterLoopExceptionHandling:
    """When _become_leader_and_run throws, decide between SIGTERM and retry
    based on whether we previously held a now-dead lock."""

    @staticmethod
    def _patches(is_locked: bool, session_alive: bool):
        """run() overwrites self._lock via locks.get_lock; we have to
        substitute the lock at that boundary instead of post-init."""
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        lock.is_locked.return_value = is_locked
        lock.is_session_alive.return_value = session_alive
        return mock.patch('sky.utils.locks.get_lock', return_value=lock), lock

    def test_sigterm_when_was_leader_and_session_dead(self):
        """Acquired the lock, then recovery threw because the underlying
        PG session died — running again would race the new leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, lock = self._patches(is_locked=True, session_alive=False)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=RuntimeError('recovery boom')), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_lock_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            thread.run()
        suicide.assert_called_once()

    def test_retry_when_acquire_threw(self):
        """acquire() itself failed (e.g. another replica holds the lock,
        or transient PG hiccup); is_locked stays False, just retry."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, lock = self._patches(is_locked=False, session_alive=False)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_lock_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        suicide.assert_not_called()

    def test_retry_when_lock_still_held(self):
        """Recovery threw on transient error but our lock session is
        still alive — keep retrying as leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, lock = self._patches(is_locked=True, session_alive=True)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_lock_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        suicide.assert_not_called()


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
