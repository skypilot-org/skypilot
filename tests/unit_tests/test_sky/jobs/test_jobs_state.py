"""Unit tests for sky.jobs.state module."""

import asyncio
import contextlib
import datetime
import time
from unittest import mock

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.skylet import constants


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state and monkeypatch engines.

    Follows the pattern from test_managed_jobs_service.py.
    """
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    # Monkeypatch Alembic DB lock to a workspace path to avoid writing to ~/.sky
    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)

    # Monkeypatch module-level engines used by state
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)

    # Create schema
    state.create_table(engine)

    yield engine


@pytest.fixture
def _seed_test_jobs(_mock_managed_jobs_db_conn):
    """Seed the database with test jobs in various states for comprehensive testing."""

    # Mock callback function for async state transitions
    async def mock_callback(status: str):
        pass

    async def create_job_states():
        # Job 1: PENDING state (just created)
        job_id1 = state.set_job_info_without_job_id(name='test-job-a',
                                                    workspace='ws1',
                                                    entrypoint='ep1',
                                                    pool='pool1',
                                                    pool_hash='hash123',
                                                    user_hash='user1')
        state.set_pending(job_id1,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        # Set priority and schedule state
        state.scheduler_set_waiting([job_id1], '/tmp/dag1.yaml',
                                    '/tmp/user1.yaml', '/tmp/env1', None, 100)

        # Job 2: STARTING state (launched but not yet running)
        job_id2 = state.set_job_info_without_job_id(name='test-job-b',
                                                    workspace='ws1',
                                                    entrypoint='ep2',
                                                    pool=None,
                                                    pool_hash=None,
                                                    user_hash='user1')
        state.set_pending(job_id2,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([job_id2], '/tmp/dag2.yaml',
                                    '/tmp/user2.yaml', '/tmp/env2', None, 200)
        await state.set_starting_async(job_id2, 0, 'run_123', time.time(), '{}',
                                       {}, mock_callback)

        # Job 3: RUNNING state (actively running)
        job_id3 = state.set_job_info_without_job_id(name='test-job-a',
                                                    workspace='ws2',
                                                    entrypoint='ep3',
                                                    pool='pool2',
                                                    pool_hash=None,
                                                    user_hash='user2')
        state.set_pending(job_id3,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([job_id3], '/tmp/dag3.yaml',
                                    '/tmp/user3.yaml', '/tmp/env3', None, 50)
        await state.set_starting_async(job_id3, 0, 'run_456', time.time(), '{}',
                                       {}, mock_callback)
        await state.set_started_async(job_id3, 0, time.time(), mock_callback)

        # Job 4: SUCCEEDED state (completed successfully)
        job_id4 = state.set_job_info_without_job_id(name='test-job-c',
                                                    workspace='ws1',
                                                    entrypoint='ep4',
                                                    pool='pool1',
                                                    pool_hash='hash123',
                                                    user_hash='user1')
        state.set_pending(job_id4,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([job_id4], '/tmp/dag4.yaml',
                                    '/tmp/user4.yaml', '/tmp/env4', None, 75)
        await state.set_starting_async(job_id4, 0, 'run_789', time.time(), '{}',
                                       {}, mock_callback)
        await state.set_started_async(job_id4, 0, time.time(), mock_callback)
        await state.set_succeeded_async(job_id4, 0, time.time(), mock_callback)
        state.scheduler_set_done(job_id4)

        # Job 5: FAILED state
        job_id5 = state.set_job_info_without_job_id(name='test-job-d',
                                                    workspace='ws2',
                                                    entrypoint='ep5',
                                                    pool=None,
                                                    pool_hash=None,
                                                    user_hash='user2')
        state.set_pending(job_id5,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([job_id5], '/tmp/dag5.yaml',
                                    '/tmp/user5.yaml', '/tmp/env5', None, 150)
        await state.set_starting_async(job_id5, 0, 'run_abc', time.time(), '{}',
                                       {}, mock_callback)
        await state.set_started_async(job_id5, 0, time.time(), mock_callback)
        await state.set_failed_async(job_id5, 0, state.ManagedJobStatus.FAILED,
                                     'Test failure', mock_callback)
        state.scheduler_set_done(job_id5)

        return {
            'job_id1': job_id1,
            'job_id2': job_id2,
            'job_id3': job_id3,
            'job_id4': job_id4,
            'job_id5': job_id5,
        }

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(create_job_states())
    finally:
        loop.close()


@pytest.fixture
def _seed_multi_task_job(_mock_managed_jobs_db_conn):
    """Seed a multi-task (pipeline) job plus an unrelated terminal job.

    The pipeline job has task 0 SUCCEEDED and task 1 RUNNING, so the job
    overall is still running. A second single-task FAILED job exists so that
    filter assertions prove row selection among other jobs, not just against
    a one-job database. Used to characterize how the row/task-level
    `statuses` filter behaves on pipelines.

    Returns a dict with 'pipeline_job_id' and 'failed_job_id'.
    """

    async def mock_callback(status: str):
        pass

    async def create_jobs():
        pipeline_job_id = state.set_job_info_without_job_id(
            name='pipe-status-test',
            workspace='ws1',
            entrypoint='ep',
            pool=None,
            pool_hash=None,
            user_hash='user1')
        # Two tasks under one job.
        state.set_pending(pipeline_job_id,
                          task_id=0,
                          task_name='extract',
                          resources_str='{}',
                          metadata='{}')
        state.set_pending(pipeline_job_id,
                          task_id=1,
                          task_name='transform',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([pipeline_job_id], '/tmp/dag.yaml',
                                    '/tmp/user.yaml', '/tmp/env', None, 100)
        # Task 0 -> SUCCEEDED.
        await state.set_starting_async(pipeline_job_id, 0, 'run_0', time.time(),
                                       '{}', {}, mock_callback)
        await state.set_started_async(pipeline_job_id, 0, time.time(),
                                      mock_callback)
        await state.set_succeeded_async(pipeline_job_id, 0, time.time(),
                                        mock_callback)
        # Task 1 -> RUNNING (job not done: no scheduler_set_done).
        await state.set_starting_async(pipeline_job_id, 1, 'run_1', time.time(),
                                       '{}', {}, mock_callback)
        await state.set_started_async(pipeline_job_id, 1, time.time(),
                                      mock_callback)

        # An unrelated single-task job that already FAILED.
        failed_job_id = state.set_job_info_without_job_id(
            name='other-failed-job',
            workspace='ws1',
            entrypoint='ep',
            pool=None,
            pool_hash=None,
            user_hash='user1')
        state.set_pending(failed_job_id,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        state.scheduler_set_waiting([failed_job_id], '/tmp/dag-failed.yaml',
                                    '/tmp/user-failed.yaml', '/tmp/env-failed',
                                    None, 100)
        await state.set_starting_async(failed_job_id, 0, 'run_failed',
                                       time.time(), '{}', {}, mock_callback)
        await state.set_started_async(failed_job_id, 0, time.time(),
                                      mock_callback)
        await state.set_failed_async(failed_job_id, 0,
                                     state.ManagedJobStatus.FAILED,
                                     'Test failure', mock_callback)
        state.scheduler_set_done(failed_job_id)
        return {
            'pipeline_job_id': pipeline_job_id,
            'failed_job_id': failed_job_id,
        }

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(create_jobs())
    finally:
        loop.close()


class TestMapResponseFieldToDbColumn:
    """Test class for _map_response_field_to_db_column function."""

    def test_alias_mapping_job_id(self):
        """Test alias mapping for '_job_id' field."""
        result = state._map_response_field_to_db_column('_job_id')
        assert result == state.spot_table.c.job_id

    def test_alias_mapping_task_name(self):
        """Test alias mapping for '_task_name' field."""
        result = state._map_response_field_to_db_column('_task_name')
        assert result == state.spot_table.c.job_name

    def test_alias_mapping_public_job_id(self):
        """Test alias mapping for 'job_id' field (public job id)."""
        result = state._map_response_field_to_db_column('job_id')
        assert result == state.spot_table.c.spot_job_id

    def test_alias_mapping_job_name(self):
        """Test alias mapping for 'job_name' field."""
        result = state._map_response_field_to_db_column('job_name')
        assert result == state.job_info_table.c.name

    def test_alias_mapping_job_info_job_id(self):
        """Test alias mapping for '_job_info_job_id' field."""
        result = state._map_response_field_to_db_column('_job_info_job_id')
        assert result == state.job_info_table.c.spot_job_id

    def test_direct_match_spot_table(self):
        """Test direct match on spot_table columns."""
        result = state._map_response_field_to_db_column('status')
        assert result == state.spot_table.c.status

        result = state._map_response_field_to_db_column('task_id')
        assert result == state.spot_table.c.task_id

        result = state._map_response_field_to_db_column('submitted_at')
        assert result == state.spot_table.c.submitted_at

    def test_direct_match_job_info_table(self):
        """Test direct match on job_info_table columns."""
        result = state._map_response_field_to_db_column('schedule_state')
        assert result == state.job_info_table.c.schedule_state

        result = state._map_response_field_to_db_column('priority')
        assert result == state.job_info_table.c.priority

        result = state._map_response_field_to_db_column('workspace')
        assert result == state.job_info_table.c.workspace

    def test_unknown_field(self):
        """Test fallback case for unknown fields."""
        with pytest.raises(ValueError, match='Unknown field: unknown_field'):
            state._map_response_field_to_db_column('unknown_field')


class TestGetManagedJobsTotal:
    """Test class for get_managed_jobs_total function."""

    def test_empty_database(self, _mock_managed_jobs_db_conn):
        """Test get_managed_jobs_total with empty database."""
        total = state.get_managed_jobs_total()
        assert total == 0

    def test_with_seeded_jobs(self, _seed_test_jobs):
        """Test get_managed_jobs_total with seeded jobs."""
        total = state.get_managed_jobs_total()
        # We have 5 jobs seeded
        assert total == 5

    def test_incremental_job_addition(self, _mock_managed_jobs_db_conn):
        """Test that total increases as jobs are added."""
        initial_total = state.get_managed_jobs_total()
        assert initial_total == 0

        # Add first job
        job_id1 = state.set_job_info_without_job_id(name='job1',
                                                    workspace='ws1',
                                                    entrypoint='ep1',
                                                    pool=None,
                                                    pool_hash=None,
                                                    user_hash='user1')
        state.set_pending(job_id1, 0, 'task0', '{}', '{}')

        total_after_one = state.get_managed_jobs_total()
        assert total_after_one == 1

        # Add second job
        job_id2 = state.set_job_info_without_job_id(name='job2',
                                                    workspace='ws1',
                                                    entrypoint='ep2',
                                                    pool=None,
                                                    pool_hash=None,
                                                    user_hash='user1')
        state.set_pending(job_id2, 0, 'task0', '{}', '{}')

        total_after_two = state.get_managed_jobs_total()
        assert total_after_two == 2


class TestGetManagedJobsHighestPriority:
    """Test class for get_managed_jobs_highest_priority function."""

    def test_empty_database(self, _mock_managed_jobs_db_conn):
        """Test get_managed_jobs_highest_priority with empty database."""
        priority = state.get_managed_jobs_highest_priority()
        # Should return MIN_PRIORITY when no jobs exist
        assert priority == constants.MIN_PRIORITY

    def test_with_seeded_jobs(self, _seed_test_jobs):
        """Test get_managed_jobs_highest_priority with seeded jobs."""
        # Job 2 has priority 200 and is in LAUNCHING state
        # Job 5 has priority 150 but is in DONE state (terminal)
        # Job 1 has priority 100 and is in WAITING state
        # Job 4 has priority 75 but is in DONE state
        # Job 3 has priority 50 and is in ALIVE state (from set_started_async)
        # Job 2 with priority 200 should be the highest
        priority = state.get_managed_jobs_highest_priority()
        assert priority == 200

    def test_only_terminal_jobs(self, _mock_managed_jobs_db_conn):
        """Test when only terminal jobs exist."""
        # Create a job and mark it as done
        job_id = state.set_job_info_without_job_id(name='job1',
                                                   workspace='ws1',
                                                   entrypoint='ep1',
                                                   pool=None,
                                                   pool_hash=None,
                                                   user_hash='user1')
        state.set_pending(job_id, 0, 'task0', '{}', '{}')
        state.scheduler_set_waiting([job_id], '/tmp/dag.yaml', '/tmp/user.yaml',
                                    '/tmp/env', None, 300)
        state.scheduler_set_done(job_id)

        priority = state.get_managed_jobs_highest_priority()
        # Should return MIN_PRIORITY when all jobs are in DONE state
        assert priority == constants.MIN_PRIORITY

    def test_mixed_schedule_states(self, _mock_managed_jobs_db_conn):
        """Test with jobs in various schedule states."""

        async def setup_jobs():
            mock_callback = mock.Mock()

            # Job in WAITING state with priority 100
            job_id1 = state.set_job_info_without_job_id(name='job1',
                                                        workspace='ws1',
                                                        entrypoint='ep1',
                                                        pool=None,
                                                        pool_hash=None,
                                                        user_hash='user1')
            state.set_pending(job_id1, 0, 'task0', '{}', '{}')
            state.scheduler_set_waiting([job_id1], '/tmp/dag1.yaml',
                                        '/tmp/user1.yaml', '/tmp/env1', None,
                                        100)

            # Job in LAUNCHING state with priority 250
            job_id2 = state.set_job_info_without_job_id(name='job2',
                                                        workspace='ws1',
                                                        entrypoint='ep2',
                                                        pool=None,
                                                        pool_hash=None,
                                                        user_hash='user1')
            state.set_pending(job_id2, 0, 'task0', '{}', '{}')
            state.scheduler_set_waiting([job_id2], '/tmp/dag2.yaml',
                                        '/tmp/user2.yaml', '/tmp/env2', None,
                                        250)
            await state.scheduler_set_launching_async(job_id2)

            # Job in DONE state with priority 500 (should be ignored)
            job_id3 = state.set_job_info_without_job_id(name='job3',
                                                        workspace='ws1',
                                                        entrypoint='ep3',
                                                        pool=None,
                                                        pool_hash=None,
                                                        user_hash='user1')
            state.set_pending(job_id3, 0, 'task0', '{}', '{}')
            state.scheduler_set_waiting([job_id3], '/tmp/dag3.yaml',
                                        '/tmp/user3.yaml', '/tmp/env3', None,
                                        500)
            state.scheduler_set_done(job_id3)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(setup_jobs())
        finally:
            loop.close()

        priority = state.get_managed_jobs_highest_priority()
        # Should return 250 (highest among WAITING and LAUNCHING jobs)
        assert priority == 250


class TestBuildManagedJobsWithFiltersNoStatusQuery:
    """Test class for build_managed_jobs_with_filters_no_status_query function."""

    def test_basic_query_no_filters(self):
        """Test basic query construction without filters."""
        query = state.build_managed_jobs_with_filters_no_status_query()
        # Query should be constructed without errors
        assert query is not None
        # Verify it's a SQLAlchemy Select object
        assert hasattr(query, 'compile')

    def test_with_field_selection(self):
        """Test query with specific field selection."""
        fields = ['job_id', 'job_name', 'status', 'workspace']
        query = state.build_managed_jobs_with_filters_no_status_query(
            fields=fields)
        assert query is not None

    def test_with_job_ids_filter(self):
        """Test query with job_ids filter."""
        job_ids = [1, 2, 3]
        query = state.build_managed_jobs_with_filters_no_status_query(
            job_ids=job_ids)
        assert query is not None

    def test_with_accessible_workspaces_filter(self):
        """Test query with accessible_workspaces filter."""
        workspaces = ['ws1', 'ws2']
        query = state.build_managed_jobs_with_filters_no_status_query(
            accessible_workspaces=workspaces)
        assert query is not None

    def test_with_workspace_match_filter(self):
        """Test query with workspace_match filter."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            workspace_match='test')
        assert query is not None

    def test_with_name_match_filter(self):
        """Test query with name_match filter."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            name_match='job-a')
        assert query is not None

    def test_with_pool_match_filter(self):
        """Test query with pool_match filter."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            pool_match='pool1')
        assert query is not None

    def test_with_user_hashes_filter(self):
        """Test query with user_hashes filter."""
        user_hashes = ['user1', 'user2']
        query = state.build_managed_jobs_with_filters_no_status_query(
            user_hashes=user_hashes)
        assert query is not None

    def test_with_skip_finished_flag(self):
        """Test query with skip_finished flag."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            skip_finished=True)
        assert query is not None

    def test_with_count_only_flag(self):
        """Test query with count_only flag."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            count_only=True)
        assert query is not None

    def test_with_status_count_flag(self):
        """Test query with status_count flag."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            status_count=True)
        assert query is not None

    def test_combined_filters(self):
        """Test query with multiple filters combined."""
        query = state.build_managed_jobs_with_filters_no_status_query(
            job_ids=[1, 2],
            accessible_workspaces=['ws1'],
            name_match='test',
            skip_finished=True)
        assert query is not None


class TestBuildManagedJobsWithFiltersQuery:
    """Test class for build_managed_jobs_with_filters_query function."""

    def test_basic_query_no_filters(self):
        """Test basic query construction without filters."""
        query = state.build_managed_jobs_with_filters_query()
        assert query is not None

    def test_with_status_filter(self):
        """Test query with status filter."""
        statuses = [
            state.ManagedJobStatus.PENDING.value,
            state.ManagedJobStatus.RUNNING.value
        ]
        query = state.build_managed_jobs_with_filters_query(statuses=statuses)
        assert query is not None

    def test_with_multiple_filters_and_status(self):
        """Test query with multiple filters including status."""
        query = state.build_managed_jobs_with_filters_query(
            job_ids=[1, 2, 3],
            accessible_workspaces=['ws1', 'ws2'],
            statuses=[state.ManagedJobStatus.RUNNING.value],
            skip_finished=False)
        assert query is not None

    def test_status_filter_none(self):
        """Test that query works when status filter is None."""
        query = state.build_managed_jobs_with_filters_query(job_ids=[1],
                                                            statuses=None)
        assert query is not None


class TestGetStatusCountWithFilters:
    """Test class for get_status_count_with_filters function."""

    def test_empty_database(self, _mock_managed_jobs_db_conn):
        """Test status count with empty database."""
        result = state.get_status_count_with_filters()
        assert result == {}

    def test_with_seeded_jobs(self, _seed_test_jobs):
        """Test status count with seeded jobs."""
        result = state.get_status_count_with_filters()

        # We have:
        # Job 1: PENDING
        # Job 2: STARTING
        # Job 3: RUNNING
        # Job 4: SUCCEEDED
        # Job 5: FAILED
        assert result[state.ManagedJobStatus.PENDING.value] == 1
        assert result[state.ManagedJobStatus.STARTING.value] == 1
        assert result[state.ManagedJobStatus.RUNNING.value] == 1
        assert result[state.ManagedJobStatus.SUCCEEDED.value] == 1
        assert result[state.ManagedJobStatus.FAILED.value] == 1

    def test_with_job_ids_filter(self, _seed_test_jobs):
        """Test status count with job_ids filter."""
        job_ids = [_seed_test_jobs['job_id1'], _seed_test_jobs['job_id2']]
        result = state.get_status_count_with_filters(job_ids=job_ids)

        # Should only count job 1 (PENDING) and job 2 (STARTING)
        assert result[state.ManagedJobStatus.PENDING.value] == 1
        assert result[state.ManagedJobStatus.STARTING.value] == 1
        assert state.ManagedJobStatus.RUNNING.value not in result
        assert len(result) == 2

    def test_with_workspace_filter(self, _seed_test_jobs):
        """Test status count with accessible_workspaces filter."""
        result = state.get_status_count_with_filters(
            accessible_workspaces=['ws1'])

        # Jobs in ws1: job1 (PENDING), job2 (STARTING), job4 (SUCCEEDED)
        assert result[state.ManagedJobStatus.PENDING.value] == 1
        assert result[state.ManagedJobStatus.STARTING.value] == 1
        assert result[state.ManagedJobStatus.SUCCEEDED.value] == 1
        assert len(result) == 3

    def test_with_skip_finished_flag(self, _seed_test_jobs):
        """Test status count with skip_finished flag."""
        result = state.get_status_count_with_filters(skip_finished=True)

        # Should exclude SUCCEEDED and FAILED jobs
        assert result[state.ManagedJobStatus.PENDING.value] == 1
        assert result[state.ManagedJobStatus.STARTING.value] == 1
        assert result[state.ManagedJobStatus.RUNNING.value] == 1
        assert state.ManagedJobStatus.SUCCEEDED.value not in result
        assert state.ManagedJobStatus.FAILED.value not in result

    def test_with_user_hashes_filter(self, _seed_test_jobs):
        """Test status count with user_hashes filter."""
        result = state.get_status_count_with_filters(user_hashes=['user1'])

        # Jobs with user1: job1 (PENDING), job2 (STARTING), job4 (SUCCEEDED)
        assert result[state.ManagedJobStatus.PENDING.value] == 1
        assert result[state.ManagedJobStatus.STARTING.value] == 1
        assert result[state.ManagedJobStatus.SUCCEEDED.value] == 1
        assert len(result) == 3


class TestGetManagedJobsWithFilters:
    """Test class for get_managed_jobs_with_filters function."""

    def test_empty_database(self, _mock_managed_jobs_db_conn):
        """Test get_managed_jobs_with_filters with empty database."""
        jobs, total = state.get_managed_jobs_with_filters()
        assert jobs == []
        assert total == 0

    def test_with_seeded_jobs(self, _seed_test_jobs):
        """Test get_managed_jobs_with_filters with seeded jobs."""
        jobs, total = state.get_managed_jobs_with_filters()
        assert len(jobs) == 5
        assert total == 5

        # Verify that jobs are returned with correct structure
        for job in jobs:
            assert 'job_id' in job
            assert 'job_name' in job
            assert 'status' in job
            assert isinstance(job['status'], state.ManagedJobStatus)

    def test_with_job_ids_filter(self, _seed_test_jobs):
        """Test filtering by job_ids."""
        job_ids = [_seed_test_jobs['job_id1'], _seed_test_jobs['job_id3']]
        jobs, total = state.get_managed_jobs_with_filters(job_ids=job_ids)

        assert len(jobs) == 2
        assert total == 2
        returned_job_ids = [job['job_id'] for job in jobs]
        assert set(returned_job_ids) == set(job_ids)

    def test_with_workspace_filter(self, _seed_test_jobs):
        """Test filtering by accessible_workspaces."""
        jobs, total = state.get_managed_jobs_with_filters(
            accessible_workspaces=['ws1'])

        # Jobs in ws1: job1, job2, job4
        assert len(jobs) == 3
        assert total == 3
        for job in jobs:
            assert job['workspace'] == 'ws1'

    def test_with_name_match_filter(self, _seed_test_jobs):
        """Test filtering by name_match."""
        jobs, total = state.get_managed_jobs_with_filters(
            name_match='test-job-a')

        # Jobs with name containing 'test-job-a': job1 and job3
        assert len(jobs) == 2
        assert total == 2
        for job in jobs:
            assert 'test-job-a' in job['job_name']

    def test_with_pool_match_filter(self, _seed_test_jobs):
        """Test filtering by pool_match."""
        jobs, total = state.get_managed_jobs_with_filters(pool_match='pool1')

        # Jobs with pool containing 'pool1': job1 and job4
        assert len(jobs) == 2
        assert total == 2
        for job in jobs:
            assert job['pool'] is not None
            assert 'pool1' in job['pool']

    def test_with_user_hashes_filter(self, _seed_test_jobs):
        """Test filtering by user_hashes."""
        jobs, total = state.get_managed_jobs_with_filters(user_hashes=['user2'])

        # Jobs with user2: job3 and job5
        assert len(jobs) == 2
        assert total == 2
        for job in jobs:
            assert job['user_hash'] == 'user2'

    def test_with_status_filter(self, _seed_test_jobs):
        """Test filtering by status."""
        statuses = [
            state.ManagedJobStatus.PENDING.value,
            state.ManagedJobStatus.RUNNING.value
        ]
        jobs, total = state.get_managed_jobs_with_filters(statuses=statuses)

        # Jobs: job1 (PENDING) and job3 (RUNNING)
        assert len(jobs) == 2
        assert total == 2
        for job in jobs:
            assert job['status'] in [
                state.ManagedJobStatus.PENDING, state.ManagedJobStatus.RUNNING
            ]

    def test_with_skip_finished_flag(self, _seed_test_jobs):
        """Test with skip_finished flag."""
        jobs, total = state.get_managed_jobs_with_filters(skip_finished=True)

        # Should exclude SUCCEEDED and FAILED jobs (job4 and job5)
        assert len(jobs) == 3
        assert total == 3
        for job in jobs:
            assert not job['status'].is_terminal()

    def test_with_pagination(self, _seed_test_jobs):
        """Test pagination with page and limit."""
        # Get first page with limit 2
        jobs_page1, total = state.get_managed_jobs_with_filters(page=1, limit=2)
        assert len(jobs_page1) == 2
        assert total == 5  # Total should be all jobs

        # Get second page with limit 2
        jobs_page2, total = state.get_managed_jobs_with_filters(page=2, limit=2)
        assert len(jobs_page2) == 2
        assert total == 5

        # Get third page with limit 2 (should have 1 job)
        jobs_page3, total = state.get_managed_jobs_with_filters(page=3, limit=2)
        assert len(jobs_page3) == 1
        assert total == 5

        # Verify no overlap between pages
        job_ids_page1 = {job['job_id'] for job in jobs_page1}
        job_ids_page2 = {job['job_id'] for job in jobs_page2}
        job_ids_page3 = {job['job_id'] for job in jobs_page3}
        assert len(job_ids_page1 & job_ids_page2) == 0
        assert len(job_ids_page1 & job_ids_page3) == 0
        assert len(job_ids_page2 & job_ids_page3) == 0

    def test_with_field_selection(self, _seed_test_jobs):
        """Test with specific field selection."""
        fields = ['job_id', 'job_name', 'status']
        jobs, total = state.get_managed_jobs_with_filters(fields=fields)

        assert len(jobs) == 5
        assert total == 5
        for job in jobs:
            # Selected fields should be present
            assert 'job_id' in job
            assert 'job_name' in job
            assert 'status' in job

    def test_combined_filters(self, _seed_test_jobs):
        """Test with multiple filters combined."""
        jobs, total = state.get_managed_jobs_with_filters(
            accessible_workspaces=['ws1'],
            statuses=[
                state.ManagedJobStatus.PENDING.value,
                state.ManagedJobStatus.STARTING.value
            ],
            user_hashes=['user1'])

        # Should match job1 (PENDING, ws1, user1) and job2 (STARTING, ws1, user1)
        assert len(jobs) == 2
        assert total == 2
        for job in jobs:
            assert job['workspace'] == 'ws1'
            assert job['user_hash'] == 'user1'
            assert job['status'] in [
                state.ManagedJobStatus.PENDING, state.ManagedJobStatus.STARTING
            ]

    def test_job_ordering(self, _seed_test_jobs):
        """Test that jobs are returned in descending order by job_id."""
        jobs, total = state.get_managed_jobs_with_filters()

        # According to the query, jobs should be ordered by spot_job_id desc
        job_ids = [job['job_id'] for job in jobs]
        assert job_ids == sorted(job_ids, reverse=True)

    def test_with_workspace_match(self, _seed_test_jobs):
        """Test filtering by workspace_match (partial match)."""
        jobs, total = state.get_managed_jobs_with_filters(workspace_match='ws')

        # All jobs have workspaces starting with 'ws'
        assert len(jobs) == 5
        assert total == 5


class TestGetLatestRecoveryReasons:
    """Recovery-reason coverage via get_latest_recovery_and_pending_reasons."""

    def test_empty_job_ids(self, _mock_managed_jobs_db_conn):
        assert state.get_latest_recovery_and_pending_reasons([], [])[0] == {}

    def test_latest_reason_wins_and_filters(self, _mock_managed_jobs_db_conn):
        early = datetime.datetime(2026, 1, 1, 0, 0, 0)
        late = datetime.datetime(2026, 1, 1, 0, 5, 0)
        # Job 1: two RECOVERING events -> the latest reason wins.
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'older reason',
                            timestamp=early)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'OOMKilled (exit code 137)',
                            timestamp=late)
        # Job 2: a RUNNING event is ignored; only the RECOVERING reason returns.
        state.add_job_event(2,
                            0,
                            state.ManagedJobStatus.RUNNING,
                            'Job has recovered',
                            timestamp=late)
        state.add_job_event(2,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'preempted',
                            timestamp=early)
        # Job 3 has a RECOVERING event but is not requested.
        state.add_job_event(3,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'unrelated',
                            timestamp=late)
        result = state.get_latest_recovery_and_pending_reasons([1, 2], [])[0]
        assert result == {
            1: 'OOMKilled (exit code 137)',
            2: 'preempted',
        }

    def test_empty_reason_skipped(self, _mock_managed_jobs_db_conn):
        early = datetime.datetime(2026, 1, 1, 0, 0, 0)
        late = datetime.datetime(2026, 1, 1, 0, 5, 0)
        # The most recent RECOVERING event has an empty reason -> fall back to
        # the most recent non-empty one.
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'real reason',
                            timestamp=early)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            '',
                            timestamp=late)
        assert state.get_latest_recovery_and_pending_reasons([1], [])[0] == {
            1: 'real reason'
        }

    def test_no_recovering_events_returns_empty(self,
                                                _mock_managed_jobs_db_conn):
        state.add_job_event(1, 0, state.ManagedJobStatus.RUNNING, 'running')
        assert state.get_latest_recovery_and_pending_reasons([1], [])[0] == {}


# Fixed epoch timestamps (seconds) for the time-range fixture. submitted_at is
# stored as epoch seconds (a sqlalchemy.Float column), matching time.time().
_T100 = 100.0
_T200 = 200.0
_T300 = 300.0
# Bounds far in the past (_T*, ~1970) and future (~year 2286) relative to the
# current time, used to exercise NULL submitted_at (pending) handling.
_FAR_FUTURE = 9_999_999_999.0


@pytest.fixture
def _seed_timed_jobs(_mock_managed_jobs_db_conn):
    """Seed three RUNNING jobs with explicit, distinct submitted_at times."""

    async def mock_callback(status: str):
        pass

    async def create_job_states():
        ids = {}
        for key, workspace, submit_time in (
            ('early', 'ws1', _T100),
            ('mid', 'ws1', _T200),
            ('late', 'ws2', _T300),
        ):
            job_id = state.set_job_info_without_job_id(name=f'job-{key}',
                                                       workspace=workspace,
                                                       entrypoint='ep',
                                                       pool=None,
                                                       pool_hash=None,
                                                       user_hash='user1')
            state.set_pending(job_id,
                              task_id=0,
                              task_name='task0',
                              resources_str='{}',
                              metadata='{}')
            state.scheduler_set_waiting([job_id], f'/tmp/dag-{key}.yaml',
                                        f'/tmp/user-{key}.yaml',
                                        f'/tmp/env-{key}', None, 100)
            # submitted_at is written here from the explicit submit_time.
            await state.set_starting_async(job_id, 0, f'run-{key}', submit_time,
                                           '{}', {}, mock_callback)
            await state.set_started_async(job_id, 0, submit_time, mock_callback)
            ids[key] = job_id
        return ids

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(create_job_states())
    finally:
        loop.close()


@pytest.fixture
def _seed_pending_job(_mock_managed_jobs_db_conn):
    """Seed one job left in PENDING, so its submitted_at stays NULL."""
    job_id = state.set_job_info_without_job_id(name='job-pending',
                                               workspace='ws1',
                                               entrypoint='ep',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user1')
    state.set_pending(job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')
    return job_id


@pytest.fixture
def _seed_terminal_no_submit(_mock_managed_jobs_db_conn):
    """Seed a job cancelled while PENDING: terminal, with a NULL submitted_at
    (it never reached STARTING)."""
    job_id = state.set_job_info_without_job_id(name='job-cancelled',
                                               workspace='ws1',
                                               entrypoint='ep',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user1')
    state.set_pending(job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')
    state.scheduler_set_waiting([job_id], '/tmp/dag-c.yaml', '/tmp/user-c.yaml',
                                '/tmp/env-c', None, 100)
    state.set_pending_cancelled(job_id)
    return job_id


class TestSubmittedAtRangeFilter:
    """Time-range filter on submitted_at via get_managed_jobs_with_filters."""

    def _submitted_ats(self, jobs):
        return sorted(job['submitted_at'] for job in jobs)

    def test_submitted_after_excludes_earlier(self, _seed_timed_jobs):
        jobs, total = state.get_managed_jobs_with_filters(submitted_after=_T200)
        # Inclusive lower bound: mid (_T200) and late (_T300) match.
        assert total == 2
        assert self._submitted_ats(jobs) == [_T200, _T300]

    def test_submitted_after_boundary_is_inclusive(self, _seed_timed_jobs):
        # A row exactly at submitted_after is included (>=).
        jobs, _ = state.get_managed_jobs_with_filters(submitted_after=_T300)
        assert self._submitted_ats(jobs) == [_T300]

    def test_submitted_before_excludes_later(self, _seed_timed_jobs):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_before=_T200)
        # Inclusive upper bound: early (_T100) and mid (_T200) match.
        assert total == 2
        assert self._submitted_ats(jobs) == [_T100, _T200]

    def test_submitted_before_boundary_is_inclusive(self, _seed_timed_jobs):
        jobs, _ = state.get_managed_jobs_with_filters(submitted_before=_T100)
        assert self._submitted_ats(jobs) == [_T100]

    def test_window_combines_after_and_before(self, _seed_timed_jobs):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_after=_T200, submitted_before=_T200)
        # Only the row exactly at _T200 falls inside [_T200, _T200].
        assert total == 1
        assert self._submitted_ats(jobs) == [_T200]

    def test_empty_window_returns_nothing(self, _seed_timed_jobs):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_after=_T300 + 1)
        assert jobs == []
        assert total == 0

    def test_no_window_returns_all(self, _seed_timed_jobs):
        jobs, total = state.get_managed_jobs_with_filters()
        assert total == 3
        assert self._submitted_ats(jobs) == [_T100, _T200, _T300]

    def test_window_with_other_filters(self, _seed_timed_jobs):
        # Combine the window with an accessible_workspaces filter: only the
        # ws1 rows within [_T100, _T200] should match (early, mid).
        jobs, total = state.get_managed_jobs_with_filters(
            accessible_workspaces=['ws1'], submitted_before=_T200)
        assert total == 2
        assert self._submitted_ats(jobs) == [_T100, _T200]

    def test_status_count_respects_window(self, _seed_timed_jobs):
        # All three jobs are RUNNING; the window must cut the status counts.
        counts = state.get_status_count_with_filters(submitted_after=_T200)
        assert counts == {state.ManagedJobStatus.RUNNING.value: 2}

    # A pending job (NULL submitted_at) is treated as submitted "now", so the
    # window keeps or drops it the same way it would a job submitted right now.
    def test_pending_kept_by_past_lower_bound(self, _seed_pending_job):
        jobs, total = state.get_managed_jobs_with_filters(submitted_after=_T100)
        assert total == 1
        assert jobs[0]['submitted_at'] is None

    def test_pending_dropped_by_past_upper_bound(self, _seed_pending_job):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_before=_T300)
        assert jobs == []
        assert total == 0

    def test_pending_kept_by_future_upper_bound(self, _seed_pending_job):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_before=_FAR_FUTURE)
        assert total == 1
        assert jobs[0]['submitted_at'] is None

    def test_pending_dropped_by_future_lower_bound(self, _seed_pending_job):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_after=_FAR_FUTURE)
        assert jobs == []
        assert total == 0

    # A terminal job that never got a submitted_at (cancelled/failed before
    # STARTING) has no submission time, so it is excluded from any window —
    # it must not be treated as submitted "now" like a still-pending job.
    def test_terminal_no_submit_present_without_filter(
            self, _seed_terminal_no_submit):
        jobs, total = state.get_managed_jobs_with_filters()
        assert total == 1
        assert jobs[0]['submitted_at'] is None
        assert jobs[0]['status'].is_terminal()

    def test_terminal_no_submit_excluded_from_lower_bound(
            self, _seed_terminal_no_submit):
        jobs, total = state.get_managed_jobs_with_filters(submitted_after=_T100)
        assert jobs == []
        assert total == 0

    def test_terminal_no_submit_excluded_from_future_upper_bound(
            self, _seed_terminal_no_submit):
        jobs, total = state.get_managed_jobs_with_filters(
            submitted_before=_FAR_FUTURE)
        assert jobs == []
        assert total == 0


class TestMultiTaskStatusFilterCharacterization:
    """Pin the CURRENT (row/task-level) behavior of the `statuses` filter on a
    multi-task (pipeline) job.

    The `statuses` filter matches individual task rows, unlike `skip_finished`,
    which is job-level. So filtering a pipeline by a terminal status returns a
    partial view of a job that is still running. These assertions characterize
    today's behavior; if `statuses` is ever made job-aware, they are expected
    to change.
    """

    def test_terminal_status_returns_only_the_finished_task(
            self, _seed_multi_task_job):
        pipeline_id = _seed_multi_task_job['pipeline_job_id']
        # --status SUCCEEDED returns only the pipeline's finished task, even
        # though the job is still running (task 1 is RUNNING). The FAILED job
        # does not match.
        jobs, total = state.get_managed_jobs_with_filters(
            statuses=[state.ManagedJobStatus.SUCCEEDED.value])
        assert [(j['job_id'], j['task_id']) for j in jobs] == [(pipeline_id, 0)]
        assert jobs[0]['status'] == state.ManagedJobStatus.SUCCEEDED
        assert total == 1

    def test_running_status_returns_only_the_running_task(
            self, _seed_multi_task_job):
        pipeline_id = _seed_multi_task_job['pipeline_job_id']
        jobs, total = state.get_managed_jobs_with_filters(
            statuses=[state.ManagedJobStatus.RUNNING.value])
        assert [(j['job_id'], j['task_id']) for j in jobs] == [(pipeline_id, 1)]
        assert jobs[0]['status'] == state.ManagedJobStatus.RUNNING
        assert total == 1

    def test_failed_status_returns_only_the_other_job(self,
                                                      _seed_multi_task_job):
        failed_id = _seed_multi_task_job['failed_job_id']
        jobs, total = state.get_managed_jobs_with_filters(
            statuses=[state.ManagedJobStatus.FAILED.value])
        assert [(j['job_id'], j['task_id']) for j in jobs] == [(failed_id, 0)]
        assert jobs[0]['status'] == state.ManagedJobStatus.FAILED
        assert total == 1

    def test_both_statuses_return_both_tasks(self, _seed_multi_task_job):
        pipeline_id = _seed_multi_task_job['pipeline_job_id']
        jobs, total = state.get_managed_jobs_with_filters(statuses=[
            state.ManagedJobStatus.SUCCEEDED.value,
            state.ManagedJobStatus.RUNNING.value,
        ])
        assert sorted((j['job_id'], j['task_id']) for j in jobs) == [
            (pipeline_id, 0),
            (pipeline_id, 1),
        ]
        # total counts unique jobs that have a matching task: two matching
        # task rows, one job.
        assert total == 1

    def test_skip_finished_is_job_level(self, _seed_multi_task_job):
        pipeline_id = _seed_multi_task_job['pipeline_job_id']
        # Contrast: skip_finished is job-level. The still-active pipeline
        # keeps ALL its tasks, including the finished one, while the FAILED
        # job is dropped entirely.
        jobs, total = state.get_managed_jobs_with_filters(skip_finished=True)
        assert sorted((j['job_id'], j['task_id']) for j in jobs) == [
            (pipeline_id, 0),
            (pipeline_id, 1),
        ]
        assert {j['status'] for j in jobs} == {
            state.ManagedJobStatus.SUCCEEDED, state.ManagedJobStatus.RUNNING
        }
        assert total == 1


class TestStatusExprSeam:
    """The optional status_expr seam lets a caller surface a refined
    user-facing status (e.g. a plugin override) in the status counts and the
    status filter without changing the raw spot.status column."""

    def _seed(self, engine):
        import sqlalchemy
        rows = [
            # (job_id, status, status_override)
            (1, 'STARTING', 'PENDING'),  # queued -> surfaces as PENDING
            (2, 'STARTING', None),  # genuinely starting
            (3, 'RUNNING', 'PENDING'),  # stale override -> must stay RUNNING
            (4, 'PENDING', None),  # real pending
            (5, 'SUCCEEDED', None),
        ]
        with engine.begin() as conn:
            for jid, st, ov in rows:
                conn.execute(state.spot_table.insert().values(
                    spot_job_id=jid,
                    task_id=0,
                    job_name=f'j{jid}',
                    status=st,
                    status_override=ov))

    @staticmethod
    def _override_expr():
        import sqlalchemy
        spot = state.spot_table
        starting = state.ManagedJobStatus.STARTING.value
        return sqlalchemy.case((sqlalchemy.and_(
            spot.c.status == starting,
            spot.c.status_override.isnot(None)), spot.c.status_override),
                               else_=spot.c.status)

    def test_status_override_column_exists(self, _mock_managed_jobs_db_conn):
        import sqlalchemy
        cols = [
            c['name'] for c in sqlalchemy.inspect(
                _mock_managed_jobs_db_conn).get_columns('spot')
        ]
        assert 'status_override' in cols

    def test_counts_collapse_with_status_expr(self, _mock_managed_jobs_db_conn):
        self._seed(_mock_managed_jobs_db_conn)
        raw = state.get_status_count_with_filters()
        assert raw.get('STARTING') == 2 and raw.get('PENDING') == 1
        collapsed = state.get_status_count_with_filters(
            status_expr=self._override_expr())
        # job1 STARTING -> PENDING; job3 stays RUNNING (stale override ignored)
        assert collapsed.get('PENDING') == 2
        assert collapsed.get('STARTING') == 1
        assert collapsed.get('RUNNING') == 1

    def test_status_filter_with_status_expr(self, _mock_managed_jobs_db_conn):
        self._seed(_mock_managed_jobs_db_conn)
        expr = self._override_expr()
        pending, _ = state.get_managed_jobs_with_filters(statuses=['PENDING'],
                                                         status_expr=expr,
                                                         page=1,
                                                         limit=50)
        assert sorted(j['job_id'] for j in pending) == [1, 4]
        starting, _ = state.get_managed_jobs_with_filters(statuses=['STARTING'],
                                                          status_expr=expr,
                                                          page=1,
                                                          limit=50)
        assert sorted(j['job_id'] for j in starting) == [2]

    def test_status_expr_none_is_noop(self, _mock_managed_jobs_db_conn):
        self._seed(_mock_managed_jobs_db_conn)
        # Without the seam, behaviour is unchanged: raw statuses are used.
        starting, _ = state.get_managed_jobs_with_filters(statuses=['STARTING'],
                                                          page=1,
                                                          limit=50)
        assert sorted(j['job_id'] for j in starting) == [1, 2]

    def test_sort_by_status_uses_status_expr(self, _mock_managed_jobs_db_conn):
        # sort_by='status' must order by the refined status (the paginated
        # subquery sorts by MAX(status_expr); the final query by status_expr).
        self._seed(_mock_managed_jobs_db_conn)
        expr = self._override_expr()
        # Refined status per seeded job (job1 is raw STARTING but refined
        # PENDING; job3 is RUNNING with a stale override -> stays RUNNING).
        refined = {
            1: 'PENDING',
            2: 'STARTING',
            3: 'RUNNING',
            4: 'PENDING',
            5: 'SUCCEEDED'
        }
        asc, _ = state.get_managed_jobs_with_filters(
            status_expr=expr,
            page=1,
            limit=50,
            sort_by='status',
            sort_order='asc',
            fields=['job_id', 'status', 'task_id'])
        seq = [refined[j['job_id']] for j in asc]
        assert seq == sorted(seq), seq  # ascending by REFINED status
        order = [j['job_id'] for j in asc]
        # The queued job (raw STARTING, refined PENDING) sorts before a
        # genuinely STARTING job and a RUNNING job -- which would NOT hold if
        # the sort fell back to the raw spot.status column.
        assert order.index(1) < order.index(2)  # PENDING before STARTING
        assert order.index(1) < order.index(3)  # PENDING before RUNNING
        desc, _ = state.get_managed_jobs_with_filters(
            status_expr=expr,
            page=1,
            limit=50,
            sort_by='status',
            sort_order='desc',
            fields=['job_id', 'status', 'task_id'])
        seq_d = [refined[j['job_id']] for j in desc]
        assert seq_d == sorted(seq_d, reverse=True), seq_d


class TestGetLatestPendingReasons:
    """Pending-reason coverage via get_latest_recovery_and_pending_reasons."""

    def test_empty_job_ids(self, _mock_managed_jobs_db_conn):
        assert state.get_latest_recovery_and_pending_reasons([], [])[1] == {}

    def test_latest_reason_wins_and_filters(self, _mock_managed_jobs_db_conn):
        early = datetime.datetime(2026, 1, 1, 0, 0, 0)
        late = datetime.datetime(2026, 1, 1, 0, 5, 0)
        # Job 1: two PENDING events -> the latest reason wins.
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'Job submitted to queue',
                            timestamp=early)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'Job is in backoff',
                            timestamp=late)
        # Job 2: a RUNNING event is ignored; only the PENDING reason returns.
        state.add_job_event(2,
                            0,
                            state.ManagedJobStatus.RUNNING,
                            'Job started',
                            timestamp=late)
        state.add_job_event(2,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'Job submitted to queue',
                            timestamp=early)
        # Job 3 has a PENDING event but is not requested.
        state.add_job_event(3,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'unrelated',
                            timestamp=late)
        result = state.get_latest_recovery_and_pending_reasons([], [1, 2])[1]
        assert result == {
            1: 'Job is in backoff',
            2: 'Job submitted to queue',
        }

    def test_empty_reason_skipped(self, _mock_managed_jobs_db_conn):
        early = datetime.datetime(2026, 1, 1, 0, 0, 0)
        late = datetime.datetime(2026, 1, 1, 0, 5, 0)
        # The most recent PENDING event has an empty reason -> fall back to
        # the most recent non-empty one.
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'Job submitted to queue',
                            timestamp=early)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.PENDING,
                            '',
                            timestamp=late)
        assert state.get_latest_recovery_and_pending_reasons([], [1])[1] == {
            1: 'Job submitted to queue'
        }

    def test_no_pending_events_returns_empty(self, _mock_managed_jobs_db_conn):
        state.add_job_event(1, 0, state.ManagedJobStatus.RUNNING, 'running')
        assert state.get_latest_recovery_and_pending_reasons([], [1])[1] == {}


class TestGetLatestRecoveryAndPendingReasons:
    """Tests for the single-query get_latest_recovery_and_pending_reasons."""

    def test_both_empty(self, _mock_managed_jobs_db_conn):
        assert state.get_latest_recovery_and_pending_reasons([], []) == ({}, {})

    def test_returns_reasons_per_status(self, _mock_managed_jobs_db_conn):
        # Job 1 is recovering; job 2 is pending. Each reason must land in its
        # own bucket, matched to the status it was requested under.
        state.add_job_event(1, 0, state.ManagedJobStatus.RECOVERING,
                            'OOMKilled')
        state.add_job_event(2, 0, state.ManagedJobStatus.PENDING,
                            'Job submitted to queue')
        recovery, pending = state.get_latest_recovery_and_pending_reasons([1],
                                                                          [2])
        assert recovery == {1: 'OOMKilled'}
        assert pending == {2: 'Job submitted to queue'}

    def test_status_scoped_per_job(self, _mock_managed_jobs_db_conn):
        # Job 1 has both a RECOVERING and a (later) PENDING event -- e.g. it
        # went back to PENDING during launch backoff. When requested only under
        # PENDING, its RECOVERING reason must not leak into the recovery bucket,
        # and its PENDING reason must not appear in the recovery bucket either.
        early = datetime.datetime(2026, 1, 1, 0, 0, 0)
        late = datetime.datetime(2026, 1, 1, 0, 5, 0)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.RECOVERING,
                            'stale recovering',
                            timestamp=early)
        state.add_job_event(1,
                            0,
                            state.ManagedJobStatus.PENDING,
                            'Job is in backoff',
                            timestamp=late)
        recovery, pending = state.get_latest_recovery_and_pending_reasons([],
                                                                          [1])
        assert recovery == {}
        assert pending == {1: 'Job is in backoff'}
