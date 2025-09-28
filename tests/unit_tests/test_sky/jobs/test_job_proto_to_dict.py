"""Unit tests for job_proto_to_dict conversion logic."""

import pytest

from sky.jobs import state as managed_job_state
from sky.jobs import utils as jobs_utils
from sky.schemas.generated import managed_jobsv1_pb2


class TestJobProtoToDict:
    """Test the conversion from protobuf ManagedJobInfo to dictionary."""

    def _create_minimal_job_proto(self) -> managed_jobsv1_pb2.ManagedJobInfo:
        """Create a protobuf job with only required fields."""
        return managed_jobsv1_pb2.ManagedJobInfo(
            job_id=2,
            task_id=0,
            job_name="sky-cmd",
            task_name="sky-cmd",
            job_duration=128.0,
            status=managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUCCEEDED,
            schedule_state=managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_DONE,
            resources="1x[CPU:1+]",
            cluster_resources="-",
            cluster_resources_full="-",
            cloud="-",
            region="-",
            infra="-",
            recovery_count=0)

    def _create_full_job_proto(self) -> managed_jobsv1_pb2.ManagedJobInfo:
        """Create a protobuf job with all optional fields populated."""
        job = managed_jobsv1_pb2.ManagedJobInfo(
            job_id=2,
            task_id=0,
            job_name="sky-cmd",
            task_name="sky-cmd",
            job_duration=128.0,
            status=managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUCCEEDED,
            schedule_state=managed_jobsv1_pb2.MANAGED_JOB_SCHEDULE_STATE_DONE,
            resources="1x[CPU:1+]",
            cluster_resources="-",
            cluster_resources_full="-",
            cloud="-",
            region="-",
            infra="-",
            accelerators={"H100": 8.0},
            recovery_count=0,
            # Optional fields
            workspace="default",
            details=None,
            failure_reason=None,
            user_name="bob",
            user_hash="7a2eebbf",
            submitted_at=1758070663.033409,
            start_at=1758070656.0,
            end_at=1758070784.0,
            user_yaml=
            "name: sky-cmd\n---\n\nname: sky-cmd\n\nresources:\n  job_recovery:\n    strategy: EAGER_NEXT_REGION\n  disk_size: 256\n\nnum_nodes: 1\n\nrun: echo hi\n\nfile_mounts: {}\n\nvolumes: {}\n",
            entrypoint="sky jobs launch echo hi",
            pool=None,
            pool_hash=None)

        return job

    def test_basic_required_fields(self):
        """Test conversion of required fields."""
        job_proto = self._create_minimal_job_proto()
        job_dict = jobs_utils._job_proto_to_dict(job_proto)

        assert job_dict['job_id'] == 2
        assert job_dict['task_id'] == 0
        assert job_dict['job_name'] == "sky-cmd"
        assert job_dict['task_name'] == "sky-cmd"
        assert job_dict['job_duration'] == 128.0
        assert job_dict['resources'] == "1x[CPU:1+]"
        assert job_dict['cluster_resources'] == "-"
        assert job_dict['cluster_resources_full'] == "-"
        assert job_dict['cloud'] == "-"
        assert job_dict['region'] == "-"
        assert job_dict['infra'] == "-"
        assert job_dict['recovery_count'] == 0
        assert isinstance(job_dict['status'],
                          managed_job_state.ManagedJobStatus)
        assert job_dict[
            'status'] == managed_job_state.ManagedJobStatus.SUCCEEDED
        # schedule_state is a string for backwards compatibility reasons
        assert isinstance(job_dict['schedule_state'], str)
        assert job_dict[
            'schedule_state'] == managed_job_state.ManagedJobScheduleState.DONE.value

    def test_all_optional_fields_populated(self):
        """Test conversion when all optional fields are populated."""
        job_proto = self._create_full_job_proto()
        job_dict = jobs_utils._job_proto_to_dict(job_proto)

        # Check optional string fields
        assert job_dict['workspace'] == "default"
        assert job_dict['details'] is None
        assert job_dict['failure_reason'] is None
        assert job_dict['user_name'] == "bob"
        assert job_dict['user_hash'] == "7a2eebbf"
        assert "name: sky-cmd" in job_dict['user_yaml']
        assert "run: echo hi" in job_dict['user_yaml']
        assert job_dict['entrypoint'] == "sky jobs launch echo hi"
        assert job_dict['pool'] is None
        assert job_dict['pool_hash'] is None

        # Check timestamp fields
        assert job_dict['submitted_at'] == 1758070663.033409
        assert job_dict['start_at'] == 1758070656.0
        assert job_dict['end_at'] == 1758070784.0

        # Check map fields
        assert job_dict['accelerators'] == {"H100": 8.0}
        assert job_dict['metadata'] == {}

    def test_optional_fields_missing(self):
        """Test that missing optional fields are set to None."""
        job_proto = self._create_minimal_job_proto()
        job_dict = jobs_utils._job_proto_to_dict(job_proto)

        # Check that optional fields are None when not set
        optional_fields = [
            'workspace', 'details', 'failure_reason', 'user_name', 'user_hash',
            'submitted_at', 'start_at', 'end_at', 'user_yaml', 'entrypoint',
            'pool', 'pool_hash'
        ]

        for field in optional_fields:
            assert job_dict[
                field] is None, f"Field '{field}' should be None when not set"

        # Maps should be empty when not populated
        assert job_dict['accelerators'] == {}
        assert job_dict['metadata'] == {}

    def test_status_enum_conversion_all_values(self):
        """Test status enum conversion for all possible values."""
        status_mappings = [
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_PENDING,
             managed_job_state.ManagedJobStatus.PENDING),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUBMITTED,
             managed_job_state.ManagedJobStatus.DEPRECATED_SUBMITTED),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_STARTING,
             managed_job_state.ManagedJobStatus.STARTING),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_RUNNING,
             managed_job_state.ManagedJobStatus.RUNNING),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_RECOVERING,
             managed_job_state.ManagedJobStatus.RECOVERING),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLING,
             managed_job_state.ManagedJobStatus.CANCELLING),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_SUCCEEDED,
             managed_job_state.ManagedJobStatus.SUCCEEDED),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_CANCELLED,
             managed_job_state.ManagedJobStatus.CANCELLED),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED,
             managed_job_state.ManagedJobStatus.FAILED),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_SETUP,
             managed_job_state.ManagedJobStatus.FAILED_SETUP),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_PRECHECKS,
             managed_job_state.ManagedJobStatus.FAILED_PRECHECKS),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_NO_RESOURCE,
             managed_job_state.ManagedJobStatus.FAILED_NO_RESOURCE),
            (managed_jobsv1_pb2.MANAGED_JOB_STATUS_FAILED_CONTROLLER,
             managed_job_state.ManagedJobStatus.FAILED_CONTROLLER),
        ]

        for proto_status, expected_status in status_mappings:
            job_proto = self._create_minimal_job_proto()
            job_proto.status = proto_status
            job_dict = jobs_utils._job_proto_to_dict(job_proto)
            assert job_dict['status'] == expected_status

    def test_status_unspecified_converts_to_none(self):
        """Test that UNSPECIFIED status converts to None."""
        job_proto = self._create_minimal_job_proto()
        job_proto.status = managed_jobsv1_pb2.MANAGED_JOB_STATUS_UNSPECIFIED
        job_dict = jobs_utils._job_proto_to_dict(job_proto)
        assert job_dict['status'] is None

    def test_map_fields_empty_and_populated(self):
        """Test map fields (accelerators, metadata) in various states."""
        # Test empty maps
        job_proto = self._create_minimal_job_proto()
        job_dict = jobs_utils._job_proto_to_dict(job_proto)
        assert job_dict['accelerators'] == {}
        assert job_dict['metadata'] == {}

        # Test single entry maps
        job_proto.accelerators["H200"] = 1.0
        job_proto.metadata["key"] = "value"
        job_dict = jobs_utils._job_proto_to_dict(job_proto)
        assert job_dict['accelerators'] == {"H200": 1.0}
        assert job_dict['metadata'] == {"key": "value"}

        # Test multiple entries
        job_proto.accelerators["H100"] = 1.0
        job_proto.metadata["another_key"] = "another_value"
        job_dict = jobs_utils._job_proto_to_dict(job_proto)
        assert job_dict['accelerators'] == {"H100": 1.0, "H200": 1.0}
        assert job_dict['metadata'] == {
            "key": "value",
            "another_key": "another_value"
        }

    def test_large_numeric_values(self):
        """Test handling of large numeric values."""
        job_proto = self._create_minimal_job_proto()
        job_proto.job_id = 9223372036854775807
        job_proto.task_id = 0
        job_proto.job_duration = 999999.999999
        job_proto.submitted_at = 2147483647.0

        job_dict = jobs_utils._job_proto_to_dict(job_proto)

        assert job_dict['job_id'] == 9223372036854775807
        assert job_dict['task_id'] == 0
        assert job_dict['job_duration'] == 999999.999999
        assert job_dict['submitted_at'] == 2147483647.0

    def test_empty_strings_vs_none(self):
        """Test distinction between empty strings and None values."""
        job_proto = self._create_minimal_job_proto()
        job_proto.workspace = ""  # Empty string (present but empty)
        job_proto.details = ""  # Empty string (present but empty)
        # user_name not set (should be None)

        job_dict = jobs_utils._job_proto_to_dict(job_proto)

        assert job_dict['workspace'] == ""  # Empty string preserved
        assert job_dict['details'] == ""  # Empty string preserved
        assert job_dict['user_name'] is None  # Not set, should be None
