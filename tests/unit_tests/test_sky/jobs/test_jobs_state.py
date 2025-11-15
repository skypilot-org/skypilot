"""Unit tests for sky.jobs.state module."""

import asyncio
import contextlib
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
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE', engine)
    monkeypatch.setattr(state, '_SQLALCHEMY_ENGINE_ASYNC', async_engine)

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
        state.scheduler_set_waiting(job_id1, '/tmp/dag1.yaml',
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
        state.scheduler_set_waiting(job_id2, '/tmp/dag2.yaml',
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
        state.scheduler_set_waiting(job_id3, '/tmp/dag3.yaml',
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
        state.scheduler_set_waiting(job_id4, '/tmp/dag4.yaml',
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
        state.scheduler_set_waiting(job_id5, '/tmp/dag5.yaml',
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

    return asyncio.get_event_loop().run_until_complete(create_job_states())


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
        state.scheduler_set_waiting(job_id, '/tmp/dag.yaml', '/tmp/user.yaml',
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
            state.scheduler_set_waiting(job_id1, '/tmp/dag1.yaml',
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
            state.scheduler_set_waiting(job_id2, '/tmp/dag2.yaml',
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
            state.scheduler_set_waiting(job_id3, '/tmp/dag3.yaml',
                                        '/tmp/user3.yaml', '/tmp/env3', None,
                                        500)
            state.scheduler_set_done(job_id3)

        asyncio.get_event_loop().run_until_complete(setup_jobs())

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
