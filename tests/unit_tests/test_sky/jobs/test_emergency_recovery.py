"""Unit tests for managed-job emergency recovery.

Covers the two layers of the feature:
- State transitions and budget bookkeeping (sky/jobs/state.py), run against
  a real temporary SQLite database.
- The retry loop in JobController.run() (sky/jobs/controller.py), driven
  with mocked state collaborators so every decision branch (retry, fail,
  cancellation ordering) is exercised deterministically.
"""
import asyncio
import contextlib
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import exceptions
from sky.jobs import constants as jobs_constants
from sky.jobs import controller
from sky.jobs import scheduler
from sky.jobs import state

_PID = 1234
_PID_STARTED_AT = 111.0


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
                state.job_info_table.c.spot_job_id == job_id)).mappings().one()
    return dict(row)


def _get_recovering_events(engine, job_id: int = 1):
    """recovery_source of each RECOVERING job event, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.select(state.job_events_table.c.recovery_source).where(
                sqlalchemy.and_(
                    state.job_events_table.c.spot_job_id == job_id,
                    state.job_events_table.c.new_status ==
                    state.ManagedJobStatus.RECOVERING.value,
                )).order_by(state.job_events_table.c.id.asc())).fetchall()
    return [r[0] for r in rows]


def _make_callback():
    calls = []

    async def callback(status: str):
        calls.append(status)

    return callback, calls


def _assert_jittered(sleeps, nominals):
    """The emergency backoff is jittered +/-50% around each nominal value."""
    assert len(sleeps) == len(nominals), (sleeps, nominals)
    for actual, nom in zip(sleeps, nominals):
        assert 0.5 * nom <= actual <= 1.5 * nom, (actual, nom)


class TestEmergencyRecoveryState:
    """State transitions and budget bookkeeping on a real SQLite DB."""

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_labels_episode(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, calls = _make_callback()

        applied = await state.set_emergency_recovering_async(
            1, 0, reason='test reason', callback_func=callback)

        assert applied is True
        row = _get_task_row(engine)
        # Visible status is the normal RECOVERING; the emergency cause is on
        # the event. The episode is open but carries no failure credit.
        assert row['status'] == 'RECOVERING'
        assert row['recovering_from_failure'] is not None
        assert not row['recovering_from_failure']
        assert calls == ['RECOVERING']
        events = state.get_job_events(1)
        assert any(e['new_status'] == state.ManagedJobStatus.RECOVERING and
                   e['reason'] == 'test reason' for e in events)
        # The RECOVERING event is tagged EMERGENCY.
        recovering_events = _get_recovering_events(engine, 1)
        assert recovering_events == [state.RecoverySource.EMERGENCY.value]

    @pytest.mark.asyncio
    async def test_emergency_preserves_failure_credit(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        callback, _ = _make_callback()

        # Re-running the bookkeeping (e.g. after a transient DB failure on a
        # later step) keeps the episode open and uncredited.
        _seed_job(engine, job_id=1, status='RUNNING')
        assert await state.set_emergency_recovering_async(
            1, 0, reason='first', callback_func=callback)
        assert await state.set_emergency_recovering_async(
            1, 0, reason='re-run', callback_func=callback)
        row1 = _get_task_row(engine, 1)
        assert row1['recovering_from_failure'] is not None
        assert not row1['recovering_from_failure']

        # An emergency that interrupts an in-flight FAILURE recovery is a
        # system-driven interruption: it must not erase the episode's failure
        # credit — the eventual completion still counts as a genuine failure
        # recovery. (The event log records both occurrences separately.)
        _seed_job(engine, job_id=2, status='RUNNING')
        await state.set_recovering_async(2,
                                         0,
                                         force_transit_to_recovering=False,
                                         callback_func=callback)
        assert await state.set_emergency_recovering_async(
            2, 0, reason='emergency mid-recovery', callback_func=callback)
        assert _get_task_row(engine, 2)['recovering_from_failure']
        assert _get_recovering_events(engine, 2) == [
            state.RecoverySource.FAILURE.value,
            state.RecoverySource.EMERGENCY.value,
        ]

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
        assert row['recovering_from_failure'] is None
        assert not calls
        assert not state.get_job_events(1)

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_leaves_pending(
            self, _mock_managed_jobs_db_conn):
        """A PENDING task never initialized (set_starting has not run).
        Marking it RECOVERING would make the retry treat it as a resume and
        skip initialization forever, leaving submitted_at/run_timestamp/
        resources/specs NULL (get_task_specs then does json.loads(None) ->
        TypeError). The emergency setter must leave PENDING untouched so the
        retry relaunches it fresh."""
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='PENDING')
        callback, calls = _make_callback()

        applied = await state.set_emergency_recovering_async(
            1, 0, reason='boom', callback_func=callback)

        assert applied is False
        row = _get_task_row(engine)
        assert row['status'] == 'PENDING'
        assert row['recovering_from_failure'] is None
        assert not calls
        assert _get_recovering_events(engine, 1) == []

    @pytest.mark.asyncio
    async def test_set_emergency_recovering_emit_event_false_skips_event(
            self, _mock_managed_jobs_db_conn):
        """emit_event=False applies the status transition but appends no
        RECOVERING event and fires no callback — so an outer-retry re-run
        (the caller already emitted the event once) cannot duplicate it."""
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RUNNING')
        callback, calls = _make_callback()

        applied = await state.set_emergency_recovering_async(
            1, 0, reason='boom', callback_func=callback, emit_event=False)

        assert applied is True
        assert _get_task_row(engine)['status'] == 'RECOVERING'
        assert _get_recovering_events(engine, 1) == []
        assert not calls

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
    async def test_set_recovering_records_source(self,
                                                 _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        callback, _ = _make_callback()

        # Default source is FAILURE (preemption/failure); the episode is
        # labeled on both the event and the row.
        _seed_job(engine, job_id=1, status='RUNNING')
        await state.set_recovering_async(1,
                                         0,
                                         force_transit_to_recovering=False,
                                         callback_func=callback)
        assert _get_recovering_events(
            engine, 1) == [state.RecoverySource.FAILURE.value]
        assert _get_task_row(engine, 1)['recovering_from_failure']

        # A RESTART-tagged force recovery records RESTART.
        _seed_job(engine, job_id=2, status='STARTING')
        await state.set_recovering_async(
            2,
            0,
            force_transit_to_recovering=True,
            callback_func=callback,
            recovery_source=state.RecoverySource.RESTART)
        assert _get_recovering_events(
            engine, 2) == [state.RecoverySource.RESTART.value]
        row2 = _get_task_row(engine, 2)
        assert row2['recovering_from_failure'] is not None
        assert not row2['recovering_from_failure']

    @pytest.mark.asyncio
    async def test_failure_credit_cleared_on_exits(self,
                                                   _mock_managed_jobs_db_conn):
        # A stale recovering_from_failure must not survive into a later
        # unrelated recovery (it would corrupt that episode's recovery_count
        # accounting); every exit transition clears it.
        engine = _mock_managed_jobs_db_conn
        callback, _ = _make_callback()

        async def _assert_cleared_after(setter):
            _seed_job(engine, job_id=1, status='RUNNING')
            await state.set_emergency_recovering_async(1,
                                                       0,
                                                       reason='x',
                                                       callback_func=callback)
            assert _get_task_row(engine, 1)['recovering_from_failure'] \
                is not None
            await setter()
            assert _get_task_row(engine, 1)['recovering_from_failure'] \
                is None
            # reset for the next sub-case
            with engine.connect() as conn:
                conn.execute(state.spot_table.delete())
                conn.execute(state.job_info_table.delete())
                conn.commit()

        # The reachable exits from an emergency RECOVERING: forced/normal
        # recovery completing (RECOVERING -> RUNNING), and failing the task.
        await _assert_cleared_after(lambda: state.set_recovered_async(
            1, 0, recovered_time=time.time(), callback_func=callback))
        await _assert_cleared_after(lambda: state.set_failed_async(
            1,
            0,
            failure_type=state.ManagedJobStatus.FAILED,
            failure_reason='boom',
            callback_func=callback))

    @pytest.mark.asyncio
    async def test_recovery_count_only_counts_failure_episodes(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        callback, _ = _make_callback()

        # FAILURE episode: counts.
        _seed_job(engine, job_id=1, status='RUNNING')
        await state.set_recovering_async(1,
                                         0,
                                         force_transit_to_recovering=False,
                                         callback_func=callback)
        await state.set_recovered_async(1,
                                        0,
                                        recovered_time=time.time(),
                                        callback_func=callback)
        assert _get_task_row(engine, 1)['recovery_count'] == 1

        # EMERGENCY episode (always relaunched): system-driven, not counted.
        _seed_job(engine, job_id=2, status='RUNNING')
        await state.set_emergency_recovering_async(2,
                                                   0,
                                                   reason='x',
                                                   callback_func=callback)
        await state.set_recovered_async(2,
                                        0,
                                        recovered_time=time.time(),
                                        callback_func=callback)
        assert _get_task_row(engine, 2)['recovery_count'] == 0

        # RESTART episode (forced recovery on controller restart): not counted.
        _seed_job(engine, job_id=3, status='STARTING')
        await state.set_recovering_async(
            3,
            0,
            force_transit_to_recovering=True,
            callback_func=callback,
            recovery_source=state.RecoverySource.RESTART)
        await state.set_recovered_async(3,
                                        0,
                                        recovered_time=time.time(),
                                        callback_func=callback)
        assert _get_task_row(engine, 3)['recovery_count'] == 0

        # Legacy row (RECOVERING written before the column existed): NULL
        # source is treated as FAILURE for back-compat.
        _seed_job(engine, job_id=4, status='RECOVERING')
        await state.set_recovered_async(4,
                                        0,
                                        recovered_time=time.time(),
                                        callback_func=callback)
        assert _get_task_row(engine, 4)['recovery_count'] == 1

        # count_recovery=False (kept-STARTING resume whose relaunch retry
        # drifted the row to RECOVERING): never counted.
        _seed_job(engine, job_id=5, status='RECOVERING')
        await state.set_recovered_async(5,
                                        0,
                                        recovered_time=time.time(),
                                        callback_func=callback,
                                        count_recovery=False)
        assert _get_task_row(engine, 5)['recovery_count'] == 0

    @pytest.mark.asyncio
    async def test_budget_roundtrip(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)

        assert await state.get_emergency_recovery_budget_async(1) == (0, None)

        now = time.time()
        await state.record_emergency_recovery_attempt_async(1, 1, now)
        assert await state.get_emergency_recovery_budget_async(1) == (1, now)

        # The write is an absolute value: re-running the same attempt is
        # idempotent and cannot double-spend the budget.
        await state.record_emergency_recovery_attempt_async(1, 1, now)
        assert await state.get_emergency_recovery_budget_async(1) == (1, now)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'schedule_state,expected',
        [
            # Stuck launch-adjacent states: reset back to ALIVE (they
            # hold launch accounting / block lower-priority scheduling).
            ('LAUNCHING', 'ALIVE'),
            ('ALIVE_WAITING', 'ALIVE'),
            ('ALIVE_BACKOFF', 'ALIVE'),
            # Already ALIVE: no-op.
            ('ALIVE', 'ALIVE'),
            # Reset by something else (e.g. restart recovery): left
            # untouched.
            ('WAITING', 'WAITING'),
        ])
    async def test_normalize_schedule_state(self, _mock_managed_jobs_db_conn,
                                            schedule_state, expected):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, schedule_state=schedule_state)

        await state.normalize_schedule_state_for_emergency_retry_async(1)

        assert _get_job_info_row(engine)['schedule_state'] == expected

    @pytest.mark.asyncio
    async def test_launch_slot_released_when_launch_raises(
            self, _mock_managed_jobs_db_conn):
        # An unexpected error escaping the launch (the emergency-recovery
        # trigger) must release the in-memory launching slot (the
        # LAUNCHES_PER_WORKER gate) on the way out, so the job never holds a
        # slot during the emergency backoff. The stuck DB LAUNCHING state it
        # leaves behind is released by the emergency bookkeeping.
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='STARTING', schedule_state='LAUNCHING')

        starting: set = set()
        lock = asyncio.Lock()
        signal = asyncio.Condition(lock=lock)
        with pytest.raises(RuntimeError):
            async with scheduler.scheduled_launch(1, starting, lock, signal):
                assert 1 in starting
                raise RuntimeError('boom')
        assert not starting
        # The launch never completed, so the job is still LAUNCHING in the
        # DB...
        assert _get_job_info_row(engine)['schedule_state'] == 'LAUNCHING'
        # ...until the emergency bookkeeping normalizes it back to an alive,
        # non-launching state for the duration of the backoff.
        await state.normalize_schedule_state_for_emergency_retry_async(1)
        assert _get_job_info_row(engine)['schedule_state'] == 'ALIVE'

    def test_backoff_schedule_states_block_controller_autostop(
            self, _mock_managed_jobs_db_conn):
        # The controller-VM autostop keep-alive
        # (sky/skylet/events.py::AutostopEvent) does not consider the
        # controller idle while get_num_alive_jobs() > 0. Every schedule
        # state a job can hold during an emergency backoff must count, so a
        # backoff longer than the 10-minute idle window cannot let the
        # controller autostop out from under the pending retry.
        engine = _mock_managed_jobs_db_conn
        backoff_states = ('ALIVE', 'ALIVE_BACKOFF', 'ALIVE_WAITING',
                          'LAUNCHING')
        for i, schedule_state in enumerate(backoff_states, start=1):
            _seed_job(engine, job_id=i, schedule_state=schedule_state)
        assert state.get_num_alive_jobs() == len(backoff_states)

        # Sanity-check the complement: WAITING/DONE jobs do not keep the
        # controller alive.
        _seed_job(engine, job_id=10, schedule_state='WAITING')
        _seed_job(engine, job_id=11, schedule_state='DONE')
        assert state.get_num_alive_jobs() == len(backoff_states)


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
        jc._pool = None
        jc._emergency_backoff_seconds = None
        jc._run_one_task = AsyncMock(side_effect=body_effects)
        jc._update_failed_task_state = AsyncMock()
        jc._cleanup_cluster = AsyncMock()
        # Shared launching-slot primitives (real JobController gets these from
        # the ControllerManager). Seed the job into `starting` so slot-release
        # on the emergency path is observable.
        jc.starting = {1}
        jc.starting_lock = asyncio.Lock()
        jc.starting_signal = asyncio.Condition(jc.starting_lock)
        # The runtime log-capture hook is only invoked when a runtime is
        # registered; unregistered by default so the harness path is inert.
        jc._backend = MagicMock()
        # The real _load_dag reloads from the DB; the harness keeps the
        # mocked dag.
        jc._load_dag = MagicMock()
        self.jc = jc

        # State collaborators, default to the happy path: fresh budget,
        # transitions apply, schedule state is clean.
        self.get_budget = AsyncMock(return_value=(0, None))
        self.record_attempt = AsyncMock()
        self.get_latest_task = AsyncMock(
            return_value=(0, state.ManagedJobStatus.RUNNING))
        self.set_emergency = AsyncMock(return_value=True)
        self.normalize = AsyncMock()
        self.set_cancelling = AsyncMock()
        self.set_cancelled = AsyncMock()
        self.sleeps = []

        async def _fake_sleep(seconds):
            self.sleeps.append(seconds)

        mjs = 'sky.jobs.controller.managed_job_state'
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
        monkeypatch.setattr(f'{mjs}.set_cancelling_async', self.set_cancelling)
        monkeypatch.setattr(f'{mjs}.set_cancelled_async', self.set_cancelled)
        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_utils.event_callback_func',
            MagicMock(return_value=AsyncMock()))
        monkeypatch.setattr('asyncio.sleep', _fake_sleep)


class TestEmergencyRetryLoop:
    """The retry loop in JobController.run(), all decision branches."""

    @pytest.mark.asyncio
    async def test_unexpected_error_retries_and_succeeds(self, monkeypatch):
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])

        await h.jc.run()

        assert h.jc._run_one_task.call_count == 2
        h.jc._update_failed_task_state.assert_not_called()
        # One attempt recorded with the first backoff.
        h.record_attempt.assert_awaited_once()
        assert h.record_attempt.await_args.args[1] == 1  # attempt count
        _assert_jittered(
            h.sleeps, [jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS])
        # The doomed cluster is torn down during the bookkeeping (before the
        # backoff), not left running until the retry's forced recovery.
        h.jc._cleanup_cluster.assert_awaited_once()
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
    async def test_known_terminal_exceptions_unchanged(self, monkeypatch, error,
                                                       expected_status):
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
    async def test_cancelling_task_retries_without_backoff(self, monkeypatch):
        # set_emergency_recovering refuses (task is CANCELLING); the body is
        # retried immediately and re-raises the cancellation via the resume
        # path.
        h = _RetryLoopHarness(monkeypatch,
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

        _assert_jittered(
            h.sleeps, [jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS])
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
        nominal = [min(base * 2**i, cap) for i in range(max_attempts)]
        # The sleep is jittered +/-50% around the nominal backoff, so assert
        # each falls in [nominal/2, 3*nominal/2] rather than an exact value.
        assert len(h.sleeps) == max_attempts
        for actual, nom in zip(h.sleeps, nominal):
            assert 0.5 * nom <= actual <= 1.5 * nom, (actual, nom)
        assert h.jc._run_one_task.call_count == max_attempts + 1
        h.jc._update_failed_task_state.assert_awaited_once()
        assert h.jc._update_failed_task_state.await_args.args[1] == (
            state.ManagedJobStatus.FAILED_CONTROLLER)

    @pytest.mark.asyncio
    async def test_backoff_is_jittered(self, monkeypatch):
        """The backoff is not deterministic: repeated attempts at the same
        nominal (the capped steady state) produce different sleeps, each
        within +/-50% of the nominal."""
        max_attempts = jobs_constants.EMERGENCY_RECOVERY_MAX_ATTEMPTS
        h = _RetryLoopHarness(monkeypatch, RuntimeError('boom'))
        now = time.time()
        h.get_budget.side_effect = [
            (i, None if i == 0 else now) for i in range(max_attempts + 1)
        ]

        await h.jc.run()

        cap = jobs_constants.EMERGENCY_RECOVERY_BACKOFF_CAP_SECONDS
        # The tail of the schedule is all at the cap; jitter must make those
        # sleeps differ (a deterministic backoff would repeat the cap value).
        capped = [s for s in h.sleeps if 0.5 * cap <= s <= 1.5 * cap]
        assert len(capped) >= 2
        assert len(set(capped)) > 1, capped

    @pytest.mark.asyncio
    async def test_emergency_releases_launch_slot_during_backoff(
            self, monkeypatch):
        """A backing-off emergency episode must not keep holding one of the
        LAUNCHES_PER_WORKER slots (the in-memory `starting` set) for the
        whole backoff — that would starve new-job admission and other jobs'
        recovery launches on this worker. The slot must be freed before the
        backoff sleep."""
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        assert 1 in h.jc.starting  # seeded by start_job

        held_at_backoff = {}

        async def _sleep(seconds):
            h.sleeps.append(seconds)
            held_at_backoff['held'] = 1 in h.jc.starting

        monkeypatch.setattr('asyncio.sleep', _sleep)

        await h.jc.run()

        # The slot was released before the emergency backoff, not held for
        # its whole duration.
        assert held_at_backoff.get('held') is False
        assert 1 not in h.jc.starting

    @pytest.mark.asyncio
    async def test_budget_not_double_spent_across_rounds(self, monkeypatch):
        """The retry budget is read once per escaped error and reused across
        the outer-retry rounds. A transient failure after the attempt is
        recorded must not make the next round re-read the incremented count
        and spend a second unit."""
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        # Mirror the real DB: a re-read after recording attempt 1 would
        # observe 1 (i.e. a second spend), which must never happen.
        h.get_budget.side_effect = [(0, None), (1, time.time())]

        normalize_calls = {'n': 0}

        async def _normalize(job_id):
            normalize_calls['n'] += 1
            if normalize_calls['n'] == 1:
                raise ConnectionError('db blip after recording the attempt')

        h.normalize.side_effect = _normalize

        await h.jc.run()

        # Budget read exactly once (memoized); the incremented (1, ...) read
        # is never consumed, so every recorded attempt is 1.
        assert h.get_budget.await_count == 1
        assert h.record_attempt.await_count >= 2  # re-ran the round
        assert all(c.args[1] == 1 for c in h.record_attempt.await_args_list)
        h.jc._update_failed_task_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_emergency_on_pending_relaunches_fresh(self, monkeypatch):
        """An emergency on a PENDING task must not mark it RECOVERING (that
        would make the retry skip initialization). It is left PENDING for a
        fresh relaunch, but still budgeted and backed off like any
        episode."""
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        h.get_latest_task.return_value = (0, state.ManagedJobStatus.PENDING)

        await h.jc.run()

        h.set_emergency.assert_not_awaited()
        h.record_attempt.assert_awaited()
        _assert_jittered(
            h.sleeps, [jobs_constants.EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS])
        h.jc._update_failed_task_state.assert_not_called()
        assert h.jc._run_one_task.call_count == 2

    @pytest.mark.asyncio
    async def test_emergency_calls_on_before_recovery_before_teardown(
            self, monkeypatch):
        """The emergency path tears the cluster down before the retry runs,
        so it must give the runtime a chance to snapshot the about-to-be-lost
        run's logs first (on_before_recovery), mirroring the normal recovery
        path. Otherwise every emergency records a capture failure."""
        h = _RetryLoopHarness(monkeypatch, [RuntimeError('boom'), True])
        order = []

        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_runtime.is_registered',
            lambda: True)

        def _hook(*args, **kwargs):
            order.append('hook')

        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_runtime.on_before_recovery', _hook)
        monkeypatch.setattr(
            'sky.jobs.controller.global_user_state.'
            'get_handle_from_cluster_name', lambda name: MagicMock())

        async def _cleanup(name):
            order.append('teardown')

        h.jc._cleanup_cluster = AsyncMock(side_effect=_cleanup)

        await h.jc.run()

        # The hook ran, and it ran before the teardown.
        assert order[:2] == ['hook', 'teardown']


class TestBatchCoordinatorEmergencyResume:
    """Batch coordinator resume after an emergency recovery (real DB)."""

    def _make_batch_controller(self):
        jc = controller.JobController.__new__(controller.JobController)
        jc._job_id = 1
        jc._backend = MagicMock()
        jc._pool = None
        return jc

    def _batch_task(self):
        task = MagicMock()
        task.name = 'batch-task'
        task.metadata = {
            'batch_coordinator': True,
            'batch_dataset_path': 'ds',
            'batch_output_path': 'out',
            'batch_size': 1,
            'batch_pool_name': 'pool',
            'batch_serialized_fn': b'',
            'batch_input_format': {},
            'batch_output_formats': {},
        }
        return task

    @pytest.mark.asyncio
    async def test_batch_resume_from_recovering_succeeds(
            self, _mock_managed_jobs_db_conn, monkeypatch):
        """An emergency marks the batch coordinator RECOVERING. The resume
        runs the coordinator inline and finishes via set_succeeded_async,
        which only transitions from RUNNING/WINDING_DOWN — so a RECOVERING
        row would make a fully successful run end FAILED. The resume must
        restore RUNNING first."""
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, status='RECOVERING')

        jc = self._make_batch_controller()
        task = self._batch_task()

        fake_coordinator = MagicMock()
        fake_coordinator.run = MagicMock(return_value=None)
        monkeypatch.setattr(
            'sky.jobs.controller.batch_coordinator.BatchCoordinator',
            MagicMock(return_value=fake_coordinator))

        callback, _ = _make_callback()
        result = await jc._run_batch_coordinator_task(0,
                                                      task,
                                                      callback,
                                                      is_resume=True)

        assert result is True
        assert _get_task_row(engine)['status'] == 'SUCCEEDED'
        fake_coordinator.run.assert_called_once()


class TestPoolEmergencyDuplicateGuard:
    """A forced (emergency / restart) recovery of a pool job must cancel the
    stale pool submission before recover() resubmits, or two copies of the
    user code run concurrently on the shared pool cluster."""

    @pytest.mark.asyncio
    async def test_forced_recovery_cancels_stale_pool_submission(
            self, monkeypatch):
        jc = controller.JobController.__new__(controller.JobController)
        jc._job_id = 1
        jc._pool = 'mypool'
        jc._backend = MagicMock()
        jc._cleanup_cluster = AsyncMock()

        # Cluster is UP (the controller error was not a preemption); the old
        # submission is therefore still running on it.
        handle = MagicMock()
        monkeypatch.setattr(
            'sky.jobs.controller.backend_utils.refresh_cluster_status_handle',
            lambda *a, **k: (controller.status_lib.ClusterStatus.UP, handle))
        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_runtime.is_registered',
            lambda: False)
        # The task is already RECOVERING (marked by the emergency handler),
        # so the forced iteration does not re-announce.
        monkeypatch.setattr(
            'sky.jobs.controller.managed_job_state.'
            'get_job_status_with_task_id_async',
            AsyncMock(return_value=state.ManagedJobStatus.RECOVERING))

        order = []
        cancel_spy = MagicMock(
            side_effect=lambda **k: order.append(('cancel', k.get('job_ids'))))
        monkeypatch.setattr('sky.jobs.controller.core.cancel', cancel_spy)

        class _StopLoop(Exception):
            pass

        async def _recover():
            order.append(('recover', None))
            raise _StopLoop()

        executor = MagicMock()
        executor.recover = _recover

        task = MagicMock()
        task.num_nodes = 1
        callback, _ = _make_callback()

        with pytest.raises(_StopLoop):
            await jc._monitor_one_task(task_id=0,
                                       task=task,
                                       cluster_name='pool-cluster',
                                       executor=executor,
                                       job_id_on_pool_cluster=777,
                                       callback_func=callback,
                                       cleanup_cluster_on_success=True,
                                       force_transit_to_recovering=True)

        # The stale submission (777) was cancelled, and strictly before the
        # relaunch — so the two copies never overlap.
        assert order == [('cancel', [777]), ('recover', None)]
        cancel_spy.assert_called_once()
