"""Tests for ``jobs.controller.max_concurrent_launches_per_user``."""
# pylint: disable=redefined-outer-name
import asyncio
import contextlib
from typing import Optional

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky import skypilot_config
from sky.jobs import controller as controller_module
from sky.jobs import state
from sky.jobs.state import ManagedJobScheduleState
from sky.utils import config_utils


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for sky.jobs.state (sync + async engines)."""
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(section: str):
        with filelock.FileLock(str(tmp_path / f'.{section}.lock'), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    # pylint: disable=protected-access
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _add_job(engine,
             job_id: int,
             schedule_state: ManagedJobScheduleState,
             user_hash: Optional[str],
             pool: Optional[str] = None,
             priority: int = 500) -> None:
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.job_info_table).values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                schedule_state=schedule_state.value,
                user_hash=user_hash,
                pool=pool,
                priority=priority))
        session.commit()


def _claim(cap: Optional[int]) -> Optional[int]:
    claimed = asyncio.run(
        state.get_waiting_job_async(pid=1,
                                    pid_started_at=1.0,
                                    max_concurrent_launches_per_user=cap))
    return None if claimed is None else claimed['job_id']


def _set_state(engine, job_id: int,
               schedule_state: ManagedJobScheduleState) -> None:
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values(
                    schedule_state=schedule_state.value))
        session.commit()


class TestPerUserLaunchCap:
    """The claim query honours max_concurrent_launches_per_user."""

    def test_no_cap_claims_in_priority_order(self, jobs_db):
        for job_id in range(1, 5):
            _add_job(jobs_db, job_id, ManagedJobScheduleState.LAUNCHING,
                     'alice')
        _add_job(jobs_db, 5, ManagedJobScheduleState.WAITING, 'alice')
        _add_job(jobs_db, 6, ManagedJobScheduleState.WAITING, 'bob')

        assert _claim(None) == 5
        assert _claim(None) == 6

    def test_user_at_cap_yields_to_other_users(self, jobs_db):
        _add_job(jobs_db, 1, ManagedJobScheduleState.LAUNCHING, 'alice')
        _add_job(jobs_db, 2, ManagedJobScheduleState.LAUNCHING, 'alice')
        # alice's next job outranks bob's, but alice is at the cap.
        _add_job(jobs_db,
                 3,
                 ManagedJobScheduleState.WAITING,
                 'alice',
                 priority=1000)
        _add_job(jobs_db, 4, ManagedJobScheduleState.WAITING, 'bob')

        assert _claim(2) == 4
        assert _claim(2) is None

        # Once one of alice's launches completes she is under the cap again.
        _set_state(jobs_db, 1, ManagedJobScheduleState.ALIVE)
        assert _claim(2) == 3
        # ...and now at the cap once more.
        _add_job(jobs_db, 5, ManagedJobScheduleState.WAITING, 'alice')
        assert _claim(2) is None

    def test_cap_counts_launching_only(self, jobs_db):
        # ALIVE, ALIVE_WAITING, ALIVE_BACKOFF and DONE jobs are not launching.
        _add_job(jobs_db, 1, ManagedJobScheduleState.ALIVE, 'alice')
        _add_job(jobs_db, 2, ManagedJobScheduleState.ALIVE_WAITING, 'alice')
        _add_job(jobs_db, 3, ManagedJobScheduleState.ALIVE_BACKOFF, 'alice')
        _add_job(jobs_db, 4, ManagedJobScheduleState.DONE, 'alice')
        _add_job(jobs_db, 5, ManagedJobScheduleState.LAUNCHING, 'alice')
        _add_job(jobs_db, 6, ManagedJobScheduleState.WAITING, 'alice')

        assert _claim(2) == 6

    def test_pool_jobs_neither_count_nor_wait(self, jobs_db):
        # Pool jobs in LAUNCHING do not count towards alice's cap...
        _add_job(jobs_db,
                 1,
                 ManagedJobScheduleState.LAUNCHING,
                 'alice',
                 pool='p')
        _add_job(jobs_db,
                 2,
                 ManagedJobScheduleState.LAUNCHING,
                 'alice',
                 pool='p')
        _add_job(jobs_db, 3, ManagedJobScheduleState.WAITING, 'alice')
        assert _claim(2) == 3

        # ...and a WAITING pool job is claimed even when alice is at the cap.
        _add_job(jobs_db, 4, ManagedJobScheduleState.LAUNCHING, 'alice')
        _add_job(jobs_db,
                 5,
                 ManagedJobScheduleState.WAITING,
                 'alice',
                 priority=1000)
        _add_job(jobs_db, 6, ManagedJobScheduleState.WAITING, 'alice', pool='p')
        assert _claim(2) == 6
        assert _claim(2) is None

    def test_cap_is_per_user(self, jobs_db):
        _add_job(jobs_db, 1, ManagedJobScheduleState.LAUNCHING, 'alice')
        _add_job(jobs_db, 2, ManagedJobScheduleState.LAUNCHING, 'bob')
        _add_job(jobs_db, 3, ManagedJobScheduleState.WAITING, 'alice')
        _add_job(jobs_db, 4, ManagedJobScheduleState.WAITING, 'bob')
        _add_job(jobs_db, 5, ManagedJobScheduleState.WAITING, 'carol')

        # alice and bob each hold one launch: only carol is under a cap of 1.
        assert _claim(1) == 5
        assert _claim(1) is None

    def test_job_without_user_is_never_held_back(self, jobs_db):
        _add_job(jobs_db, 1, ManagedJobScheduleState.LAUNCHING, None)
        _add_job(jobs_db, 2, ManagedJobScheduleState.LAUNCHING, None)
        _add_job(jobs_db, 3, ManagedJobScheduleState.WAITING, None)

        assert _claim(1) == 3


class TestControllerReadsCap:
    """The controller reads the cap from jobs.controller config."""

    def test_unset_means_no_cap(self):
        with skypilot_config.replace_skypilot_config_in_process(
                config_utils.Config()):
            assert controller_module.get_max_concurrent_launches_per_user(
            ) is None

    def test_reads_jobs_controller_config(self):
        config = config_utils.Config()
        config.set_nested(
            ('jobs', 'controller', 'max_concurrent_launches_per_user'), 16)
        with skypilot_config.replace_skypilot_config_in_process(config):
            assert controller_module.get_max_concurrent_launches_per_user(
            ) == 16
