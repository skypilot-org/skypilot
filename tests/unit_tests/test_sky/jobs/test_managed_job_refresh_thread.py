"""Unit tests for sky.jobs.managed_job_refresh_thread.

These tests cover the state machine of the leader-elected refresh thread,
not the full daemon loop:

* the role is elected through ``sky.utils.leader_election`` (so the deployment's
  backend switch applies to it) with timing looser than that module's defaults.
* the bid loop keeps re-bidding instead of dying on a transient failure, and a
  won bid starts a renewer that keeps the role alive across the long blocking
  steps that follow.
* ``_suicide_on_role_loss`` sends ``SIGTERM`` to the API server PID so
  K8s restarts the pod and the leader is re-elected on another replica -- and
  does *not* release the role, so the remaining lease margin keeps another
  replica out until the controllers killed here are gone.
* ``start_managed_job_refresh_daemon`` gates on consolidation mode,
  preserving the historical ``should_skip_managed_job_status_refresh``
  semantics now that the daemon no longer lives in
  ``INTERNAL_REQUEST_DAEMONS``.
"""
import signal
from unittest import mock

import pytest

from sky.jobs import constants as managed_job_constants
from sky.jobs import managed_job_refresh_thread as mjrt
from sky.utils import leader_election


def _fake_elector():
    elector = mock.create_autospec(leader_election.LeaderElector, instance=True)
    elector.lock_id = managed_job_constants.CONSOLIDATION_MODE_LOCK_ID
    return elector


def _fake_renewer(holding: bool = True):
    renewer = mock.create_autospec(leader_election.LeadershipRenewer,
                                   instance=True)
    renewer.holding = holding
    return renewer


def _leading_thread(holding: bool = True):
    """A thread that already holds the role, so `_acquire_role` is skipped."""
    thread = mjrt.ManagedJobRefreshDaemonThread()
    thread._elector = _fake_elector()
    thread._renewer = _fake_renewer(holding)
    return thread


class TestElectorConfiguration:
    """The role goes through leader_election, with this role's own timing."""

    def test_run_elects_through_leader_election(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        with mock.patch.object(mjrt.leader_election,
                               'get_elector') as get_elector, \
                mock.patch.object(mjrt.ManagedJobRefreshDaemonThread,
                                  '_become_leader_and_run'):
            thread.run()
        get_elector.assert_called_once_with(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID,
            ttl_seconds=mjrt._LEASE_TTL_SECONDS,
            renew_interval_seconds=mjrt._LOCK_PROBE_INTERVAL_SECONDS,
            renew_deadline_seconds=mjrt._RENEW_DEADLINE_SECONDS)

    def test_timing_is_looser_than_the_module_defaults(self):
        """Pin the intent, not the numbers.

        This role tolerates a slow failover far better than two leaders (it
        owns the controller process group) and a step-down here costs an API
        server restart, so both the lease TTL and the renew deadline must stay
        strictly looser than the defaults sized for idempotent per-tick
        daemons. Tightening either back to the default is the regression this
        guards.
        """
        assert (mjrt._LEASE_TTL_SECONDS >
                leader_election.DEFAULT_LEASE_TTL_SECONDS)
        assert (mjrt._RENEW_DEADLINE_SECONDS >
                leader_election.DEFAULT_RENEW_DEADLINE_SECONDS)
        # The lease constructor enforces this too, but a violation here would
        # only surface on a Postgres deployment with the lease backend on.
        assert (0 < mjrt._LOCK_PROBE_INTERVAL_SECONDS <
                mjrt._RENEW_DEADLINE_SECONDS < mjrt._LEASE_TTL_SECONDS)


class TestAcquireRole:
    """The bid loop replaces the historical blocking `lock.acquire()`."""

    def test_rebids_until_it_wins_then_starts_renewing(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        thread._elector.try_acquire.side_effect = [False, False, True]
        renewer = _fake_renewer()
        slept = []
        with mock.patch.object(mjrt.leader_election,
                               'LeadershipRenewer',
                               return_value=renewer) as renewer_cls, \
                mock.patch.object(mjrt.time, 'sleep', slept.append):
            thread._acquire_role()
        assert thread._elector.try_acquire.call_count == 3
        assert slept == [mjrt._ACQUIRE_RETRY_INTERVAL_SECONDS] * 2
        renewer_cls.assert_called_once_with(thread._elector)
        renewer.start.assert_called_once_with()
        assert thread._renewer is renewer

    def test_a_raising_bid_is_retried_not_fatal(self):
        """The old blocking acquire() sat outside the retry loop, so a database
        blip while bidding ended the thread for the lifetime of the pod."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        thread._elector.try_acquire.side_effect = [
            RuntimeError('db blip'), True
        ]
        with mock.patch.object(mjrt.leader_election,
                               'LeadershipRenewer',
                               return_value=_fake_renewer()), \
                mock.patch.object(mjrt.time, 'sleep'):
            thread._acquire_role()
        assert thread._elector.try_acquire.call_count == 2
        assert thread._renewer is not None

    def test_renewer_starts_only_after_the_bid_is_won(self):
        """Renewing before we hold the role would log failures forever; more
        importantly the renewer's clock must start from the acquisition."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        order = []
        thread._elector.try_acquire.side_effect = (
            lambda: order.append('acquire') or True)
        renewer = _fake_renewer()
        renewer.start.side_effect = lambda: order.append('renew')
        with mock.patch.object(mjrt.leader_election,
                               'LeadershipRenewer',
                               return_value=renewer), \
                mock.patch.object(mjrt.time, 'sleep'):
            thread._acquire_role()
        assert order == ['acquire', 'renew']


class TestSuicideOnRoleLoss:
    """Role loss must SIGTERM the current process, not the thread."""

    def test_sends_sigterm_to_current_pid(self):
        thread = _leading_thread()
        with mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'):
            thread._suicide_on_role_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)

    def test_stops_renewing_and_never_releases_the_role(self):
        """Two halves of the same invariant.

        Stop renewing, so nothing re-establishes a role this process has
        decided to give up. But do NOT release: releasing hands the role to a
        follower immediately, and the controllers killed just below have to be
        gone before another replica adopts their jobs. Sitting out the rest of
        the lease (ttl - renew_deadline) is what buys that time.
        """
        thread = _leading_thread()
        renewer = thread._renewer
        elector = thread._elector
        with mock.patch.object(mjrt.os, 'kill'), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'):
            thread._suicide_on_role_loss()
        renewer.stop.assert_called_once()
        elector.release.assert_not_called()

    def test_kills_local_controllers_before_sigterm(self):
        """Controllers must be SIGTERMed before the API server SIGTERM —
        the role is on its way out here, so another replica can schedule
        soon after. Killing first prevents split-brain."""
        thread = _leading_thread()
        call_order = []
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=lambda: call_order.append('kill_controllers')), \
                mock.patch.object(
                    mjrt.os, 'kill',
                    side_effect=lambda *a, **kw: call_order.append('sigterm')), \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_role_loss()
        assert call_order == ['kill_controllers', 'sigterm']

    def test_sigterm_still_sent_when_controller_kill_raises(self):
        """A failure killing controllers must not block the SIGTERM —
        otherwise the replica would stay up holding nothing useful."""
        thread = _leading_thread()
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=RuntimeError('boom')), \
                mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_role_loss()
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

        thread = _leading_thread()
        order = []
        with mock.patch.object(
                mjrt.managed_job_scheduler,
                'kill_local_job_controllers',
                side_effect=lambda: order.append('kill')), \
                mock.patch.object(
                    mjrt.os, 'kill',
                    side_effect=lambda *a, **kw: order.append('sigterm')), \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_role_loss()

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

        thread = _leading_thread()
        with mock.patch.object(mjrt.pathlib.Path, 'touch', boom_touch), \
                mock.patch.object(mjrt.managed_job_scheduler,
                                  'kill_local_job_controllers'), \
                mock.patch.object(mjrt.os, 'kill') as kill_mock, \
                mock.patch.object(mjrt.os, 'getpid', return_value=12345):
            thread._suicide_on_role_loss()
        kill_mock.assert_called_once_with(12345, signal.SIGTERM)


class TestOuterLoopStopsAfterSuicide:
    """Normal return from _become_leader_and_run only happens after a probe
    detected role loss and called _suicide_on_role_loss. The outer run() loop
    must stop the thread instead of re-entering — otherwise the next iteration
    would re-contend, run ha_recovery_for_consolidation_mode and spawn fresh
    controllers while the new leader on another replica is doing the same.
    """

    def test_run_returns_after_become_leader_returns_normally(self):
        thread = mjrt.ManagedJobRefreshDaemonThread()
        # If the outer loop incorrectly iterates, _become_leader_and_run
        # would be called more than once. Count the calls.
        call_count = {'n': 0}

        def normal_return(self):
            call_count['n'] += 1

        with mock.patch.object(mjrt.leader_election, 'get_elector'), \
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
    based on whether we were leading and have since lost the role."""

    def test_sigterm_when_was_leader_and_role_is_gone(self):
        """Acquired the role, then recovery threw because we could no longer
        renew — running again would race the new leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._renewer = _fake_renewer(holding=False)
        with mock.patch.object(mjrt.leader_election, 'get_elector'), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=RuntimeError('recovery boom')), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_role_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            thread.run()
        suicide.assert_called_once()

    def test_retry_when_the_bid_threw(self):
        """We never won the role (another replica holds it, or a transient
        database hiccup); there is nothing to step down from, just retry."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        assert thread._renewer is None
        with mock.patch.object(mjrt.leader_election, 'get_elector'), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_role_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        suicide.assert_not_called()

    def test_retry_when_the_role_is_still_held(self):
        """Recovery threw on a transient error but we are still renewing —
        keep retrying as leader."""
        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._renewer = _fake_renewer(holding=True)
        with mock.patch.object(mjrt.leader_election, 'get_elector'), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_become_leader_and_run',
                    side_effect=[RuntimeError('boom'),
                                 SystemExit()]), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_role_loss') as suicide, \
                mock.patch.object(mjrt.time, 'sleep'):
            with pytest.raises(SystemExit):
                thread.run()
        suicide.assert_not_called()


class TestBecomeLeaderOrdering:
    """The recovery signal file must exist BEFORE the role is acquired, and
    recovery must wait briefly after acquiring it.

    During a rolling update we contend while the old API server still holds
    the role. If the gate file is missing in that window, a controller started
    on this replica would be invisible to the old server's
    update_managed_jobs_statuses, which could mark the job FAILED_CONTROLLER.
    The signal file gates controller starts, so it must be touched up-front,
    not after we win the role.

    After acquiring the role we also wait briefly before recovery: the old
    pod's detached controllers can outlive the handoff by a moment, and
    recovery resetting jobs while they are still alive lets them re-claim and
    re-stamp soon-dead PIDs (split brain across the upgrade overlap).
    """

    def test_signal_file_touched_before_the_bid(self, tmp_path, monkeypatch):
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        order = []

        def on_bid():
            # The gate file must already be in place by the time we start
            # bidding — that is the whole point.
            assert signal_file.exists(), (
                'signal file must be touched before bidding for the role')
            order.append('acquire')
            return True

        thread._elector.try_acquire.side_effect = on_bid

        def on_sleep(*args, **kwargs):
            order.append('sleep')

        def recovery_and_stop():
            order.append('recovery')
            # Raise to skip the infinite event loop that follows recovery.
            raise RuntimeError('stop before event loop')

        with mock.patch.object(mjrt.leader_election,
                               'LeadershipRenewer',
                               return_value=_fake_renewer()), \
                mock.patch.object(mjrt.time, 'sleep', side_effect=on_sleep), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'ha_recovery_for_consolidation_mode',
                                  side_effect=recovery_and_stop):
            with pytest.raises(RuntimeError, match='stop before event loop'):
                thread._become_leader_and_run()

        # Recovery runs only after the role is acquired AND after the wait.
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

        thread = _leading_thread()
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

    def test_reuses_the_role_it_already_holds(self, tmp_path, monkeypatch):
        """A retry after a transient error while still leading must not bid
        again: a second renewer would double the renew traffic and leave the
        first one running with nobody reading it."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))
        thread = _leading_thread()
        renewer = thread._renewer
        with mock.patch.object(mjrt.time, 'sleep'), \
                mock.patch.object(mjrt.leader_election,
                                  'LeadershipRenewer') as renewer_cls, \
                mock.patch.object(
                    mjrt.managed_job_utils,
                    'ha_recovery_for_consolidation_mode',
                    side_effect=RuntimeError('stop before event loop')):
            with pytest.raises(RuntimeError, match='stop before event loop'):
                thread._become_leader_and_run()
        thread._elector.try_acquire.assert_not_called()
        renewer_cls.assert_not_called()
        assert thread._renewer is renewer

    def test_steps_down_if_role_lost_during_wait(self, tmp_path, monkeypatch):
        """If the role goes away during the post-acquire wait, we must NOT run
        recovery — another replica may now hold it. Step down via
        _suicide_on_role_loss and leave the gate file in place (the suicide
        path re-touches it to keep controllers gated)."""
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = _leading_thread(holding=False)

        with mock.patch.object(mjrt.time, 'sleep'), \
                mock.patch.object(
                    mjrt.managed_job_utils,
                    'ha_recovery_for_consolidation_mode') as recovery, \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_role_loss') as suicide:
            thread._become_leader_and_run()

        suicide.assert_called_once()
        recovery.assert_not_called()
        # The gate file is NOT removed on the step-down path; the suicide
        # routine owns re-touching it for the shutdown drain.
        assert signal_file.exists()


class TestEventLoopProbe:
    """The event loop confirms the role before it acts on it."""

    def test_probes_before_the_first_event_tick(self, tmp_path, monkeypatch):
        """Recovery walks every managed job, so it can outlast the role. If
        the loop waited a probe interval before its first check it would run a
        full status sweep — which mutates job rows — as a stale leader.
        """
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        # Holding through the pre-recovery check, gone by the time recovery
        # returns.
        holding = {'value': True}
        renewer = _fake_renewer()
        type(renewer).holding = property(lambda self: holding['value'])
        thread._renewer = renewer

        refresh_event = mock.Mock()

        def recovery():
            holding['value'] = False

        with mock.patch.object(mjrt.time, 'sleep'), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'ha_recovery_for_consolidation_mode',
                                  side_effect=recovery), \
                mock.patch.object(mjrt.events,
                                  'ManagedJobEvent',
                                  return_value=refresh_event), \
                mock.patch.object(
                    mjrt.ManagedJobRefreshDaemonThread,
                    '_suicide_on_role_loss') as suicide:
            thread._become_leader_and_run()

        suicide.assert_called_once()
        refresh_event.run.assert_not_called()

    def test_checks_the_role_on_every_pass_not_on_a_slower_interval(
            self, tmp_path, monkeypatch):
        """`holding` is an in-memory read -- the renewer owns the round trip --
        so rate-limiting it would only add latency to the step-down, and that
        latency is spent out of the margin for killing this replica's
        controllers before anyone else adopts their jobs.

        Pinned by counting sleeps, not by wall clock: the loop must consult the
        role once per 1s sleep. A version that gated the check behind an
        interval would sleep many times per check -- and, because the tests
        stub `time.sleep` while `time.monotonic` keeps running for real, it
        would still eventually reach the same read count, just slower. So the
        sleep budget is the assertion that actually discriminates.
        """
        signal_file = tmp_path / 'restart_signal'
        monkeypatch.setattr(mjrt.constants,
                            'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                            str(signal_file))

        class _Stop(Exception):
            pass

        counts = {'reads': 0, 'sleeps': 0}
        renewer = _fake_renewer()

        def _holding(_self):
            counts['reads'] += 1
            if counts['reads'] >= 3:
                raise _Stop()
            return True

        type(renewer).holding = property(_holding)

        def _sleep(_seconds):
            counts['sleeps'] += 1
            # Escape hatch: a gated implementation would spin here instead of
            # reaching the third read, so bound the run rather than hang.
            if counts['sleeps'] > 4:
                raise _Stop()

        thread = mjrt.ManagedJobRefreshDaemonThread()
        thread._elector = _fake_elector()
        thread._renewer = renewer

        refresh_event = mock.Mock()
        with mock.patch.object(mjrt.time, 'sleep', _sleep), \
                mock.patch.object(mjrt.managed_job_utils,
                                  'ha_recovery_for_consolidation_mode'), \
                mock.patch.object(mjrt.events,
                                  'ManagedJobEvent',
                                  return_value=refresh_event):
            with pytest.raises(_Stop):
                thread._become_leader_and_run()

        # Three passes, each consulting the role, cost at most one sleep each.
        assert counts['reads'] == 3, counts
        assert counts['sleeps'] <= 3, counts


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
