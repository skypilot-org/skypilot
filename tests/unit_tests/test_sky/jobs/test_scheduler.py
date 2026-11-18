"""Unit tests for sky.jobs.scheduler controller process management."""
import signal
import subprocess
import sys
import time
from unittest import mock

import psutil
import pytest

from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.utils import subprocess_utils


def _record(pid: int, started_at: float = 0.0):
    return managed_job_state.ControllerPidRecord(pid=pid, started_at=started_at)


class TestKillLocalConsolidationControllers:
    """Used during shutdown (lock-loss suicide and uvicorn graceful shutdown)
    to prevent split-brain: this server's controllers must not outlive the
    moment another server's refresh daemon could acquire the consolidation
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


class TestForceStopLocalJobControllers:

    @pytest.fixture
    def draining(self, monkeypatch, tmp_path):
        monkeypatch.setenv('HOME', str(tmp_path))
        path = tmp_path / 'controllers_draining'
        path.touch()
        monkeypatch.setattr(scheduler.managed_job_constants,
                            'CONTROLLERS_DRAINING_PATH', str(path))
        monkeypatch.setattr(scheduler, 'runs_local_controller_pool',
                            lambda: True)
        return path

    def test_gates_before_killing_without_reading_cancel_db(
            self, monkeypatch, draining):
        gate = draining.parent / '.sky/.controller_recovery_restarting_signal'

        def kill(sig):
            assert gate.exists()
            assert sig == signal.SIGKILL
            return 2

        monkeypatch.setattr(scheduler, 'kill_local_job_controllers', kill)
        monkeypatch.setattr(
            scheduler.state, 'get_status',
            mock.Mock(side_effect=RuntimeError('database unavailable')))
        assert scheduler.force_stop_local_job_controllers() == 2
        scheduler.state.get_status.assert_not_called()

    @pytest.mark.parametrize('owns_pool,is_draining', [(False, True),
                                                       (True, False)])
    def test_leaves_other_pools_and_worker_restarts_alone(
            self, monkeypatch, draining, owns_pool, is_draining):
        monkeypatch.setattr(scheduler, 'runs_local_controller_pool',
                            lambda: owns_pool)
        if not is_draining:
            draining.unlink()
        kill = mock.Mock()
        monkeypatch.setattr(scheduler, 'kill_local_job_controllers', kill)
        assert scheduler.force_stop_local_job_controllers() == 0
        kill.assert_not_called()

    def test_gate_failure_prevents_kill(self, monkeypatch, draining):
        (draining.parent / '.sky').write_text('not a directory')
        kill = mock.Mock()
        monkeypatch.setattr(scheduler, 'kill_local_job_controllers', kill)
        with pytest.raises(OSError):
            scheduler.force_stop_local_job_controllers()
        kill.assert_not_called()


def _gone(pid: int) -> bool:
    """Whether a process has exited, collected by its parent or not."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


class TestStopLocalJobControllers:
    """An API server that is shutting down stops its draining controllers."""

    @pytest.fixture
    def pool(self, monkeypatch, tmp_path):
        """A pool of one live controller process with a child, in tmp_path.

        As in a container whose init does not collect orphans, the controller
        is not this process's child and stays a zombie once it is killed.
        """
        monkeypatch.setenv('HOME', str(tmp_path))
        draining = tmp_path / 'job_controllers_draining'
        signals = tmp_path / 'signals'
        signals.mkdir()
        monkeypatch.setattr(scheduler.managed_job_constants,
                            'CONTROLLERS_DRAINING_PATH', str(draining))
        monkeypatch.setattr(scheduler.managed_job_constants,
                            'CONSOLIDATED_SIGNAL_PATH', str(signals))
        monkeypatch.setattr(scheduler, 'JOB_CONTROLLER_PID_PATH',
                            str(tmp_path / 'job_controller_pid'))
        pids = tmp_path / 'pids'
        idle = 'import time; time.sleep(600)'
        controller_program = (
            'import os, pathlib, subprocess, sys\n'
            f'child = subprocess.Popen([sys.executable, "-c", {idle!r}])\n'
            f'pathlib.Path({str(pids)!r}).write_text('
            'f"{os.getpid()} {child.pid}")\n'
            f'{idle}')
        parent = subprocess.Popen([
            sys.executable, '-c', 'import subprocess, sys\n'
            'subprocess.Popen([sys.executable, "-c", '
            f'{controller_program!r}])\n'
            f'{idle}'
        ])
        try:
            deadline = time.time() + 30
            while not pids.exists() or len(pids.read_text().split()) < 2:
                assert time.time() < deadline
                time.sleep(0.05)
            controller_pid, child_pid = (
                int(pid) for pid in pids.read_text().split())
            scheduler._append_controller_pid_record(  # pylint: disable=protected-access
                controller_pid,
                psutil.Process(controller_pid).create_time())
            yield controller_pid, child_pid, draining, signals
        finally:
            subprocess_utils.kill_children_processes(parent_pids=[parent.pid],
                                                     force=True)
            parent.wait()

    def test_stops_a_draining_pool(self, pool):
        controller_pid, child_pid, draining, _ = pool
        draining.touch()

        scheduler.stop_local_job_controllers(30)

        assert _gone(controller_pid)
        assert _gone(child_pid)

    def test_stops_nothing_unless_draining(self, pool):
        """A worker that the supervisor restarts runs the same shutdown."""
        controller_pid, _, _, _ = pool

        scheduler.stop_local_job_controllers(30)

        assert not _gone(controller_pid)

    def test_waits_until_a_cancel_is_recorded(self, pool):
        """An acknowledged cancel that is only in this host's signal file, or
        only in its controller's memory, would be lost with the controller."""
        controller_pid, _, draining, signals = pool
        draining.touch()
        signal_file = signals / '7'
        signal_file.touch()
        (signals / '7.lock').touch()
        statuses = iter([
            managed_job_state.ManagedJobStatus.RUNNING,
            managed_job_state.ManagedJobStatus.RUNNING,
            managed_job_state.ManagedJobStatus.CANCELLING,
        ])

        def get_status(job_id):
            assert job_id == 7
            assert not _gone(controller_pid)
            # The controller takes the signal before it records the cancel.
            signal_file.unlink(missing_ok=True)
            return next(statuses)

        with mock.patch.object(scheduler.state, 'get_status',
                               side_effect=get_status), \
                mock.patch.object(scheduler.time, 'sleep'):
            scheduler.stop_local_job_controllers(30)

        assert next(statuses, None) is None
        assert _gone(controller_pid)

    def test_stops_a_pool_that_does_not_record_a_cancel_in_time(self, pool):
        controller_pid, _, draining, signals = pool
        draining.touch()
        (signals / '7').touch()

        with mock.patch.object(
                scheduler.state,
                'get_status',
                return_value=managed_job_state.ManagedJobStatus.RUNNING):
            scheduler.stop_local_job_controllers(-1)

        assert _gone(controller_pid)

    def test_cancel_db_failure_is_not_silently_ignored(self, pool):
        controller_pid, _, draining, signals = pool
        draining.touch()
        (signals / '7').touch()
        with mock.patch.object(
                scheduler.state, 'get_status',
                side_effect=RuntimeError('database unavailable')), \
                pytest.raises(RuntimeError, match='database unavailable'):
            scheduler.stop_local_job_controllers(30)
        assert not _gone(controller_pid)
        assert (draining.parent /
                '.sky/.controller_recovery_restarting_signal').exists()

    @pytest.mark.parametrize('target,attribute,replacement', [
        (scheduler, 'get_controller_process_records', lambda: None),
        (subprocess_utils, 'kill_children_processes', lambda **_: None),
    ])
    def test_raises_when_a_controller_may_still_run(self, pool, target,
                                                    attribute, replacement):
        _, _, draining, _ = pool
        draining.touch()

        with mock.patch.object(target, attribute, replacement), \
                pytest.raises(RuntimeError):
            scheduler.stop_local_job_controllers(30)


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

    def test_stops_mid_start_when_ownership_is_lost(self):
        """The rest of the pool is not started once the lease is gone."""
        still_owned = mock.Mock(side_effect=[True, False])
        with mock.patch.object(scheduler, 'logger') as log:
            calls = _start_calls(alive=0, wanted=5, still_owned=still_owned)
        assert calls.count('start') == 2
        assert still_owned.call_count == 2
        # How far the pool got is the only clue to how many controllers this
        # server left behind; it must reach the log.
        assert any('after starting 2 of 5 controllers' in str(c)
                   for c in log.error.call_args_list)

    def test_single_start_does_not_check(self):
        """No sleep happens before the first start, so there is nothing
        stale to re-check; the caller's own check still holds."""
        still_owned = mock.Mock(return_value=False)
        assert _start_calls(alive=30, wanted=31,
                            still_owned=still_owned) == ['start']
        still_owned.assert_not_called()

    def test_full_pool_does_not_check(self):
        still_owned = mock.Mock(return_value=False)
        assert _start_calls(alive=31, wanted=31, still_owned=still_owned) == []
        still_owned.assert_not_called()

    def test_owned_throughout_starts_the_whole_pool(self):
        still_owned = mock.Mock(return_value=True)
        calls = _start_calls(alive=0, wanted=4, still_owned=still_owned)
        assert calls.count('start') == 4
        # One check after each of the three sleeps.
        assert still_owned.call_count == 3
