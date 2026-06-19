"""Regression tests for _update_cluster_status init_reason for abnormal clusters.

When the Kubernetes provisioner maps a Failed pod to ClusterStatus.INIT,
a single-pod sky cluster ends up with node_statuses == {pod: (INIT, ...)}
and handle.launched_nodes == 1. The previous logic fell through to
"some but not all nodes are stopped", which is nonsensical for a single
INIT node. _update_cluster_status should now recognize nodes in
unexpected (non-UP, non-STOPPED) states and report them distinctly.
"""
import datetime
import time
from unittest import mock

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.utils import status_lib


def _make_handle(cached_cluster_info=None):
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cached_cluster_info = cached_cluster_info
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


def _capture_init_log_message(node_statuses,
                              launched_nodes=1,
                              cached_cluster_info=None,
                              status=status_lib.ClusterStatus.UP):
    """Drive _update_cluster_status with a fake node_statuses and return
    the log_message passed to global_user_state.add_cluster_event when the
    cluster transitions to INIT."""
    handle = _make_handle(cached_cluster_info=cached_cluster_info)
    handle.launched_nodes = launched_nodes
    record = _make_record(handle, status=status)

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

    def test_recovered_reason_is_bounded_by_the_cluster_launch(self):
        # Pod names are a function of the cluster name and Kubernetes keeps
        # events for an hour, so a cluster that reuses the name of one that
        # was torn down would otherwise be told why the earlier cluster's
        # pods were deleted. This cluster's own history starts when it was
        # launched.
        launched_at = 1700000000
        handle = _make_handle()
        record = _make_record(handle)
        record['launched_at'] = launched_at
        events_lookup = mock.Mock(return_value=None)
        external_failure = mock.Mock()
        external_failure.get.return_value = None
        with mock.patch.object(backend_utils,
                               '_query_cluster_status_via_cloud_api',
                               return_value={
                                   'pod-0': (status_lib.ClusterStatus.UP, None)
                               }), \
             mock.patch.object(backend_utils, 'ExternalFailureSource',
                               external_failure), \
             mock.patch.object(backend_utils.global_user_state,
                               'add_cluster_event'), \
             mock.patch.object(backend_utils.global_user_state,
                               'add_or_update_cluster'), \
             mock.patch.object(backend_utils.global_user_state,
                               'get_cluster_from_name',
                               return_value=record), \
             mock.patch.object(backend_utils.global_user_state,
                               'get_cluster_yaml_dict',
                               return_value={'provider': {
                                   'namespace': 'default',
                                   'context': 'ctx'
                               }}), \
             mock.patch.object(backend_utils.k8s_instance,
                               'get_cluster_failure_reason_from_events',
                               events_lookup), \
             mock.patch.object(backend_utils.k8s_instance,
                               'get_cluster_failure_reason_from_pods',
                               return_value=None):
            backend_utils._update_cluster_status('test-cluster',
                                                 record,
                                                 retry_if_missing=False)
        assert events_lookup.call_count == 1
        assert events_lookup.call_args.kwargs['since'] == (
            datetime.datetime.fromtimestamp(launched_at,
                                            tz=datetime.timezone.utc))

    def test_some_nodes_terminated(self):
        # 1 of 2 launched nodes is missing from node_statuses.
        msg = _capture_init_log_message(
            {'pod-0': (status_lib.ClusterStatus.UP, None)}, launched_nodes=2)
        assert 'one or more nodes terminated' in msg, msg

    def test_some_nodes_terminated_surfaces_node_state(self):
        # A node that vanished from under the cluster is named in the event.
        handle_info = mock.Mock()
        handle_info.instances = {
            'pod-0': [mock.Mock(node_name='node-0')],
            'pod-1': [mock.Mock(node_name='node-1')],
        }
        with mock.patch.object(
                backend_utils.global_user_state,
                'get_cluster_yaml_dict',
                return_value={'provider': {
                    'namespace': 'default',
                    'context': 'ctx'
                }}), \
             mock.patch.object(backend_utils.k8s_instance,
                               'get_missing_node_reason',
                               return_value='node node-1 no longer exists'
                              ) as mock_reason:
            msg = _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)},
                launched_nodes=2,
                cached_cluster_info=handle_info)
        assert 'one or more nodes terminated' in msg, msg
        assert 'node node-1 no longer exists' in msg, msg
        # Only the missing pod's node: node-0 still runs pod-0.
        assert mock_reason.call_args.args[0] == ['node-1']

    def test_some_nodes_terminated_falls_back_when_nodes_healthy(self):
        # Nodes all present and healthy -> keep the generic message.
        handle_info = mock.Mock()
        handle_info.instances = {
            'pod-0': [mock.Mock(node_name='node-0')],
            'pod-1': [mock.Mock(node_name='node-1')],
        }
        with mock.patch.object(
                backend_utils.global_user_state,
                'get_cluster_yaml_dict',
                return_value={'provider': {
                    'namespace': 'default',
                    'context': 'ctx'
                }}), \
             mock.patch.object(backend_utils.k8s_instance,
                               'get_missing_node_reason',
                               return_value=None):
            msg = _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)},
                launched_nodes=2,
                cached_cluster_info=handle_info)
        assert 'one or more nodes terminated' in msg, msg

    def test_some_nodes_terminated_skips_node_query_when_already_init(self):
        # A cluster stays INIT with the pod missing until it is downed or
        # relaunched, and the event is not re-written once it is INIT. Don't
        # poll the K8s API every refresh to build a discarded string.
        handle_info = mock.Mock()
        handle_info.instances = {
            'pod-0': [mock.Mock(node_name='node-0')],
            'pod-1': [mock.Mock(node_name='node-1')],
        }
        with mock.patch.object(backend_utils.k8s_instance,
                               'get_missing_node_reason') as mock_reason:
            _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)},
                launched_nodes=2,
                cached_cluster_info=handle_info,
                status=status_lib.ClusterStatus.INIT)
        mock_reason.assert_not_called()

    def test_some_nodes_terminated_without_cached_cluster_info(self):
        # No cached ClusterInfo (e.g. an older cluster) -> generic message,
        # and no attempt to query Kubernetes.
        with mock.patch.object(backend_utils.k8s_instance,
                               'get_missing_node_reason') as mock_reason:
            msg = _capture_init_log_message(
                {'pod-0': (status_lib.ClusterStatus.UP, None)},
                launched_nodes=2,
                cached_cluster_info=None)
        assert 'one or more nodes terminated' in msg, msg
        mock_reason.assert_not_called()


class TestUpdateClusterStatusBareHandle:
    """A bare pre-provision handle (no cached IPs, has_ray=False) must not
    be promoted to UP by a status refresh.

    `sky launch` persists such a handle at INIT before provisioning starts;
    the completed handle is only persisted after runtime setup. If the launch
    is interrupted in between while the nodes keep running (on Kubernetes,
    pods report Running long before runtime setup finishes), a refresh used
    to skip the ray health check (has_ray=False) and mark the cluster UP
    with head_ip=None — making every subsequent operation (e.g. sky exec)
    fail with ClusterNotUpError.
    """

    def _refresh(self, handle):
        record = _make_record(handle, status=status_lib.ClusterStatus.INIT)

        backend = mock.Mock(spec=backends.CloudVmRayBackend)
        backend.is_definitely_autostopping.return_value = False

        external_failure = mock.Mock()
        external_failure.get.return_value = None

        events = []

        def _capture_event(cluster_name, new_status, message, event_type,
                           **kwargs):
            events.append((new_status, message))

        add_or_update = mock.Mock()
        with mock.patch.object(backend_utils,
                               '_query_cluster_status_via_cloud_api',
                               return_value={
                                   'pod-0': (status_lib.ClusterStatus.UP, None)
                               }), \
             mock.patch.object(backend_utils, 'ExternalFailureSource',
                               external_failure), \
             mock.patch.object(backend_utils, 'get_backend_from_handle',
                               return_value=backend), \
             mock.patch.object(backend_utils.global_user_state,
                               'add_cluster_event',
                               side_effect=_capture_event), \
             mock.patch.object(backend_utils.global_user_state,
                               'add_or_update_cluster', add_or_update), \
             mock.patch.object(backend_utils.global_user_state,
                               'get_cluster_from_name',
                               return_value=record):
            backend_utils._update_cluster_status('test-cluster',
                                                 record,
                                                 retry_if_missing=False)
        return add_or_update, events

    def test_no_cached_ips_is_not_promoted_to_up(self):
        handle = _make_handle()
        handle.provision_runtime_metadata.has_ray = False
        handle.head_ip = None

        add_or_update, events = self._refresh(handle)

        assert add_or_update.call_count == 1
        assert add_or_update.call_args.kwargs['ready'] is False
        # The cluster must not be marked UP (no UP status event either; the
        # record is already INIT so no INIT event is re-added).
        up_events = [e for e in events if e[0] == status_lib.ClusterStatus.UP]
        assert not up_events, up_events

    def test_cached_ips_without_ray_is_promoted_to_up(self):
        # A fully provisioned cluster whose runtime legitimately has no ray
        # (has_ray=False) but has cached IPs must still be marked UP.
        handle = _make_handle()
        handle.provision_runtime_metadata.has_ray = False
        handle.head_ip = '1.2.3.4'

        add_or_update, events = self._refresh(handle)

        assert add_or_update.call_count == 1
        assert add_or_update.call_args.kwargs['ready'] is True
        assert any(status == status_lib.ClusterStatus.UP
                   for status, _ in events), events

    def test_stopped_then_manually_restarted_still_runs_ray_check(self):
        # Stopping a cluster clears head_ip, so a cluster that was stopped
        # and then restarted outside SkyPilot has head_ip=None but
        # has_ray=True. The ray health check must still run: it is what
        # detects the unreachable (stale) IP and prints the
        # "sky start ... to recover from INIT status" hint.
        handle = _make_handle()
        handle.launched_resources.cloud = clouds.AWS()
        handle.provision_runtime_metadata.has_ray = True
        handle.head_ip = None
        # The stale head IP from before the stop is unreachable.
        head_runner = mock.Mock()
        head_runner.run.return_value = (
            255, '',
            'ssh: connect to host 1.2.3.4 port 22: Connection timed out')
        handle.get_command_runners.return_value = [head_runner]

        with mock.patch.object(backend_utils.logger, 'warning') as warning, \
             mock.patch.object(backend_utils,
                               'DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS', 0):
            add_or_update, events = self._refresh(handle)

        head_runner.run.assert_called_once()
        assert any('to recover from INIT status' in str(c)
                   for c in warning.call_args_list), warning.call_args_list
        assert add_or_update.call_args.kwargs['ready'] is False
        assert not any(status == status_lib.ClusterStatus.UP
                       for status, _ in events), events


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
                           'get_effective_workspace_region_config',
                           return_value=flag_enabled), \
         mock.patch.object(backend_utils, '_kubernetes_pod_loss_reason',
                           return_value=pod_loss_reason), \
         mock.patch.object(backend_utils, '_get_terminated_cluster_reason',
                           return_value='cluster removed by refresh'), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_last_cluster_event',
                           return_value=None), \
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
