"""Unit tests for sky.jobs.utils functions."""
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest

from sky import exceptions
from sky.jobs import state as managed_job_state
from sky.jobs import utils as jobs_utils


class TestClusterHandleNotRequired:

    def test_returns_true_when_no_cluster_handle_fields(self):
        """Test that function returns True when fields don't require cluster handle."""
        fields = ['job_id', 'task_name', 'status']
        assert jobs_utils._cluster_handle_not_required(fields) is True

    def test_returns_false_when_cluster_resources_present(self):
        """Test that function returns False when cluster_resources is in fields."""
        fields = ['job_id', 'cluster_resources', 'status']
        assert jobs_utils._cluster_handle_not_required(fields) is False

    def test_returns_false_when_cloud_present(self):
        """Test that function returns False when cloud is in fields."""
        fields = ['job_id', 'cloud', 'status']
        assert jobs_utils._cluster_handle_not_required(fields) is False

    def test_returns_false_when_region_present(self):
        """Test that function returns False when region is in fields."""
        fields = ['job_id', 'region', 'status']
        assert jobs_utils._cluster_handle_not_required(fields) is False

    def test_returns_false_when_multiple_cluster_handle_fields(self):
        """Test that function returns False when multiple cluster handle fields present."""
        fields = ['job_id', 'cloud', 'region', 'zone']
        assert jobs_utils._cluster_handle_not_required(fields) is False

    def test_empty_fields_list(self):
        """Test with empty fields list."""
        fields = []
        assert jobs_utils._cluster_handle_not_required(fields) is True


class TestUpdateFields:

    def test_always_includes_status_and_job_id(self):
        """Test that status and job_id are always added if not present."""
        fields = ['task_name']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'status' in updated_fields
        assert 'job_id' in updated_fields

    def test_does_not_duplicate_status_and_job_id(self):
        """Test that status and job_id are not duplicated."""
        fields = ['status', 'job_id', 'task_name']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert updated_fields.count('status') == 1
        assert updated_fields.count('job_id') == 1

    def test_adds_user_hash_when_user_name_present(self):
        """Test that user_hash is added when user_name is present."""
        fields = ['user_name']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'user_hash' in updated_fields

    def test_does_not_duplicate_user_hash(self):
        """Test that user_hash is not duplicated if already present."""
        fields = ['user_name', 'user_hash']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert updated_fields.count('user_hash') == 1

    def test_adds_dependencies_for_job_duration(self):
        """Test that last_recovered_at and end_at are added when job_duration is present."""
        fields = ['job_duration']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'last_recovered_at' in updated_fields
        assert 'end_at' in updated_fields

    def test_adds_task_name_when_job_name_present(self):
        """Test that task_name is added when job_name is present."""
        fields = ['job_name']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'task_name' in updated_fields

    def test_adds_dependencies_for_details(self):
        """Test that schedule_state, priority, and failure_reason are added when details is present."""
        fields = ['details']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'schedule_state' in updated_fields
        assert 'priority' in updated_fields
        assert 'failure_reason' in updated_fields

    def test_adds_original_user_yaml_path_for_user_yaml(self):
        """Test that original_user_yaml_path is added when user_yaml is present."""
        fields = ['user_yaml']
        updated_fields, _ = jobs_utils._update_fields(fields)
        assert 'original_user_yaml_path' in updated_fields

    def test_removes_non_db_fields(self):
        """Test that non-DB fields are removed from updated_fields."""
        fields = [
            'job_id', 'cluster_resources', 'cloud', 'user_name', 'details'
        ]
        updated_fields, _ = jobs_utils._update_fields(fields)
        # These are _NON_DB_FIELDS and should be removed
        assert 'cluster_resources' not in updated_fields
        assert 'user_name' not in updated_fields
        assert 'details' not in updated_fields
        # But job_id should remain
        assert 'job_id' in updated_fields
        # 'cloud' is a non-DB handle field, but because a cluster handle is
        # required here it is re-added as a DB column for the cached infra
        # fallback (see _update_fields).
        assert 'cloud' in updated_fields

    def test_cluster_handle_required_false_when_no_handle_fields(self):
        """Test that cluster_handle_required is False when no cluster handle fields."""
        fields = ['job_id', 'status']
        _, cluster_handle_required = jobs_utils._update_fields(fields)
        assert cluster_handle_required is False

    def test_cluster_handle_required_true_when_handle_fields_present(self):
        """Test that cluster_handle_required is True when cluster handle fields present."""
        fields = ['job_id', 'cluster_resources']
        _, cluster_handle_required = jobs_utils._update_fields(fields)
        assert cluster_handle_required is True

    def test_adds_task_name_and_current_cluster_name_when_handle_required(self):
        """Test that task_name and current_cluster_name are added when cluster handle is required."""
        fields = ['job_id', 'cloud']  # cloud requires cluster handle
        updated_fields, cluster_handle_required = jobs_utils._update_fields(
            fields)
        assert cluster_handle_required is True
        assert 'task_name' in updated_fields
        assert 'current_cluster_name' in updated_fields

    def test_does_not_modify_original_list(self):
        """Test that the original fields list is not modified."""
        fields = ['job_id']
        original_fields = fields.copy()
        jobs_utils._update_fields(fields)
        assert fields == original_fields

    def test_complex_scenario_with_multiple_dependencies(self):
        """Test a complex scenario with multiple field dependencies."""
        fields = ['job_name', 'user_name', 'job_duration', 'details', 'cloud']
        updated_fields, cluster_handle_required = jobs_utils._update_fields(
            fields)

        # From job_name
        assert 'task_name' in updated_fields
        # From user_name
        assert 'user_hash' in updated_fields
        # From job_duration
        assert 'last_recovered_at' in updated_fields
        assert 'end_at' in updated_fields
        # From details
        assert 'schedule_state' in updated_fields
        assert 'priority' in updated_fields
        assert 'failure_reason' in updated_fields
        # From cloud (cluster handle required)
        assert 'current_cluster_name' in updated_fields
        # Always added
        assert 'status' in updated_fields
        assert 'job_id' in updated_fields
        # 'cloud' is re-added as a DB column for the cached infra fallback
        # since a cluster handle is required.
        assert 'cloud' in updated_fields
        # Should be true due to cloud
        assert cluster_handle_required is True

    def test_adds_cached_infra_fields_when_handle_required(self):
        """Cached infra/resources fields are re-added for the fallback.

        When a cluster handle is required, the last-cached infra columns
        ('cloud'/'region'/'zone') and the requested 'resources' string must be
        selected from the DB so get_managed_job_queue can fall back to them for
        finished jobs whose cluster handle is gone.
        """
        fields = ['job_id', 'cluster_resources']
        updated_fields, cluster_handle_required = jobs_utils._update_fields(
            fields)
        assert cluster_handle_required is True
        # cluster_resources itself is a non-DB field and is removed.
        assert 'cluster_resources' not in updated_fields
        # The DB-backed fallback fields are present.
        for field in ('cloud', 'region', 'zone', 'resources'):
            assert field in updated_fields

    def test_does_not_add_cached_infra_fields_when_handle_not_required(self):
        """Fallback fields are not force-added when no handle is required."""
        fields = ['job_id', 'status']
        updated_fields, cluster_handle_required = jobs_utils._update_fields(
            fields)
        assert cluster_handle_required is False
        for field in ('cloud', 'region', 'zone', 'resources'):
            assert field not in updated_fields


class TestGetManagedJobQueue:

    def _make_test_job(self, job_id: int, **kwargs) -> Dict[str, Any]:
        """Create a test job with default values."""
        defaults = {
            'job_id': job_id,
            'task_name': f'task_{job_id}',
            'job_name': f'job_{job_id}',
            'workspace': 'default',
            'pool': 'default',
            'status': managed_job_state.ManagedJobStatus.PENDING,
            'schedule_state': managed_job_state.ManagedJobScheduleState.WAITING,
            'priority': 1,
            'user_hash': 'user1',
            'last_recovered_at': time.time(),
            'job_duration': 0,
            'end_at': None,
            'failure_reason': None,
        }
        defaults.update(kwargs)
        return defaults

    def _patch_managed_job_state(self, monkeypatch: pytest.MonkeyPatch,
                                 jobs: List[Dict[str, Any]]):
        """Patch managed_job_state functions for testing."""

        def fake_get_managed_jobs_total():
            return len(jobs)

        def fake_get_status_count_with_filters(**kwargs):
            # Simple implementation for testing
            status_counts = {}
            for job in jobs:
                status_value = job['status'].value
                status_counts[status_value] = status_counts.get(
                    status_value, 0) + 1
            return status_counts

        def fake_get_managed_jobs_with_filters(**kwargs):
            # Return all jobs for simplicity, filtering would be tested separately
            return jobs, len(jobs)

        def fake_get_managed_jobs_highest_priority():
            if not jobs:
                return 0
            return max(job.get('priority', 0) for job in jobs)

        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_managed_jobs_total',
                            fake_get_managed_jobs_total)
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_status_count_with_filters',
                            fake_get_status_count_with_filters)
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_managed_jobs_with_filters',
                            fake_get_managed_jobs_with_filters)
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_managed_jobs_highest_priority',
                            fake_get_managed_jobs_highest_priority)

    def _patch_global_user_state(self, monkeypatch: pytest.MonkeyPatch):
        """Patch global_user_state for testing."""

        def fake_get_cluster_name_to_handle_map(is_managed: bool = True):
            return {}

        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_cluster_name_to_handle_map',
                            fake_get_cluster_name_to_handle_map)

    def test_basic_functionality_without_filters(self, monkeypatch):
        """Test basic get_managed_job_queue without any filters."""
        jobs = [
            self._make_test_job(1),
            self._make_test_job(2),
            self._make_test_job(3),
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        assert 'jobs' in result
        assert 'total' in result
        assert 'total_no_filter' in result
        assert 'status_counts' in result
        assert len(result['jobs']) == 3
        assert result['total'] == 3
        assert result['total_no_filter'] == 3

    def test_job_duration_calculation_for_running_job(self, monkeypatch):
        """Test job_duration calculation for a running job."""
        current_time = time.time()
        jobs = [
            self._make_test_job(1,
                                last_recovered_at=current_time - 100,
                                job_duration=50,
                                end_at=None)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        # For running job: duration = time.time() - (last_recovered_at - job_duration)
        expected_duration = current_time - (current_time - 100 - 50)
        assert abs(job['job_duration'] -
                   expected_duration) < 2  # Allow 2s tolerance

    def test_job_duration_calculation_for_recovering_job(self, monkeypatch):
        """Test job_duration calculation for a recovering job."""
        jobs = [
            self._make_test_job(
                1,
                status=managed_job_state.ManagedJobStatus.RECOVERING,
                job_duration=60)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        # For recovering job, duration should be exactly job_duration
        assert job['job_duration'] == 60

    def test_job_duration_calculation_for_finished_job(self, monkeypatch):
        """Test job_duration calculation for a finished job."""
        current_time = time.time()
        jobs = [
            self._make_test_job(1,
                                last_recovered_at=current_time - 200,
                                job_duration=50,
                                end_at=current_time - 50)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        # For finished job: duration = end_at - (last_recovered_at - job_duration)
        expected_duration = (current_time - 50) - (current_time - 200 - 50)
        assert job['job_duration'] == expected_duration

    def test_finished_job_falls_back_to_cached_infra(self, monkeypatch):
        """A finished job with no live handle shows last-cached infra/resources.

        When the cluster handle is gone (e.g. the job finished and the cluster
        was torn down), infra/resources should fall back to the values
        persisted in the jobs DB instead of a bare '-'.
        """
        jobs = [
            self._make_test_job(
                1,
                status=managed_job_state.ManagedJobStatus.SUCCEEDED,
                cloud='AWS',
                region='us-east-1',
                zone='us-east-1a',
                resources='1x[Spot]A100:8',
            )
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)  # empty handle map

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert job['cloud'] == 'AWS'
        assert job['region'] == 'us-east-1'
        assert job['zone'] == 'us-east-1a'
        assert job['infra'] == 'AWS (us-east-1a)'
        assert job['cluster_resources'] == '1x[Spot]A100:8'
        assert job['cluster_resources_full'] == '1x[Spot]A100:8'

    def test_finished_job_without_cached_infra_shows_dash(self, monkeypatch):
        """Legacy finished jobs without persisted infra still show '-'."""
        jobs = [
            self._make_test_job(
                1, status=managed_job_state.ManagedJobStatus.SUCCEEDED)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)  # empty handle map

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert job['cloud'] == '-'
        assert job['region'] == '-'
        assert job['zone'] == '-'
        assert job['infra'] == '-'
        assert job['cluster_resources'] == '-'
        assert job['cluster_resources_full'] == '-'

    def test_unlaunched_job_does_not_show_requested_resources(
            self, monkeypatch):
        """A job that never launched does not show requested resources.

        The cached resources fallback only applies when the job was actually
        launched (infra persisted); a PENDING job with a requested resources
        string should still show '-' for cluster resources.
        """
        jobs = [
            self._make_test_job(
                1,
                status=managed_job_state.ManagedJobStatus.PENDING,
                resources='1x[CPU:1+]',
            )
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)  # empty handle map

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert job['cluster_resources'] == '-'
        assert job['cluster_resources_full'] == '-'
        assert job['infra'] == '-'

    def test_status_converted_to_string(self, monkeypatch):
        """Test that status is converted from enum to string."""
        jobs = [self._make_test_job(1)]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert isinstance(job['status'], str)
        assert job['status'] == 'PENDING'

    def test_schedule_state_converted_to_string(self, monkeypatch):
        """Test that schedule_state is converted from enum to string."""
        jobs = [self._make_test_job(1)]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert isinstance(job['schedule_state'], str)
        assert job['schedule_state'] == 'WAITING'

    def test_details_for_alive_backoff_state(self, monkeypatch):
        """Test details generation for ALIVE_BACKOFF schedule state."""
        jobs = [
            self._make_test_job(1,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.ALIVE_BACKOFF)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert 'In backoff, waiting for resources' in job['details']

    def test_details_for_waiting_state_with_higher_priority(self, monkeypatch):
        """Test details generation for WAITING state when blocked by higher priority."""
        jobs = [
            self._make_test_job(1, priority=5),  # Lower priority
            self._make_test_job(2, priority=10),  # Higher priority
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        # Job 1 has lower priority than the highest (10)
        job1 = next(j for j in result['jobs'] if j['job_id'] == 1)
        assert 'Waiting for higher priority jobs to launch' in job1['details']

    def test_details_for_waiting_state_with_same_priority(self, monkeypatch):
        """Test details generation for WAITING state with same priority."""
        jobs = [
            self._make_test_job(1, priority=10),
            self._make_test_job(2, priority=10),
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job1 = next(j for j in result['jobs'] if j['job_id'] == 1)
        assert 'Waiting for other jobs to launch' in job1['details']

    def test_details_combines_state_and_failure_reason(self, monkeypatch):
        """Test that details combines state_details and failure_reason."""
        jobs = [
            self._make_test_job(1,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.ALIVE_BACKOFF,
                                failure_reason='Out of capacity')
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert 'In backoff, waiting for resources' in job['details']
        assert 'Out of capacity' in job['details']

    def test_details_shows_failure_reason_only(self, monkeypatch):
        """Test that details shows failure reason when no state_details."""
        jobs = [
            self._make_test_job(
                1,
                schedule_state=managed_job_state.ManagedJobScheduleState.ALIVE,
                failure_reason='Test failure')
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert job['details'] == 'Failure: Test failure'

    def test_details_is_none_when_no_state_or_failure(self, monkeypatch):
        """Test that details is None when no state_details or failure_reason."""
        jobs = [
            self._make_test_job(
                1,
                schedule_state=managed_job_state.ManagedJobScheduleState.ALIVE,
                failure_reason=None)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        assert job['details'] is None

    def test_with_fields_parameter(self, monkeypatch):
        """Test get_managed_job_queue with fields parameter."""
        jobs = [self._make_test_job(1)]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue(
            fields=['job_id', 'task_name', 'status'])

        assert 'jobs' in result
        assert len(result['jobs']) == 1

    def test_schedule_state_none_when_not_in_fields(self, monkeypatch):
        """Test that schedule_state is None when not requested in fields."""
        jobs = [self._make_test_job(1)]

        def fake_get_managed_jobs_with_filters(**kwargs):
            # Return jobs without schedule_state in updated_fields
            return jobs, len(jobs)

        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_managed_jobs_total', lambda: len(jobs))
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_status_count_with_filters',
                            lambda **kwargs: {'PENDING': 1})
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_managed_jobs_with_filters',
                            fake_get_managed_jobs_with_filters)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue(fields=['job_id', 'status'])

        job = result['jobs'][0]
        assert job['schedule_state'] is None

    def test_empty_jobs_list(self, monkeypatch):
        """Test with empty jobs list."""
        jobs = []
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        assert result['jobs'] == []
        assert result['total'] == 0
        assert result['total_no_filter'] == 0

    def test_job_duration_zero_when_not_started(self, monkeypatch):
        """Test job_duration is 0 when job hasn't started (last_recovered_at not set)."""
        current_time = time.time()
        jobs = [
            self._make_test_job(1,
                                last_recovered_at=0,
                                job_duration=0,
                                end_at=None)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        # When job_submitted_at <= 0, job_duration should be 0
        assert job['job_duration'] == 0

    def test_job_duration_zero_when_submitted_at_negative(self, monkeypatch):
        """Test job_duration is 0 when calculated job_submitted_at is negative."""
        jobs = [
            self._make_test_job(1,
                                last_recovered_at=10,
                                job_duration=20,
                                end_at=None)
        ]
        self._patch_managed_job_state(monkeypatch, jobs)
        self._patch_global_user_state(monkeypatch)

        result = jobs_utils.get_managed_job_queue()

        job = result['jobs'][0]
        # job_submitted_at = 10 - 20 = -10, which is <= 0
        assert job['job_duration'] == 0

    def test_cluster_handle_with_resources(self, monkeypatch):
        """Test cluster information extraction when CloudVmRayResourceHandle exists."""
        from unittest.mock import Mock

        jobs = [
            self._make_test_job(1,
                                task_name='test_task',
                                current_cluster_name='test-cluster')
        ]
        self._patch_managed_job_state(monkeypatch, jobs)

        # Create mock handle with launched_resources
        mock_handle = Mock()
        mock_handle.__class__.__name__ = 'CloudVmRayResourceHandle'
        mock_handle.launched_resources = Mock()
        mock_handle.launched_resources.cloud = Mock(__str__=lambda self: 'AWS')
        mock_handle.launched_resources.region = 'us-west-2'
        mock_handle.launched_resources.zone = 'us-west-2a'
        mock_handle.launched_resources.accelerators = {'V100': 1}
        mock_handle.cached_cluster_info = None

        def fake_get_cluster_name_to_handle_map(is_managed: bool = True):
            return {'test-cluster': mock_handle}

        def fake_get_readable_resources_repr(handle, simplified_only=False):
            if simplified_only:
                return ('1x V100', None)
            return ('1x V100', '1x V100 (AWS)')

        # Patch functions
        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_cluster_name_to_handle_map',
                            fake_get_cluster_name_to_handle_map)
        monkeypatch.setattr(jobs_utils.resources_utils,
                            'get_readable_resources_repr',
                            fake_get_readable_resources_repr)
        monkeypatch.setattr(jobs_utils.backends, 'CloudVmRayResourceHandle',
                            type(mock_handle))

        # Mock InfraInfo
        class MockInfraInfo:

            def __init__(self, cloud, region, zone):
                self.cloud = cloud
                self.region = region
                self.zone = zone

            def formatted_str(self):
                return f'{self.cloud}/{self.region}/{self.zone}'

        monkeypatch.setattr(jobs_utils.infra_utils, 'InfraInfo', MockInfraInfo)

        result = jobs_utils.get_managed_job_queue(
            fields=['job_id', 'cloud', 'region', 'zone', 'cluster_resources'])

        job = result['jobs'][0]
        assert job['cluster_resources'] == '1x V100'
        assert job['cluster_resources_full'] == '1x V100 (AWS)'
        assert job['cloud'] == 'AWS'
        assert job['region'] == 'us-west-2'
        assert job['zone'] == 'us-west-2a'
        assert job['infra'] == 'AWS/us-west-2/us-west-2a'
        assert job['accelerators'] == {'V100': 1}

    def test_cluster_handle_without_valid_handle(self, monkeypatch):
        """Test cluster information defaults when no valid handle exists."""
        jobs = [
            self._make_test_job(1,
                                task_name='test_task',
                                current_cluster_name='nonexistent-cluster')
        ]
        self._patch_managed_job_state(monkeypatch, jobs)

        def fake_get_cluster_name_to_handle_map(is_managed: bool = True):
            return {}  # No handle available

        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_cluster_name_to_handle_map',
                            fake_get_cluster_name_to_handle_map)

        result = jobs_utils.get_managed_job_queue(
            fields=['job_id', 'cloud', 'region', 'cluster_resources'])

        job = result['jobs'][0]
        # When no handle exists, all cluster fields should be '-'
        assert job['cluster_resources'] == '-'
        assert job['cluster_resources_full'] == '-'
        assert job['cloud'] == '-'
        assert job['region'] == '-'
        assert job['zone'] == '-'
        assert job['infra'] == '-'

    def test_cluster_handle_with_none_handle(self, monkeypatch):
        """Test cluster information defaults when handle is None."""
        jobs = [
            self._make_test_job(1,
                                task_name='test_task',
                                current_cluster_name='test-cluster')
        ]
        self._patch_managed_job_state(monkeypatch, jobs)

        def fake_get_cluster_name_to_handle_map(is_managed: bool = True):
            return {'test-cluster': None}  # Handle is None

        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_cluster_name_to_handle_map',
                            fake_get_cluster_name_to_handle_map)

        result = jobs_utils.get_managed_job_queue(
            fields=['job_id', 'cloud', 'region', 'cluster_resources'])

        job = result['jobs'][0]
        # When handle is None, all cluster fields should be '-'
        assert job['cluster_resources'] == '-'
        assert job['cluster_resources_full'] == '-'
        assert job['cloud'] == '-'
        assert job['region'] == '-'
        assert job['zone'] == '-'
        assert job['infra'] == '-'

    def test_cluster_name_generation_when_not_provided(self, monkeypatch):
        """Test that cluster name is generated when current_cluster_name is not set."""
        jobs = [self._make_test_job(42, task_name='my_task')]
        # Remove current_cluster_name to test generation
        if 'current_cluster_name' in jobs[0]:
            del jobs[0]['current_cluster_name']

        self._patch_managed_job_state(monkeypatch, jobs)

        generated_cluster_name = None

        def fake_get_cluster_name_to_handle_map(is_managed: bool = True):
            return {}

        def fake_generate_managed_job_cluster_name(task_name, job_id):
            nonlocal generated_cluster_name
            generated_cluster_name = f'{task_name}-{job_id}'
            return generated_cluster_name

        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_cluster_name_to_handle_map',
                            fake_get_cluster_name_to_handle_map)
        monkeypatch.setattr(jobs_utils, 'generate_managed_job_cluster_name',
                            fake_generate_managed_job_cluster_name)

        result = jobs_utils.get_managed_job_queue(
            fields=['job_id', 'cloud', 'cluster_resources'])

        # Verify that generate_managed_job_cluster_name was called
        assert generated_cluster_name == 'my_task-42'


class TestControllerProcessAlive:

    def test_controller_process_alive_matches_start_time(self, monkeypatch):
        """Process considered alive when start time matches recorded value."""
        expected_pid = 1234
        expected_start = 1700000000.0

        class _FakeProcess:

            def __init__(self, pid):
                assert pid == expected_pid

            def create_time(self):
                return expected_start

            def cmdline(self):
                return ['python', '-m', 'sky.jobs.controller']

            def is_running(self):
                return True

        monkeypatch.setattr(jobs_utils.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(
            pid=expected_pid, started_at=expected_start)
        assert jobs_utils.controller_process_alive(record, legacy_job_id=42)

    def test_controller_process_alive_mismatched_start_time(self, monkeypatch):
        """Process considered dead when start time does not match."""
        expected_pid = 5678
        recorded_start = 1700000000.0
        actual_start = recorded_start + 5.0

        class _FakeProcess:

            def __init__(self, pid):
                assert pid == expected_pid

            def create_time(self):
                return actual_start

            def cmdline(self):
                return ['python', '-m', 'sky.jobs.controller']

            def is_running(self):
                return True

        monkeypatch.setattr(jobs_utils.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(
            pid=expected_pid, started_at=recorded_start)
        assert (jobs_utils.controller_process_alive(record, legacy_job_id=42) is
                False)

    def test_controller_process_alive_fallback_requires_keyword(
            self, monkeypatch):
        """Without start time, fallback relies on command keywords."""
        expected_pid = 2468
        monkeypatch.setattr(jobs_utils.psutil, 'pid_exists',
                            lambda pid: pid == expected_pid)

        class _KeywordProcess:

            def __init__(self, pid):
                assert pid == expected_pid

            def create_time(self):
                return 1700000000.0

            def cmdline(self):
                return ['python', '-m', 'sky.jobs.controller']

            def is_running(self):
                return True

        monkeypatch.setattr(jobs_utils.psutil, 'Process', _KeywordProcess)
        record = managed_job_state.ControllerPidRecord(pid=expected_pid,
                                                       started_at=None)
        assert (jobs_utils.controller_process_alive(record, legacy_job_id=42) is
                True)

        class _NoKeywordProcess(_KeywordProcess):

            def cmdline(self):
                return ['python', '-m', 'some.other.module']

        monkeypatch.setattr(jobs_utils.psutil, 'Process', _NoKeywordProcess)
        assert (jobs_utils.controller_process_alive(record, legacy_job_id=42) is
                False)


class TestStreamLogsByIdTaskFiltering:
    """Tests for task filtering logic in stream_logs_by_id.

    These tests verify that the task parameter correctly filters logs by
    task ID (when int) or task name (when str).
    """

    def _create_task_info(
        self,
        tasks: List[Tuple[int, str]],
        log_file: Optional[str] = None
    ) -> List[Tuple[int, str, managed_job_state.ManagedJobStatus, Optional[str],
                    Optional[str]]]:
        """Create task info tuples for mocking.

        Args:
            tasks: List of (task_id, task_name) tuples.
            log_file: Log file path for each task. If None, log reading is
                skipped which is useful for unit tests that don't need to
                verify log content.

        Returns:
            List of task info tuples (task_id, task_name, status, log_path,
            logs_cleaned_at).
        """
        return [(t_id, t_name, managed_job_state.ManagedJobStatus.SUCCEEDED,
                 log_file, None) for t_id, t_name in tasks]

    def test_task_filter_by_int_matches_task_id(self, monkeypatch):
        """Test that int task filter matches against task_id."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval'), (2, 'export')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        # Task filter is int 1, should match task_id=1 (eval)
        # We need to verify the filter finds the right task by checking
        # that it doesn't return NOT_FOUND
        # Mock get_status to return a terminal status so we exit early
        monkeypatch.setattr(
            jobs_utils.managed_job_state, 'get_status',
            lambda jid: managed_job_state.ManagedJobStatus.SUCCEEDED)

        # The function will try to stream logs, but we just want to verify
        # task filtering works. If it returns NOT_FOUND, the filter failed.
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task=1)

        # Should NOT return NOT_FOUND since task_id=1 exists
        assert exit_code != exceptions.JobExitCode.NOT_FOUND
        assert 'No task found matching' not in msg

    def test_task_filter_by_str_matches_task_name(self, monkeypatch):
        """Test that str task filter matches against task_name."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval'), (2, 'export')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )
        monkeypatch.setattr(
            jobs_utils.managed_job_state, 'get_status',
            lambda jid: managed_job_state.ManagedJobStatus.SUCCEEDED)

        # Task filter is str 'eval', should match task_name='eval'
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task='eval')

        # Should NOT return NOT_FOUND since task_name='eval' exists
        assert exit_code != exceptions.JobExitCode.NOT_FOUND
        assert 'No task found matching' not in msg

    def test_task_filter_int_not_found(self, monkeypatch):
        """Test that int task filter returns NOT_FOUND when task_id doesn't exist."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        # Task filter is int 5, which doesn't exist
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task=5)

        assert exit_code == exceptions.JobExitCode.NOT_FOUND
        assert 'No task found matching 5' in msg
        assert 'Valid task IDs are 0-1' in msg

    def test_task_filter_str_not_found(self, monkeypatch):
        """Test that str task filter returns NOT_FOUND when task_name doesn't exist."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        # Task filter is str 'nonexistent', which doesn't exist
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task='nonexistent')

        assert exit_code == exceptions.JobExitCode.NOT_FOUND
        assert "No task found matching 'nonexistent'" in msg

    def test_task_filter_int_does_not_match_task_name(self, monkeypatch):
        """Test that int task filter does NOT match task_name even if numeric."""
        job_id = 1
        # Task with numeric name '99' but task_id=0
        tasks = [(0, '99')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        # Task filter is int 99, should NOT match task_name='99',
        # should only try to match task_id
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task=99)

        # Should return NOT_FOUND because task_id=99 doesn't exist
        assert exit_code == exceptions.JobExitCode.NOT_FOUND
        assert 'No task found matching 99' in msg

    def test_task_filter_str_does_not_match_task_id(self, monkeypatch):
        """Test that str task filter does NOT match task_id even if numeric."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        # Task filter is str '1', should NOT match task_id=1,
        # should only try to match task_name
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task='1')

        # Should return NOT_FOUND because no task_name='1' exists
        assert exit_code == exceptions.JobExitCode.NOT_FOUND
        assert "No task found matching '1'" in msg

    def test_task_filter_none_does_not_filter(self, monkeypatch):
        """Test that None task filter shows all tasks (no filtering)."""
        job_id = 1
        tasks = [(0, 'train'), (1, 'eval')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )
        monkeypatch.setattr(
            jobs_utils.managed_job_state, 'get_status',
            lambda jid: managed_job_state.ManagedJobStatus.SUCCEEDED)

        # Task filter is None, should not filter
        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task=None)

        # Should NOT return NOT_FOUND
        assert exit_code != exceptions.JobExitCode.NOT_FOUND
        assert 'No task found matching' not in msg

    def test_task_filter_single_task_valid_range_message(self, monkeypatch):
        """Test that error message shows correct valid range for single task."""
        job_id = 1
        tasks = [(0, 'train')]
        task_info = self._create_task_info(tasks)

        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_num_tasks',
                            lambda jid: len(tasks))
        monkeypatch.setattr(
            jobs_utils.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            lambda jid: task_info,
        )

        msg, exit_code = jobs_utils.stream_logs_by_id(job_id,
                                                      follow=False,
                                                      task=5)

        assert exit_code == exceptions.JobExitCode.NOT_FOUND
        # Single task should show '0' not '0-0'
        assert 'Valid task IDs are 0.' in msg or 'Valid task IDs are 0,' in msg


class TestFormatJobDetails:
    """Tests for _format_job_details (the 'details' column)."""

    def _details(self,
                 *,
                 schedule_state='ALIVE',
                 failure_reason=None,
                 status='RECOVERING',
                 recovery_reason=None,
                 pending_reason=None,
                 cloud=None):
        job = {
            'schedule_state': schedule_state,
            'failure_reason': failure_reason,
            'status': status,
            'cloud': cloud,
        }
        jobs_utils._format_job_details(job=job,
                                       highest_blocking_priority=0,
                                       recovery_reason=recovery_reason,
                                       pending_reason=pending_reason)
        return job['details']

    def test_recovery_reason_surfaced(self):
        assert self._details(
            recovery_reason='podX is not ready (OOMKilled (exit code 137))'
        ) == 'Recovering: podX is not ready (OOMKilled (exit code 137))'

    def test_recovery_reason_multiline_collapsed(self):
        # Multi-line pod-termination reasons must render as a single line.
        multiline = ('Cluster is abnormal because head is not ready '
                     '(Terminated unexpectedly.\nLast known state: PodFailed.\n'
                     'Container errors: OOMKilled). Transitioned to INIT.')
        result = self._details(recovery_reason=multiline)
        assert '\n' not in result
        assert result == (
            'Recovering: Cluster is abnormal because head is not ready '
            '(Terminated unexpectedly. Last known state: PodFailed. '
            'Container errors: OOMKilled). Transitioned to INIT.')

    def test_no_recovery_reason_is_none(self):
        assert self._details(recovery_reason=None) is None

    def test_failure_reason_takes_precedence_over_recovery(self):
        # A terminal failure_reason should win over a (stale) recovery reason.
        assert self._details(failure_reason='boom',
                             recovery_reason='ignored') == 'Failure: boom'

    def test_backoff_state_takes_precedence_over_recovery(self):
        assert self._details(
            schedule_state='ALIVE_BACKOFF',
            recovery_reason='ignored') == 'In backoff, waiting for resources'

    def test_recovery_reason_oom_appends_hint_on_kubernetes(self):
        result = self._details(cloud='Kubernetes',
                               recovery_reason='podX OOMKilled (exit code 137)')
        assert result.startswith('Recovering: podX OOMKilled (exit code 137)')
        assert 'resources.memory' in result

    def test_recovery_reason_ephemeral_appends_hint_on_kubernetes(self):
        result = self._details(
            cloud='Kubernetes',
            recovery_reason='Evicted: Pod ephemeral local storage usage '
            'exceeds the total limit of containers 2Gi.')
        assert 'resources.ephemeral_storage' in result

    def test_recovery_reason_no_hint_on_non_kubernetes(self):
        # A non-k8s reason containing a matched word ('Insufficient') must not
        # be decorated with a Kubernetes hint.
        result = self._details(cloud='AWS',
                               recovery_reason='Insufficient capacity')
        assert result == 'Recovering: Insufficient capacity'

    def test_recovery_reason_no_hint_when_cloud_unknown(self):
        # No cloud info -> surface the reason without a (possibly wrong) hint.
        result = self._details(recovery_reason='podX OOMKilled (exit code 137)')
        assert result == 'Recovering: podX OOMKilled (exit code 137)'

    def test_pending_reason_surfaced(self):
        # A PENDING reason is surfaced in details when nothing else applies.
        assert self._details(
            schedule_state='INACTIVE',
            status='PENDING',
            pending_reason='Job submitted to queue') == 'Job submitted to queue'

    def test_pending_reason_multiline_collapsed(self):
        result = self._details(schedule_state='INACTIVE',
                               status='PENDING',
                               pending_reason='Rate limited.\nRetrying soon.')
        assert '\n' not in result
        assert result == 'Rate limited. Retrying soon.'

    def test_no_pending_reason_is_none(self):
        assert self._details(schedule_state='INACTIVE',
                             status='PENDING',
                             pending_reason=None) is None

    def test_failure_reason_takes_precedence_over_pending(self):
        assert self._details(status='PENDING',
                             failure_reason='boom',
                             pending_reason='ignored') == 'Failure: boom'

    def test_backoff_state_takes_precedence_over_pending(self):
        # The schedule-state-derived message is more informative than the raw
        # PENDING event reason, so it wins.
        assert self._details(
            schedule_state='ALIVE_BACKOFF',
            status='PENDING',
            pending_reason='ignored') == 'In backoff, waiting for resources'


class TestReadProvisionStatusFromLog:
    """Tests for relaying the provisioning spinner from the controller log."""

    @staticmethod
    def _status_line(control, msg=''):
        from sky.utils import message_utils
        return message_utils.encode_payload(control.encode(msg))

    def test_missing_file_returns_inputs(self, tmp_path):
        path = str(tmp_path / 'missing.log')
        pos, msg = jobs_utils.read_provision_status_from_log(path, 0, 'prev')
        assert pos == 0
        assert msg == 'prev'

    def test_reads_latest_spinner_message_incrementally(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        path.write_text('a plain provisioning log line\n' +
                        self._status_line(rich_utils.Control.INIT, 'Launching'))

        pos, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg == 'Launching'
        assert pos > 0

        # Append an UPDATE; only the newly appended bytes should be read.
        with open(path, 'a', encoding='utf-8') as f:
            f.write(
                self._status_line(rich_utils.Control.UPDATE,
                                  'Preparing SkyPilot runtime (1/3)'))
        pos2, msg2 = jobs_utils.read_provision_status_from_log(
            str(path), pos, msg)
        assert msg2 == 'Preparing SkyPilot runtime (1/3)'
        assert pos2 > pos

    def test_stop_control_clears_message(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        path.write_text(
            self._status_line(rich_utils.Control.INIT, 'Launching') +
            self._status_line(rich_utils.Control.EXIT))
        _, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg is None

    def test_partial_trailing_line_is_not_consumed(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        full = self._status_line(rich_utils.Control.INIT, 'Launching')
        # Write a complete payload plus a partial (no trailing newline) one.
        partial = self._status_line(rich_utils.Control.UPDATE,
                                    'half').rstrip('\n')
        path.write_text(full + partial)

        pos, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg == 'Launching'
        # Complete the partial line; the next read should pick it up.
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n')
        _, msg2 = jobs_utils.read_provision_status_from_log(str(path), pos, msg)
        assert msg2 == 'half'

    def test_start_does_not_revert_to_stale_message(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        # A nested status enter emits UPDATE(nested) then START(original); the
        # START carries the stale init message, so it must not overwrite the
        # live UPDATE text.
        path.write_text(
            self._status_line(rich_utils.Control.INIT, 'Launching') +
            self._status_line(rich_utils.Control.UPDATE,
                              'Preparing SkyPilot runtime (1/3)') +
            self._status_line(rich_utils.Control.START, 'Launching'))
        _, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg == 'Preparing SkyPilot runtime (1/3)'

    def test_stop_keeps_message(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        # STOP only pauses the spinner; it must not clear the message.
        path.write_text(
            self._status_line(rich_utils.Control.INIT, 'Launching') +
            self._status_line(rich_utils.Control.STOP))
        _, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg == 'Launching'

    def test_truncated_log_resets_offset(self, tmp_path):
        from sky.utils import rich_utils
        path = tmp_path / 'controller.log'
        path.write_text(
            self._status_line(rich_utils.Control.INIT, 'Launching') +
            self._status_line(rich_utils.Control.UPDATE, 'Preparing'))
        pos, msg = jobs_utils.read_provision_status_from_log(str(path), 0, None)
        assert msg == 'Preparing'
        assert pos > 0

        # Recreate (truncate) the log; the saved offset now points past EOF.
        path.write_text(self._status_line(rich_utils.Control.INIT, 'Relaunch'))
        _, msg2 = jobs_utils.read_provision_status_from_log(str(path), pos, msg)
        assert msg2 == 'Relaunch'


class TestProvisionStatusHeadline:
    """Tests for extracting the blue headline from a provisioning message."""

    def test_extracts_blue_headline_and_drops_hint(self):
        msg = ('[bold cyan]Preparing SkyPilot runtime (1/3)[/]  '
               '[dim]View logs at: ~/sky_logs/x/provision.log[/]')
        assert (jobs_utils._provision_status_headline(msg) ==
                'Preparing SkyPilot runtime (1/3)')

    def test_headline_without_hint(self):
        msg = '[bold cyan]Launching[/]'
        assert jobs_utils._provision_status_headline(msg) == 'Launching'

    def test_returns_none_when_not_blue(self):
        # No [bold cyan] wrapper: nothing should be displayed.
        assert jobs_utils._provision_status_headline('Launching') is None

    def test_nested_markup_is_preserved(self):
        # Nested markup inside the headline must not be truncated at the first
        # closing tag, and the trailing dim hint is still dropped.
        msg = '[bold cyan]Doing [bold]X[/] now[/]  [dim]hint[/]'
        assert (jobs_utils._provision_status_headline(msg) ==
                'Doing [bold]X[/] now')

    def test_extracts_headline_from_real_spinner_message(self):
        # Regression: real spinner messages append the log hint with raw ANSI
        # (colorama) codes, not rich `[dim]...[/]` markup, so the headline does
        # not end the string. The headline must still be extracted (otherwise
        # the provisioning detail under "Waiting for task to start" vanishes).
        from sky.utils import ux_utils
        msg = ux_utils.spinner_message('Preparing SkyPilot runtime (1/3)',
                                       log_path='~/sky_logs/x/provision.log')
        assert msg != '[bold cyan]Preparing SkyPilot runtime (1/3)[/]'
        assert (jobs_utils._provision_status_headline(msg) ==
                'Preparing SkyPilot runtime (1/3)')

    def test_extracts_headline_with_provision_hint(self):
        # The provision-log hint variant (sky logs --provision <cluster>) also
        # appends an ANSI-colored, bold-wrapped hint after the headline.
        from sky.utils import ux_utils
        msg = ux_utils.spinner_message('Launching', cluster_name='my-cluster')
        assert jobs_utils._provision_status_headline(msg) == 'Launching'


class TestIsRelayedStatusPayloadLine:
    """Tests for hiding relayed rich-status payloads from --controller logs."""

    def test_payload_line_detected(self):
        from sky.utils import message_utils
        from sky.utils import rich_utils
        line = message_utils.encode_payload(
            rich_utils.Control.UPDATE.encode('Preparing'))
        assert jobs_utils._is_relayed_status_payload_line(line) is True

    def test_plain_log_line_not_detected(self):
        assert jobs_utils._is_relayed_status_payload_line(
            'Preparing SkyPilot runtime (1/3)\n') is False
