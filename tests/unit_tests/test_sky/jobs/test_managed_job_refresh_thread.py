"""Unit tests for sky.jobs.managed_job_refresh_thread.

These tests cover the state machine of the leader-elected refresh thread,
not the full daemon loop:

* ``_lock_still_held`` dispatches correctly between ``PostgresLock``
  (probes the underlying PG session) and any other ``DistributedLock``
  (trusts the local ``is_locked`` flag).
* ``_step_down_on_lock_loss`` hands leadership over *without* killing the
  API server process in the common case: it stops the job controllers
  this replica owns, confirms they are gone, and replaces the lock object
  so the next acquire is real. When it *cannot* confirm they are gone it
  falls back to exiting the process, because declining to lead does not
  stop another replica from re-adopting those jobs.
* the outer ``run`` loop re-contends after a clean step-down, and stops
  the thread after an unconfirmed one (where the process is exiting).
* ``start_managed_job_refresh_daemon`` gates on consolidation mode,
  preserving the historical ``should_skip_managed_job_status_refresh``
  semantics now that the daemon no longer lives in
  ``INTERNAL_REQUEST_DAEMONS``.
"""
import signal
from unittest import mock

import pytest

from sky.jobs import managed_job_refresh_thread as mjrt
from sky.jobs import state as managed_job_state
from sky.utils import locks


def _records(*pids):
    return [
        managed_job_state.ControllerPidRecord(pid=p, started_at=1.0)
        for p in pids
    ]


def _thread_with_lock():
    thread = mjrt.ManagedJobRefreshDaemonThread()
    thread._lock = mock.create_autospec(locks.PostgresLock,
                                        instance=True,
                                        spec_set=True)
    return thread


class TestLockStillHeld:
    """`_lock_still_held` dispatches on lock type."""

    def test_postgres_lock_session_alive(self):
        thread = _thread_with_lock()
        thread._lock.is_session_alive.return_value = True
        assert thread._lock_still_held() is True
        thread._lock.is_session_alive.assert_called_once_with()

    def test_postgres_lock_session_dead(self):
        """Silent PG conn loss: PostgresLock thinks acquired locally but
        ``is_session_alive`` says otherwise. Probe must return False so
        the caller can step down."""
        thread = _thread_with_lock()
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


class TestStepDownOnLockLoss:
    """Losing the lock hands over the leader role, not the process."""

    def test_does_not_kill_the_process(self, tmp_path, monkeypatch):
        """The headline behaviour. The API server keeps serving; only the
        leader role moves. Killing the process here takes this replica's
        share of the Service's traffic down with it, which is a far larger
        blast radius than the leadership change requires."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        with mock.patch('os.kill') as kill_mock, \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'get_controller_process_records',
                                  return_value=[]), \
                mock.patch.object(mjrt.locks, 'get_lock'):
            assert thread._step_down_on_lock_loss() is True
        kill_mock.assert_not_called()

    def test_touches_the_gate_file_before_draining(self, tmp_path, monkeypatch):
        """The recovery signal file must be in place before we start killing, so
        the FAILED_CONTROLLER sweep does not run against jobs whose controllers
        we are in the middle of stopping. (It does not gate controller starts —
        maybe_start_controllers never reads it; see the comment on step 1 of
        _step_down_on_lock_loss.)"""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))
        thread = _thread_with_lock()
        order = []

        def on_kill(*args, **kwargs):
            assert signal_file.exists(), (
                'controller starts must be gated before we kill anything')
            order.append('kill')
            return 1

        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=_records(101)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers',
                                  side_effect=on_kill), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=False), \
                mock.patch.object(mjrt.locks, 'get_lock'):
            assert thread._step_down_on_lock_loss() is True

        assert signal_file.exists(), (
            'the gate file must survive the step-down; the next leader\'s '
            'recovery is what unlinks it')
        assert order == ['kill']

    def test_sigterm_is_enough_when_controllers_exit(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=_records(101, 102)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers') as kill, \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=False), \
                mock.patch.object(mjrt.locks, 'get_lock'):
            assert thread._step_down_on_lock_loss() is True
        # SIGTERM only (no signal argument), no escalation.
        assert kill.call_args_list == [mock.call()]

    def test_escalates_to_sigkill_when_sigterm_ignored(self, tmp_path,
                                                       monkeypatch):
        """Stepping down in place removes the container teardown that used to
        reap controllers which ignored SIGTERM, so the drain must escalate
        itself."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        # Zero budgets so neither phase sleeps: each phase does exactly one
        # liveness check and then hits its deadline.
        monkeypatch.setattr(mjrt, '_STEP_DOWN_SIGTERM_GRACE_SECONDS', 0)
        monkeypatch.setattr(mjrt, '_STEP_DOWN_DRAIN_TIMEOUT_SECONDS', 0)
        thread = _thread_with_lock()
        # Alive for the post-SIGTERM check, gone for the post-SIGKILL check.
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=_records(101)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers') as kill, \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  side_effect=[True, False]), \
                mock.patch.object(mjrt.locks, 'get_lock'):
            assert thread._step_down_on_lock_loss() is True
        assert kill.call_args_list == [mock.call(), mock.call(signal.SIGKILL)]

    def test_exits_the_process_when_controllers_survive(self, tmp_path,
                                                        monkeypatch):
        """An undead controller is the two-controllers-on-one-job case, and
        merely declining to lead does not prevent it: the new leader on another
        replica cannot see our survivors (its liveness check is a local psutil
        lookup) and re-adopts their jobs regardless. Only the process exiting —
        and the container teardown that follows — reaps them, so fall back to
        that."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        monkeypatch.setattr(mjrt, '_STEP_DOWN_SIGTERM_GRACE_SECONDS', 0)
        monkeypatch.setattr(mjrt, '_STEP_DOWN_DRAIN_TIMEOUT_SECONDS', 0)
        thread = _thread_with_lock()
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=_records(101)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(mjrt.locks, 'get_lock'), \
                mock.patch('os.kill') as kill_mock, \
                mock.patch('os.getpid', return_value=4242):
            assert thread._step_down_on_lock_loss() is False
        kill_mock.assert_called_once_with(4242, signal.SIGTERM)

    def test_exits_the_process_when_pid_file_unreadable(self, tmp_path,
                                                        monkeypatch):
        """``get_controller_process_records`` returns None when the pid file
        cannot be read. That is 'unknown', not 'none': we cannot even enumerate
        our controllers, so nothing is signalled and the process must exit for
        the teardown to reap whatever is there."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=None), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers') as kill, \
                mock.patch.object(mjrt.locks, 'get_lock'), \
                mock.patch('os.kill') as kill_mock, \
                mock.patch('os.getpid', return_value=4242):
            assert thread._step_down_on_lock_loss() is False
        kill.assert_not_called()
        kill_mock.assert_called_once_with(4242, signal.SIGTERM)

    def test_clean_drain_does_not_exit_the_process(self, tmp_path, monkeypatch):
        """The whole point of the change: the common case hands over leadership
        without costing the replica. Only an unconfirmed drain escalates."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=_records(101)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=False), \
                mock.patch.object(mjrt.locks, 'get_lock'), \
                mock.patch('os.kill') as kill_mock:
            assert thread._step_down_on_lock_loss() is True
        kill_mock.assert_not_called()

    def test_replaces_the_lock_object(self, tmp_path, monkeypatch):
        """A release() on a dead connection leaves PostgresLock._acquired True,
        and is_locked() returns that flag — so reusing the object would make the
        next _become_leader_and_run skip acquire() and lead without the lock."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        old_lock = thread._lock
        fresh = mock.create_autospec(locks.PostgresLock,
                                     instance=True,
                                     spec_set=True)
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=[]), \
                mock.patch.object(mjrt.locks, 'get_lock',
                                  return_value=fresh) as get_lock:
            assert thread._step_down_on_lock_loss() is True
        old_lock.release.assert_called_once_with()
        get_lock.assert_called_once_with(
            mjrt.managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)
        assert thread._lock is fresh

    def test_release_failure_still_replaces_the_lock(self, tmp_path,
                                                     monkeypatch):
        """Releasing a lock whose session already died routinely raises; that
        must not leave the stale object in place."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        thread._lock.release.side_effect = RuntimeError('conn already dead')
        fresh = mock.create_autospec(locks.PostgresLock,
                                     instance=True,
                                     spec_set=True)
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               return_value=[]), \
                mock.patch.object(mjrt.locks, 'get_lock', return_value=fresh):
            assert thread._step_down_on_lock_loss() is True
        assert thread._lock is fresh

    def test_drain_failure_still_replaces_the_lock(self, tmp_path, monkeypatch):
        """Even when we cannot prove the controllers are gone, the lock object
        must be dropped — otherwise a later retry could lead on a stale flag."""
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(tmp_path / 'restart_signal'))
        thread = _thread_with_lock()
        fresh = mock.create_autospec(locks.PostgresLock,
                                     instance=True,
                                     spec_set=True)
        with mock.patch.object(mjrt.managed_job_scheduler,
                               'get_controller_process_records',
                               side_effect=RuntimeError('boom')), \
                mock.patch.object(mjrt.locks, 'get_lock', return_value=fresh), \
                mock.patch('os.kill'):
            assert thread._step_down_on_lock_loss() is False
        assert thread._lock is fresh

    def test_signal_file_touch_failure_does_not_block_the_drain(
            self, monkeypatch):
        """If the FS refuses the touch (read-only, full), still stop the
        controllers — that is the part that prevents split-brain."""

        def boom_touch(self, *args, **kwargs):  # pylint: disable=unused-argument
            raise OSError('read-only fs')

        thread = _thread_with_lock()
        with mock.patch.object(mjrt.pathlib.Path, 'touch', boom_touch), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'get_controller_process_records',
                                  return_value=_records(101)), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers') as kill, \
                mock.patch.object(mjrt.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=False), \
                mock.patch.object(mjrt.locks, 'get_lock'):
            assert thread._step_down_on_lock_loss() is True
        kill.assert_called_once_with()


class TestOuterLoopAfterStepDown:
    """run() re-contends after a clean step-down and stops after a dirty one."""

    def test_reenters_after_a_clean_step_down(self):
        """A clean step-down leaves this replica eligible: controller starts are
        gated, our controllers are gone, and the lock object was replaced — so
        the next pass really does acquire() before leading."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        with mock.patch('sky.utils.locks.get_lock', return_value=lock), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[True, True, SystemExit()]) as become, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        assert become.call_count == 3, (
            'run() must contend again after a clean step-down')

    def test_stops_when_step_down_could_not_confirm_the_drain(self):
        """If our controllers might still be alive, contending again could put a
        second controller on one of their jobs. Stop the thread instead."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        with mock.patch('sky.utils.locks.get_lock', return_value=lock), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    return_value=False) as become, \
                mock.patch.object(mjrt.time, 'sleep'):
            thread.run()
        assert become.call_count == 1


class TestOuterLoopExceptionHandling:
    """When _become_leader_and_run throws, decide between stepping down and
    retrying based on whether we previously held a now-dead lock."""

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

    def test_steps_down_when_was_leader_and_session_dead(self):
        """Acquired the lock, then recovery threw because the underlying
        PG session died — running again would race the new leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, _ = self._patches(is_locked=True, session_alive=False)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('recovery boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_step_down_on_lock_loss',
                    return_value=True) as step_down, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        step_down.assert_called_once()

    def test_stops_when_step_down_says_unsafe(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, _ = self._patches(is_locked=True, session_alive=False)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=RuntimeError('recovery boom')), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_step_down_on_lock_loss',
                    return_value=False) as step_down, \
                mock.patch.object(mjrt.time, 'sleep'):
            thread.run()
        step_down.assert_called_once()

    def test_retry_when_acquire_threw(self):
        """acquire() itself failed (e.g. another replica holds the lock,
        or transient PG hiccup); is_locked stays False, just retry."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, _ = self._patches(is_locked=False, session_alive=False)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_step_down_on_lock_loss') as step_down, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        step_down.assert_not_called()

    def test_retry_when_lock_still_held(self):
        """Recovery threw on transient error but our lock session is
        still alive — keep retrying as leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        get_lock_p, _ = self._patches(is_locked=True, session_alive=True)
        with get_lock_p, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_step_down_on_lock_loss') as step_down, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        step_down.assert_not_called()


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
        down and leave the gate file in place (the step-down path re-touches it
        to keep controllers gated), and propagate its verdict to run()."""
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
                    '_step_down_on_lock_loss',
                    return_value=True) as step_down:
            assert thread._become_leader_and_run() is True

        step_down.assert_called_once()
        recovery.assert_not_called()
        # The gate file is NOT removed on the step-down path; the step-down
        # routine owns re-touching it while this replica is not the leader.
        assert signal_file.exists()

    def test_lock_lost_in_event_loop_propagates_step_down_verdict(
            self, tmp_path, monkeypatch):
        """The steady-state probe path must return the step-down's verdict too,
        so an unconfirmed drain stops the thread instead of re-contending."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        lock = mock.create_autospec(locks.PostgresLock,
                                    instance=True,
                                    spec_set=True)
        lock.is_locked.return_value = False
        # Alive for the post-acquire re-check, dead at the first loop probe.
        lock.is_session_alive.side_effect = [True, False]
        thread._lock = lock

        with mock.patch.object(mjrt.time, 'sleep'), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'ha_recovery_for_consolidation_mode'), \
                mock.patch.object(mjrt.events, 'ManagedJobEvent'), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_step_down_on_lock_loss',
                    return_value=False) as step_down:
            assert thread._become_leader_and_run() is False

        step_down.assert_called_once()


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
