"""Unit tests for get_pending_stall_candidates (stuck-PENDING detection)."""

import contextlib
import datetime
import time

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state


@pytest.fixture
def _db(tmp_path, monkeypatch):
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


def _seed_job(engine, *, schedule_state, tasks, last_event_age_s):
    """Seed one job. tasks = list of (task_id, status_str)."""
    job_id = state.set_job_info_without_job_id(name='j',
                                               workspace='ws',
                                               entrypoint='e',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='u')
    # Match the codebase convention: job_events.timestamp is written as a naive
    # datetime.datetime.now() (see state.add_job_event).
    event_ts = (datetime.datetime.now() -
                datetime.timedelta(seconds=last_event_age_s))
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values(
                    schedule_state=schedule_state))
        for task_id, status in tasks:
            conn.execute(
                sqlalchemy.insert(state.spot_table).values(
                    spot_job_id=job_id,
                    task_id=task_id,
                    task_name=f't{task_id}',
                    status=status,
                    end_at=None))
        # One event row per job is enough; the query takes MAX(timestamp).
        conn.execute(
            sqlalchemy.insert(state.job_events_table).values(
                spot_job_id=job_id,
                task_id=0,
                new_status=tasks[0][1],
                timestamp=event_ts))
    return job_id


_LAUNCHING = state.ManagedJobScheduleState.LAUNCHING.value
_ALIVE = state.ManagedJobScheduleState.ALIVE.value
_WAITING = state.ManagedJobScheduleState.WAITING.value


def _by_job(rows):
    """rows = [(workspace, user, job_id, last_ts)] -> {job_id: last_ts}."""
    return {job_id: last_ts for _, _, job_id, last_ts in rows}


def test_single_task_stall_is_detected(_db):
    jid = _seed_job(_db,
                    schedule_state=_LAUNCHING,
                    tasks=[(0, 'PENDING')],
                    last_event_age_s=1000)
    rows = state.get_pending_stall_candidates()
    assert len(rows) == 1
    workspace, user, job_id, last_ts = rows[0]
    assert job_id == jid
    assert workspace == 'ws' and user == 'u'
    # Caller derives seconds = now - last_ts; ~1000s here.
    assert time.time() - last_ts > 900


def test_revert_check_states_and_progress_guards(_db):
    # (1) stalled single-task LAUNCHING -> candidate
    j1 = _seed_job(_db,
                   schedule_state=_LAUNCHING,
                   tasks=[(0, 'PENDING')],
                   last_event_age_s=1000)
    # (2) healthy pipeline: task0 RUNNING -> NOT candidate (active task)
    _seed_job(_db,
              schedule_state=_ALIVE,
              tasks=[(0, 'RUNNING'), (1, 'PENDING')],
              last_event_age_s=1000)
    # (3) legitimately queued WAITING -> NOT candidate (not launching)
    _seed_job(_db,
              schedule_state=_WAITING,
              tasks=[(0, 'PENDING')],
              last_event_age_s=1000)
    # (4) bare ALIVE between tasks (multi-task slot-wait) -> NOT candidate.
    # This is the false-positive guard: a job waiting for a launch slot for its
    # next task sits in bare ALIVE; we only flag LAUNCHING. If `driving`
    # regressed to include ALIVE, this would wrongly appear.
    _seed_job(_db,
              schedule_state=_ALIVE,
              tasks=[(0, 'SUCCEEDED'), (1, 'PENDING')],
              last_event_age_s=1000)
    # (5) multi-task job that died launching its next task (LAUNCHING) ->
    # candidate.
    j5 = _seed_job(_db,
                   schedule_state=_LAUNCHING,
                   tasks=[(0, 'SUCCEEDED'), (1, 'PENDING')],
                   last_event_age_s=500)
    # (6) fresh LAUNCHING -> still a candidate (the function applies no time
    # filter; the alert threshold does), with a small age.
    j6 = _seed_job(_db,
                   schedule_state=_LAUNCHING,
                   tasks=[(0, 'PENDING')],
                   last_event_age_s=5)

    by_job = _by_job(state.get_pending_stall_candidates())
    # Only 1, 5, 6 qualify. If `driving` regressed to include ALIVE, (4) would
    # wrongly appear. If the progressing guard regressed, (2) would too.
    assert set(by_job) == {j1, j5, j6}
    assert time.time() - by_job[j1] > 900  # stalled
    assert time.time() - by_job[j6] < 60  # fresh; rule won't fire on it


def test_no_candidates_returns_empty(_db):
    _seed_job(_db,
              schedule_state=_ALIVE,
              tasks=[(0, 'RUNNING'), (1, 'PENDING')],
              last_event_age_s=1000)
    _seed_job(_db,
              schedule_state=_WAITING,
              tasks=[(0, 'PENDING')],
              last_event_age_s=1000)
    assert state.get_pending_stall_candidates() == []
