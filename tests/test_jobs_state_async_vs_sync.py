import asyncio
import time
from typing import Dict, List, Tuple

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.skylet import constants as sky_constants


@pytest.fixture
def _mock_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for jobs.state and monkeypatch engines.

    Mirrors the pattern used in tests/test_jobs_and_serve.py for
    global_user_state but targets sky.jobs.state.
    """
    db_path = tmp_path / 'jobs_state_testing.db'

    # Create sync and async engines pointing to the same DB file
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    # Monkeypatch Alembic DB lock to a workspace path to avoid writing to ~/.sky
    import contextlib

    import filelock

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)

    # Monkeypatch module-level engines used by state
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE', engine)
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE_ASYNC', async_engine)

    # Create schema
    state.create_table(engine)

    yield


@pytest.fixture
def _seed_one_job(_mock_jobs_db_conn) -> int:
    # Create a job_info row and a single pending task (task_id=0)
    job_id = state.set_job_info_without_job_id(name='test_job',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    state.set_pending(
        job_id=job_id,
        task_id=0,
        task_name='task0',
        resources_str='{}',
        metadata='{}',
    )
    return job_id


@pytest.fixture
def _seed_complex_job(_mock_jobs_db_conn) -> int:
    # Create a job with multiple tasks inserted in PENDING status
    job_id = state.set_job_info_without_job_id(name='complex_job',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    # Create gaps in task ids to test ordering
    state.set_pending(job_id=job_id,
                      task_id=0,
                      task_name='t0',
                      resources_str='{}',
                      metadata='{}')
    state.set_pending(job_id=job_id,
                      task_id=2,
                      task_name='t2',
                      resources_str='{}',
                      metadata='{}')
    state.set_pending(job_id=job_id,
                      task_id=5,
                      task_name='t5',
                      resources_str='{}',
                      metadata='{}')
    return job_id


def _set_statuses(job_id: int, updates: Dict[int, state.ManagedJobStatus]):
    """Helper to set statuses for specific task_ids for the mocked DB."""
    engine = state._SQLALCHEMY_ENGINE
    assert engine is not None
    from sqlalchemy import and_
    from sqlalchemy import orm as sa_orm
    from sqlalchemy import update
    with sa_orm.Session(engine) as session:
        for task_id, status in updates.items():
            session.execute(
                update(state.spot_table).where(
                    and_(state.spot_table.c.spot_job_id == job_id,
                         state.spot_table.c.task_id == task_id)).values(
                             {state.spot_table.c.status: status.value}))
        session.commit()


@pytest.mark.asyncio
async def test_get_latest_task_id_status_same(_seed_one_job: int):
    job_id = _seed_one_job
    sync_result = state.get_latest_task_id_status(job_id)
    async_result = await state.get_latest_task_id_status_async(job_id)
    assert sync_result == async_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status_updates,expected',
    [
        # First non-terminal should be returned
        ({
            0: state.ManagedJobStatus.SUCCEEDED,
            2: state.ManagedJobStatus.RUNNING
        }, (2, state.ManagedJobStatus.RUNNING)),
        # All terminal: return the last task (by ascending id order)
        ({
            0: state.ManagedJobStatus.SUCCEEDED,
            2: state.ManagedJobStatus.FAILED,
            5: state.ManagedJobStatus.SUCCEEDED
        }, (5, state.ManagedJobStatus.SUCCEEDED)),
        # All pending: the first task id
        ({
            0: state.ManagedJobStatus.PENDING,
            2: state.ManagedJobStatus.PENDING,
            5: state.ManagedJobStatus.PENDING
        }, (0, state.ManagedJobStatus.PENDING)),
        # Gap in ids: ensure ordering by task_id (make 2 terminal so 5 is next non-terminal)
        ({
            0: state.ManagedJobStatus.SUCCEEDED,
            2: state.ManagedJobStatus.SUCCEEDED,
            5: state.ManagedJobStatus.PENDING
        }, (5, state.ManagedJobStatus.PENDING)),
    ])
async def test_latest_task_id_status_edge_cases(
        _seed_complex_job: int, status_updates: Dict[int,
                                                     state.ManagedJobStatus],
        expected: Tuple[int, state.ManagedJobStatus]):
    job_id = _seed_complex_job
    _set_statuses(job_id, status_updates)
    sync_result = state.get_latest_task_id_status(job_id)
    async_result = await state.get_latest_task_id_status_async(job_id)
    assert sync_result == async_result == expected


@pytest.mark.asyncio
async def test_get_status_same(_seed_one_job: int):
    job_id = _seed_one_job
    sync_status = state.get_status(job_id)
    async_status = await state.get_status_async(job_id)
    assert sync_status == async_status


@pytest.mark.asyncio
async def test_get_pool_submit_info_same(_seed_one_job: int):
    job_id = _seed_one_job
    # Set values using sync setters, then verify both getters read identically
    state.set_current_cluster_name(job_id, 'cluster-A')

    sync_info: Tuple[str, int] = state.get_pool_submit_info(job_id)
    async_info: Tuple[str, int] = await state.get_pool_submit_info_async(job_id)
    assert sync_info == async_info


@pytest.mark.asyncio
async def test_get_job_schedule_state_same(_seed_one_job: int):
    job_id = _seed_one_job
    # Transition to WAITING using the scheduler API
    state.scheduler_set_waiting(
        job_id,
        dag_yaml_path='dummy.yaml',
        original_user_yaml_path='dummy_user.yaml',
        env_file_path='dummy.env',
        priority=1,
    )

    sync_state = state.get_job_schedule_state(job_id)
    async_state = await state.get_job_schedule_state_async(job_id)
    assert sync_state == async_state


@pytest.mark.asyncio
async def test_schedule_state_transitions_same(_mock_jobs_db_conn):
    # Start from INACTIVE after job creation
    job_id = state.set_job_info_without_job_id(name='sched_job',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')

    # INACTIVE
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # WAITING
    state.scheduler_set_waiting(
        job_id,
        dag_yaml_path='d.yaml',
        original_user_yaml_path='u.yaml',
        env_file_path='e.env',
        priority=10,
    )
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # LAUNCHING
    from sqlalchemy import orm as sa_orm
    from sqlalchemy import update as sa_update
    eng = state._SQLALCHEMY_ENGINE
    assert eng is not None
    with sa_orm.Session(eng) as sess:
        sess.execute(
            sa_update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    state.job_info_table.c.schedule_state:
                        state.ManagedJobScheduleState.LAUNCHING.value
                }))
        sess.commit()
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # ALIVE
    with sa_orm.Session(eng) as sess:
        sess.execute(
            sa_update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    state.job_info_table.c.schedule_state:
                        state.ManagedJobScheduleState.ALIVE.value
                }))
        sess.commit()
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # ALIVE_BACKOFF
    with sa_orm.Session(eng) as sess:
        sess.execute(
            sa_update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    state.job_info_table.c.schedule_state:
                        state.ManagedJobScheduleState.ALIVE_BACKOFF.value
                }))
        sess.commit()
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # ALIVE_WAITING
    with sa_orm.Session(eng) as sess:
        sess.execute(
            sa_update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    state.job_info_table.c.schedule_state:
                        state.ManagedJobScheduleState.ALIVE_WAITING.value
                }))
        sess.commit()
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)

    # DONE
    state.scheduler_set_done(job_id, idempotent=True)
    assert state.get_job_schedule_state(
        job_id) == await state.get_job_schedule_state_async(job_id)


@pytest.mark.asyncio
async def test_get_status_no_tasks_returns_none(_mock_jobs_db_conn):
    # Create a job with no tasks; status should be (None, None) -> None
    job_id = state.set_job_info_without_job_id(name='empty_job',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    assert state.get_latest_task_id_status(job_id) == (None, None)
    assert await state.get_latest_task_id_status_async(job_id) == (None, None)
    assert state.get_status(job_id) is None
    assert await state.get_status_async(job_id) is None


@pytest.mark.asyncio
async def test_missing_job_status_and_ids_same(_mock_jobs_db_conn):
    missing_id = 9999
    # latest task id + status
    assert state.get_latest_task_id_status(missing_id) == (None, None)
    assert await state.get_latest_task_id_status_async(missing_id) == (None,
                                                                       None)
    # status
    assert state.get_status(missing_id) is None
    assert await state.get_status_async(missing_id) is None


@pytest.mark.asyncio
async def test_missing_job_pool_submit_info_same(_mock_jobs_db_conn):
    missing_id = 9999
    assert state.get_pool_submit_info(missing_id) == (None, None)
    assert await state.get_pool_submit_info_async(missing_id) == (None, None)


@pytest.mark.asyncio
async def test_override_terminal_failure_reason_prepend(_mock_jobs_db_conn):
    """Test that when override_terminal=True, new failure reasons are prepended to existing ones."""
    # Create two identical jobs - one for sync, one for async testing
    sync_job_id = state.set_job_info_without_job_id(name='sync_job',
                                                    workspace='default',
                                                    entrypoint='echo',
                                                    pool=None,
                                                    pool_hash=None,
                                                    user_hash='abcd1234')
    async_job_id = state.set_job_info_without_job_id(name='async_job',
                                                     workspace='default',
                                                     entrypoint='echo',
                                                     pool=None,
                                                     pool_hash=None,
                                                     user_hash='abcd1234')

    # Set up initial pending task for both jobs
    state.set_pending(sync_job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')
    state.set_pending(async_job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')

    failure_reasons = [
        "Initial failure: out of memory", "Secondary failure: disk full",
        "Final failure: network timeout", "Ultimate failure: system crash"
    ]

    # Apply the same sequence to both jobs: sync to first job, async to second job
    for reason in failure_reasons:
        # All failures use override_terminal=True to test prepending behavior
        state.set_failed(sync_job_id,
                         task_id=0,
                         failure_type=state.ManagedJobStatus.FAILED,
                         failure_reason=reason,
                         override_terminal=True)
        await state.set_failed_async(async_job_id,
                                     task_id=0,
                                     failure_type=state.ManagedJobStatus.FAILED,
                                     failure_reason=reason,
                                     override_terminal=True)

    # Verify both jobs have identical failure reasons
    sync_failure_reason = state.get_failure_reason(sync_job_id)
    async_failure_reason = state.get_failure_reason(async_job_id)

    assert sync_failure_reason == async_failure_reason
    assert sync_failure_reason is not None

    # Verify the structure contains all failure reasons in the correct order
    assert "Ultimate failure: system crash" in sync_failure_reason
    assert "Final failure: network timeout" in sync_failure_reason
    assert "Secondary failure: disk full" in sync_failure_reason
    assert "Initial failure: out of memory" in sync_failure_reason


def test_missing_job_other_helpers(_mock_jobs_db_conn):
    missing_id = 9999
    # workspace defaults to SKY default for non-existent job
    assert state.get_workspace(
        missing_id) == sky_constants.SKYPILOT_DEFAULT_WORKSPACE
    # failure reason is None
    assert state.get_failure_reason(missing_id) is None
    # pool is None
    assert state.get_pool_from_job_id(missing_id) is None
    # num tasks and ids list
    assert state.get_num_tasks(missing_id) == 0
    assert state.get_all_task_ids_names_statuses_logs(missing_id) == []


@pytest.mark.asyncio
async def test_set_backoff_pending_async_success(_mock_jobs_db_conn):
    """Test set_backoff_pending_async correctly updates status and returns rowcount."""
    # Create a job with a task in STARTING status
    job_id = state.set_job_info_without_job_id(name='backoff_test',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    state.set_pending(
        job_id=job_id,
        task_id=0,
        task_name='task0',
        resources_str='{}',
        metadata='{}',
    )

    # Set the task to STARTING status (simulating launch attempt)
    _set_statuses(job_id, {0: state.ManagedJobStatus.STARTING})

    # Verify the task is in STARTING status
    task_id, status = await state.get_latest_task_id_status_async(job_id)
    assert task_id == 0
    assert status == state.ManagedJobStatus.STARTING

    # Call set_backoff_pending_async - should succeed and update status
    await state.set_backoff_pending_async(job_id, 0)

    # Verify the task is now in PENDING status
    task_id, status = await state.get_latest_task_id_status_async(job_id)
    assert task_id == 0
    assert status == state.ManagedJobStatus.PENDING


@pytest.mark.asyncio
async def test_set_backoff_pending_async_from_recovering(_mock_jobs_db_conn):
    """Test set_backoff_pending_async works when task is in RECOVERING status."""
    # Create a job with a task in RECOVERING status
    job_id = state.set_job_info_without_job_id(name='backoff_recovering_test',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    state.set_pending(
        job_id=job_id,
        task_id=0,
        task_name='task0',
        resources_str='{}',
        metadata='{}',
    )

    # Set the task to RECOVERING status
    _set_statuses(job_id, {0: state.ManagedJobStatus.RECOVERING})

    # Verify the task is in RECOVERING status
    task_id, status = await state.get_latest_task_id_status_async(job_id)
    assert task_id == 0
    assert status == state.ManagedJobStatus.RECOVERING

    # Call set_backoff_pending_async - should succeed and update status
    await state.set_backoff_pending_async(job_id, 0)

    # Verify the task is now in PENDING status
    task_id, status = await state.get_latest_task_id_status_async(job_id)
    assert task_id == 0
    assert status == state.ManagedJobStatus.PENDING


@pytest.mark.asyncio
async def test_set_backoff_pending_async_no_matching_rows(_mock_jobs_db_conn):
    """Test set_backoff_pending_async raises error when no rows match criteria."""
    # Create a job with a task in RUNNING status (not STARTING/RECOVERING)
    job_id = state.set_job_info_without_job_id(name='backoff_no_match_test',
                                               workspace='default',
                                               entrypoint='echo',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='abcd1234')
    state.set_pending(
        job_id=job_id,
        task_id=0,
        task_name='task0',
        resources_str='{}',
        metadata='{}',
    )

    # Set the task to RUNNING status (not eligible for backoff)
    _set_statuses(job_id, {0: state.ManagedJobStatus.RUNNING})

    # Call set_backoff_pending_async - should raise ManagedJobStatusError
    with pytest.raises(state.exceptions.ManagedJobStatusError,
                       match='Failed to set the task back to pending') as exc:
        await state.set_backoff_pending_async(job_id, 0)

    message = str(exc.value)
    assert 'rows matched job' in message
    assert 'Status: RUNNING' in message


@pytest.mark.asyncio
async def test_set_recovered_async_error_details(_seed_one_job: int):
    """Transition failure surfaces detailed status information."""
    job_id = _seed_one_job

    async def noop_callback(_):
        return None

    with pytest.raises(state.exceptions.ManagedJobStatusError) as exc_info:
        await state.set_recovered_async(job_id, 0, time.time(), noop_callback)

    message = str(exc_info.value)
    assert 'rows matched job' in message
    assert 'Status: PENDING' in message


@pytest.mark.asyncio
async def test_transition_failures_surface_details_for_all_functions(
        _seed_one_job: int):
    """Every transition failure includes contextual task details."""
    job_id = _seed_one_job
    missing_task_id = 1

    async def noop_callback(_):
        return None

    async def expect_failure(coro_factory):
        with pytest.raises(state.exceptions.ManagedJobStatusError) as exc:
            await coro_factory()
        message = str(exc.value)
        assert (f'0 rows matched job {job_id} and task {missing_task_id}'
                in message)
        return message

    await expect_failure(
        lambda: state.set_backoff_pending_async(job_id, missing_task_id))
    await expect_failure(
        lambda: state.set_restarting_async(job_id, missing_task_id, False))
    await expect_failure(lambda: state.set_starting_async(
        job_id, missing_task_id, 'run-id', time.time(), '{}', {}, noop_callback)
                        )
    await expect_failure(lambda: state.set_started_async(
        job_id, missing_task_id, time.time(), noop_callback))
    await expect_failure(lambda: state.set_recovering_async(
        job_id, missing_task_id, False, noop_callback))
    await expect_failure(lambda: state.set_recovered_async(
        job_id, missing_task_id, time.time(), noop_callback))
    await expect_failure(lambda: state.set_succeeded_async(
        job_id, missing_task_id, time.time(), noop_callback))
