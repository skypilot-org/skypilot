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

    def test_eviction_recovered_from_pod_events(self):
        # When pod.status names no cause (reason=None) but an eviction is in
        # the pod's events, the abnormal reason should surface it instead of
        # the generic message.
        evict = ('Evicted: Pod ephemeral local storage usage exceeds the '
                 'total limit of containers 2Gi.')
        with mock.patch.object(
                backend_utils.global_user_state,
                'get_cluster_yaml_dict',
                return_value={'provider': {
                    'namespace': 'default',
                    'context': 'ctx'
                }}), \
             mock.patch.object(
                 backend_utils.k8s_instance,
                 'get_cluster_failure_reason_from_events',
                 return_value=evict):
            msg = _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)})
        assert 'Evicted' in msg, msg
        assert 'ephemeral' in msg, msg

    def test_oom_recovered_from_pod_last_state(self):
        # A container that OOMKilled and restarted reports Ready again, so the
        # live per-pod status names no cause and the events lookup finds
        # nothing (OOM is not a pod event). The durable last_state reason is
        # recovered from the pods fallback.
        oom = 'OOMKilled (exit code 137)'
        with mock.patch.object(
                backend_utils.global_user_state,
                'get_cluster_yaml_dict',
                return_value={'provider': {
                    'namespace': 'default',
                    'context': 'ctx'
                }}), \
             mock.patch.object(
                 backend_utils.k8s_instance,
                 'get_cluster_failure_reason_from_events',
                 return_value=None), \
             mock.patch.object(
                 backend_utils.k8s_instance,
                 'get_cluster_failure_reason_from_pods',
                 return_value=oom):
            msg = _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)})
        assert 'OOMKilled' in msg, msg

    def test_some_nodes_terminated(self):
        # 1 of 2 launched nodes is missing from node_statuses.
        msg = _capture_init_log_message(
            {'pod-0': (status_lib.ClusterStatus.UP, None)}, launched_nodes=2)
        assert 'one or more nodes terminated' in msg, msg


def _run_update_status_pod_loss(record_status,
                                flag_enabled,
                                pod_loss_reason='SystemOOM victim: python'):
    """Drive _update_cluster_status for a Kubernetes cluster whose pods are
    gone (empty node_statuses). Returns (events, post_teardown_mock).

    events is a list of (new_status, message) captured from add_cluster_event;
    post_teardown_mock is the mocked CloudVmRayBackend.post_teardown_cleanup.
    """
    handle = _make_handle()
    record = _make_record(handle, status=record_status)
    events = []

    def _capture_event(cluster_name, new_status, message, event_type, **kwargs):
        events.append((new_status, message))

    external_failure = mock.Mock()
    external_failure.get.return_value = None

    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value={}), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(backend_utils.skypilot_config,
                           'get_effective_region_config',
                           return_value=flag_enabled), \
         mock.patch.object(backend_utils, '_kubernetes_pod_loss_reason',
                           return_value=pod_loss_reason), \
         mock.patch.object(backend_utils.backends,
                           'CloudVmRayBackend') as mock_backend_cls, \
         mock.patch.object(backend_utils.global_user_state, 'add_cluster_event',
                           side_effect=_capture_event), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster'), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name',
                           return_value=record):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return events, mock_backend_cls.return_value.post_teardown_cleanup


class TestKubernetesDestructiveStop:
    """`kubernetes.allow_unmanaged_cluster_destructive_stop` behavior when a
    cluster's pods disappear (e.g. OOM / eviction / node loss)."""

    def test_pod_loss_stops_cluster_when_flag_enabled(self):
        # Flag on + pods gone => STOP (terminate=False), keep the record/PVC.
        events, post_teardown = _run_update_status_pod_loss(
            status_lib.ClusterStatus.UP, flag_enabled=True)
        post_teardown.assert_called_once()
        assert post_teardown.call_args.kwargs.get('terminate') is False
        stopped_msgs = [
            m for s, m in events if s == status_lib.ClusterStatus.STOPPED
        ]
        assert stopped_msgs, events
        # The detected cause is surfaced in the event.
        assert 'SystemOOM' in stopped_msgs[0]

    def test_pod_loss_terminates_when_flag_disabled(self):
        # Flag off => existing behavior: terminate (terminate=True).
        _, post_teardown = _run_update_status_pod_loss(
            status_lib.ClusterStatus.UP, flag_enabled=False)
        post_teardown.assert_called_once()
        assert post_teardown.call_args.kwargs.get('terminate') is True

    def test_already_stopped_cluster_is_preserved(self):
        # An already-STOPPED cluster with no pods stays STOPPED without
        # re-running teardown.
        _, post_teardown = _run_update_status_pod_loss(
            status_lib.ClusterStatus.STOPPED, flag_enabled=True)
        post_teardown.assert_not_called()

    def test_generic_cause_when_no_reason_detected(self):
        # Container cgroup OOMKill leaves no recoverable signal; fall back to
        # the generic cause.
        events, post_teardown = _run_update_status_pod_loss(
            status_lib.ClusterStatus.UP,
            flag_enabled=True,
            pod_loss_reason=None)
        post_teardown.assert_called_once()
        stopped_msgs = [
            m for s, m in events if s == status_lib.ClusterStatus.STOPPED
        ]
        assert stopped_msgs, events
        assert 'OOM, eviction, or node loss' in stopped_msgs[0]
