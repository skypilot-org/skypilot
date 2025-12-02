"""Unit tests for sky.jobs.state."""
import contextlib
import time
from typing import Optional

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
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
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE', engine)
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE_ASYNC', async_engine)

    # Create schema via migrations
    state.create_table(engine)
    yield engine


def _insert_task(
    engine,
    job_id: int,
    task_id: int,
    *,
    status: ManagedJobStatus,
    end_at: Optional[float] = None,
    local_log_file: Optional[str] = None,
    logs_cleaned_at: Optional[float] = None,
):
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.spot_table).values(
                spot_job_id=job_id,
                task_id=task_id,
                task_name=f'task-{task_id}',
                status=status.value,
                end_at=end_at,
                local_log_file=local_log_file,
                logs_cleaned_at=logs_cleaned_at,
            ))
        session.commit()


def _insert_job_info(engine,
                     *,
                     controller_logs_cleaned_at: Optional[float] = None):
    with orm.Session(engine) as session:
        # Insert row; let PK autoincrement.
        if (state._SQLALCHEMY_ENGINE.dialect.name ==
                state.db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = state.sqlite.insert
        elif (state._SQLALCHEMY_ENGINE.dialect.name ==
              state.db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = state.postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmt = insert_func(state.job_info_table).values(
            name='job',
            schedule_state=state.ManagedJobScheduleState.INACTIVE.value,
            controller_logs_cleaned_at=controller_logs_cleaned_at,
        )
        result = session.execute(insert_stmt)
        # SQLite: lastrowid holds PK
        job_id = result.lastrowid
        session.commit()
        return job_id


def test_get_task_logs_to_clean_basic(_mock_managed_jobs_db_conn):
    now = time.time()
    retention = 60

    # Prepare one job with multiple tasks
    job_id = state.set_job_info_without_job_id(
        name='job-a',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
    )

    # Qualifies: terminal + old + not cleaned
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 120,
        local_log_file='/tmp/a.log',
        logs_cleaned_at=None,
    )
    # Not old enough
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        1,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 30,
        local_log_file='/tmp/b.log',
        logs_cleaned_at=None,
    )
    # Already cleaned
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        2,
        status=ManagedJobStatus.FAILED,
        end_at=now - 120,
        local_log_file='/tmp/c.log',
        logs_cleaned_at=now - 10,
    )
    # Non-terminal
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        3,
        status=ManagedJobStatus.RUNNING,
        end_at=None,
        local_log_file='/tmp/d.log',
        logs_cleaned_at=None,
    )
    # Terminal and old, but local_log_file is None -> should not qualify
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        6,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 200,
        local_log_file=None,
        logs_cleaned_at=None,
    )

    state.scheduler_set_done(job_id)

    res = state.get_task_logs_to_clean(retention, batch_size=10)
    # Only task 0 should be returned
    assert len(res) == 1
    assert res[0]['job_id'] == job_id
    assert res[0]['task_id'] == 0
    assert res[0]['local_log_file'] == '/tmp/a.log'

    # Batch size respected: add two more qualifying tasks
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        4,
        status=ManagedJobStatus.CANCELLED,
        end_at=now - 200,
        local_log_file='/tmp/e.log',
        logs_cleaned_at=None,
    )
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        5,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 300,
        local_log_file='/tmp/f.log',
        logs_cleaned_at=None,
    )

    res2 = state.get_task_logs_to_clean(retention, batch_size=2)
    assert len(res2) == 2  # limited by batch size


def test_set_task_logs_cleaned(_mock_managed_jobs_db_conn):
    now = time.time()
    retention = 60

    job_id = state.set_job_info_without_job_id(
        name='job-b',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
    )
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_id,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 120,
        local_log_file='/tmp/a.log',
        logs_cleaned_at=None,
    )

    state.scheduler_set_done(job_id)

    res = state.get_task_logs_to_clean(retention, batch_size=10)
    assert len(res) == 1

    ts = now
    state.set_task_logs_cleaned([(job_id, 0)], ts)

    # Verify updated
    with orm.Session(state._SQLALCHEMY_ENGINE) as session:
        row = session.execute(
            state.sqlalchemy.select(state.spot_table.c.logs_cleaned_at).where(
                state.sqlalchemy.and_(
                    state.spot_table.c.spot_job_id == job_id,
                    state.spot_table.c.task_id == 0))).fetchone()
        assert row is not None
        assert row[0] == ts

    # Should no longer be returned
    res2 = state.get_task_logs_to_clean(retention, batch_size=10)
    assert res2 == []


def test_get_controller_logs_to_clean_basic(_mock_managed_jobs_db_conn):
    now = time.time()
    retention = 60

    # Job A: qualifies (max end_at old, controller logs not cleaned)
    job_a = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=None)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_a,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 200,
        local_log_file='/tmp/a0.log',
        logs_cleaned_at=None,
    )
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_a,
        1,
        status=ManagedJobStatus.FAILED,
        end_at=now - 150,
        local_log_file='/tmp/a1.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_a)

    # Job B: not old enough
    job_b = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=None)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_b,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 30,
        local_log_file='/tmp/b0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_b)

    # Job C: already cleaned controller logs
    job_c = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=now - 10)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_c,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 200,
        local_log_file='/tmp/c0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_c)

    # Job D: terminal but end_at is None -> does not qualify
    job_d = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=None)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_d,
        0,
        status=ManagedJobStatus.CANCELLED,
        end_at=None,
        local_log_file='/tmp/d0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_d)

    res = state.get_controller_logs_to_clean(retention, batch_size=10)
    job_ids = {r['job_id'] for r in res}
    assert job_ids == {job_a}

    # Batch size respected: clone more qualifying jobs
    job_e = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=None)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_e,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 400,
        local_log_file='/tmp/e0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_e)
    job_f = _insert_job_info(state._SQLALCHEMY_ENGINE,
                             controller_logs_cleaned_at=None)
    _insert_task(
        state._SQLALCHEMY_ENGINE,
        job_f,
        0,
        status=ManagedJobStatus.FAILED,
        end_at=now - 500,
        local_log_file='/tmp/f0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_f)

    res2 = state.get_controller_logs_to_clean(retention, batch_size=2)
    assert len(res2) == 2


def test_set_controller_logs_cleaned(_mock_managed_jobs_db_conn):
    now = time.time()

    job_id = _insert_job_info(state._SQLALCHEMY_ENGINE,
                              controller_logs_cleaned_at=None)

    state.set_controller_logs_cleaned([job_id], now)

    with orm.Session(state._SQLALCHEMY_ENGINE) as session:
        row = session.execute(
            state.sqlalchemy.select(
                state.job_info_table.c.controller_logs_cleaned_at).where(
                    state.job_info_table.c.spot_job_id == job_id)).fetchone()
        assert row is not None
        assert row[0] == now
