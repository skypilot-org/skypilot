"""Unit tests for sky.server.requests.serializers.encoders module."""
from sky import resources as resources_lib
from sky.backends import cloud_vm_ray_backend
from sky.schemas.api import responses
from sky.server.requests.serializers import decoders
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

        # [DEPICKLE PROTOTYPE] encode_handle now emits a JSON dict.
        encoded_handle = cluster_data['handle']
        assert isinstance(encoded_handle, dict)
        decoded_handle = decoders.decode_handle(encoded_handle)

        # NOTE: skylet_ssh_tunnel is not part of the wire projection
        # (to_dict/from_dict); it lives in its own DB column.
        assert getattr(decoded_handle, 'skylet_ssh_tunnel', None) is None

        # Other attributes should be preserved
        assert decoded_handle.cluster_name == "test-cluster"
        assert decoded_handle.cluster_name_on_cloud == "test-cluster-123"

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

        # [DEPICKLE PROTOTYPE] encode_handle now emits a JSON dict.
        encoded_handle = cluster_data['handle']
        assert isinstance(encoded_handle, dict)
        decoded_handle = decoders.decode_handle(encoded_handle)
        assert decoded_handle.cluster_name == "test-cluster"
        assert decoded_handle.cluster_name_on_cloud == "test-cluster-123"


class TestEncodeJobsQueue:
    """Test the encode_jobs_queue and encode_jobs_queue_v2 functions."""

    def test_encode_jobs_queue_with_network_fields(self):
        """Test that encode_jobs_queue encodes jobs with network fields."""
        from sky.jobs import state as managed_jobs

        # Create a mock job with network fields
        job = {
            'job_id': 1,
            'task_id': 0,
            'job_name': 'test-job',
            'task_name': 'test-task',
            'status': managed_jobs.ManagedJobStatus.RUNNING,
            'internal_external_ips': [('10.0.0.1', '35.1.2.3')],
            'internal_services': None,
        }

        result = encoders.encode_jobs_queue([job])

        assert len(result) == 1
        encoded_job = result[0]
        assert encoded_job[
            'status'] == managed_jobs.ManagedJobStatus.RUNNING.value

        # Network fields should be preserved as-is (JSON serializable)
        assert encoded_job['internal_external_ips'] == [('10.0.0.1', '35.1.2.3')
                                                       ]
        assert encoded_job['internal_services'] is None

    def test_encode_jobs_queue_v2_with_network_fields(self):
        """Test that encode_jobs_queue_v2 encodes jobs with network fields."""
        from sky.jobs import state as managed_jobs

        job = responses.ManagedJobRecord(
            job_id=1,
            task_id=0,
            job_name='test-job',
            task_name='test-task',
            status=managed_jobs.ManagedJobStatus.RUNNING,
            internal_external_ips=[('10.0.0.2', '35.1.2.4')],
            internal_services={'pod-0': 'pod-0.svc.cluster.local'},
        )

        result = encoders.encode_jobs_queue_v2([job])

        assert len(result) == 1
        encoded_job = result[0]
        assert encoded_job[
            'status'] == managed_jobs.ManagedJobStatus.RUNNING.value

        # Network fields should be preserved as-is (JSON serializable)
        assert encoded_job['internal_external_ips'] == [('10.0.0.2', '35.1.2.4')
                                                       ]
        assert encoded_job['internal_services'] == {
            'pod-0': 'pod-0.svc.cluster.local'
        }

    def test_encode_jobs_queue_v2_dict_format(self):
        """Test encode_jobs_queue_v2 with dict return format."""
        from sky.jobs import state as managed_jobs

        job = responses.ManagedJobRecord(
            job_id=1,
            task_id=0,
            job_name='test-job',
            task_name='test-task',
            status=managed_jobs.ManagedJobStatus.RUNNING,
            internal_external_ips=[('10.0.0.1', '35.1.2.3')],
            internal_services=None,
        )

        result = encoders.encode_jobs_queue_v2(([job], 1, {'RUNNING': 1}, 1))

        assert isinstance(result, dict)
        assert result['total'] == 1
        assert result['status_counts'] == {'RUNNING': 1}
        assert len(result['jobs']) == 1
        assert result['jobs'][0]['internal_external_ips'] == [('10.0.0.1',
                                                               '35.1.2.3')]
