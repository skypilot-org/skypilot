"""state.get_launched_task_name_async: which cluster a claimed job may have."""
import asyncio
import contextlib
from typing import List, Optional, Tuple

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs.state import ManagedJobScheduleState
from sky.jobs.state import ManagedJobStatus


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


def _add_job(engine,
             job_id: int,
             tasks: List[Tuple[str, ManagedJobStatus, Optional[float]]],
             execution: str = 'serial') -> None:
    """Adds a job; each task is (name, status, submitted_at)."""
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.job_info_table).values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                schedule_state=ManagedJobScheduleState.LAUNCHING.value,
                execution=execution))
        for task_id, (name, status, submitted_at) in enumerate(tasks):
            session.execute(
                state.sqlalchemy.insert(state.spot_table).values(
                    spot_job_id=job_id,
                    task_id=task_id,
                    task_name=name,
                    status=status.value,
                    submitted_at=submitted_at))
        session.commit()


def _name(job_id: int) -> Optional[str]:
    return asyncio.run(state.get_launched_task_name_async(job_id))


def test_running_task_name(_db):
    _add_job(_db, 1, [('train', ManagedJobStatus.RUNNING, 100.0)])
    assert _name(1) == 'train'


def test_task_never_submitted(_db):
    _add_job(_db, 1, [('train', ManagedJobStatus.PENDING, None)])
    assert _name(1) is None


def test_pipeline_uses_the_current_task(_db):
    _add_job(_db, 1, [('prep', ManagedJobStatus.SUCCEEDED, 100.0),
                      ('train', ManagedJobStatus.RECOVERING, 200.0),
                      ('eval', ManagedJobStatus.PENDING, None)])
    assert _name(1) == 'train'


def test_pipeline_next_task_not_submitted_yet(_db):
    _add_job(_db, 1, [('prep', ManagedJobStatus.SUCCEEDED, 100.0),
                      ('train', ManagedJobStatus.PENDING, None)])
    assert _name(1) is None


def test_job_group_has_no_single_cluster(_db):
    _add_job(_db,
             1, [('trainer', ManagedJobStatus.RUNNING, 100.0),
                 ('evaluator', ManagedJobStatus.RUNNING, 100.0)],
             execution='parallel')
    assert _name(1) is None


def test_unknown_job(_db):
    assert _name(42) is None
