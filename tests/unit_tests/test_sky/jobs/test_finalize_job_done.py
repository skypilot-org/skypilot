"""Tests for atomic job finalization.

Covers state.finalize_job_done_async (the single-transaction replacement for
the {set_cancelled -> status check -> set_failed -> job_done} sequence at the
end of the controller's job loop) and the reworked state.set_pending_cancelled
(cancel of a never-launched job, atomically reaching a terminal status and a
DONE schedule_state).

The property under test throughout: a job must never be observable with a
terminal task status but a schedule_state the scheduler would still act on.
"""
import contextlib
from typing import List, Optional, Tuple

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs.state import ManagedJobScheduleState
from sky.jobs.state import ManagedJobStatus


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Isolated SQLite DB for sky.jobs.state (sync + async engines)."""
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(
        f'sqlite+aiosqlite:///{db_path}',
        connect_args={'timeout': 30},
    )

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


def _seed_job(statuses: List[Tuple[ManagedJobStatus, Optional[float]]],
              schedule_state: ManagedJobScheduleState) -> int:
    """Create a job with one task per (status, end_at) and a schedule state."""
    job_id = state.set_job_info_without_job_id(name='job',
                                               workspace='ws',
                                               entrypoint='ep',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user')
    engine = state._db_manager.get_engine()
    with orm.Session(engine) as session:
        for task_id, (status, end_at) in enumerate(statuses):
            session.execute(
                sqlalchemy.insert(state.spot_table).values(
                    spot_job_id=job_id,
                    task_id=task_id,
                    task_name=f'task-{task_id}',
                    status=status.value,
                    end_at=end_at,
                ))
        session.execute(
            sqlalchemy.update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    state.job_info_table.c.schedule_state: schedule_state.value
                }))
        session.commit()
    return job_id


def _task_rows(job_id: int) -> List[Tuple[ManagedJobStatus, Optional[float]]]:
    engine = state._db_manager.get_engine()
    with orm.Session(engine) as session:
        rows = session.execute(
            sqlalchemy.select(
                state.spot_table.c.status, state.spot_table.c.end_at).where(
                    state.spot_table.c.spot_job_id == job_id).order_by(
                        state.spot_table.c.task_id.asc()))
        return [(ManagedJobStatus(row[0]), row[1]) for row in rows.fetchall()]


def _schedule_state(job_id: int) -> ManagedJobScheduleState:
    return state.get_job_schedule_state(job_id)


def _event_statuses(job_id: int) -> List[ManagedJobStatus]:
    return [event['new_status'] for event in state.get_job_events(job_id)]


class TestFinalizeJobDone:
    """finalize_job_done_async: last status write + DONE in one transaction."""

    @pytest.mark.asyncio
    async def test_cancelling_job_finalized(self, _mock_managed_jobs_db_conn):
        job_id = _seed_job([(ManagedJobStatus.CANCELLING, None)],
                           ManagedJobScheduleState.LAUNCHING)

        callback_observed = []

        async def callback(status: str):
            # The callback runs a user-supplied command; by the time it
            # fires, the whole transition (including DONE) must already be
            # committed.
            callback_observed.append((status, _schedule_state(job_id)))

        await state.finalize_job_done_async(job_id,
                                            cancelling=True,
                                            callback_func=callback)

        (status, end_at), = _task_rows(job_id)
        assert status == ManagedJobStatus.CANCELLED
        assert end_at is not None
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        assert _event_statuses(job_id) == [ManagedJobStatus.CANCELLED]
        assert callback_observed == [('CANCELLED', ManagedJobScheduleState.DONE)
                                    ]

    @pytest.mark.asyncio
    async def test_abnormal_exit_failed_controller(self,
                                                   _mock_managed_jobs_db_conn):
        # The controller exited with the job still non-terminal (e.g. launch
        # failed after MAX_RETRY).
        job_id = _seed_job([(ManagedJobStatus.RUNNING, None)],
                           ManagedJobScheduleState.ALIVE)

        await state.finalize_job_done_async(job_id, cancelling=False)

        (status, end_at), = _task_rows(job_id)
        assert status == ManagedJobStatus.FAILED_CONTROLLER
        assert end_at is not None
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        assert _event_statuses(job_id) == [ManagedJobStatus.FAILED_CONTROLLER]
        events = state.get_job_events(job_id)
        assert events[0]['reason'].startswith('Job failed: ')

    @pytest.mark.asyncio
    async def test_recovering_job_stamps_last_recovered_at(
            self, _mock_managed_jobs_db_conn):
        job_id = _seed_job([(ManagedJobStatus.RECOVERING, None)],
                           ManagedJobScheduleState.ALIVE)

        await state.finalize_job_done_async(job_id, cancelling=False)

        (status, end_at), = _task_rows(job_id)
        assert status == ManagedJobStatus.FAILED_CONTROLLER
        engine = state._db_manager.get_engine()
        with orm.Session(engine) as session:
            last_recovered_at = session.execute(
                sqlalchemy.select(state.spot_table.c.last_recovered_at).where(
                    state.spot_table.c.spot_job_id == job_id)).fetchone()[0]
        assert last_recovered_at == end_at

    @pytest.mark.asyncio
    async def test_already_terminal_only_marks_done(self,
                                                    _mock_managed_jobs_db_conn):
        end_at = 1234.5
        job_id = _seed_job([(ManagedJobStatus.SUCCEEDED, end_at)],
                           ManagedJobScheduleState.ALIVE)

        await state.finalize_job_done_async(job_id, cancelling=False)

        assert _task_rows(job_id) == [(ManagedJobStatus.SUCCEEDED, end_at)]
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        # No transition happened, so no event either.
        assert _event_statuses(job_id) == []

    @pytest.mark.asyncio
    async def test_cancelling_flag_with_terminal_job_is_noop(
            self, _mock_managed_jobs_db_conn):
        # cancelling=True but the job already reached CANCELLED (e.g. an
        # earlier finalize was interrupted after committing): no double
        # event, no callback, DONE still converges.
        end_at = 1234.5
        job_id = _seed_job([(ManagedJobStatus.CANCELLED, end_at)],
                           ManagedJobScheduleState.LAUNCHING)

        callback_calls = []

        async def callback(status: str):
            callback_calls.append(status)

        await state.finalize_job_done_async(job_id,
                                            cancelling=True,
                                            callback_func=callback)

        assert _task_rows(job_id) == [(ManagedJobStatus.CANCELLED, end_at)]
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        assert _event_statuses(job_id) == []
        assert callback_calls == []

    @pytest.mark.asyncio
    async def test_idempotent_second_call(self, _mock_managed_jobs_db_conn):
        job_id = _seed_job([(ManagedJobStatus.CANCELLING, None)],
                           ManagedJobScheduleState.LAUNCHING)

        await state.finalize_job_done_async(job_id, cancelling=True)
        rows_after_first = _task_rows(job_id)
        await state.finalize_job_done_async(job_id, cancelling=True)

        assert _task_rows(job_id) == rows_after_first
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        assert _event_statuses(job_id) == [ManagedJobStatus.CANCELLED]

    @pytest.mark.asyncio
    async def test_pipeline_earlier_tasks_untouched(self,
                                                    _mock_managed_jobs_db_conn):
        earlier_end_at = 1000.0
        job_id = _seed_job([(ManagedJobStatus.SUCCEEDED, earlier_end_at),
                            (ManagedJobStatus.CANCELLING, None)],
                           ManagedJobScheduleState.LAUNCHING)

        await state.finalize_job_done_async(job_id, cancelling=True)

        rows = _task_rows(job_id)
        assert rows[0] == (ManagedJobStatus.SUCCEEDED, earlier_end_at)
        assert rows[1][0] == ManagedJobStatus.CANCELLED
        assert rows[1][1] is not None
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE


class TestSetPendingCancelled:
    """set_pending_cancelled: PENDING cancel reaches CANCELLED+DONE atomically.
    """

    def _seed(self, schedule_state: ManagedJobScheduleState,
              statuses: List[ManagedJobStatus]) -> int:
        return _seed_job([(status, None) for status in statuses],
                         schedule_state)

    def test_waiting_job_cancelled_and_done(self, _mock_managed_jobs_db_conn):
        job_id = self._seed(ManagedJobScheduleState.WAITING,
                            [ManagedJobStatus.PENDING])

        assert state.set_pending_cancelled(job_id)

        (status, end_at), = _task_rows(job_id)
        assert status == ManagedJobStatus.CANCELLED
        assert end_at is not None
        # The job is DONE without ever launching a controller.
        assert _schedule_state(job_id) == ManagedJobScheduleState.DONE
        assert _event_statuses(job_id) == [ManagedJobStatus.CANCELLED]

    def test_inactive_job_cancelled_keeps_schedule_state(
            self, _mock_managed_jobs_db_conn):
        # INACTIVE = mid-submission. The in-flight submission overwrites the
        # schedule_state to WAITING unconditionally (scheduler_set_waiting has
        # no state guard), so writing DONE here would be undone; the
        # schedule_state must stay INACTIVE and converge through the normal
        # path instead.
        job_id = self._seed(ManagedJobScheduleState.INACTIVE,
                            [ManagedJobStatus.PENDING])

        assert state.set_pending_cancelled(job_id)

        (status, end_at), = _task_rows(job_id)
        assert status == ManagedJobStatus.CANCELLED
        assert end_at is not None
        assert _schedule_state(job_id) == ManagedJobScheduleState.INACTIVE
        assert _event_statuses(job_id) == [ManagedJobStatus.CANCELLED]

    def test_claimed_job_untouched(self, _mock_managed_jobs_db_conn):
        # A controller claimed the job (LAUNCHING) between the caller's
        # status check and this call: the short-circuit must lose cleanly.
        job_id = self._seed(ManagedJobScheduleState.LAUNCHING,
                            [ManagedJobStatus.PENDING])

        assert not state.set_pending_cancelled(job_id)

        assert _task_rows(job_id) == [(ManagedJobStatus.PENDING, None)]
        assert _schedule_state(job_id) == ManagedJobScheduleState.LAUNCHING
        assert _event_statuses(job_id) == []

    def test_waiting_job_with_non_pending_task_rolled_back(
            self, _mock_managed_jobs_db_conn):
        # A WAITING job reset for recovery mid-run has non-PENDING tasks that
        # still need cleanup; the whole transaction (including the DONE claim
        # on job_info) must roll back so a controller can reclaim the job.
        job_id = self._seed(ManagedJobScheduleState.WAITING,
                            [ManagedJobStatus.RUNNING])

        assert not state.set_pending_cancelled(job_id)

        assert _task_rows(job_id) == [(ManagedJobStatus.RUNNING, None)]
        assert _schedule_state(job_id) == ManagedJobScheduleState.WAITING
        assert _event_statuses(job_id) == []

    def test_partially_pending_pipeline_rolled_back(self,
                                                    _mock_managed_jobs_db_conn):
        job_id = self._seed(
            ManagedJobScheduleState.WAITING,
            [ManagedJobStatus.SUCCEEDED, ManagedJobStatus.PENDING])

        assert not state.set_pending_cancelled(job_id)

        rows = _task_rows(job_id)
        assert rows[0][0] == ManagedJobStatus.SUCCEEDED
        assert rows[1][0] == ManagedJobStatus.PENDING
        assert _schedule_state(job_id) == ManagedJobScheduleState.WAITING
        assert _event_statuses(job_id) == []
