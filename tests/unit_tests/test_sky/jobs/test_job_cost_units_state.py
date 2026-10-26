"""job_info.cost_units: recorded by the controller, returned by the claim."""
import asyncio
import contextlib

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs.state import ManagedJobScheduleState


@pytest.fixture
def _db(tmp_path, monkeypatch):
    """Isolated SQLite DB for sky.jobs.state (sync + async engines)."""
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        with filelock.FileLock(str(tmp_path / f'.{_section}.lock'), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _add_waiting_job(engine, job_id: int) -> None:
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.job_info_table).values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                schedule_state=ManagedJobScheduleState.WAITING.value,
                priority=500))
        session.commit()


def _claim():
    return asyncio.run(state.get_waiting_job_async(pid=1, pid_started_at=1.0))


def test_a_job_never_costed_is_claimed_without_a_cost(_db):
    _add_waiting_job(_db, 1)
    assert _claim() == {'job_id': 1, 'pool': None, 'cost_units': None}


def test_a_recorded_cost_comes_back_with_the_claim(_db):
    _add_waiting_job(_db, 1)
    asyncio.run(state.set_job_cost_units_async(1, 1))
    assert _claim()['cost_units'] == 1


def test_recording_a_cost_changes_only_that_job(_db):
    _add_waiting_job(_db, 1)
    _add_waiting_job(_db, 2)
    asyncio.run(state.set_job_cost_units_async(2, 3))
    with orm.Session(_db) as session:
        rows = dict(
            session.execute(
                state.sqlalchemy.select(
                    state.job_info_table.c.spot_job_id,
                    state.job_info_table.c.cost_units)).fetchall())
    assert rows == {1: None, 2: 3}
