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
                mock.patch.object(mjrt.os, 'getpid', return_value=12345), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'):
            thread._suicide_on_lock_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)

    def test_kills_local_controllers_before_sigterm(self):
        """Controllers must be SIGTERMed before the API server SIGTERM —
        the lock is already released here, so a new leader can schedule
        within milliseconds. Killing first prevents split-brain."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        call_order = []
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=lambda: call_order.append('kill_controllers')), \
                mock.patch.object(
                    mjrt.os, 'kill',
                    side_effect=lambda *a, **kw: call_order.append('sigterm')), \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_lock_loss()
        assert call_order == ['kill_controllers', 'sigterm']

    def test_sigterm_still_sent_when_controller_kill_raises(self):
        """A failure killing controllers must not block the SIGTERM —
        otherwise the replica would stay up holding nothing useful."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=RuntimeError('boom')), \
                mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_lock_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)

    def test_touches_recovery_signal_file(self, tmp_path, monkeypatch):
        """The signal file must be touched BEFORE we kill controllers, so
        any in-flight submit_jobs racing this path on another worker
        process short-circuits maybe_start_controllers rather than
        spawning a new controller that we'd then orphan."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        order = []
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=lambda: order.append('kill')), \
                mock.patch.object(
                    mjrt.os, 'kill',
                    side_effect=lambda *a, **kw: order.append('sigterm')), \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_lock_loss()

        assert signal_file.exists()
        # File must be created BEFORE kill_controllers and SIGTERM,
        # otherwise a fresh submit_jobs slipped in just before the
        # kill could still spawn a controller.
        assert order == ['kill', 'sigterm']

    def test_signal_file_touch_failure_does_not_block_sigterm(
            self, monkeypatch):
        """If the FS refuses the touch (read-only, full, etc.), proceed
        with kill + SIGTERM anyway. Better than blocking shutdown."""

        def boom_touch(self, *args, **kwargs):  # pylint: disable=unused-argument
            raise OSError('read-only fs')

        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._lock = mock.create_autospec(locks.PostgresLock,
                                            instance=True,
                                            spec_set=True)
        with mock.patch.object(mjrt.pathlib.Path, 'touch', boom_touch), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'), \
                mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_lock_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)


class TestOuterLoopStopsAfterSuicide:
    """Normal return from _become_leader_and_run only happens after the
    inner loop's probe detected lock loss and called _suicide_on_lock_loss.
    The outer run() loop must stop the thread instead of re-entering —
    otherwise the next iteration would skip the lock acquire (the local
    _acquired flag is stale) and run ha_recovery_for_consolidation_mode,
    which would spawn fresh controllers under a now-released lock while
    the new leader on another replica is doing the same.
    """

    def test_run_returns_after_become_leader_returns_normally(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        # If the outer loop incorrectly iterates, _become_leader_and_run
        # would be called more than once. Use a side_effect that fails
        # the test if that happens.
        call_count = {'n': 0}

        def normal_return(self):
            call_count['n'] += 1

        with mock.patch('sky.utils.locks.get_lock', return_value=lock), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    normal_return), \
                mock.patch.object(mjrt.time, 'sleep'):
            thread.run()
        assert call_count['n'] == 1, (
            'run() must not re-enter _become_leader_and_run after a '
            'normal return — that path can only follow a suicide.')


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


class TestBecomeLeaderOrdering:
    """The recovery signal file must exist BEFORE the lock is acquired, and
    recovery must wait briefly after acquiring it.

    During a rolling update we block on acquire() while the old API server
    still holds the lock. If the gate file is missing in that window, a
    controller started on this replica would be invisible to the old
    server's update_managed_jobs_statuses, which could mark the job
    FAILED_CONTROLLER. The signal file gates controller starts, so it must
    be touched up-front, not after we win the lock.

    After acquiring the lock we also wait briefly before recovery: the old
    pod's detached controllers can outlive the lock release by a moment, and
    recovery resetting jobs while they are still alive lets them re-claim and
    re-stamp soon-dead PIDs (split brain across the upgrade overlap).
    """

    def test_signal_file_touched_before_lock_acquire(self, tmp_path,
                                                     monkeypatch):
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        lock.is_locked.return_value = False
        lock.is_session_alive.return_value = True
        thread._lock = lock

        order = []

        def on_acquire(*args, **kwargs):
            # The gate file must already be in place by the time we start
            # blocking on acquire — that is the whole point of the fix.
            assert signal_file.exists(), (
                'signal file must be touched before acquiring the lock')
            order.append('acquire')

        lock.acquire.side_effect = on_acquire

        def on_sleep(*args, **kwargs):
            order.append('sleep')

        def recovery_and_stop():
            order.append('recovery')
            # Raise to skip the infinite event loop that follows recovery.
            raise RuntimeError('stop before event loop')

        with mock.patch.object(mjrt.time, 'sleep', side_effect=on_sleep), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'ha_recovery_for_consolidation_mode',
                                  side_effect=recovery_and_stop):
            with pytest.raises(RuntimeError, match='stop before event loop'):
                thread._become_leader_and_run()

        # Recovery runs only after the lock is acquired AND after the wait.
        assert order == ['acquire', 'sleep', 'recovery']
        # The finally block removes the gate file even when recovery fails.
        assert not signal_file.exists()

    def test_waits_for_configured_duration_before_recovery(
            self, tmp_path, monkeypatch):
        """The wait must use _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS, and the
        gate file must still be present while we wait (so controllers stay
        gated and update_managed_jobs_statuses does not fire)."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))
        monkeypatch.setattr(mjrt, '_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS', 7)

        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        lock.is_locked.return_value = False
        lock.is_session_alive.return_value = True
        thread._lock = lock

        slept = []

        def on_sleep(seconds, *args, **kwargs):
            # The gate file must still be in place during the wait.
            assert signal_file.exists(), (
                'signal file must persist through the post-acquire wait')
            slept.append(seconds)

        with mock.patch.object(mjrt.time, 'sleep', side_effect=on_sleep), \
                mock.patch.object(
                    mjrt.managed_job_utils,
                    'ha_recovery_for_consolidation_mode',
                    side_effect=RuntimeError('stop before event loop')):
            with pytest.raises(RuntimeError, match='stop before event loop'):
                thread._become_leader_and_run()

        assert slept == [7]

    def test_steps_down_if_lock_lost_during_wait(self, tmp_path, monkeypatch):
        """If the lock session goes stale during the post-acquire wait, we
        must NOT run recovery — another replica may now hold the lock. Step
        down via _suicide_on_lock_loss and leave the gate file in place (the
        suicide path re-touches it to keep controllers gated)."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        lock.is_locked.return_value = False
        # Session is dead by the time we re-check after the wait.
        lock.is_session_alive.return_value = False
        thread._lock = lock

        with mock.patch.object(mjrt.time, 'sleep'), \
                mock.patch.object(
                    mjrt.managed_job_utils,
                    'ha_recovery_for_consolidation_mode') as recovery, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_lock_loss') as suicide:
            thread._become_leader_and_run()

        suicide.assert_called_once()
        recovery.assert_not_called()
        # The gate file is NOT removed on the step-down path; the suicide
        # routine owns re-touching it for the shutdown drain.
        assert signal_file.exists()


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
