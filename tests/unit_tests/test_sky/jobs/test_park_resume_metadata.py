"""A controller restart during a parked launch must not rewrite the task's
submission and start times, nor drop an open recovery episode.

A parked launch sets an already-started task back to PENDING while it waits
to resume (``set_backoff_pending_async``). A controller restart in that
window reads the status as PENDING, classifies the task as a fresh start,
and drives it back to RUNNING through ``set_starting_async`` /
``set_started_async`` instead of the recovery path. Those two transitions
must therefore be idempotent for the facts a restart cannot re-establish:
``submitted_at``, ``start_at``, and the recovery count.

Runs against a real temporary SQLite database (fixture pattern from
test_recovery_metrics_state.py).
"""
import contextlib

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


async def _noop(_):
    pass


def _seed(engine) -> int:
    job_id = state.set_job_info_without_job_id(name='test_job',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    state.set_pending(job_id=job_id,
                      task_id=0,
                      task_name='task',
                      resources_str='1x[CPU:1]',
                      metadata='{}')
    return job_id


def _row(engine, job_id: int):
    t = state.spot_table
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.select(
                t.c.status, t.c.submitted_at, t.c.run_timestamp, t.c.start_at,
                t.c.last_recovered_at, t.c.recovery_count,
                t.c.recovering_from_failure).where(
                    sqlalchemy.and_(t.c.spot_job_id == job_id,
                                    t.c.task_id == 0))).one()._mapping


async def _start(job_id: int, run_timestamp: str, submit_time: float):
    await state.set_starting_async(job_id,
                                   0,
                                   run_timestamp,
                                   submit_time,
                                   resources_str='1x[CPU:1]',
                                   specs={},
                                   callback_func=_noop)


@pytest.mark.asyncio
async def test_fresh_start_records_submission_and_start(_db):
    job_id = _seed(_db)

    await _start(job_id, 'run-1', 100.0)
    assert _row(_db, job_id)['submitted_at'] == 100.0

    await state.set_started_async(job_id, 0, 110.0, _noop)
    row = _row(_db, job_id)
    assert row['status'] == 'RUNNING'
    assert row['start_at'] == 110.0
    assert row['last_recovered_at'] == 110.0
    # A fresh start has no episode open, so nothing is counted.
    assert row['recovery_count'] == 0
    assert row['recovering_from_failure'] is None


@pytest.mark.asyncio
async def test_restart_while_parked_from_recovery_keeps_times_and_counts(_db):
    """Preemption -> recovery -> park -> controller restart."""
    job_id = _seed(_db)
    await _start(job_id, 'run-1', 100.0)
    await state.set_started_async(job_id, 0, 110.0, _noop)

    await state.set_recovering_async(
        job_id,
        0,
        force_transit_to_recovering=False,
        callback_func=_noop,
        recovery_source=state.RecoverySource.FAILURE)
    assert _row(_db, job_id)['recovering_from_failure'] is True

    # The relaunch parks while it waits to resume.
    await state.set_backoff_pending_async(job_id, 0, reason='waiting to resume')
    assert _row(_db, job_id)['status'] == 'PENDING'

    # The controller restarts: it reads PENDING, treats the task as a fresh
    # start, and replays both transitions with the new run's timestamps.
    await _start(job_id, 'run-2', 900.0)
    row = _row(_db, job_id)
    assert row['submitted_at'] == 100.0, 'submission time must survive'
    assert row['run_timestamp'] == 'run-2', 'logs live under the new run'

    await state.set_started_async(job_id, 0, 910.0, _noop)
    row = _row(_db, job_id)
    assert row['status'] == 'RUNNING'
    assert row['submitted_at'] == 100.0
    assert row['start_at'] == 110.0, 'first start must survive'
    assert row['last_recovered_at'] == 910.0, 'running-since does move'
    assert row['recovery_count'] == 1, 'the preemption recovery is counted'
    assert row['recovering_from_failure'] is None, 'the episode is closed'


@pytest.mark.asyncio
async def test_restart_while_parked_from_system_recovery_counts_nothing(_db):
    """A system-driven episode stays uncounted across the same restart."""
    job_id = _seed(_db)
    await _start(job_id, 'run-1', 100.0)
    await state.set_started_async(job_id, 0, 110.0, _noop)

    await state.set_recovering_async(
        job_id,
        0,
        force_transit_to_recovering=False,
        callback_func=_noop,
        recovery_source=state.RecoverySource.RESTART)
    assert _row(_db, job_id)['recovering_from_failure'] is False

    await state.set_backoff_pending_async(job_id, 0)
    await _start(job_id, 'run-2', 900.0)
    await state.set_started_async(job_id, 0, 910.0, _noop)

    row = _row(_db, job_id)
    assert row['submitted_at'] == 100.0
    assert row['start_at'] == 110.0
    assert row['recovery_count'] == 0


@pytest.mark.asyncio
async def test_restart_while_parked_from_launch_backoff(_db):
    """Park during the initial launch: no episode, and no start to preserve."""
    job_id = _seed(_db)
    await _start(job_id, 'run-1', 100.0)

    await state.set_backoff_pending_async(job_id, 0)
    assert _row(_db, job_id)['status'] == 'PENDING'

    await _start(job_id, 'run-2', 900.0)
    await state.set_started_async(job_id, 0, 910.0, _noop)

    row = _row(_db, job_id)
    assert row['submitted_at'] == 100.0
    assert row['start_at'] == 910.0, 'the task had never run before'
    assert row['recovery_count'] == 0
