import asyncio
from typing import Tuple

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state


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
                                               pool_hash=None)
    state.set_pending(
        job_id=job_id,
        task_id=0,
        task_name='task0',
        resources_str='{}',
        metadata='{}',
    )
    return job_id


@pytest.mark.asyncio
async def test_get_latest_task_id_status_same(_seed_one_job: int):
    job_id = _seed_one_job
    sync_result = state.get_latest_task_id_status(job_id)
    async_result = await state.get_latest_task_id_status_async(job_id)
    assert sync_result == async_result


@pytest.mark.asyncio
async def test_get_status_same(_seed_one_job: int):
    job_id = _seed_one_job
    sync_status = state.get_status(job_id)
    async_status = await state.get_status_async(job_id)
    assert sync_status == async_status


@pytest.mark.xfail(
    reason=
    'get_pool_submit_info_async uses sync Session; needs fix in sky/jobs/state.py'
)
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
        user_hash='user',
        priority=1,
    )

    sync_state = state.get_job_schedule_state(job_id)
    async_state = await state.get_job_schedule_state_async(job_id)
    assert sync_state == async_state
