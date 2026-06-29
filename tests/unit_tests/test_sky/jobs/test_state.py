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
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)

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
        engine = state._db_manager.get_engine()
        if (engine.dialect.name == state.db_utils.SQLAlchemyDialect.SQLITE.value
           ):
            insert_func = state.sqlite.insert
        elif (engine.dialect.name ==
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
    engine = state._db_manager.get_engine()
    # Qualifies: terminal + old + not cleaned
    _insert_task(
        engine,
        job_id,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 120,
        local_log_file='/tmp/a.log',
        logs_cleaned_at=None,
    )
    # Not old enough
    _insert_task(
        engine,
        job_id,
        1,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 30,
        local_log_file='/tmp/b.log',
        logs_cleaned_at=None,
    )
    # Already cleaned
    _insert_task(
        engine,
        job_id,
        2,
        status=ManagedJobStatus.FAILED,
        end_at=now - 120,
        local_log_file='/tmp/c.log',
        logs_cleaned_at=now - 10,
    )
    # Non-terminal
    _insert_task(
        engine,
        job_id,
        3,
        status=ManagedJobStatus.RUNNING,
        end_at=None,
        local_log_file='/tmp/d.log',
        logs_cleaned_at=None,
    )
    # Terminal and old, but local_log_file is None -> should not qualify
    _insert_task(
        engine,
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
        engine,
        job_id,
        4,
        status=ManagedJobStatus.CANCELLED,
        end_at=now - 200,
        local_log_file='/tmp/e.log',
        logs_cleaned_at=None,
    )
    _insert_task(
        engine,
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
    engine = state._db_manager.get_engine()
    _insert_task(
        engine,
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
    with orm.Session(engine) as session:
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
    engine = state._db_manager.get_engine()
    job_a = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
        job_a,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 200,
        local_log_file='/tmp/a0.log',
        logs_cleaned_at=None,
    )
    _insert_task(
        engine,
        job_a,
        1,
        status=ManagedJobStatus.FAILED,
        end_at=now - 150,
        local_log_file='/tmp/a1.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_a)

    # Job B: not old enough
    job_b = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
        job_b,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 30,
        local_log_file='/tmp/b0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_b)

    # Job C: already cleaned controller logs
    job_c = _insert_job_info(engine, controller_logs_cleaned_at=now - 10)
    _insert_task(
        engine,
        job_c,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 200,
        local_log_file='/tmp/c0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_c)

    # Job D: terminal with end_at None (e.g. cancelled while PENDING) -> still
    # qualifies, so its controller log row is cleaned rather than re-scanned
    # forever.
    job_d = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
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
    assert job_ids == {job_a, job_d}

    # Batch size respected: clone more qualifying jobs
    job_e = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
        job_e,
        0,
        status=ManagedJobStatus.SUCCEEDED,
        end_at=now - 400,
        local_log_file='/tmp/e0.log',
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_e)
    job_f = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
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


def test_get_controller_logs_to_clean_without_local_log_file(
        _mock_managed_jobs_db_conn):
    """Controller logs are cleaned even when no task log was downloaded.

    Jobs that terminate without a downloaded task log must still be eligible for
    controller-log GC despite local_log_file being NULL:
    - FAILED_CONTROLLER on a controller crash: end_at is set, controller log
      exists on disk.
    - Cancelled while still PENDING (set_pending_cancelled): end_at is never
      set, so max(end_at) is NULL; it must be cleaned immediately rather than
      filtered out and re-scanned forever.
    """
    now = time.time()
    retention = 60
    engine = state._db_manager.get_engine()

    # Job cancelled before the task ran: terminal, end_at never set, no local
    # log (mirrors set_pending_cancelled, which leaves end_at NULL).
    job_cancelled = _insert_job_info(engine, controller_logs_cleaned_at=None)
    _insert_task(
        engine,
        job_cancelled,
        0,
        status=ManagedJobStatus.CANCELLED,
        end_at=None,
        local_log_file=None,
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_cancelled)

    # Job that crashed the controller: terminal, old end_at, no local log.
    job_failed_controller = _insert_job_info(engine,
                                             controller_logs_cleaned_at=None)
    _insert_task(
        engine,
        job_failed_controller,
        0,
        status=ManagedJobStatus.FAILED_CONTROLLER,
        end_at=now - 150,
        local_log_file=None,
        logs_cleaned_at=None,
    )
    state.scheduler_set_done(job_failed_controller)

    res = state.get_controller_logs_to_clean(retention, batch_size=10)
    job_ids = {r['job_id'] for r in res}
    assert job_ids == {job_cancelled, job_failed_controller}


def test_set_controller_logs_cleaned(_mock_managed_jobs_db_conn):
    now = time.time()

    engine = state._db_manager.get_engine()
    job_id = _insert_job_info(engine, controller_logs_cleaned_at=None)

    state.set_controller_logs_cleaned([job_id], now)

    with orm.Session(engine) as session:
        row = session.execute(
            state.sqlalchemy.select(
                state.job_info_table.c.controller_logs_cleaned_at).where(
                    state.job_info_table.c.spot_job_id == job_id)).fetchone()
        assert row is not None
        assert row[0] == now


def test_get_active_file_mounts_blob_ids(_mock_managed_jobs_db_conn):
    engine = _mock_managed_jobs_db_conn

    # Non-terminal job holding a blob -> should be returned.
    active_job = state.set_job_info_without_job_id(
        name='active',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
        file_mounts_blob_id='blob-active',
    )
    _insert_task(engine, active_job, 0, status=ManagedJobStatus.RUNNING)

    # Terminal job -> should NOT be returned even though it has a blob.
    terminal_job = state.set_job_info_without_job_id(
        name='done',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
        file_mounts_blob_id='blob-done',
    )
    _insert_task(engine, terminal_job, 0, status=ManagedJobStatus.SUCCEEDED)

    # Non-terminal job without a blob -> should NOT be returned.
    no_blob_job = state.set_job_info_without_job_id(
        name='no-blob',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
    )
    _insert_task(engine, no_blob_job, 0, status=ManagedJobStatus.PENDING)

    # Queued (non-terminal) job -> should be returned.
    queued_job = state.set_job_info_without_job_id(
        name='queued',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
        file_mounts_blob_id='blob-queued',
    )
    _insert_task(engine, queued_job, 0, status=ManagedJobStatus.PENDING)

    # Recovering job -> should be returned (long-tail case that motivated
    # this ref tracking).
    recovering_job = state.set_job_info_without_job_id(
        name='recovering',
        workspace='ws',
        entrypoint='entry',
        pool=None,
        pool_hash=None,
        user_hash='u',
        file_mounts_blob_id='blob-recovering',
    )
    _insert_task(engine, recovering_job, 0, status=ManagedJobStatus.RECOVERING)

    blob_ids = state.get_active_file_mounts_blob_ids()
    assert blob_ids == {'blob-active', 'blob-queued', 'blob-recovering'}


def _new_pool_job(engine,
                  *,
                  pool: str,
                  status: ManagedJobStatus,
                  cluster_name=None) -> int:
    """Create a managed job in `pool` with optional `current_cluster_name`."""
    job_id = state.set_job_info_without_job_id(
        name=f'job-{pool}',
        workspace='ws',
        entrypoint='entry',
        pool=pool,
        pool_hash=None,
        user_hash='u',
    )
    _insert_task(engine, job_id, 0, status=status)
    if cluster_name is not None:
        state.set_current_cluster_name(job_id, cluster_name)
    return job_id


def test_get_nonterminal_job_ids_by_pool_grouped(_mock_managed_jobs_db_conn):
    """Verify the batched grouped query matches the per-call helper."""
    engine = state._db_manager.get_engine()

    # Pool A: unassigned job, two replicas with one nonterminal job each,
    # one job on a replica that is already SUCCEEDED (should be excluded).
    unassigned_a = _new_pool_job(engine,
                                 pool='pool-a',
                                 status=ManagedJobStatus.PENDING)
    r1_running_a = _new_pool_job(engine,
                                 pool='pool-a',
                                 status=ManagedJobStatus.RUNNING,
                                 cluster_name='replica-1')
    r1_recovering_a = _new_pool_job(engine,
                                    pool='pool-a',
                                    status=ManagedJobStatus.RECOVERING,
                                    cluster_name='replica-1')
    r2_running_a = _new_pool_job(engine,
                                 pool='pool-a',
                                 status=ManagedJobStatus.RUNNING,
                                 cluster_name='replica-2')
    _new_pool_job(engine,
                  pool='pool-a',
                  status=ManagedJobStatus.SUCCEEDED,
                  cluster_name='replica-1')  # terminal -> filtered

    # Pool B: separate pool to ensure the filter is scoped correctly.
    _new_pool_job(engine, pool='pool-b', status=ManagedJobStatus.RUNNING)

    grouped = state.get_nonterminal_job_ids_by_pool_grouped('pool-a')

    assert set(grouped.keys()) == {None, 'replica-1', 'replica-2'}
    assert grouped[None] == [unassigned_a]
    assert grouped['replica-1'] == sorted([r1_running_a, r1_recovering_a])
    assert grouped['replica-2'] == [r2_running_a]

    # Grouped result must agree with the legacy per-call helper.
    assert sorted(grouped['replica-1']) == sorted(
        state.get_nonterminal_job_ids_by_pool('pool-a',
                                              cluster_name='replica-1'))
    assert sorted(grouped['replica-2']) == sorted(
        state.get_nonterminal_job_ids_by_pool('pool-a',
                                              cluster_name='replica-2'))
    all_jobs_a = sorted(state.get_nonterminal_job_ids_by_pool('pool-a'))
    flattened = sorted(j for ids in grouped.values() for j in ids)
    assert flattened == all_jobs_a


def test_get_nonterminal_job_ids_by_pool_grouped_empty(
        _mock_managed_jobs_db_conn):
    """No jobs in pool -> empty dict (not raise)."""
    assert not state.get_nonterminal_job_ids_by_pool_grouped('nope')


def test_get_nonterminal_job_ids_by_pool_grouped_all_terminal(
        _mock_managed_jobs_db_conn):
    """Pool with only finished jobs should also yield an empty grouping."""
    engine = state._db_manager.get_engine()
    _new_pool_job(engine,
                  pool='pool-done',
                  status=ManagedJobStatus.SUCCEEDED,
                  cluster_name='replica-x')
    _new_pool_job(engine, pool='pool-done', status=ManagedJobStatus.FAILED)
    assert not state.get_nonterminal_job_ids_by_pool_grouped('pool-done')
