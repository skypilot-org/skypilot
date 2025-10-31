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
        # TODO(kevin): Mock user_name and user_hash too.

        # Job 1: PENDING state (just created)
        job_id1 = state.set_job_info_without_job_id(name='a',
                                                    workspace='ws1',
                                                    entrypoint='ep1',
                                                    pool='test-pool',
                                                    pool_hash='hash123',
                                                    user_hash='abcd1234')
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
                                                    pool_hash=None,
                                                    user_hash='abcd1234')
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
                                                    pool_hash=None,
                                                    user_hash='abcd1234')
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
                                                    pool_hash='hash123',
                                                    user_hash='abcd1234')
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

        # Empty string should be treated differently from None
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
            accessible_workspaces=managed_jobsv1_pb2.Workspaces(
                workspaces=['ws1', 'ws2'])  # Include both workspaces
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
        assert target_job.workspace == 'ws1'
        assert not target_job.HasField('details')
        assert not target_job.HasField('failure_reason')
        assert not target_job.HasField('user_name')
        assert target_job.user_hash == 'abcd1234'
        assert not target_job.HasField('submitted_at')
        assert not target_job.HasField('start_at')
        assert not target_job.HasField('end_at')
        assert not target_job.HasField('user_yaml')
        assert target_job.entrypoint == 'ep1'
        assert target_job.pool == 'test-pool'
        assert target_job.pool_hash == 'hash123'

        # Validate that workspaces are correct
        workspaces = [job.workspace for job in response.jobs]
        assert 'ws1' in workspaces
        assert 'ws2' in workspaces

    def test_get_job_table_no_accessible_workspaces(self):
        """Test basic GetJobTable functionality without specified accessible
         workspaces - should return all jobs."""
        request = managed_jobsv1_pb2.GetJobTableRequest()
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
        assert target_job.workspace == 'ws1'
        assert not target_job.HasField('details')
        assert not target_job.HasField('failure_reason')
        assert not target_job.HasField('user_name')
        assert target_job.user_hash == 'abcd1234'
        assert not target_job.HasField('submitted_at')
        assert not target_job.HasField('start_at')
        assert not target_job.HasField('end_at')
        assert not target_job.HasField('user_yaml')
        assert target_job.entrypoint == 'ep1'
        assert target_job.pool == 'test-pool'
        assert target_job.pool_hash == 'hash123'

        # Validate that workspaces are correct
        workspaces = [job.workspace for job in response.jobs]
        assert 'ws1' in workspaces
        assert 'ws2' in workspaces

    def test_get_job_table_empty_accessible_workspaces(self):
        """Test basic GetJobTable functionality with empty accessible
         workspaces - should return no jobs."""
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=managed_jobsv1_pb2.Workspaces(workspaces=[]))
        context_mock = mock.Mock()

        response = self.service.GetJobTable(request, context_mock)
        assert isinstance(response, managed_jobsv1_pb2.GetJobTableResponse)
        context_mock.abort.assert_not_called()
        assert len(response.jobs) == 0
        assert response.total == 0
        assert response.total_no_filter == 4

    def test_get_job_table_different_states(self):
        """Test that our seeded jobs are in different states as expected."""
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=managed_jobsv1_pb2.Workspaces(
                workspaces=['ws1', 'ws2']))
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

    def test_get_job_table_with_fields_filter(self):
        """Test GetJobTable with specific fields filter - other fields should be empty/default."""
        # Request only specific fields: job_name, workspace, pool
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=managed_jobsv1_pb2.Workspaces(
                workspaces=['ws1', 'ws2']),
            fields=managed_jobsv1_pb2.Fields(
                fields=['job_name', 'workspace', 'pool']))
        context_mock = mock.Mock()

        response = self.service.GetJobTable(request, context_mock)
        assert isinstance(response, managed_jobsv1_pb2.GetJobTableResponse)
        context_mock.abort.assert_not_called()
        assert len(response.jobs) == 4

        # Validate that requested fields have values
        for job in response.jobs:
            # job_id and status are always included (required fields)
            assert job.job_id > 0
            # status should be set to a valid value (not None)
            assert job.status is not None
            # Requested fields should have values
            assert job.job_name in ['a', 'b', 'd']
            assert job.workspace in ['ws1', 'ws2']
            # pool can be empty for some jobs, but should be set for jobs with pools
            if job.job_id in [self.job_ids['job_id1'], self.job_ids['job_id4']]:
                assert job.pool == 'test-pool'
            else:
                assert job.pool == ''

            # Non-requested fields should be empty/default
            # These fields should NOT have meaningful values since they weren't requested
            assert job.task_id == 0  # default int value
            assert job.job_duration == 0.0  # default float value
            assert job.resources == ''
            assert job.cluster_resources == ''
            assert job.cluster_resources_full == ''
            assert job.cloud == ''
            assert job.region == ''
            assert job.infra == ''
            assert job.recovery_count == 0
            assert len(job.accelerators) == 0
            assert len(job.metadata) == 0
            assert not job.HasField('details')
            assert not job.HasField('failure_reason')
            assert not job.HasField('user_name')
            assert not job.HasField('user_hash')
            assert not job.HasField('submitted_at')
            assert not job.HasField('start_at')
            assert not job.HasField('end_at')
            assert not job.HasField('user_yaml')
            assert not job.HasField('entrypoint')
            assert not job.HasField('pool_hash')

    def test_get_job_table_with_empty_fields_filter(self):
        """Test GetJobTable with empty fields list - should return minimal fields (job_id, status)."""
        request = managed_jobsv1_pb2.GetJobTableRequest(
            accessible_workspaces=managed_jobsv1_pb2.Workspaces(
                workspaces=['ws1', 'ws2']),
            fields=managed_jobsv1_pb2.Fields(fields=[]))
        context_mock = mock.Mock()

        response = self.service.GetJobTable(request, context_mock)
        assert isinstance(response, managed_jobsv1_pb2.GetJobTableResponse)
        context_mock.abort.assert_not_called()
        assert len(response.jobs) == 4

        # Even with empty fields, job_id and status are always returned
        for job in response.jobs:
            assert job.job_id > 0
            # status should be set to a valid value (not None)
            assert job.status is not None


class TestCancelJobs:
    """Test class for CancelJobs RPC method."""

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

        request = managed_jobsv1_pb2.CancelJobsRequest(
            job_ids=managed_jobsv1_pb2.JobIds(ids=job_ids_to_cancel),
            user_hash=None,
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        assert response.message.lower(
        ) == f"jobs with ids {job_ids_to_cancel[0]}, {job_ids_to_cancel[1]} are scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_validation_error_no_criteria(self):
        """Test validation error when no criteria is specified."""
        request = managed_jobsv1_pb2.CancelJobsRequest(current_workspace="ws1",)
        context_mock = mock.Mock()

        # Create custom exception that won't be caught by general except block
        class GrpcAbortException(BaseException):
            """Custom exception to simulate gRPC abort behavior."""
            pass

        # Configure abort to raise exception like the real gRPC context
        context_mock.abort.side_effect = GrpcAbortException()

        with pytest.raises(GrpcAbortException):
            self.service.CancelJobs(request, context_mock)

        context_mock.abort.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT,
            'exactly one cancellation criteria must be specified.')

    def test_cancel_jobs_by_name_multiple_jobs(self):
        """Test cancelling jobs by name 'a' which matches multiple jobs."""
        # Name 'a' matches job_id1 (PENDING) and job_id3 (RUNNING)
        job_name = "a"

        request = managed_jobsv1_pb2.CancelJobsRequest(job_name=job_name,
                                                       current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Should indicate multiple jobs found (actual message shows job IDs)
        assert "multiple running jobs found with name 'a'" in response.message.lower(
        )
        assert f"job ids: [{self.job_ids['job_id3']}, {self.job_ids['job_id1']}]" in response.message.lower(
        )
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_name_single_job(self):
        """Test cancelling job by name 'b' which matches only one job."""
        # Name 'b' matches only job_id2 (STARTING)
        job_name = "b"

        request = managed_jobsv1_pb2.CancelJobsRequest(job_name=job_name,
                                                       current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Should indicate job to cancel
        assert response.message.lower(
        ) == f"'b' job with id {self.job_ids['job_id2']} is scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_name_wrong_workspace(self):
        """Test cancelling job by name 'b' which matches only one job."""
        # Name 'b' matches only job_id2 (STARTING), but it's in workspace 'ws1'
        # The cancel will skip it due to workspace mismatch
        job_name = "b"

        request = managed_jobsv1_pb2.CancelJobsRequest(job_name=job_name,
                                                       current_workspace="ws9")
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Should indicate no job to cancel due to workspace mismatch
        assert "no job to cancel" in response.message.lower()
        assert f"job with id {self.job_ids['job_id2']} is skipped as they are not in the active workspace 'ws9'" in response.message.lower(
        )
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_pool_success(self):
        """Test successful job cancellation by pool using real seed data."""
        # Use the pool name from seed data
        # job_id1 has pool='test-pool' and is PENDING
        # job_id4 has pool='test-pool' and is SUCCEEDED
        pool_name = "test-pool"

        request = managed_jobsv1_pb2.CancelJobsRequest(pool_name=pool_name,
                                                       current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Only job_id1 should be cancelled, as job_id4 is already SUCCEEDED
        assert response.message.lower(
        ) == f"job with id {self.job_ids['job_id1']} is scheduled to be cancelled."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_by_nonexistent_pool(self):
        """Test cancelling jobs by a pool that doesn't exist."""
        pool_name = "nonexistent-pool"

        request = managed_jobsv1_pb2.CancelJobsRequest(pool_name=pool_name,
                                                       current_workspace="ws1")
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Should indicate no jobs found in the pool
        assert response.message.lower(
        ) == f"no running job found in pool '{pool_name}'."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_all_users_true(self):
        """Test cancelling all jobs for all users (--all-users)."""
        request = managed_jobsv1_pb2.CancelJobsRequest(
            all_users=True,
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Jobs 1, 2 should be cancelled (in workspace 'ws1', non-terminal states)
        # Job 3 is in workspace 'ws2' so gets skipped
        # Job 4 is SUCCEEDED so should not be cancelled
        expected_jobs = [self.job_ids['job_id1'], self.job_ids['job_id2']]
        assert response.message.lower(
        ) == f"jobs with ids {expected_jobs[0]}, {expected_jobs[1]} are scheduled to be cancelled. job with id {self.job_ids['job_id3']} is skipped as they are not in the active workspace 'ws1'. check the workspace of the job with: sky jobs queue"
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_all_users_false(self):
        """Test cancelling all jobs for current user (--all)."""
        request = managed_jobsv1_pb2.CancelJobsRequest(
            all_users=False,
            user_hash="test_user_hash",
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        # Test data doesn't have jobs with specific user_hash, so expect no jobs found
        assert response.message.lower() == "no job to cancel."
        context_mock.abort.assert_not_called()

    def test_cancel_jobs_all_users_validation_error(self):
        """Test validation error when all_users=True but user_hash is provided (removed overly strict validation)."""
        # This test is now obsolete since we allow user_hash with all_users=True
        # The validation was removed as it was overly restrictive
        request = managed_jobsv1_pb2.CancelJobsRequest(
            all_users=True,
            user_hash="should_not_error_anymore",
            current_workspace="ws1",
        )
        context_mock = mock.Mock()

        response = self.service.CancelJobs(request, context_mock)

        # Should succeed now, not error
        assert isinstance(response, managed_jobsv1_pb2.CancelJobsResponse)
        assert "are scheduled to be cancelled" in response.message.lower()
        context_mock.abort.assert_not_called()
