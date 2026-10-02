"""Unit tests for sky.jobs.scheduler controller process management."""
import signal
from unittest import mock

import pytest

from sky.jobs import scheduler
from sky.jobs import state as managed_job_state


def _record(pid: int, started_at: float = 0.0):
    return managed_job_state.ControllerPidRecord(pid=pid, started_at=started_at)


class TestKillLocalConsolidationControllers:
    """Used during shutdown (lock-loss suicide and uvicorn graceful shutdown)
    to prevent split-brain: this replica's controllers must not outlive the
    moment another replica's refresh daemon could acquire the consolidation
    lock. The helper must be best-effort -- it runs on shutdown paths where
    raising would either prevent SIGTERM or stall drain.
    """

    def test_no_pid_file_returns_zero(self):
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=[]):
            assert scheduler.kill_local_job_controllers() == 0

    def test_records_none_returns_zero(self):
        """Helper must tolerate the PID-file read failing (returns None)."""
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=None):
            assert scheduler.kill_local_job_controllers() == 0

    def test_signals_live_records(self):
        recs = [_record(101), _record(202), _record(303)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 3
        kill_mock.assert_has_calls([
            mock.call(101, signal.SIGTERM),
            mock.call(202, signal.SIGTERM),
            mock.call(303, signal.SIGTERM)
        ],
                                   any_order=True)

    def test_skips_dead_records(self):
        """Stale entries (process exited or wrong started_at) are skipped —
        otherwise we'd SIGTERM unrelated PIDs that the OS reused."""
        recs = [_record(101), _record(202)]
        alive_lookup = {101: True, 202: False}
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(
                    scheduler.managed_job_utils,
                    'controller_process_alive',
                    side_effect=lambda r: alive_lookup[r.pid]), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1
        kill_mock.assert_called_once_with(101, signal.SIGTERM)

    def test_tolerates_process_lookup_error(self):
        """Race between alive-check and kill: the PID died in between.
        Not counted as signaled, but doesn't abort the loop."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[ProcessLookupError(), None]) as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1  # Only the second succeeded.
        assert kill_mock.call_count == 2

    def test_continues_on_oserror(self):
        """Per-PID OSError (e.g. EPERM) must not stop the rest."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[OSError('EPERM'), None]):
            n = scheduler.kill_local_job_controllers()
        assert n == 1

    def test_custom_signal(self):
        recs = [_record(101)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            scheduler.kill_local_job_controllers(sig=signal.SIGKILL)
        kill_mock.assert_called_once_with(101, signal.SIGKILL)


def _start_calls(alive: int, wanted: int, still_owned=None, calls=None):
    """Runs maybe_start_controllers, returning an ordered start/sleep log.

    Pass `calls` to inspect the log of a run that is expected to raise.
    """
    calls = [] if calls is None else calls
    fake_time = mock.MagicMock()
    fake_time.sleep.side_effect = lambda s: calls.append(('sleep', s))

    with mock.patch.object(scheduler.filelock, 'FileLock'), \
            mock.patch.object(scheduler,
                              'get_alive_controllers',
                              return_value=alive), \
            mock.patch.object(scheduler.controller_utils,
                              'get_number_of_jobs_controllers',
                              return_value=wanted), \
            mock.patch.object(scheduler,
                              'start_controller',
                              side_effect=lambda: calls.append('start')), \
            mock.patch.object(scheduler, 'time', fake_time):
        scheduler.maybe_start_controllers(still_owned=still_owned)
    return calls


class TestMaybeStartControllersStagger:
    """Controller starts are spaced instead of fanned out at once.

    Every controller opens its own state-DB connections as it starts, so
    starting the whole pool at once makes a transaction-mode pooler open one
    server connection per concurrent client. The spacing is a fixed interval
    rather than a fixed total window, because the pool size scales with API
    server memory -- a fixed window would compress more starts into the same
    time on a larger server.
    """

    def test_first_start_is_immediate(self):
        interval = scheduler._CONTROLLER_START_INTERVAL_SECONDS
        assert _start_calls(alive=0, wanted=3) == [
            'start',
            ('sleep', interval),
            'start',
            ('sleep', interval),
            'start',
        ]

    def test_topping_up_one_controller_does_not_sleep(self):
        assert _start_calls(alive=30, wanted=31) == ['start']

    def test_full_pool_starts_nothing(self):
        assert _start_calls(alive=31, wanted=31) == []

    def test_spread_scales_with_pool_size(self):
        """A bigger API server takes proportionally longer, not the same time.

        That keeps the start rate -- and so the pooler's connection-open rate
        -- identical across API server sizes.
        """
        interval = scheduler._CONTROLLER_START_INTERVAL_SECONDS
        for wanted in (8, 31, 64):
            calls = _start_calls(alive=0, wanted=wanted)
            sleeps = [c for c in calls if c != 'start']
            assert calls.count('start') == wanted
            assert len(sleeps) == wanted - 1
            assert sum(s for _, s in sleeps) == pytest.approx(
                (wanted - 1) * interval)


class TestMaybeStartControllersOwnership:
    """Ownership of the pool is re-checked while it starts.

    The consolidation leader holds the pool by a lease (a Postgres advisory
    lock) and checks it once before recovery, which starts the pool before
    recovering jobs. Spreading the starts over tens of seconds makes that one
    check stale: a replica that silently lost the lease would keep starting
    controllers while the new leader recovers the same jobs, leaving two
    controllers on one job.
    """

    def test_aborts_mid_start_when_ownership_is_lost(self):
        """The rest of the pool is not started once the lease is gone."""
        calls = []
        still_owned = mock.Mock(side_effect=[True, False])
        with pytest.raises(scheduler.ControllerPoolNotOwnedError):
            _start_calls(alive=0,
                         wanted=5,
                         still_owned=still_owned,
                         calls=calls)
        assert calls.count('start') == 2
        assert still_owned.call_count == 2

    def test_checks_once_more_after_the_last_start(self):
        """Recovery runs straight after, so the last sleep must not go
        unchecked."""
        calls = []
        still_owned = mock.Mock(side_effect=[True, False])
        with pytest.raises(scheduler.ControllerPoolNotOwnedError):
            _start_calls(alive=0,
                         wanted=2,
                         still_owned=still_owned,
                         calls=calls)
        assert calls.count('start') == 2
        assert still_owned.call_count == 2

    def test_single_start_is_checked_too(self):
        still_owned = mock.Mock(return_value=False)
        with pytest.raises(scheduler.ControllerPoolNotOwnedError):
            _start_calls(alive=30, wanted=31, still_owned=still_owned)

    def test_full_pool_does_not_check(self):
        still_owned = mock.Mock(return_value=False)
        assert _start_calls(alive=31, wanted=31, still_owned=still_owned) == []
        still_owned.assert_not_called()

    def test_owned_throughout_starts_the_whole_pool(self):
        still_owned = mock.Mock(return_value=True)
        calls = _start_calls(alive=0, wanted=4, still_owned=still_owned)
        assert calls.count('start') == 4
        # Three between starts, one after the last.
        assert still_owned.call_count == 4
