"""Unit tests for sky.jobs.managed_job_refresh_thread.

These tests cover the state machine of the leader-elected refresh thread,
not the full daemon loop:

* ``_lock_still_held`` probes the PG session for a ``PostgresLock`` and trusts
  the local ``is_locked`` flag for any other ``DistributedLock``.
* ``_step_down_on_lock_loss`` hands leadership over *without* killing the API
  server process: it stops the controllers this replica owns, confirms they are
  gone, and replaces the lock object so the next acquire is real. When it cannot
  confirm, it exits the process, because declining to lead does not stop another
  replica from re-adopting those jobs.
* the outer ``run`` loop re-contends after a clean step-down and stops the
  thread after an unconfirmed one (where the process is exiting).
* ``start_managed_job_refresh_daemon`` gates on consolidation mode, preserving
  the historical ``should_skip_managed_job_status_refresh`` semantics.
"""
import contextlib
import signal
import types
from unittest import mock

import pytest

from sky.jobs import managed_job_refresh_thread as mjrt
from sky.jobs import state as managed_job_state
from sky.utils import locks

_PID = 4242


def _records(*pids):
    return [
        managed_job_state.ControllerPidRecord(pid=p, started_at=1.0)
        for p in pids
    ]


def _autospec_lock():
    return mock.create_autospec(locks.PostgresLock,
                                instance=True,
                                spec_set=True)


def _thread_with_lock():
    thread = mjrt.ManagedJobRefreshDaemonThread()
    thread._lock = _autospec_lock()
    return thread


@contextlib.contextmanager
def _drain_patched(records, alive=False, fresh_lock=None):
    """Patch every collaborator of ``_step_down_on_lock_loss``.

    ``records`` is what ``get_controller_process_records`` returns, or an
    exception to raise from it; ``alive`` is the ``controller_process_alive``
    result, or a list of per-call results. Yields the mocks worth asserting on.
    """
    exc = isinstance(records, Exception)
    alive_kwargs = ({
        'side_effect': alive
    } if isinstance(alive, list) else {
        'return_value': alive
    })
    with mock.patch.object(mjrt.managed_job_scheduler,
                           'get_controller_process_records',
                           **({
                               'side_effect': records
                           } if exc else {
                               'return_value': records
                           })), \
            mock.patch.object(mjrt.managed_job_scheduler,
                              'kill_local_job_controllers') as kill, \
            mock.patch.object(mjrt.managed_job_utils,
                              'controller_process_alive',
                              **alive_kwargs), \
            mock.patch.object(mjrt.locks, 'get_lock',
                              return_value=fresh_lock or
                              mock.MagicMock()) as get_lock, \
            mock.patch('os.kill') as os_kill, \
            mock.patch('os.getpid', return_value=_PID):
        yield types.SimpleNamespace(kill=kill,
                                    get_lock=get_lock,
                                    os_kill=os_kill)


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

    @pytest.fixture(autouse=True)
    def signal_file(self, tmp_path, monkeypatch):
        """Redirect the recovery signal file, and make the drain never sleep.

        Zeroing both budgets is safe here: ``_controllers_gone`` checks liveness
        before its deadline, so a drain that succeeds still succeeds.
        """
        path = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE', str(path))
        monkeypatch.setattr(mjrt, '_STEP_DOWN_SIGTERM_GRACE_SECONDS', 0)
        monkeypatch.setattr(mjrt, '_STEP_DOWN_SIGKILL_GRACE_SECONDS', 0)
        return path

    @pytest.mark.parametrize('records', [[], _records(101), _records(101, 102)])
    def test_clean_drain_hands_over_without_exiting(self, records):
        """The headline behaviour: the server keeps serving and only the leader
        role moves, whether we own no controllers, one, or several. Killing the
        process here would take this replica's share of the Service's traffic
        with it — a far larger blast radius than the handover needs."""
        thread = _thread_with_lock()
        with _drain_patched(records) as m:
            assert thread._step_down_on_lock_loss() is True
        m.os_kill.assert_not_called()
        # SIGTERM only (no signal argument), and nothing to signal when we own
        # no controllers.
        assert m.kill.call_args_list == ([] if not records else [mock.call()])

    def test_touches_the_gate_file_before_draining(self, signal_file):
        """The file must be in place before we start killing, so the
        FAILED_CONTROLLER sweep does not run against jobs whose controllers we
        are mid-way through stopping. (It does not gate controller starts — see
        step 1 of _step_down_on_lock_loss.)"""
        thread = _thread_with_lock()
        order = []

        def on_kill(*args, **kwargs):
            assert signal_file.exists(), (
                'the FAILED_CONTROLLER sweep must be held off before we kill '
                'anything')
            order.append('kill')
            return 1

        with _drain_patched(_records(101)) as m:
            m.kill.side_effect = on_kill
            assert thread._step_down_on_lock_loss() is True

        assert signal_file.exists(), (
            'the gate file must survive the step-down; the next leader\'s '
            'recovery is what unlinks it')
        assert order == ['kill']

    def test_escalates_to_sigkill_when_sigterm_ignored(self):
        """Stepping down in place removes the container teardown that used to
        reap controllers which ignored SIGTERM, so the drain must escalate
        itself."""
        thread = _thread_with_lock()
        # Alive for the post-SIGTERM check, gone for the post-SIGKILL check.
        with _drain_patched(_records(101), alive=[True, False]) as m:
            assert thread._step_down_on_lock_loss() is True
        assert m.kill.call_args_list == [mock.call(), mock.call(signal.SIGKILL)]

    def test_exits_the_process_when_controllers_survive(self):
        """A survivor is the two-controllers-on-one-job case, and declining to
        lead does not prevent it: the new leader cannot see our survivors (local
        psutil check) and re-adopts their jobs regardless. Only exiting — and
        the container teardown that follows — reaps them."""
        thread = _thread_with_lock()
        with _drain_patched(_records(101), alive=True) as m:
            assert thread._step_down_on_lock_loss() is False
        m.os_kill.assert_called_once_with(_PID, signal.SIGTERM)

    def test_exits_the_process_when_pid_file_unreadable(self):
        """A None from ``get_controller_process_records`` is 'unknown', not
        'none': we cannot even enumerate our controllers, so nothing is
        signalled and the process must exit for the teardown to reap them."""
        thread = _thread_with_lock()
        with _drain_patched(None) as m:
            assert thread._step_down_on_lock_loss() is False
        m.kill.assert_not_called()
        m.os_kill.assert_called_once_with(_PID, signal.SIGTERM)

    @pytest.mark.parametrize(
        'records,alive,release_exc,verdict',
        [
            # Clean step-down.
            ([], False, None, True),
            # A release() on an already-dead session routinely raises.
            ([], False, RuntimeError('conn already dead'), True),
            # Unconfirmed drain: the object must still be dropped, or a later
            # retry could lead on a stale flag.
            (_records(101), True, None, False),
            # ... including when enumerating the controllers fails outright.
            (RuntimeError('boom'), False, None, False),
        ])
    def test_always_replaces_the_lock_object(self, records, alive, release_exc,
                                             verdict):
        """A release() on a dead connection leaves PostgresLock._acquired True
        and is_locked() returns that flag, so reusing the object would make the
        next _become_leader_and_run skip acquire() and lead without the lock."""
        thread = _thread_with_lock()
        old_lock = thread._lock
        old_lock.release.side_effect = release_exc
        fresh = _autospec_lock()
        with _drain_patched(records, alive=alive, fresh_lock=fresh) as m:
            assert thread._step_down_on_lock_loss() is verdict
        old_lock.release.assert_called_once_with()
        m.get_lock.assert_called_once_with(
            mjrt.managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)
        assert thread._lock is fresh

    def test_signal_file_touch_failure_does_not_block_the_drain(self):
        """If the FS refuses the touch (read-only, full), still stop the
        controllers — that is the part that prevents split-brain."""

        def boom_touch(self, *args, **kwargs):  # pylint: disable=unused-argument
            raise OSError('read-only fs')

        thread = _thread_with_lock()
        with mock.patch.object(mjrt.pathlib.Path, 'touch', boom_touch), \
                _drain_patched(_records(101)) as m:
            assert thread._step_down_on_lock_loss() is True
        m.kill.assert_called_once_with()


class TestOuterLoopAfterStepDown:
    """run() re-contends after a clean step-down and stops after a dirty one."""

    def test_reenters_after_a_clean_step_down(self):
        """A clean step-down leaves this replica eligible: our controllers are
        confirmed gone and the lock object was replaced — so the next pass
        really does acquire() before leading."""
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

    Touched up-front because acquire() blocks for the whole rolling update while
    the old server holds the lock, and the FAILED_CONTROLLER sweep must be held
    off for all of it. Waiting after acquiring covers the old pod's detached
    controllers, which outlive the lock release by a moment and would re-claim
    the jobs recovery had just reset, stamping soon-dead PIDs back onto them.
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
        to hold off the sweep), and propagate its verdict to run()."""
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
