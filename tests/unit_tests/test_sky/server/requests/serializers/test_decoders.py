"""Unit tests for sky.server.requests.serializers.decoders module."""
from sky.jobs import state as managed_jobs
from sky.schemas.api import responses
from sky.server.requests.serializers import decoders


class TestDecodeJobsQueue:
    """Test the decode_jobs_queue and decode_jobs_queue_v2 functions."""

    def test_decode_jobs_queue_v2_with_network_fields(self):
        """Test that decode_jobs_queue_v2 handles network fields properly."""
        # Create job data with network fields
        job_data = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING.value,
            'internal_external_ips': [('10.0.0.1', '35.1.2.3')],
            'internal_services': {
                'pod-0': 'pod-0.svc.cluster.local'
            },
        }

        result = decoders.decode_jobs_queue_v2([job_data])

        assert isinstance(result, list)
        assert len(result) == 1
        job = result[0]
        assert isinstance(job, responses.ManagedJobRecord)
        assert job.status == managed_jobs.ManagedJobStatus.RUNNING

        # Network fields should be preserved
        assert job.internal_external_ips == [('10.0.0.1', '35.1.2.3')]
        assert job.internal_services == {'pod-0': 'pod-0.svc.cluster.local'}

    def test_decode_jobs_queue_v2_with_none_network_fields(self):
        """Test that decode_jobs_queue_v2 handles None network fields."""
        job_data = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.PENDING.value,
            'internal_external_ips': None,
            'internal_services': None,
        }

        result = decoders.decode_jobs_queue_v2([job_data])

        assert len(result) == 1
        assert result[0].internal_external_ips is None
        assert result[0].internal_services is None

    def test_decode_jobs_queue_v2_dict_format(self):
        """Test decode_jobs_queue_v2 with dict return format."""
        input_data = {
            'jobs': [{
                'job_id': 1,
                'task_id': 0,
                'job_name': 'test-job',
                'task_name': 'test-task',
                'status': managed_jobs.ManagedJobStatus.RUNNING.value,
                'internal_external_ips': [('10.0.0.1', '35.1.2.3')],
                'internal_services': None,
            }],
            'total': 1,
            'total_no_filter': 1,
            'status_counts': {
                'RUNNING': 1
            },
        }

        result = decoders.decode_jobs_queue_v2(input_data)

        assert isinstance(result, tuple)
        jobs, total, status_counts, total_no_filter = result
        assert total == 1
        assert status_counts == {'RUNNING': 1}
        assert len(jobs) == 1
        assert jobs[0].internal_external_ips == [('10.0.0.1', '35.1.2.3')]

    def test_decode_jobs_queue_legacy_format(self):
        """Test decode_jobs_queue with legacy list format."""
        job_data = [{
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING.value,
            'internal_external_ips': [('10.0.0.2', '35.1.2.4')],
            'internal_services': None,
        }]

        result = decoders.decode_jobs_queue(job_data)

        # Legacy format returns same as queue_v2
        assert len(result) == 1
        assert result[0].internal_external_ips == [('10.0.0.2', '35.1.2.4')]
