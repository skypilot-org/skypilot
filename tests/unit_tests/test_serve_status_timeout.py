"""Tests for sky serve status endpoint retrieval timeout.

Verifies that the url property on ReplicaInfo times out instead of blocking
indefinitely when the cloud API is slow (e.g., K8s LoadBalancer IP polling).

See: https://linear.app/skypilot/issue/SKY-3767
"""
import time
from unittest import mock

from sky.serve import replica_managers
from sky.serve import serve_state
from sky.utils import common_utils


def _make_replica_info(replica_id: int = 1) -> replica_managers.ReplicaInfo:
    """Create a ReplicaInfo with minimal required fields."""
    return replica_managers.ReplicaInfo(
        replica_id=replica_id,
        cluster_name=f'test-cluster-{replica_id}',
        replica_port='8000',
        is_spot=False,
        location=None,
        version=1,
        resources_override=None,
    )


def _set_replica_status(info: replica_managers.ReplicaInfo,
                        status: serve_state.ReplicaStatus) -> None:
    """Set the replica to return a specific status via status_property."""
    prop = info.status_property
    if status == serve_state.ReplicaStatus.PROVISIONING:
        prop.sky_launch_status = common_utils.ProcessStatus.RUNNING
    elif status == serve_state.ReplicaStatus.STARTING:
        prop.sky_launch_status = common_utils.ProcessStatus.SUCCEEDED
        prop.service_ready_now = False
        prop.first_ready_time = None
    elif status == serve_state.ReplicaStatus.READY:
        prop.sky_launch_status = common_utils.ProcessStatus.SUCCEEDED
        prop.service_ready_now = True
        prop.first_ready_time = time.time()
    elif status == serve_state.ReplicaStatus.NOT_READY:
        prop.sky_launch_status = common_utils.ProcessStatus.SUCCEEDED
        prop.service_ready_now = False
        prop.first_ready_time = time.time()


class TestUrlPropertyTimeout:
    """Test that the url property times out instead of blocking."""

    def test_url_returns_none_on_timeout(self):
        """url should return None when get_endpoints is slow."""
        info = _make_replica_info()

        mock_handle = mock.MagicMock()
        mock_handle.cluster_name = 'test-cluster-1'

        def slow_get_endpoints(*args, **kwargs):
            time.sleep(30)
            return {8000: 'http://1.2.3.4:8000'}

        with mock.patch.object(type(info), '_URL_QUERY_TIMEOUT_SECONDS', new=1):
            with mock.patch.object(info, 'handle', return_value=mock_handle):
                with mock.patch(
                        'sky.serve.replica_managers.backend_utils'
                        '.get_endpoints',
                        side_effect=slow_get_endpoints):
                    start = time.monotonic()
                    result = info.url
                    elapsed = time.monotonic() - start
                    assert result is None
                    # Should return well before the 30s sleep in
                    # slow_get_endpoints completes.
                    assert elapsed < 5, f'url took {elapsed:.1f}s, expected <5s'

    def test_url_returns_endpoint_when_fast(self):
        """url should return the endpoint when get_endpoints is fast."""
        info = _make_replica_info()

        mock_handle = mock.MagicMock()
        mock_handle.cluster_name = 'test-cluster-1'

        with mock.patch.object(info, 'handle', return_value=mock_handle):
            with mock.patch(
                    'sky.serve.replica_managers.backend_utils.get_endpoints',
                    return_value={8000: 'http://1.2.3.4:8000'}):
                result = info.url
                assert result == 'http://1.2.3.4:8000'

    def test_url_returns_none_on_cluster_not_up(self):
        """url should return None when cluster is not UP."""
        from sky import exceptions as sky_exceptions
        info = _make_replica_info()

        mock_handle = mock.MagicMock()
        mock_handle.cluster_name = 'test-cluster-1'

        with mock.patch.object(info, 'handle', return_value=mock_handle):
            with mock.patch(
                    'sky.serve.replica_managers.backend_utils.get_endpoints',
                    side_effect=sky_exceptions.ClusterNotUpError(
                        'not up', cluster_status=None)):
                result = info.url
                assert result is None

    def test_url_returns_none_when_no_handle(self):
        """url should return None when handle is None."""
        info = _make_replica_info()

        with mock.patch.object(info, 'handle', return_value=None):
            result = info.url
            assert result is None

    @mock.patch('sky.serve.replica_managers.global_user_state'
                '.get_cluster_from_name',
                return_value=None)
    def test_to_info_dict_not_blocked_by_slow_url(self, _mock_cluster):
        """to_info_dict should complete quickly even with slow url queries."""
        info = _make_replica_info()
        _set_replica_status(info, serve_state.ReplicaStatus.STARTING)

        mock_handle = mock.MagicMock()
        mock_handle.cluster_name = 'test-cluster-1'

        def slow_get_endpoints(*args, **kwargs):
            time.sleep(30)
            return {8000: 'http://1.2.3.4:8000'}

        with mock.patch.object(type(info), '_URL_QUERY_TIMEOUT_SECONDS', new=1):
            with mock.patch.object(info, 'handle', return_value=mock_handle):
                with mock.patch(
                        'sky.serve.replica_managers.backend_utils'
                        '.get_endpoints',
                        side_effect=slow_get_endpoints):
                    start = time.monotonic()
                    result = info.to_info_dict(with_handle=False, with_url=True)
                    elapsed = time.monotonic() - start
                    assert result['endpoint'] is None
                    assert elapsed < 5, (
                        f'to_info_dict took {elapsed:.1f}s, expected <5s')
