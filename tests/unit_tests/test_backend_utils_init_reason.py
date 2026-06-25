"""Regression tests for _update_cluster_status init_reason for abnormal clusters.

When the Kubernetes provisioner maps a Failed pod to ClusterStatus.INIT,
a single-pod sky cluster ends up with node_statuses == {pod: (INIT, ...)}
and handle.launched_nodes == 1. The previous logic fell through to
"some but not all nodes are stopped", which is nonsensical for a single
INIT node. _update_cluster_status should now recognize nodes in
unexpected (non-UP, non-STOPPED) states and report them distinctly.
"""
import contextlib
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


def _collect_cluster_events_with_ray_status(run_return_value,
                                            *,
                                            count_healthy=None,
                                            launched_nodes=2):
    """Drive _update_cluster_status while controlling the `ray status` check.

    All nodes report UP from the cloud/k8s API (so all_nodes_up is True and the
    Ray health check on the head node is exercised). `run_return_value` is what
    the head node's command runner returns for the `ray status` command: a
    non-zero return code simulates the control plane being unreachable, while a
    zero return code with a mocked `count_healthy` simulates a successful query
    that reports a given (ready_head, ready_workers).

    Returns the list of (status, message) tuples passed to add_cluster_event.
    """
    handle = _make_handle()
    handle.launched_nodes = launched_nodes
    handle.num_ips_per_node = 1
    record = _make_record(handle)

    node_statuses = {
        f'pod-{i}': (status_lib.ClusterStatus.UP, None)
        for i in range(launched_nodes)
    }

    head_runner = mock.Mock()
    head_runner.run.return_value = run_return_value
    handle.get_command_runners.return_value = [head_runner]

    events = []

    def _capture_event(cluster_name, new_status, message, event_type, **kwargs):
        events.append((new_status, message))

    external_failure = mock.Mock()
    external_failure.get.return_value = None

    patches = [
        mock.patch.object(backend_utils,
                          '_query_cluster_status_via_cloud_api',
                          return_value=node_statuses),
        mock.patch.object(backend_utils, 'ExternalFailureSource',
                          external_failure),
        # In the healthy (UP) path the backend is fetched to check for
        # autostopping; a plain Mock is not a CloudVmRayBackend, so that
        # check is skipped.
        mock.patch.object(backend_utils,
                          'get_backend_from_handle',
                          return_value=mock.Mock()),
        # Keep the abnormal-path reason deterministic and avoid real sleeps in
        # the ray status retry loop.
        mock.patch.object(backend_utils,
                          '_summarize_pod_reasons',
                          return_value=''),
        mock.patch.object(backend_utils.time, 'sleep'),
        mock.patch.object(backend_utils.global_user_state,
                          'add_cluster_event',
                          side_effect=_capture_event),
        mock.patch.object(backend_utils.global_user_state,
                          'add_or_update_cluster'),
        mock.patch.object(backend_utils.global_user_state,
                          'get_cluster_from_name',
                          return_value=record),
    ]
    if count_healthy is not None:
        patches.append(
            mock.patch.object(backend_utils,
                              '_count_healthy_nodes_from_ray',
                              return_value=count_healthy))

    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)
    return events


class TestRayStatusTransientConnectivity:
    """`ray status` failing to *execute* (a transient control-plane error)
    must not be mistaken for the Ray cluster being down."""

    def test_unreachable_control_plane_keeps_cluster_up(self):
        # Every `ray status` attempt fails to connect (e.g. `connect:
        # operation not permitted`, a 521, or a timeout). The cloud API still
        # reports all nodes UP, so the cluster must stay UP rather than flip
        # to INIT (which would trigger a destructive managed-job recovery).
        events = _collect_cluster_events_with_ray_status(
            (255, '', 'dial tcp 10.112.64.1:443: connect: operation not '
             'permitted'))
        statuses = [status for status, _ in events]
        assert status_lib.ClusterStatus.INIT not in statuses, events
        assert status_lib.ClusterStatus.UP in statuses, events

    def test_ray_reports_missing_nodes_still_goes_init(self):
        # Guard against masking real degradation: a SUCCESSFUL `ray status`
        # that reports fewer ready nodes than expected must still mark the
        # cluster INIT.
        events = _collect_cluster_events_with_ray_status(
            (0, 'ray status output', ''), count_healthy=(1, 0))
        init_messages = [
            message for status, message in events
            if status == status_lib.ClusterStatus.INIT
        ]
        assert init_messages, events
        assert 'ray cluster is unhealthy (1/2 ready)' in init_messages[0], (
            init_messages)
