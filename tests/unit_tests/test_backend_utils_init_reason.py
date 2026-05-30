"""Regression tests for _update_cluster_status init_reason for abnormal clusters.

When the Kubernetes provisioner maps a Failed pod to ClusterStatus.INIT,
a single-pod sky cluster ends up with node_statuses == {pod: (INIT, ...)}
and handle.launched_nodes == 1. The previous logic fell through to
"some but not all nodes are stopped", which is nonsensical for a single
INIT node. _update_cluster_status should now recognize nodes in
unexpected (non-UP, non-STOPPED) states and report them distinctly.
"""
import time
from unittest import mock

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.utils import status_lib


def _make_handle():
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/path/cluster.yaml'
    handle.launched_nodes = 1
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = clouds.Kubernetes()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _make_record(handle, status=status_lib.ClusterStatus.UP):
    return {
        'handle': handle,
        'status': status,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,  # old enough to skip recheck
    }


def _capture_init_log_message(node_statuses, launched_nodes=1):
    """Drive _update_cluster_status with a fake node_statuses and return
    the log_message passed to global_user_state.add_cluster_event when the
    cluster transitions to INIT."""
    handle = _make_handle()
    handle.launched_nodes = launched_nodes
    record = _make_record(handle)

    captured = {}

    def _capture_event(cluster_name, new_status, message, event_type, **kwargs):
        if new_status == status_lib.ClusterStatus.INIT:
            captured['message'] = message

    external_failure = mock.Mock()
    external_failure.get.return_value = None

    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value=node_statuses), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event',
                           side_effect=_capture_event), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster'), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name',
                           return_value=record):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return captured.get('message', '')


class TestUpdateClusterStatusInitReason:

    def test_single_init_node_does_not_claim_stopped(self):
        # Regression: 1-pod cluster with Failed pod (-> INIT) used to be
        # reported as "some but not all nodes are stopped".
        msg = _capture_init_log_message(
            {'pod-0': (status_lib.ClusterStatus.INIT, None)})
        assert 'stopped' not in msg, msg
        assert 'INIT' in msg, msg

    def test_some_init_some_up_does_not_claim_stopped(self):
        msg = _capture_init_log_message(
            {
                'pod-0': (status_lib.ClusterStatus.UP, None),
                'pod-1': (status_lib.ClusterStatus.INIT, None),
            },
            launched_nodes=2)
        assert 'stopped' not in msg, msg
        assert 'INIT' in msg, msg

    def test_mixed_up_and_stopped_preserves_existing_message(self):
        # Mix of UP and STOPPED (no INIT) should still report "some but
        # not all nodes are stopped" as before.
        msg = _capture_init_log_message(
            {
                'pod-0': (status_lib.ClusterStatus.UP, None),
                'pod-1': (status_lib.ClusterStatus.STOPPED, None),
            },
            launched_nodes=2)
        assert 'some but not all nodes are stopped' in msg, msg

    def test_some_nodes_terminated(self):
        # 1 of 2 launched nodes is missing from node_statuses.
        msg = _capture_init_log_message(
            {'pod-0': (status_lib.ClusterStatus.UP, None)}, launched_nodes=2)
        assert 'one or more nodes terminated' in msg, msg
