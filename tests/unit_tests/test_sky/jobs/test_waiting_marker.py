"""Tests for the waiting-jobs marker that wakes idle controllers.

The jobs controllers poll the DB for WAITING jobs every 10s when idle. To cut
submission latency without polling the DB faster, the submit side touches a
marker file under the signals directory and idle controllers stat it (cheap)
to wake up early. The 10s DB poll stays as the fallback.
"""
# pylint: disable=redefined-outer-name,unused-argument
import asyncio
import os
import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.jobs import constants as managed_job_constants
from sky.jobs import controller as controller_module
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.jobs import utils as jobs_utils
from sky.skylet import constants as skylet_constants
from sky.utils import controller_utils


@pytest.fixture
def signal_dir(monkeypatch, tmp_path):
    """Redirect the signals directory to a hermetic temp dir."""
    path = tmp_path / 'signals'
    monkeypatch.setattr(managed_job_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(path))
    return path


class TestMarkerHelpers:
    """touch / stat helpers in sky.jobs.utils."""

    def test_mtime_is_none_before_first_touch(self, signal_dir):
        assert jobs_utils.get_waiting_jobs_marker_mtime() is None

    def test_touch_creates_marker_and_advances_mtime(self, signal_dir):
        jobs_utils.touch_waiting_jobs_marker()
        first = jobs_utils.get_waiting_jobs_marker_mtime()
        assert first is not None
        assert (signal_dir /
                managed_job_constants.WAITING_JOBS_MARKER_NAME).is_file()

        # Make sure a second touch is observable as a change.
        time.sleep(0.01)
        jobs_utils.touch_waiting_jobs_marker()
        second = jobs_utils.get_waiting_jobs_marker_mtime()
        assert second is not None
        assert second > first

    def test_touch_is_best_effort(self, monkeypatch, tmp_path):
        # Point the signals dir at a path that cannot be a directory (a file),
        # so mkdir/touch fail. The wake-up is an optimization: the 10s poll is
        # the fallback, so failures must never propagate to the submitter.
        blocker = tmp_path / 'blocker'
        blocker.write_text('x')
        monkeypatch.setattr(managed_job_constants, 'CONSOLIDATED_SIGNAL_PATH',
                            str(blocker / 'signals'))
        jobs_utils.touch_waiting_jobs_marker()  # must not raise
        assert jobs_utils.get_waiting_jobs_marker_mtime() is None


class TestWaitForWaitingJobsSignal:
    """The controller-side wait primitive."""

    @pytest.mark.asyncio
    async def test_returns_early_when_marker_touched(self, signal_dir):
        marker_before = jobs_utils.get_waiting_jobs_marker_mtime()

        async def touch_soon():
            await asyncio.sleep(0.05)
            jobs_utils.touch_waiting_jobs_marker()

        touch_task = asyncio.create_task(touch_soon())
        start = time.monotonic()
        await controller_module.wait_for_waiting_jobs_signal(
            marker_before, timeout=5.0, check_interval=0.01)
        elapsed = time.monotonic() - start
        await touch_task
        # Woke on the touch, far ahead of the 5s timeout.
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_returns_after_timeout_without_touch(self, signal_dir):
        marker_before = jobs_utils.get_waiting_jobs_marker_mtime()
        start = time.monotonic()
        await controller_module.wait_for_waiting_jobs_signal(
            marker_before, timeout=0.1, check_interval=0.01)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_returns_immediately_if_marker_changed_before_wait(
            self, signal_dir):
        # Touch that lands between the controller's pre-query snapshot and the
        # wait must not be lost: the snapshot is stale, so return at once.
        marker_before = jobs_utils.get_waiting_jobs_marker_mtime()
        jobs_utils.touch_waiting_jobs_marker()
        start = time.monotonic()
        await controller_module.wait_for_waiting_jobs_signal(marker_before,
                                                             timeout=5.0,
                                                             check_interval=1.0)
        assert time.monotonic() - start < 0.5


class TestSubmitSideTouchesMarker:
    """Every path that makes a job WAITING must touch the marker."""

    def test_submit_jobs_touches_marker_after_set_waiting(
            self, monkeypatch, signal_dir, tmp_path):
        dag_yaml = tmp_path / 'dag.yaml'
        dag_yaml.write_text('name: x\n')
        user_yaml = tmp_path / 'user.yaml'
        user_yaml.write_text('name: x\n')
        env_file = tmp_path / 'env'
        env_file.write_text('')

        order = MagicMock()
        monkeypatch.setattr(managed_job_state, 'get_job_controller_process',
                            lambda job_id: None)
        monkeypatch.setattr(managed_job_state, 'scheduler_set_waiting',
                            order.set_waiting)
        monkeypatch.setattr(jobs_utils, 'touch_waiting_jobs_marker',
                            order.touch)
        monkeypatch.setattr(scheduler,
                            'maybe_start_controllers',
                            lambda from_scheduler=False: None)

        scheduler.submit_jobs([1],
                              str(dag_yaml),
                              str(user_yaml),
                              str(env_file),
                              priority=0)

        names = [c[0] for c in order.mock_calls]
        assert names == ['set_waiting', 'touch'], names

    def test_ha_recovery_touches_marker_when_jobs_are_requeued(
            self, monkeypatch, signal_dir, tmp_path):
        monkeypatch.setattr(scheduler,
                            'maybe_start_controllers',
                            lambda from_scheduler=False: None)
        monkeypatch.setattr(skylet_constants, 'HA_PERSISTENT_RECOVERY_LOG_PATH',
                            str(tmp_path / '{}recovery.log'))
        job = {
            'job_id': 3,
            'controller_pid': None,
            'controller_pid_started_at': None,
            'schedule_state':
                managed_job_state.ManagedJobScheduleState.LAUNCHING,
            'status': managed_job_state.ManagedJobStatus.STARTING,
        }
        monkeypatch.setattr(managed_job_state, 'get_managed_jobs_with_filters',
                            lambda fields: ([job], None))
        reset = MagicMock()
        monkeypatch.setattr(managed_job_state, 'reset_job_for_recovery', reset)

        jobs_utils.ha_recovery_for_consolidation_mode()

        reset.assert_called_once_with(3)
        assert jobs_utils.get_waiting_jobs_marker_mtime() is not None


class TestMonitorLoopWakesOnMarker:
    """End-to-end: an idle monitor_loop claims a job right after a touch."""

    @pytest.mark.asyncio
    async def test_claims_job_promptly_after_marker_touch(
            self, monkeypatch, signal_dir):
        # Without the marker, the loop would sleep the full poll timeout after
        # the first empty claim. Make that timeout long so the test only
        # passes if the touch actually wakes the loop.
        monkeypatch.setattr(controller_module,
                            'WAITING_JOB_POLL_TIMEOUT_SECONDS', 30.0)
        monkeypatch.setattr(controller_module,
                            'WAITING_JOBS_MARKER_CHECK_INTERVAL_SECONDS', 0.01)
        monkeypatch.setattr(controller_utils, 'get_number_of_jobs_controllers',
                            lambda: 1)
        monkeypatch.setattr(controller_module.metrics_lib, 'METRICS_ENABLED',
                            False)
        os.makedirs(signal_dir, exist_ok=True)

        first_empty_claim = asyncio.Event()
        claimed = asyncio.Event()
        calls = {'n': 0}

        async def fake_get_waiting_job(pid, pid_started_at):
            calls['n'] += 1
            if calls['n'] == 1:
                first_empty_claim.set()
                return None
            if calls['n'] == 2:
                return {'job_id': 7, 'pool': None}
            # Park forever afterwards so the loop does not spin.
            await asyncio.sleep(3600)
            return None

        monkeypatch.setattr(managed_job_state, 'get_waiting_job_async',
                            fake_get_waiting_job)

        manager = controller_module.ControllerManager('test-uuid')

        async def fake_start_job(job_id, pool=None):
            assert job_id == 7
            claimed.set()

        manager.start_job = fake_start_job

        loop_task = asyncio.create_task(manager.monitor_loop())
        try:
            await asyncio.wait_for(first_empty_claim.wait(), timeout=5)
            start = time.monotonic()
            jobs_utils.touch_waiting_jobs_marker()
            await asyncio.wait_for(claimed.wait(), timeout=5)
            assert time.monotonic() - start < 1.0
        finally:
            loop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop_task


class TestCancelLoopIgnoresMarker:
    """The marker lives in the cancel-signal dir and must not be mistaken."""

    @pytest.mark.asyncio
    async def test_marker_is_not_logged_as_unexpected_file(
            self, monkeypatch, signal_dir):
        os.makedirs(signal_dir, exist_ok=True)
        jobs_utils.touch_waiting_jobs_marker()
        manager = controller_module.ControllerManager('test-uuid')

        fake_logger = MagicMock()
        monkeypatch.setattr(controller_module, 'logger', fake_logger)

        # Run exactly one iteration of the cancel loop.
        async def stop(*_):
            raise asyncio.CancelledError()

        with patch('asyncio.sleep', new=stop):
            with pytest.raises(asyncio.CancelledError):
                await manager.cancel_job()

        for call in fake_logger.debug.call_args_list:
            assert 'unexpected file' not in str(call).lower(), call


class TestSharedFilesystemRobustness:
    """Behaviors that matter when the signals dir is on NFS (EFS/Filestore)."""

    @pytest.mark.asyncio
    async def test_wait_does_not_wake_when_marker_disappears(self, signal_dir):
        # A transient stat failure (or someone deleting the marker) must not
        # look like a submission: waking on None would spin the claim query.
        jobs_utils.touch_waiting_jobs_marker()
        marker_before = jobs_utils.get_waiting_jobs_marker_mtime()
        assert marker_before is not None
        os.remove(signal_dir / managed_job_constants.WAITING_JOBS_MARKER_NAME)

        start = time.monotonic()
        await controller_module.wait_for_waiting_jobs_signal(
            marker_before, timeout=0.1, check_interval=0.01)
        assert time.monotonic() - start >= 0.1

    def test_ensure_marker_exists_creates_but_never_bumps(self, signal_dir):
        # Controllers pre-create the marker at startup so that NFS negative
        # dentry caching cannot hide the first touch from another replica.
        # Pre-creating must not itself look like a submission to peers.
        assert jobs_utils.get_waiting_jobs_marker_mtime() is None
        jobs_utils.ensure_waiting_jobs_marker_exists()
        first = jobs_utils.get_waiting_jobs_marker_mtime()
        assert first is not None

        time.sleep(0.01)
        jobs_utils.ensure_waiting_jobs_marker_exists()
        assert jobs_utils.get_waiting_jobs_marker_mtime() == first

    def test_ensure_marker_exists_is_best_effort(self, monkeypatch, tmp_path):
        blocker = tmp_path / 'blocker'
        blocker.write_text('x')
        monkeypatch.setattr(managed_job_constants, 'CONSOLIDATED_SIGNAL_PATH',
                            str(blocker / 'signals'))
        jobs_utils.ensure_waiting_jobs_marker_exists()  # must not raise

    def test_mtime_is_read_through_an_open_handle(self, signal_dir,
                                                  monkeypatch):
        # On NFS a bare stat() can serve a cached mtime for up to acregmax
        # (60s by default); open() forces a GETATTR under close-to-open
        # semantics. Assert the read goes through open()+fstat, not stat().
        jobs_utils.touch_waiting_jobs_marker()
        calls = []
        real_fstat = os.fstat

        def spy_fstat(fd):
            calls.append('fstat')
            return real_fstat(fd)

        real_stat = os.stat
        marker = str(signal_dir /
                     managed_job_constants.WAITING_JOBS_MARKER_NAME)

        def guarded_stat(path, *args, **kwargs):
            if isinstance(path, str) and path == marker:
                calls.append('stat')
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, 'fstat', spy_fstat)
        monkeypatch.setattr(os, 'stat', guarded_stat)
        assert jobs_utils.get_waiting_jobs_marker_mtime() is not None
        assert calls == ['fstat']


class TestReviewFindings:
    """Regression tests for review findings on the marker wake-up."""

    def test_mtime_read_survives_fstat_failure_and_closes_fd(
            self, signal_dir, monkeypatch):
        # On NFS, open() can succeed and fstat() then fail (e.g. ESTALE).
        # That must read as "no signal" (None), not escape into monitor_loop
        # and take the controller down; and the fd must not leak.
        jobs_utils.touch_waiting_jobs_marker()
        closed = []
        real_close = os.close

        def spy_close(fd):
            closed.append(fd)
            real_close(fd)

        def failing_fstat(fd):
            raise OSError(116, 'Stale file handle')

        monkeypatch.setattr(os, 'fstat', failing_fstat)
        monkeypatch.setattr(os, 'close', spy_close)
        assert jobs_utils.get_waiting_jobs_marker_mtime() is None
        assert len(closed) == 1

    def test_wheel_update_requeue_touches_marker(self, signal_dir, monkeypatch,
                                                 tmp_path):
        # The wheel-update branch of maybe_start_controllers resets every
        # live job to WAITING; the scheduler contract says every WAITING
        # transition is followed by a marker touch.
        cur = tmp_path / 'hash'
        cur.write_text('new')
        (tmp_path / 'hash.old').write_text('old')
        monkeypatch.setattr(scheduler, 'CURRENT_HASH', str(cur))
        monkeypatch.setattr(scheduler, 'JOB_CONTROLLER_PID_LOCK',
                            str(tmp_path / 'pid.lock'))
        monkeypatch.setattr(jobs_utils, 'is_consolidation_mode', lambda: False)
        order = MagicMock()
        monkeypatch.setattr(scheduler.sdk, 'api_stop', order.api_stop)
        monkeypatch.setattr(managed_job_state, 'reset_jobs_for_recovery',
                            order.reset)
        monkeypatch.setattr(jobs_utils, 'touch_waiting_jobs_marker',
                            order.touch)
        # Stop before any controller is actually spawned.
        monkeypatch.setattr(scheduler, 'get_alive_controllers', lambda: None)

        scheduler.maybe_start_controllers(from_scheduler=True)

        names = [c[0] for c in order.mock_calls]
        assert names == ['api_stop', 'reset', 'touch'], names
