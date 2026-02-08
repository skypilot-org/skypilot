"""Unit tests for sky.server.requests.serializers.encoders module."""
import base64
import pickle

from sky import resources as resources_lib
from sky.backends import cloud_vm_ray_backend
from sky.schemas.api import responses
from sky.server.requests.serializers import encoders
from sky.utils import status_lib


class TestEncodeStatus:
    """Test the encode_status function."""

    def test_encode_status_with_ssh_tunnel_backwards_compatibility(self):
        """Test that encode_status removes SSH tunnel info for backwards compatibility."""
        resources = resources_lib.Resources(cloud=None, accelerators=None)
        handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
            cluster_name="test-cluster",
            cluster_name_on_cloud="test-cluster-123",
            cluster_yaml="/path/to/cluster.yaml",
            launched_nodes=1,
            launched_resources=resources)
        handle.skylet_ssh_tunnel = cloud_vm_ray_backend.SSHTunnelInfo(pid=1234,
                                                                      port=1234)
        status_response = responses.StatusResponse(
            name="test-cluster",
            launched_at=1234567890,
            handle=handle,
            last_use="sky launch",
            status=status_lib.ClusterStatus.UP,
            autostop=-1,
            to_down=False,
            cluster_hash="abc123",
            storage_mounts_metadata={},
            cluster_ever_up=True,
            status_updated_at=1234567890,
            user_hash="user123",
            user_name="test-user",
            workspace="/tmp/test",
            is_managed=False,
            nodes=1)
        result = encoders.encode_status([status_response])
        assert len(result) == 1
        cluster_data = result[0]
        assert cluster_data['name'] == "test-cluster"
        assert cluster_data['status'] == status_lib.ClusterStatus.UP.value

        encoded_handle = cluster_data['handle']
        assert isinstance(encoded_handle, str)
        decoded_bytes = base64.b64decode(encoded_handle)
        unpickled_handle = pickle.loads(decoded_bytes)

        # NOTE: We have removed the skylet_ssh_tunnel attribute
        # from the handle, but we keep this test for future reference.
        assert not hasattr(unpickled_handle, 'skylet_ssh_tunnel')
        # Previously, this test tests that the handle has SSH tunnel info
        # removed for backwards compatibility.
        # assert hasattr(unpickled_handle, 'skylet_ssh_tunnel')
        # assert unpickled_handle.skylet_ssh_tunnel is None

        # Other attributes should be preserved
        assert unpickled_handle.cluster_name == "test-cluster"
        assert unpickled_handle.cluster_name_on_cloud == "test-cluster-123"

    def test_encode_status(self):
        """Test that encode_status works normally when handle has no SSH tunnel info."""
        resources = resources_lib.Resources(cloud=None, accelerators=None)
        handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
            cluster_name="test-cluster",
            cluster_name_on_cloud="test-cluster-123",
            cluster_yaml="/path/to/cluster.yaml",
            launched_nodes=1,
            launched_resources=resources)
        status_response = responses.StatusResponse(
            name="test-cluster",
            launched_at=1234567890,
            handle=handle,
            last_use="sky launch",
            status=status_lib.ClusterStatus.UP,
            autostop=-1,
            to_down=False,
            cluster_hash="abc123",
            storage_mounts_metadata={},
            cluster_ever_up=True,
            status_updated_at=1234567890,
            user_hash="user123",
            user_name="test-user",
            workspace="/tmp/test",
            is_managed=False,
            nodes=1)
        result = encoders.encode_status([status_response])
        assert len(result) == 1
        cluster_data = result[0]
        assert cluster_data['name'] == "test-cluster"
        assert cluster_data['status'] == status_lib.ClusterStatus.UP.value

        encoded_handle = cluster_data['handle']
        decoded_bytes = base64.b64decode(encoded_handle)
        unpickled_handle = pickle.loads(decoded_bytes)
        assert unpickled_handle.cluster_name == "test-cluster"
        assert unpickled_handle.cluster_name_on_cloud == "test-cluster-123"


class TestEncodeJobsQueue:
    """Test the encode_jobs_queue and encode_jobs_queue_v2 functions."""

    def test_encode_jobs_queue_with_handle(self):
        """Test that encode_jobs_queue serializes handles properly."""
        from sky.jobs import state as managed_jobs

        # Create a mock job with handle
        job = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING,
            'handle': {  # Use dict as simple picklable object
                'cluster_name_on_cloud': 'test-cluster',
                'stable_internal_external_ips': [('10.0.0.1', '35.1.2.3')],
            },
        }

        result = encoders.encode_jobs_queue([job])

        assert len(result) == 1
        encoded_job = result[0]
        assert encoded_job[
            'status'] == managed_jobs.ManagedJobStatus.RUNNING.value

        # Handle should be serialized to a string
        assert isinstance(encoded_job['handle'], str)

        # Verify it can be decoded
        decoded_handle = pickle.loads(
            base64.b64decode(encoded_job['handle'].encode('utf-8')))
        assert decoded_handle['cluster_name_on_cloud'] == 'test-cluster'
        assert decoded_handle['stable_internal_external_ips'] == [('10.0.0.1',
                                                                   '35.1.2.3')]

    def test_encode_jobs_queue_with_none_handle(self):
        """Test that encode_jobs_queue handles None handle."""
        from sky.jobs import state as managed_jobs

        job = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.PENDING,
            'handle': None,
        }

        result = encoders.encode_jobs_queue([job])

        assert len(result) == 1
        assert result[0]['handle'] is None

    def test_encode_jobs_queue_v2_with_handle(self):
        """Test that encode_jobs_queue_v2 serializes handles properly."""
        from sky.jobs import state as managed_jobs

        job = responses.ManagedJobRecord(
            job_id=1,
            task_id=0,
            job_name='test-job',
            task_name='test-task',
            status=managed_jobs.ManagedJobStatus.RUNNING,
            handle={
                'cluster_name_on_cloud': 'test-cluster-v2',
                'stable_internal_external_ips': [('10.0.0.2', '35.1.2.4')],
            },
        )

        result = encoders.encode_jobs_queue_v2([job])

        assert len(result) == 1
        encoded_job = result[0]
        assert encoded_job[
            'status'] == managed_jobs.ManagedJobStatus.RUNNING.value

        # Handle should be serialized to a string
        assert isinstance(encoded_job['handle'], str)

        # Verify it can be decoded
        decoded_handle = pickle.loads(
            base64.b64decode(encoded_job['handle'].encode('utf-8')))
        assert decoded_handle['cluster_name_on_cloud'] == 'test-cluster-v2'

    def test_encode_jobs_queue_v2_dict_format(self):
        """Test encode_jobs_queue_v2 with dict return format."""
        from sky.jobs import state as managed_jobs

        job = responses.ManagedJobRecord(
            job_id=1,
            task_id=0,
            job_name='test-job',
            task_name='test-task',
            status=managed_jobs.ManagedJobStatus.RUNNING,
            handle={
                'cluster_name_on_cloud': 'test-cluster',
            },
        )

        result = encoders.encode_jobs_queue_v2(([job], 1, {'RUNNING': 1}, 1))

        assert isinstance(result, dict)
        assert result['total'] == 1
        assert result['status_counts'] == {'RUNNING': 1}
        assert len(result['jobs']) == 1
        assert isinstance(result['jobs'][0]['handle'], str)
