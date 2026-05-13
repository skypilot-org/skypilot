"""Tests for below_min_since and all_ready_at async helpers in sky.jobs.state."""

import contextlib

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state as managed_job_state
from sky.jobs.state import spot_table


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    """Temporary SQLite DB for jobs.state with patched engines."""
    db_path = tmp_path / 'jobs_state_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(managed_job_state.migration_utils, 'db_lock',
                        _tmp_db_lock)
    monkeypatch.setattr(managed_job_state._db_manager, '_engine', engine)
    monkeypatch.setattr(managed_job_state._db_manager, '_engine_async',
                        async_engine)
    managed_job_state.create_table(engine)
    yield engine


def _insert_spot_row(engine, *, spot_job_id, task_id=0,
                     below_min_since=None, all_ready_at=None):
    with engine.begin() as conn:
        conn.execute(spot_table.insert().values(
            spot_job_id=spot_job_id,
            task_id=task_id,
            job_name=f'job-{spot_job_id}',
            submitted_at=0.0,
            status='RUNNING',
            below_min_since=below_min_since,
            all_ready_at=all_ready_at,
        ))


def _read_below_min(engine, *, spot_job_id, task_id=0):
    with engine.begin() as conn:
        result = conn.execute(
            sqlalchemy.select(spot_table.c.below_min_since)
            .where(spot_table.c.spot_job_id == spot_job_id)
            .where(spot_table.c.task_id == task_id))
        row = result.one_or_none()
        return row.below_min_since if row else None


@pytest.mark.asyncio
async def test_set_below_min_since_sets_when_null(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=1, below_min_since=None)
    await managed_job_state.set_below_min_since_async(1, 0, ts=100.0)
    assert _read_below_min(jobs_db, spot_job_id=1) == 100.0


@pytest.mark.asyncio
async def test_set_below_min_since_preserves_existing(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=2, below_min_since=50.0)
    await managed_job_state.set_below_min_since_async(2, 0, ts=200.0)
    assert _read_below_min(jobs_db, spot_job_id=2) == 50.0


@pytest.mark.asyncio
async def test_clear_below_min_since(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=3, below_min_since=50.0)
    await managed_job_state.clear_below_min_since_async(3, 0)
    assert _read_below_min(jobs_db, spot_job_id=3) is None


@pytest.mark.asyncio
async def test_clear_below_min_since_idempotent_on_null(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=4, below_min_since=None)
    await managed_job_state.clear_below_min_since_async(4, 0)
    assert _read_below_min(jobs_db, spot_job_id=4) is None


def _read_all_ready_at(engine, *, spot_job_id, task_id=0):
    with engine.begin() as conn:
        result = conn.execute(
            sqlalchemy.select(spot_table.c.all_ready_at)
            .where(spot_table.c.spot_job_id == spot_job_id)
            .where(spot_table.c.task_id == task_id))
        row = result.one_or_none()
        return row.all_ready_at if row else None


@pytest.mark.asyncio
async def test_mark_all_ready_once_first_call_returns_true(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=10, all_ready_at=None)
    result = await managed_job_state.mark_all_ready_once_async(10, 0, ts=300.0)
    assert result is True
    assert _read_all_ready_at(jobs_db, spot_job_id=10) == 300.0


@pytest.mark.asyncio
async def test_mark_all_ready_once_second_call_returns_false(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=11, all_ready_at=100.0)
    result = await managed_job_state.mark_all_ready_once_async(11, 0, ts=300.0)
    assert result is False
    # Original preserved
    assert _read_all_ready_at(jobs_db, spot_job_id=11) == 100.0


@pytest.mark.asyncio
async def test_mark_all_ready_once_concurrent_only_one_wins(jobs_db):
    import asyncio
    _insert_spot_row(jobs_db, spot_job_id=12, all_ready_at=None)
    results = await asyncio.gather(
        managed_job_state.mark_all_ready_once_async(12, 0, ts=400.0),
        managed_job_state.mark_all_ready_once_async(12, 0, ts=500.0),
        managed_job_state.mark_all_ready_once_async(12, 0, ts=600.0),
    )
    assert sum(1 for r in results if r) == 1
    final = _read_all_ready_at(jobs_db, spot_job_id=12)
    assert final in (400.0, 500.0, 600.0)


@pytest.mark.asyncio
async def test_get_below_min_and_all_ready_returns_both(jobs_db):
    _insert_spot_row(jobs_db, spot_job_id=20, below_min_since=42.0,
                     all_ready_at=99.0)
    below, ready = await managed_job_state.get_below_min_and_all_ready_async(
        20, 0)
    assert below == 42.0
    assert ready == 99.0


@pytest.mark.asyncio
async def test_get_below_min_and_all_ready_returns_nulls_for_missing(jobs_db):
    below, ready = await managed_job_state.get_below_min_and_all_ready_async(
        9999, 0)
    assert below is None
    assert ready is None
