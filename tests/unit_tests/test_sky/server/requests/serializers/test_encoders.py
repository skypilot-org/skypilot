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
