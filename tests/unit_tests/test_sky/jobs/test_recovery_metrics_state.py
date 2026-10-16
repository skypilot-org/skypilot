"""Unit tests for the recovery-metric query helpers in sky/jobs/state.py.

Runs against a real temporary SQLite database (fixture pattern from
test_emergency_recovery.py). Covers:

- get_recovery_event_counts_by_source_workspace: grouping, the
  NULL-recovery_source exclusion, non-RECOVERING exclusion, and the
  NULL-workspace passthrough.
- get_active_emergency_recovery_episodes: the activity window, the
  non-terminal-task requirement, the deterministic ordering, and the
  limit.
"""
import contextlib
import time

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state."""
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


def _seed_job(engine,
              job_id: int,
              status: str = 'RUNNING',
              workspace: str = 'default',
              emergency_count=None,
              last_emergency_at=None):
    with engine.connect() as conn:
        conn.execute(state.job_info_table.insert().values(
            spot_job_id=job_id,
            name=f'job-{job_id}',
            workspace=workspace,
            emergency_recovery_count=emergency_count,
            last_emergency_recovery_at=last_emergency_at,
        ))
        conn.execute(state.spot_table.insert().values(
            job_name=f'job-{job_id}',
            status=status,
            spot_job_id=job_id,
            task_id=0,
        ))
        conn.commit()


def _seed_event(engine, job_id: int, new_status: str, recovery_source):
    with engine.connect() as conn:
        conn.execute(state.job_events_table.insert().values(
            spot_job_id=job_id,
            task_id=0,
            new_status=new_status,
            recovery_source=recovery_source,
        ))
        conn.commit()


class TestRecoveryEventCounts:

    def test_groups_by_source_and_workspace(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, 1, workspace='ws-a')
        _seed_job(engine, 2, workspace='ws-b')
        _seed_event(engine, 1, 'RECOVERING', 'EMERGENCY')
        _seed_event(engine, 1, 'RECOVERING', 'EMERGENCY')
        _seed_event(engine, 1, 'RECOVERING', 'FAILURE')
        _seed_event(engine, 2, 'RECOVERING', 'EMERGENCY')

        rows = set(state.get_recovery_event_counts_by_source_workspace())
        assert rows == {
            ('EMERGENCY', 'ws-a', 2),
            ('FAILURE', 'ws-a', 1),
            ('EMERGENCY', 'ws-b', 1),
        }

    def test_excludes_null_source_and_non_recovering(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, 1)
        # Pre-migration RECOVERING row: NULL source — excluded.
        _seed_event(engine, 1, 'RECOVERING', None)
        # Non-RECOVERING events never count, sourced or not.
        _seed_event(engine, 1, 'SUCCEEDED', None)

        assert state.get_recovery_event_counts_by_source_workspace() == []

    def test_null_workspace_passthrough(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        # Event whose job_info row is missing entirely (LEFT JOIN miss).
        _seed_event(engine, 99, 'RECOVERING', 'RESTART')

        rows = state.get_recovery_event_counts_by_source_workspace()
        assert rows == [('RESTART', None, 1)]


class TestActiveEmergencyEpisodes:

    def test_window_and_terminal_filters(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        now = time.time()
        # Active episode, non-terminal job: reported.
        _seed_job(engine,
                  1,
                  status='RUNNING',
                  emergency_count=2,
                  last_emergency_at=now - 60)
        # Episode aged out of the window: not reported.
        _seed_job(engine,
                  2,
                  status='RUNNING',
                  emergency_count=3,
                  last_emergency_at=now - 7 * 3600)
        # Active episode but the job is terminal: not reported.
        _seed_job(engine,
                  3,
                  status='SUCCEEDED',
                  emergency_count=2,
                  last_emergency_at=now - 60)
        # Never had an emergency: not reported.
        _seed_job(engine, 4, status='RUNNING')

        rows = state.get_active_emergency_recovery_episodes(now=now,
                                                            window_seconds=6 *
                                                            3600,
                                                            limit=100)
        assert rows == [(1, 'job-1', 'default', 2)]

    def test_ordering_and_limit(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        now = time.time()
        # Highest attempt count first; count ties broken by oldest
        # last-attempt first, then job id.
        _seed_job(engine, 1, emergency_count=1, last_emergency_at=now - 30)
        _seed_job(engine, 2, emergency_count=3, last_emergency_at=now - 10)
        _seed_job(engine, 3, emergency_count=1, last_emergency_at=now - 90)
        _seed_job(engine, 4, emergency_count=1, last_emergency_at=now - 90)

        rows = state.get_active_emergency_recovery_episodes(now=now,
                                                            window_seconds=6 *
                                                            3600,
                                                            limit=100)
        assert [r[0] for r in rows] == [2, 3, 4, 1]

        limited = state.get_active_emergency_recovery_episodes(
            now=now, window_seconds=6 * 3600, limit=2)
        assert [r[0] for r in limited] == [2, 3]

    def test_multi_task_job_counts_once_while_any_task_active(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        now = time.time()
        _seed_job(engine,
                  1,
                  status='SUCCEEDED',
                  emergency_count=2,
                  last_emergency_at=now - 60)
        # Second task of the same job still running -> job qualifies,
        # and only one row comes back for it.
        with engine.connect() as conn:
            conn.execute(state.spot_table.insert().values(
                job_name='job-1',
                status='RUNNING',
                spot_job_id=1,
                task_id=1,
            ))
            conn.commit()

        rows = state.get_active_emergency_recovery_episodes(now=now,
                                                            window_seconds=6 *
                                                            3600,
                                                            limit=100)
        assert rows == [(1, 'job-1', 'default', 2)]
