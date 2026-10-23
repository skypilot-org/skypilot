"""Tests for the per-user launch limit (``jobs.max_launching_jobs_per_user``).

A claimed job that cannot run yet keeps its controller slot. Without the
limit, one user with many such jobs fills every slot and everyone else's jobs
wait. With it, the scheduler skips a user's waiting jobs while that user has
the limit's worth of claimed jobs that are not running.
"""
import asyncio
import contextlib
from typing import Optional

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs import utils as managed_job_utils
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
             user_hash: Optional[str],
             schedule_state: ManagedJobScheduleState,
             *task_statuses: ManagedJobStatus,
             priority: int = 500,
             pool: Optional[str] = None) -> None:
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.job_info_table).values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                schedule_state=schedule_state.value,
                priority=priority,
                user_hash=user_hash,
                pool=pool))
        for task_id, status in enumerate(task_statuses):
            session.execute(
                state.sqlalchemy.insert(state.spot_table).values(
                    spot_job_id=job_id,
                    task_id=task_id,
                    task_name=f'task-{task_id}',
                    status=status.value))
        session.commit()


def _waiting(engine, job_id: int, user_hash: Optional[str], **kwargs) -> None:
    _add_job(engine, job_id, user_hash, ManagedJobScheduleState.WAITING,
             ManagedJobStatus.PENDING, **kwargs)


def _launching(engine,
               job_id: int,
               user_hash: Optional[str],
               status: ManagedJobStatus = ManagedJobStatus.STARTING,
               **kwargs) -> None:
    """A claimed job that is not running: launching, parked or recovering."""
    _add_job(engine, job_id, user_hash, ManagedJobScheduleState.LAUNCHING,
             status, **kwargs)


def _claim(limit: Optional[int]) -> Optional[int]:
    claimed = asyncio.run(
        state.get_waiting_job_async(pid=1,
                                    pid_started_at=1.0,
                                    max_launching_jobs_per_user=limit))
    return None if claimed is None else claimed['job_id']


class TestClaim:

    def test_skips_a_user_at_the_limit(self, _db):
        # Parked: claimed, launch waiting for resources, task back to PENDING.
        _launching(_db, 1, 'a', ManagedJobStatus.PENDING)
        _launching(_db, 2, 'a', ManagedJobStatus.STARTING)
        _waiting(_db, 3, 'a', priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        # User a is at the limit, so b's lower-priority job goes first...
        assert _claim(limit=2) == 4
        # ...and a's job is not claimed while a stays at the limit.
        assert _claim(limit=2) is None

    def test_user_below_the_limit_keeps_priority_order(self, _db):
        _launching(_db, 1, 'a')
        _waiting(_db, 3, 'a', priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        assert _claim(limit=2) == 3

    def test_no_limit_keeps_priority_order(self, _db):
        _launching(_db, 1, 'a')
        _launching(_db, 2, 'a')
        _waiting(_db, 3, 'a', priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        assert _claim(limit=None) == 3

    def test_a_running_job_does_not_count(self, _db):
        _add_job(_db, 1, 'a', ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.RUNNING)
        # A job group stays LAUNCHING while it runs.
        _add_job(_db, 2, 'a', ManagedJobScheduleState.LAUNCHING,
                 ManagedJobStatus.RUNNING, ManagedJobStatus.RUNNING)
        _waiting(_db, 3, 'a', priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        assert _claim(limit=1) == 3

    def test_a_recovering_job_counts(self, _db):
        _launching(_db, 1, 'a', ManagedJobStatus.RECOVERING)
        _waiting(_db, 3, 'a', priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        assert _claim(limit=1) == 4

    def test_pool_jobs_are_neither_counted_nor_held(self, _db):
        # Pool jobs never launch a cluster: they do not count...
        _launching(_db, 1, 'a', pool='p')
        _waiting(_db, 3, 'a', priority=1000)
        assert _claim(limit=1) == 3

        # ...and a user at the limit still gets their pool jobs claimed.
        _waiting(_db, 5, 'a', priority=1000, pool='p')
        _waiting(_db, 6, 'b', priority=100)
        assert _claim(limit=1) == 5

    def test_jobs_without_a_user_are_not_held(self, _db):
        _launching(_db, 1, None)
        _waiting(_db, 3, None, priority=1000)
        _waiting(_db, 4, 'b', priority=100)

        assert _claim(limit=1) == 3

    def test_finished_jobs_do_not_count(self, _db):
        _add_job(_db, 1, 'a', ManagedJobScheduleState.DONE,
                 ManagedJobStatus.FAILED)
        _waiting(_db, 3, 'a', priority=1000)

        assert _claim(limit=1) == 3


class TestUsersAtLimit:

    def test_users_at_the_limit(self, _db):
        _launching(_db, 1, 'a')
        _launching(_db, 2, 'a')
        _launching(_db, 3, 'b')
        _add_job(_db, 4, 'b', ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.RUNNING)

        assert state.get_users_at_launch_limit(2) == {'a'}
        assert state.get_users_at_launch_limit(1) == {'a', 'b'}


class TestHighestPriority:

    def test_a_held_waiting_job_blocks_nobody(self, _db):
        _launching(_db, 1, 'a', priority=100)
        _waiting(_db, 2, 'a', priority=1000)
        _waiting(_db, 3, 'b', priority=200)

        assert state.get_managed_jobs_highest_priority() == 1000
        assert state.get_managed_jobs_highest_priority(
            max_launching_jobs_per_user=1) == 200

    def test_a_claimed_job_of_a_user_at_the_limit_still_blocks(self, _db):
        # The claimed job holds a slot whatever its user's count.
        _launching(_db, 1, 'a', priority=1000)
        _waiting(_db, 2, 'b', priority=200)

        assert state.get_managed_jobs_highest_priority(
            max_launching_jobs_per_user=1) == 1000


class TestDetails:

    def _details(self, launch_limit: Optional[int], **job) -> str:
        job = {
            'schedule_state': 'WAITING',
            'priority': 500,
            'failure_reason': None,
            'pool': None,
            **job,
        }
        managed_job_utils._format_job_details(job=job,
                                              highest_blocking_priority=500,
                                              launch_limit=launch_limit)
        return job['details']

    def test_a_held_job_says_why(self):
        assert self._details(200) == (
            'Waiting: user already has 200 jobs launching or waiting for '
            'resources')

    def test_a_job_not_held_is_unchanged(self):
        assert self._details(None) == 'Waiting for other jobs to launch'

    def test_a_held_pool_job_is_unchanged(self):
        assert self._details(200,
                             pool='p') == ('Waiting for other jobs to launch')
