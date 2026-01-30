"""Unit tests for sky.server.requests.serializers.decoders module."""
import base64
import pickle

from sky.jobs import state as managed_jobs
from sky.schemas.api import responses
from sky.server.requests.serializers import decoders


class TestDecodeJobsQueue:
    """Test the decode_jobs_queue and decode_jobs_queue_v2 functions."""

    def test_decode_jobs_queue_v2_with_handle(self):
        """Test that decode_jobs_queue_v2 deserializes handles properly."""
        # Create a serialized handle
        handle_data = {
            'cluster_name_on_cloud': 'test-cluster',
            'stable_internal_external_ips': [('10.0.0.1', '35.1.2.3')],
        }
        serialized_handle = base64.b64encode(
            pickle.dumps(handle_data)).decode('utf-8')

        # Create job data as it would come from the encoder
        job_data = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING.value,
            'handle': serialized_handle,
        }

        result = decoders.decode_jobs_queue_v2([job_data])

        assert isinstance(result, list)
        assert len(result) == 1
        job = result[0]
        assert isinstance(job, responses.ManagedJobRecord)
        assert job.status == managed_jobs.ManagedJobStatus.RUNNING

        # Handle should be deserialized
        assert job.handle is not None
        assert job.handle['cluster_name_on_cloud'] == 'test-cluster'
        assert job.handle['stable_internal_external_ips'] == [('10.0.0.1',
                                                               '35.1.2.3')]

    def test_decode_jobs_queue_v2_with_none_handle(self):
        """Test that decode_jobs_queue_v2 handles None handle."""
        job_data = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.PENDING.value,
            'handle': None,
        }

        result = decoders.decode_jobs_queue_v2([job_data])

        assert len(result) == 1
        assert result[0].handle is None

    def test_decode_jobs_queue_v2_dict_format(self):
        """Test decode_jobs_queue_v2 with dict return format."""
        handle_data = {
            'cluster_name_on_cloud': 'test-cluster-dict',
        }
        serialized_handle = base64.b64encode(
            pickle.dumps(handle_data)).decode('utf-8')

        input_data = {
            'jobs': [{
                'job_id': 1,
                'task_id': 0,
                'job_name': 'test-job',
                'task_name': 'test-task',
                'status': managed_jobs.ManagedJobStatus.RUNNING.value,
                'handle': serialized_handle,
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
        assert jobs[0].handle['cluster_name_on_cloud'] == 'test-cluster-dict'

    def test_decode_jobs_queue_legacy_format(self):
        """Test decode_jobs_queue with legacy list format."""
        handle_data = {
            'cluster_name_on_cloud': 'test-cluster-legacy',
        }
        serialized_handle = base64.b64encode(
            pickle.dumps(handle_data)).decode('utf-8')

        job_data = [{
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING.value,
            'handle': serialized_handle,
        }]

        result = decoders.decode_jobs_queue(job_data)

        # Legacy format returns same as queue_v2
        assert len(result) == 1
        assert result[0].handle[
            'cluster_name_on_cloud'] == 'test-cluster-legacy'


class TestDecodeManagedJobHandle:
    """Test the _decode_managed_job_handle helper function."""

    def test_decode_managed_job_handle_with_string(self):
        """Test decoding a serialized handle string."""
        handle_data = {'test': 'data'}
        serialized = base64.b64encode(pickle.dumps(handle_data)).decode('utf-8')

        job = {'handle': serialized}
        decoders._decode_managed_job_handle(job)

        assert job['handle'] == {'test': 'data'}

    def test_decode_managed_job_handle_with_none(self):
        """Test that None handle is left as None."""
        job = {'handle': None}
        decoders._decode_managed_job_handle(job)

        assert job['handle'] is None

    def test_decode_managed_job_handle_missing_key(self):
        """Test that missing handle key doesn't cause error."""
        job = {'job_id': 1}
        decoders._decode_managed_job_handle(job)

        assert 'handle' not in job
