"""Unit tests for managed-job emergency recovery.

Covers the two layers of the feature:
- State transitions and budget bookkeeping (sky/jobs/state.py), run against
  a real temporary SQLite database.
- The retry loop in JobController.run() (sky/jobs/controller.py), driven
  with mocked state collaborators so every decision branch (retry, usurped,
  fail, cancellation ordering) is exercised deterministically.
"""
import asyncio
import contextlib
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import exceptions
from sky.jobs import constants as jobs_constants
from sky.jobs import controller
from sky.jobs import state

_PID = 1234
_PID_STARTED_AT = 111.0
_OWN_RECORD = state.ControllerPidRecord(pid=_PID, started_at=_PID_STARTED_AT)


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state.

    Follows the pattern from test_jobs_state.py.
    """
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _seed_job(engine,
              job_id: int = 1,
              status: str = 'RUNNING',
              schedule_state: str = 'ALIVE',
              pid: int = _PID,
              pid_started_at: float = _PID_STARTED_AT,
              last_recovered_at: float = -1.0,
              job_duration: float = 0.0):
    with engine.connect() as conn:
        conn.execute(state.job_info_table.insert().values(
            spot_job_id=job_id,
            name='test-job',
            schedule_state=schedule_state,
            controller_pid=pid,
            controller_pid_started_at=pid_started_at,
        ))
        conn.execute(state.spot_table.insert().values(
            job_name='test-job',
            status=status,
            spot_job_id=job_id,
            task_id=0,
            last_recovered_at=last_recovered_at,
            job_duration=job_duration,
        ))
        conn.commit()


def _get_task_row(engine, job_id: int = 1):
    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(state.spot_table).where(
                state.spot_table.c.spot_job_id == job_id)).mappings().one()
    return dict(row)


def _get_job_info_row(engine, job_id: int = 1):
    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(state.job_info_table).where(
                state.job_info_table.c.spot_job_id ==
                job_id)).mappings().one()
    return dict(row)


def _make_callback():
    calls = []

    async def callback(status: str):
        calls.append(status)

    return callback, calls


class TestEmergencyRecoveryState:
    """State transitions and budget bookkeeping on a real SQLite DB."""

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_saves_prior_status(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, calls = _make_callback()

        applied = await state.set_emergency_recovering_async(
            1, 0, reason='test reason', callback_func=callback)

        assert applied is True
        row = _get_task_row(engine)
        assert row['status'] == 'EMERGENCY_RECOVERING'
        assert row['status_before_emergency'] == 'RUNNING'
        assert calls == ['EMERGENCY_RECOVERING']
        events = state.get_job_events(1)
        assert any(
            e['new_status'] == state.ManagedJobStatus.EMERGENCY_RECOVERING and
            e['reason'] == 'test reason' for e in events)

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_rerun_keeps_saved_status(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, _ = _make_callback()

        assert await state.set_emergency_recovering_async(
            1, 0, reason='first', callback_func=callback)
        # Re-running the bookkeeping (e.g. after a transient DB failure on a
        # later step) must not overwrite the saved pre-emergency status.
        assert await state.set_emergency_recovering_async(
            1, 0, reason='re-run', callback_func=callback)

        row = _get_task_row(engine)
        assert row['status_before_emergency'] == 'RUNNING'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('status', ['CANCELLING', 'SUCCEEDED', 'FAILED'])
    async def test_set_emergency_recovering_leaves_cancelling_and_terminal(
            self, _mock_managed_jobs_db_conn, status):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status=status)
        callback, calls = _make_callback()

        applied = await state.set_emergency_recovering_async(
            1, 0, reason='test', callback_func=callback)

        assert applied is False
        row = _get_task_row(engine)
        assert row['status'] == status
        assert row['status_before_emergency'] is None
        assert not calls
        assert not state.get_job_events(1)

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_accumulates_duration(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        started_running_at = time.time() - 100
        _seed_job(engine,
                  status='RUNNING',
                  last_recovered_at=started_running_at,
                  job_duration=0.0)
        callback, _ = _make_callback()

        await state.set_emergency_recovering_async(1,
                                                   0,
                                                   reason='test',
                                                   callback_func=callback)

        row = _get_task_row(engine)
        # ~100s of running time accumulated at the transition.
        assert 90 < row['job_duration'] < 110
        # last_recovered_at is left untouched (it was already valid).
        assert row['last_recovered_at'] == started_running_at

    @pytest.mark.asyncio
    async def test_set_emergency_recovered_restores_running(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, calls = _make_callback()
        await state.set_emergency_recovering_async(1,
                                                   0,
                                                   reason='test',
                                                   callback_func=callback)

        restored_time = time.time()
        await state.set_emergency_recovered_async(1,
                                                  0,
                                                  restored_time=restored_time,
                                                  callback_func=callback)

        row = _get_task_row(engine)
        assert row['status'] == 'RUNNING'
        assert row['status_before_emergency'] is None
        assert row['last_recovered_at'] == restored_time
        # recovery_count counts cluster recoveries; a re-attach is not one.
        assert row['recovery_count'] == 0
        assert calls == ['EMERGENCY_RECOVERING', 'EMERGENCY_RECOVERED']

    @pytest.mark.asyncio
    async def test_set_emergency_recovered_requires_emergency_status(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, _ = _make_callback()

        with pytest.raises(exceptions.ManagedJobStatusError):
            await state.set_emergency_recovered_async(
                1, 0, restored_time=time.time(), callback_func=callback)

    @pytest.mark.asyncio
    async def test_budget_roundtrip_and_fence(self,
                                              _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)

        assert await state.get_emergency_recovery_budget_async(1) == (0, None)

        now = time.time()
        assert await state.record_emergency_recovery_attempt_async(
            1, 1, now, _PID, _PID_STARTED_AT) is True
        assert await state.get_emergency_recovery_budget_async(1) == (1, now)

        # A mismatched fence (this controller no longer owns the job) must
        # write nothing.
        assert await state.record_emergency_recovery_attempt_async(
            1, 7, now + 1, 9999, _PID_STARTED_AT) is False
        assert await state.record_emergency_recovery_attempt_async(
            1, 7, now + 1, _PID, 222.0) is False
        assert await state.get_emergency_recovery_budget_async(1) == (1, now)

        # The write is an absolute value: re-running the same attempt is
        # idempotent and cannot double-spend the budget.
        assert await state.record_emergency_recovery_attempt_async(
            1, 1, now, _PID, _PID_STARTED_AT) is True
        assert await state.get_emergency_recovery_budget_async(1) == (1, now)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'schedule_state,pid,expected',
        [
            # Stuck LAUNCHING owned by us: released back to ALIVE.
            ('LAUNCHING', _PID, 'ALIVE'),
            # LAUNCHING owned by someone else: left untouched (the caller
            # treats anything other than ALIVE as loss of ownership).
            ('LAUNCHING', 9999, 'LAUNCHING'),
            # Already ALIVE: no-op.
            ('ALIVE', _PID, 'ALIVE'),
            # Reset by something else (e.g. HA recovery): left untouched.
            ('WAITING', _PID, 'WAITING'),
        ])
    async def test_normalize_schedule_state(self, _mock_managed_jobs_db_conn,
                                            schedule_state, pid, expected):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, schedule_state=schedule_state, pid=pid)

        result = await state.normalize_schedule_state_for_emergency_retry_async(
            1, _PID, _PID_STARTED_AT)

        assert result == state.ManagedJobScheduleState(expected)
        assert _get_job_info_row(engine)['schedule_state'] == expected

    @pytest.mark.asyncio
    async def test_get_status_before_emergency_unset(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)
        assert await state.get_status_before_emergency_async(1, 0) is None


class _RetryLoopHarness:
    """Drives the real JobController.run() with mocked collaborators."""

    def __init__(self, monkeypatch, body_effects):
        """body_effects: side_effect list for _run_one_task."""
        jc = controller.JobController.__new__(controller.JobController)
        jc._job_id = 1
        task = MagicMock()
        task.name = 'task0'
        dag = MagicMock()
        dag.is_job_group.return_value = False
        dag.tasks = [task]
        jc._dag = dag
        jc._controller_pid_record = _OWN_RECORD
        jc._usurped = False
        jc._emergency_backoff_seconds = None
        jc._run_one_task = AsyncMock(side_effect=body_effects)
        jc._update_failed_task_state = AsyncMock()
        self.jc = jc

        # State collaborators, default to the happy path: we own the job,
        # fresh budget, transitions apply, schedule state is clean.
        self.get_controller_process = MagicMock(return_value=_OWN_RECORD)
        self.get_budget = AsyncMock(return_value=(0, None))
        self.record_attempt = AsyncMock(return_value=True)
        self.get_latest_task = AsyncMock(
            return_value=(0, state.ManagedJobStatus.RUNNING))
        self.set_emergency = AsyncMock(return_value=True)
        self.normalize = AsyncMock(
            return_value=state.ManagedJobScheduleState.ALIVE)
        self.set_cancelling = AsyncMock()
        self.set_cancelled = AsyncMock()
        self.sleeps = []

        async def _fake_sleep(seconds):
            self.sleeps.append(seconds)

        mjs = 'sky.jobs.controller.managed_job_state'
        monkeypatch.setattr(f'{mjs}.get_job_controller_process',
                            self.get_controller_process)
        monkeypatch.setattr(f'{mjs}.get_emergency_recovery_budget_async',
                            self.get_budget)
        monkeypatch.setattr(f'{mjs}.record_emergency_recovery_attempt_async',
                            self.record_attempt)
        monkeypatch.setattr(f'{mjs}.get_latest_task_id_status_async',
                            self.get_latest_task)
        monkeypatch.setattr(f'{mjs}.set_emergency_recovering_async',
                            self.set_emergency)
        monkeypatch.setattr(
            f'{mjs}.normalize_schedule_state_for_emergency_retry_async',
            self.normalize)
        monkeypatch.setattr(f'{mjs}.set_cancelling_async',
                            self.set_cancelling)
        monkeypatch.setattr(f'{mjs}.set_cancelled_async', self.set_cancelled)
        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_utils.event_callback_func',
            MagicMock(return_value=AsyncMock()))
        monkeypatch.setattr('asyncio.sleep', _fake_sleep)


class TestEmergencyRetryLoop:
    """The retry loop in JobController.run(), all decision branches."""

    @pytest.mark.asyncio
    async def test_unexpected_error_retries_and_succeeds(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch,
                              [RuntimeError('boom'), True])

        await h.jc.run()

        assert h.jc._run_one_task.call_count == 2
        h.jc._update_failed_task_state.assert_not_called()
        # One attempt recorded with the first backoff.
        h.record_attempt.assert_awaited_once()
        assert h.record_attempt.await_args.args[1] == 1  # attempt count
        assert h.sleeps == [
            jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS
        ]
        # Normal finally ran exactly once.
        h.set_cancelling.assert_awaited_once()
        h.set_cancelled.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize('error,expected_status', [
        (exceptions.ProvisionPrechecksError(reasons=[ValueError('bad')]),
         state.ManagedJobStatus.FAILED_PRECHECKS),
        (exceptions.ManagedJobReachedMaxRetriesError('max'),
         state.ManagedJobStatus.FAILED_NO_RESOURCE),
        (exceptions.ClusterSetUpError('oom'),
         state.ManagedJobStatus.FAILED_SETUP),
    ])
    async def test_known_terminal_exceptions_unchanged(
            self, monkeypatch, error, expected_status):
        h = _RetryLoopHarness(monkeypatch, [error])

        await h.jc.run()

        assert h.jc._run_one_task.call_count == 1
        h.jc._update_failed_task_state.assert_awaited_once()
        assert h.jc._update_failed_task_state.await_args.args[1] == (
            expected_status)
        # The emergency machinery is not involved.
        h.get_budget.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_budget_exhaustion_fails_job(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.get_budget.return_value = (
            jobs_constants.EMERGENCY_RECOVERY_MAX_ATTEMPTS, time.time())

        await h.jc.run()

        h.jc._update_failed_task_state.assert_awaited_once()
        assert h.jc._update_failed_task_state.await_args.args[1] == (
            state.ManagedJobStatus.FAILED_CONTROLLER)
        failure_reason = h.jc._update_failed_task_state.await_args.args[2]
        assert 'Emergency recovery was attempted' in failure_reason
        h.record_attempt.assert_not_awaited()
        # Normal finally still runs (full cleanup path is not skipped).
        h.set_cancelling.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_budget_decays_after_reset_window(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        old = time.time() - (
            jobs_constants.EMERGENCY_RECOVERY_RESET_WINDOW_SECONDS + 1)
        h.get_budget.return_value = (
            jobs_constants.EMERGENCY_RECOVERY_MAX_ATTEMPTS, old)

        await h.jc.run()

        # The stale episode was forgotten: attempt 1 of a new episode.
        h.record_attempt.assert_awaited_once()
        assert h.record_attempt.await_args.args[1] == 1
        h.jc._update_failed_task_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_usurped_at_ownership_check(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.get_controller_process.return_value = state.ControllerPidRecord(
            pid=9999, started_at=222.0)

        await h.jc.run()

        assert h.jc.usurped is True
        # Stand down without touching any job state: no budget spend, no
        # failure write, and the finally is skipped entirely.
        h.record_attempt.assert_not_awaited()
        h.jc._update_failed_task_state.assert_not_called()
        h.set_cancelling.assert_not_awaited()
        h.set_cancelled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_usurped_at_budget_record(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.record_attempt.return_value = False

        await h.jc.run()

        assert h.jc.usurped is True
        h.set_emergency.assert_not_awaited()
        h.set_cancelling.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_usurped_at_schedule_state_normalization(
            self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.normalize.return_value = state.ManagedJobScheduleState.WAITING

        await h.jc.run()

        assert h.jc.usurped is True
        h.set_cancelling.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancelling_task_retries_without_backoff(
            self, monkeypatch):
        # set_emergency_recovering refuses (task is CANCELLING); the body is
        # retried immediately and re-raises the cancellation via the resume
        # path.
        h = _RetryLoopHarness(
            monkeypatch,
            [RuntimeError('boom'),
             asyncio.CancelledError()])
        h.set_emergency.return_value = False

        with pytest.raises(asyncio.CancelledError):
            await h.jc.run()

        assert h.jc._run_one_task.call_count == 2
        assert not h.sleeps  # no backoff for the cancellation handoff
        # Cancellation ordering: CANCELLING is set, but CANCELLED is left to
        # run_job_loop, which only sets it after cleanup.
        h.set_cancelling.assert_awaited_once()
        h.set_cancelled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_during_backoff_sleep(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])

        async def _cancelled_sleep(seconds):
            h.sleeps.append(seconds)
            raise asyncio.CancelledError()

        monkeypatch.setattr('asyncio.sleep', _cancelled_sleep)

        with pytest.raises(asyncio.CancelledError):
            await h.jc.run()

        assert h.sleeps == [
            jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS
        ]
        h.set_cancelling.assert_awaited_once()
        h.set_cancelled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_during_bookkeeping(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.get_budget.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await h.jc.run()

        # Cancellation ordering preserved even when the cancel lands inside
        # the bookkeeping sequence.
        h.set_cancelling.assert_awaited_once()
        h.set_cancelled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bookkeeping_outer_retry_recovers(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        h.get_budget.side_effect = [
            ConnectionError('db blip'),
            ConnectionError('db blip'),
            (0, None),
        ]

        await h.jc.run()

        h.jc._update_failed_task_state.assert_not_called()
        assert h.jc._run_one_task.call_count == 2

    @pytest.mark.asyncio
    async def test_bookkeeping_exhausted_falls_back_to_failing(
            self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom')])
        h.get_budget.side_effect = ConnectionError('db down')

        await h.jc.run()

        h.jc._update_failed_task_state.assert_awaited_once()
        assert h.jc._update_failed_task_state.await_args.args[1] == (
            state.ManagedJobStatus.FAILED_CONTROLLER)
        failure_reason = h.jc._update_failed_task_state.await_args.args[2]
        assert 'bookkeeping failed' in failure_reason

    @pytest.mark.asyncio
    async def test_backoff_sequence_and_final_escape(self, monkeypatch):
        max_attempts = jobs_constants.EMERGENCY_RECOVERY_MAX_ATTEMPTS
        h = _RetryLoopHarness(monkeypatch, RuntimeError('boom'))
        now = time.time()
        h.get_budget.side_effect = [
            (i, None if i == 0 else now) for i in range(max_attempts + 1)
        ]

        await h.jc.run()

        base = jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS
        cap = jobs_constants.EMERGENCY_RECOVERY_BACKOFF_CAP_SECONDS
        expected = [min(base * 2**i, cap) for i in range(max_attempts)]
        assert h.sleeps == expected
        assert h.jc._run_one_task.call_count == max_attempts + 1
        h.jc._update_failed_task_state.assert_awaited_once()
        assert h.jc._update_failed_task_state.await_args.args[1] == (
            state.ManagedJobStatus.FAILED_CONTROLLER)
