"""Unit tests for ManagedJobsServiceImpl."""

import asyncio
import contextlib
import time
from unittest import mock

import filelock
import grpc
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.schemas.generated import managed_jobsv1_pb2
from sky.skylet import constants
from sky.skylet import services


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state and monkeypatch engines.

    Follows the pattern from test_jobs_state_async_vs_sync.py and test_jobs_and_serve.py.
    """
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    # Monkeypatch Alembic DB lock to a workspace path to avoid writing to ~/.sky
    # Copied from test_jobs_state_async_vs_sync.py
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
        job_id1 = state.set_job_info_without_job_id(name='a',
                                                    workspace='ws1',
                                                    entrypoint='ep1',
                                                    pool='test-pool',
                                                    pool_hash='hash123')
        state.set_pending(job_id1,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')

        # Job 2: STARTING state (launched but not yet running)
        job_id2 = state.set_job_info_without_job_id(name='b',
                                                    workspace='ws1',
                                                    entrypoint='ep2',
                                                    pool=None,
                                                    pool_hash=None)
        state.set_pending(job_id2,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        await state.set_starting_async(job_id2, 0, 'run_123', time.time(), '{}',
                                       {}, mock_callback)

        # Job 3: RUNNING state (actively running)
        job_id3 = state.set_job_info_without_job_id(name='a',
                                                    workspace='ws2',
                                                    entrypoint='ep3',
                                                    pool=None,
                                                    pool_hash=None)
        state.set_pending(job_id3,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        await state.set_starting_async(job_id3, 0, 'run_456', time.time(), '{}',
                                       {}, mock_callback)
        await state.set_started_async(job_id3, 0, time.time(), mock_callback)

        # Job 4: SUCCEEDED state (completed successfully)
        job_id4 = state.set_job_info_without_job_id(name='d',
                                                    workspace='ws1',
                                                    entrypoint='ep4',
                                                    pool='test-pool',
                                                    pool_hash='hash123')
        state.set_pending(job_id4,
                          task_id=0,
                          task_name='task0',
                          resources_str='{}',
                          metadata='{}')
        await state.set_starting_async(job_id4, 0, 'run_789', time.time(), '{}',
                                       {}, mock_callback)
        await state.set_started_async(job_id4, 0, time.time(), mock_callback)
        await state.set_succeeded_async(job_id4, 0, time.time(), mock_callback)

        return {
            'job_id1': job_id1,
            'job_id2': job_id2,
            'job_id3': job_id3,
            'job_id4': job_id4,
        }

    return asyncio.get_event_loop().run_until_complete(create_job_states())


class TestGetVersion:
    """Test class for GetVersion RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment with service instance."""
        self.service = services.ManagedJobsServiceImpl()

    def test_get_version_success(self):
        """Test that GetVersion returns the correct controller version."""
        request = managed_jobsv1_pb2.GetVersionRequest()
        context_mock = mock.Mock()

        response = self.service.GetVersion(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.GetVersionResponse)
        assert response.controller_version == constants.SKYLET_VERSION
        context_mock.abort.assert_not_called()


class TestGetAllJobIdsByName:
    """Test class for GetAllJobIdsByName RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self, _seed_test_jobs):
        """Setup test environment with seeded data."""
        self.job_ids = _seed_test_jobs
        self.service = services.ManagedJobsServiceImpl()

    def test_get_by_name_a(self):
        """Test getting jobs by name 'a'."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(job_name='a')
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        expected_ids = [
            self.job_ids['job_id1'],
            self.job_ids['job_id3'],
        ]
        assert sorted(response.job_ids) == sorted(expected_ids)

    def test_get_by_name_b(self):
        """Test getting jobs by name 'b'."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(job_name='b')
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        expected_ids = [self.job_ids['job_id2']]
        assert response.job_ids == expected_ids

    def test_get_by_name_d_with_pool(self):
        """Test getting jobs by name 'd' which has a pool."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(job_name='d')
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        expected_ids = [self.job_ids['job_id4']]
        assert response.job_ids == expected_ids

    def test_get_all_jobs(self):
        """Test getting all jobs when no name is specified."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest()
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        expected_ids = [
            self.job_ids['job_id1'],
            self.job_ids['job_id2'],
            self.job_ids['job_id3'],
            self.job_ids['job_id4'],
        ]
        assert sorted(response.job_ids) == sorted(expected_ids)

    def test_non_existent_name(self):
        """Test getting jobs by non-existent name."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(
            job_name='nonexistent')
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        assert response.job_ids == []

    def test_empty_name(self):
        """Test with empty string as job name."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(job_name='')
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        # Empty string should be treated differently from None, but based on the
        # implementation in state.py, empty string would match nothing
        assert response.job_ids == []

    def test_case_sensitive_matching(self):
        """Test that job name matching is case sensitive."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(
            job_name='A')  # uppercase
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        # Should not match 'A' since matching is case sensitive
        assert response.job_ids == []

    def test_response_ordering(self):
        """Test that job IDs are returned in descending order (most recent first)."""
        request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest()
        response = self.service.GetAllJobIdsByName(request, mock.Mock())

        # According to state.py, results should be ordered by spot_job_id.desc()
        # So newer job IDs (higher numbers) should come first
        job_ids = list(response.job_ids)
        assert job_ids == sorted(job_ids, reverse=True)

    def test_database_error_handling(self):
        """Test error handling when database operation fails."""
        # Mock the state function to raise an exception
        with mock.patch(
                'sky.jobs.state.get_all_job_ids_by_name') as mock_get_job_ids:
            mock_get_job_ids.side_effect = Exception(
                "Database connection failed")

            request = managed_jobsv1_pb2.GetAllJobIdsByNameRequest(job_name='a')
            context_mock = mock.Mock()

            # Should call context.abort with INTERNAL error
            self.service.GetAllJobIdsByName(request, context_mock)
            context_mock.abort.assert_called_once()

            # Check that abort was called with INTERNAL status and error message
            call_args = context_mock.abort.call_args
            assert len(call_args[0]) == 2  # status_code, message
            status_code, error_message = call_args[0]
            assert status_code == grpc.StatusCode.INTERNAL
            assert "Database connection failed" in error_message


class TestGetJobTable:
    """Test class for GetJobTable RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self, _seed_test_jobs):
        """Setup test environment with seeded data."""
        self.job_ids = _seed_test_jobs
        self.service = services.ManagedJobsServiceImpl()

    def test_get_job_table_basic(self):
        """Test basic GetJobTable functionality - should return all jobs with minimal request."""
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=['ws1', 'ws2']  # Include both workspaces
        )
        context_mock = mock.Mock()

        response = self.service.GetJobTable(request, context_mock)
        assert isinstance(response, managed_jobsv1_pb2.GetJobTableResponse)
        context_mock.abort.assert_not_called()
        assert len(response.jobs) == 4
        assert response.total == 4
        assert response.total_no_filter == 4

        # Validate that job IDs match our seeded jobs
        returned_job_ids = [job.job_id for job in response.jobs]
        expected_job_ids = [
            self.job_ids['job_id1'], self.job_ids['job_id2'],
            self.job_ids['job_id3'], self.job_ids['job_id4']
        ]
        assert sorted(returned_job_ids) == sorted(expected_job_ids)

        # Validate exact job info for a specific job
        # Find the job with job_id1 to make the test deterministic
        job_id1 = self.job_ids['job_id1']
        target_job = next(job for job in response.jobs if job.job_id == job_id1)

        # Assert all properties (job_id1 is in PENDING state)
        assert target_job.job_id == job_id1
        assert target_job.task_id == 0
        assert target_job.job_name == 'a'
        assert target_job.task_name == 'task0'
        assert target_job.job_duration == 0.0  # Not started yet
        assert target_job.status == state.ManagedJobStatus.PENDING.to_protobuf()
        assert target_job.schedule_state == state.ManagedJobScheduleState.INACTIVE.to_protobuf(
        )
        assert target_job.resources == '{}'
        assert target_job.cluster_resources == '-'
        assert target_job.cluster_resources_full == '-'
        assert target_job.cloud == '-'
        assert target_job.region == '-'
        assert target_job.infra == '-'
        assert len(target_job.accelerators) == 0
        assert target_job.recovery_count == 0
        assert target_job.metadata == {}

        # Optional fields (from proto)
        assert target_job.workspace == 'ws1'
        assert target_job.details == ''
        assert target_job.failure_reason == ''
        assert target_job.user_name == ''
        assert target_job.user_hash == ''
        assert target_job.submitted_at == 0.0
        assert target_job.start_at == 0.0
        assert target_job.end_at == 0.0
        assert target_job.user_yaml == ''
        assert target_job.entrypoint == 'ep1'
        assert target_job.pool == ''
        assert target_job.pool_hash == ''

        # Validate that workspaces are correct
        workspaces = [job.workspace for job in response.jobs]
        assert 'ws1' in workspaces
        assert 'ws2' in workspaces

    def test_get_job_table_different_states(self):
        """Test that our seeded jobs are in different states as expected."""
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=['ws1', 'ws2'])
        context_mock = mock.Mock()

        response = self.service.GetJobTable(request, context_mock)
        assert len(response.jobs) == 4

        # Validate that jobs are in expected states
        job_statuses = {job.job_id: job.status for job in response.jobs}
        assert job_statuses[self.job_ids[
            'job_id1']] == state.ManagedJobStatus.PENDING.to_protobuf()
        assert job_statuses[self.job_ids[
            'job_id2']] == state.ManagedJobStatus.STARTING.to_protobuf()
        assert job_statuses[self.job_ids[
            'job_id3']] == state.ManagedJobStatus.RUNNING.to_protobuf()
        assert job_statuses[self.job_ids[
            'job_id4']] == state.ManagedJobStatus.SUCCEEDED.to_protobuf()

        job_data = {job.job_id: job for job in response.jobs}

        # STARTING, RUNNING, SUCCEEDED jobs should have submitted_at > 0
        # PENDING job should have submitted_at = 0.0
        assert job_data[self.job_ids['job_id1']].submitted_at == 0.0
        assert job_data[self.job_ids['job_id2']].submitted_at > 0
        assert job_data[self.job_ids['job_id3']].submitted_at > 0
        assert job_data[self.job_ids['job_id4']].submitted_at > 0

        # RUNNING and SUCCEEDED jobs should have start_at > 0
        assert job_data[self.job_ids['job_id1']].start_at == 0.0
        assert job_data[self.job_ids['job_id2']].start_at == 0.0
        assert job_data[self.job_ids['job_id3']].start_at > 0
        assert job_data[self.job_ids['job_id4']].start_at > 0

        # Only SUCCEEDED job should have end_at > 0
        assert job_data[self.job_ids['job_id1']].end_at == 0.0
        assert job_data[self.job_ids['job_id2']].end_at == 0.0
        assert job_data[self.job_ids['job_id3']].end_at == 0.0
        assert job_data[self.job_ids['job_id4']].end_at > 0


class TestCancelJobsById:
    """Test class for CancelJobsById RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self, _seed_test_jobs):
        """Setup test environment with service instance and job data."""
        self.service = services.ManagedJobsServiceImpl()
        self.job_ids = _seed_test_jobs

    def test_cancel_jobs_by_id_success(self):
        """Test successful job cancellation by ID using real seed data."""
        # Use actual job IDs from seed data - cancel PENDING and STARTING jobs (non-terminal)
        job_ids_to_cancel = [self.job_ids['job_id1'], self.job_ids['job_id2']
                            ]  # PENDING and STARTING jobs

        request = managed_jobsv1_pb2.CancelJobsByIdRequest(
            job_ids=managed_jobsv1_pb2.JobIds(ids=job_ids_to_cancel),
            user_hash=None,
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        response = self.service.CancelJobsById(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsByIdResponse)
        assert response.message.lower(
        ) == f"jobs with ids {job_ids_to_cancel[0]}, {job_ids_to_cancel[1]} are scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_id_validation_error(self):
        """Test validation error when user_hash is missing."""
        request = managed_jobsv1_pb2.CancelJobsByIdRequest(
            job_ids=None,
            all_users=False,
            user_hash=None,
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        self.service.CancelJobsById(request, context_mock)

        context_mock.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            'user_hash is required when job_ids is None and all_users is False')


class TestCancelJobByName:
    """Test class for CancelJobByName RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self, _seed_test_jobs):
        """Setup test environment with service instance and job data."""
        self.service = services.ManagedJobsServiceImpl()
        self.job_ids = _seed_test_jobs

    def test_cancel_job_by_name_multiple_jobs(self):
        """Test cancelling jobs by name 'a' which matches multiple jobs."""
        # Name 'a' matches job_id1 (PENDING) and job_id3 (RUNNING)
        job_name = "a"

        request = managed_jobsv1_pb2.CancelJobByNameRequest(
            job_name=job_name, current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobByName(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobByNameResponse)
        # Should indicate multiple jobs found (actual message shows job IDs)
        assert "multiple running jobs found with name 'a'" in response.message.lower(
        )
        assert f"job ids: [{self.job_ids['job_id3']}, {self.job_ids['job_id1']}]" in response.message.lower(
        )
        context_mock.abort.assert_not_called()

    def test_cancel_job_by_name_single_job(self):
        """Test cancelling job by name 'b' which matches only one job."""
        # Name 'b' matches only job_id2 (STARTING)
        job_name = "b"

        request = managed_jobsv1_pb2.CancelJobByNameRequest(
            job_name=job_name, current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobByName(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobByNameResponse)
        # Should indicate job to cancel
        assert response.message.lower(
        ) == f"'b' job with id {self.job_ids['job_id2']} is scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_job_by_name_wrong_workspace(self):
        """Test cancelling job by name 'b' which matches only one job."""
        # Name 'b' matches only job_id2 (STARTING), but it's in workspace 'ws1'
        # The cancel will skip it due to workspace mismatch
        job_name = "b"

        request = managed_jobsv1_pb2.CancelJobByNameRequest(
            job_name=job_name, current_workspace="ws9")
        context_mock = mock.Mock()

        response = self.service.CancelJobByName(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobByNameResponse)
        # Should indicate no job to cancel due to workspace mismatch
        assert "no job to cancel" in response.message.lower()
        assert f"job with id {self.job_ids['job_id2']} is skipped as they are not in the active workspace 'ws9'" in response.message.lower(
        )
        context_mock.abort.assert_not_called()


class TestCancelJobsByPool:
    """Test class for CancelJobsByPool RPC method."""

    @pytest.fixture(autouse=True)
    def setup(self, _seed_test_jobs):
        """Setup test environment with service instance and job data."""
        self.service = services.ManagedJobsServiceImpl()
        self.job_ids = _seed_test_jobs

    def test_cancel_jobs_by_pool_success(self):
        """Test successful job cancellation by pool using real seed data."""
        # Use the pool name from seed data
        # job_id1 has pool='test-pool' and is PENDING
        # job_id4 has pool='test-pool' and is SUCCEEDED
        pool_name = "test-pool"

        request = managed_jobsv1_pb2.CancelJobsByPoolRequest(
            pool_name=pool_name, current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobsByPool(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsByPoolResponse)
        # Only job_id1 should be cancelled, as job_id4 is already SUCCEEDED
        assert response.message.lower(
        ) == f"job with id {self.job_ids['job_id1']} is scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_nonexistent_pool(self):
        """Test cancelling jobs by a pool that doesn't exist."""
        pool_name = "nonexistent-pool"

        request = managed_jobsv1_pb2.CancelJobsByPoolRequest(
            pool_name=pool_name, current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobsByPool(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsByPoolResponse)
        # Should indicate no jobs found in the pool
        assert response.message.lower(
        ) == f"no running job found in pool '{pool_name}'."
        context_mock.abort.assert_not_called()
