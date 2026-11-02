"""Abnormal Kubernetes clusters: the event says where a pod went, not only how.

A pod evicted from a failed node is Failed or Terminating, so the status
refresh records it as abnormal. The event must name the node and its state next
to the pod's own reason, and a pod that disappeared must be explained by its
deletion events, not only by its node.
"""
import datetime
import time
from types import SimpleNamespace
from unittest import mock

from sky import backends
from sky import clouds
from sky.backends import backend_utils
from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import status_lib

_ABNORMAL = 'Cluster is abnormal because'
_TAINT = 'node.kubernetes.io/unreachable'
_DISRUPTED = ('Disrupted: DeletionByTaintManager (Taint manager: deleting due '
              'to NoExecute taint) on node n1.\nLast known state: PodFailed.')


def _handle(launched_nodes, instances=None, cloud=None):
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cached_cluster_info = (SimpleNamespace(
        instances=instances) if instances is not None else None)
    handle.cluster_name = 'c'
    handle.cluster_name_on_cloud = 'c-1234'
    handle.cluster_yaml = '/fake/cluster.yaml'
    handle.launched_nodes = launched_nodes
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = cloud or clouds.Kubernetes()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _run(node_statuses,
         *,
         launched_nodes=1,
         instances=None,
         status=status_lib.ClusterStatus.UP,
         node_health=None,
         termination_cause=None,
         last_event=None,
         missing_node_reason=None,
         cloud=None,
         ever_up=True):
    """Drive _update_cluster_status; return (INIT events, k8s mocks)."""
    handle = _handle(launched_nodes, instances, cloud)
    record = {
        'handle': handle,
        'status': status,
        'cluster_hash': 'h',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
        'cluster_ever_up': ever_up,
    }
    events = []

    def add_event(cluster_name, new_status, message, event_type, **kwargs):
        del cluster_name, event_type, kwargs
        if new_status == status_lib.ClusterStatus.INIT:
            events.append(message)

    external_failure = mock.Mock()
    external_failure.get.return_value = None
    health = mock.Mock(return_value=node_health or {})
    cause = mock.Mock(return_value=termination_cause)
    missing_node = mock.Mock(return_value=missing_node_reason)
    gus = backend_utils.global_user_state
    with mock.patch.object(backend_utils, '_query_cluster_status_via_cloud_api',
                           return_value=node_statuses), \
         mock.patch.object(backend_utils, 'ExternalFailureSource',
                           external_failure), \
         mock.patch.object(gus, 'add_cluster_event', side_effect=add_event), \
         mock.patch.object(gus, 'add_or_update_cluster'), \
         mock.patch.object(gus, 'get_cluster_from_name', return_value=record), \
         mock.patch.object(gus, 'get_cluster_yaml_dict',
                           return_value={'provider': {'namespace': 'ns'}}), \
         mock.patch.object(gus, 'get_last_cluster_event',
                           return_value=last_event), \
         mock.patch.object(k8s_instance, 'get_node_health_for_cluster',
                           health), \
         mock.patch.object(k8s_instance, 'get_cluster_termination_cause',
                           cause), \
         mock.patch.object(k8s_instance, 'get_missing_node_reason',
                           missing_node), \
         mock.patch.object(k8s_instance,
                           'get_cluster_failure_reason_from_events',
                           return_value=None), \
         mock.patch.object(k8s_instance,
                           'get_cluster_failure_reason_from_pods',
                           return_value=None):
        backend_utils._update_cluster_status('c',
                                             record,
                                             retry_if_missing=False)
    return events, SimpleNamespace(health=health,
                                   cause=cause,
                                   missing_node=missing_node)


def _infos(node):
    return [SimpleNamespace(node_name=node)]


class TestNodeNamedWithThePodReason:

    def test_a_failed_pod_on_a_notready_node(self):
        """The real case: the head was evicted from its NotReady node."""
        events, k8s = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, _DISRUPTED)},
            node_health={
                'n1': k8s_instance.NodeHealthInfo(
                    f'NotReady (NoExecute taint {_TAINT})', ['pod-0'])
            })
        k8s.health.assert_called_once()
        assert events == [
            f'{_ABNORMAL} node n1 is NotReady (NoExecute taint {_TAINT}), '
            'affecting 1 out of 1 pods: pod-0 is not ready (Disrupted: '
            'DeletionByTaintManager (Taint manager: deleting due to NoExecute '
            'taint).\nLast known state: PodFailed.). Transitioned to INIT '
            '(details: cluster is unhealthy).'
        ]

    def test_the_node_lookup_filters_by_the_pods_tolerations(self):
        _, k8s = _run({'pod-0': (status_lib.ClusterStatus.INIT, _DISRUPTED)})
        kwargs = k8s.health.call_args.kwargs
        assert kwargs['tolerations'] == []
        assert isinstance(kwargs['since'], datetime.datetime)

    def test_no_pod_reason_means_no_node_lookup(self):
        _, k8s = _run({'pod-0': (status_lib.ClusterStatus.INIT, None)})
        k8s.health.assert_not_called()

    def test_an_init_cluster_without_a_cause_skips_the_lookup(self):
        """Nothing would be written, so nothing is read."""
        _, k8s = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, 'pod not ready (x)')},
            status=status_lib.ClusterStatus.INIT)
        k8s.health.assert_not_called()

    def test_pods_on_a_healthy_node_read_as_before(self):
        events, _ = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, 'Error (exit code 3)')})
        assert events == [
            f'{_ABNORMAL} pod-0 is not ready (Error (exit code 3)). '
            'Transitioned to INIT (details: cluster is unhealthy).'
        ]


class TestPartialLoss:
    """Two-pod cluster; pod-1 (on n1) is gone, pod-0 (on n0) still runs."""

    _INSTANCES = {'pod-0': _infos('n0'), 'pod-1': _infos('n1')}
    _UP = {'pod-0': (status_lib.ClusterStatus.UP, None)}
    _CAUSE = 'Pod evicted by Kubernetes: NoExecute taint k=v on node n1.'

    def test_the_missing_pods_deletion_cause_is_named(self):
        events, k8s = _run(self._UP,
                           launched_nodes=2,
                           instances=self._INSTANCES,
                           termination_cause=self._CAUSE)
        assert k8s.cause.call_args.args[1] == {'pod-1': 'n1'}
        assert events == [
            f'{_ABNORMAL} one or more nodes terminated (Pod evicted by '
            'Kubernetes: NoExecute taint k=v on node n1). Transitioned to '
            'INIT (details: cluster is unhealthy).'
        ]

    def test_the_surviving_pods_reason_is_kept_too(self):
        events, _ = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, 'Error (exit code 3)')},
            launched_nodes=2,
            instances=self._INSTANCES,
            termination_cause=self._CAUSE)
        assert events == [
            f'{_ABNORMAL} one or more nodes terminated (Pod evicted by '
            'Kubernetes: NoExecute taint k=v on node n1; pod-0 is not ready '
            '(Error (exit code 3))). Transitioned to INIT (details: cluster '
            'is unhealthy).'
        ]

    def test_an_init_cluster_reports_the_first_loss_once(self):
        """Already INIT for another reason; the pod loss is news."""
        events, k8s = _run(self._UP,
                           launched_nodes=2,
                           instances=self._INSTANCES,
                           status=status_lib.ClusterStatus.INIT,
                           termination_cause=self._CAUSE,
                           last_event=f'{_ABNORMAL} ray cluster is unhealthy')
        k8s.cause.assert_called_once()
        assert len(events) == 1 and self._CAUSE.rstrip('.') in events[0]

    def test_an_init_cluster_does_not_repoll_a_reported_loss(self):
        events, k8s = _run(
            self._UP,
            launched_nodes=2,
            instances=self._INSTANCES,
            status=status_lib.ClusterStatus.INIT,
            termination_cause=self._CAUSE,
            last_event=f'{_ABNORMAL} one or more nodes terminated (…).')
        k8s.cause.assert_not_called()
        assert not events

    def test_falls_back_to_the_nodes_state(self):
        events, k8s = _run(
            self._UP,
            launched_nodes=2,
            instances=self._INSTANCES,
            missing_node_reason='Node n1 is NotReady; pods lost.')
        assert k8s.missing_node.call_args.args[0] == ['n1']
        assert events == [
            f'{_ABNORMAL} one or more nodes terminated (Node n1 is NotReady; '
            'pods lost). Transitioned to INIT (details: cluster is unhealthy).'
        ]

    def test_a_vm_cluster_reports_a_first_loss_without_kubernetes(self):
        events, k8s = _run({'i-0': (status_lib.ClusterStatus.UP, None)},
                           launched_nodes=2,
                           status=status_lib.ClusterStatus.INIT,
                           last_event=f'{_ABNORMAL} ray cluster is unhealthy',
                           cloud=clouds.GCP())
        k8s.cause.assert_not_called()
        assert events == [
            f'{_ABNORMAL} one or more nodes terminated. Transitioned to INIT '
            '(details: cluster is unhealthy).'
        ]

    def test_a_reported_loss_is_not_overwritten_by_the_next_refresh(self):
        """The next refresh no longer looks the missing pod up; writing what
        the survivor says would bury the loss cause."""
        events, k8s = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, 'Error (exit code 3)')},
            launched_nodes=2,
            instances=self._INSTANCES,
            status=status_lib.ClusterStatus.INIT,
            termination_cause=self._CAUSE,
            last_event=(
                f'{_ABNORMAL} one or more nodes terminated (Pod evicted '
                'by Kubernetes: NoExecute taint k=v on node n1; pod-0 '
                'is not ready (Error (exit code 3))). Transitioned to '
                'INIT (details: cluster is unhealthy).'))
        assert not events
        k8s.cause.assert_not_called()
        k8s.health.assert_not_called()

    def test_a_first_loss_on_an_init_cluster_reads_the_nodes(self):
        _, k8s = _run(
            {'pod-0': (status_lib.ClusterStatus.INIT, 'pod not ready (x)')},
            launched_nodes=2,
            instances=self._INSTANCES,
            status=status_lib.ClusterStatus.INIT,
            last_event=f'{_ABNORMAL} ray cluster is unhealthy')
        k8s.health.assert_called_once()

    def test_an_interrupted_launch_is_not_reported_as_a_loss(self):
        events, k8s = _run(self._UP,
                           launched_nodes=2,
                           instances=self._INSTANCES,
                           status=status_lib.ClusterStatus.INIT,
                           last_event='Provisioning on Kubernetes',
                           ever_up=False)
        assert not events
        k8s.cause.assert_not_called()

    def test_no_cause_found_still_closes_the_gate(self):
        """Written once, generic, so later refreshes stop polling."""
        events, _ = _run(self._UP,
                         launched_nodes=2,
                         instances=self._INSTANCES,
                         status=status_lib.ClusterStatus.INIT,
                         last_event=f'{_ABNORMAL} ray cluster is unhealthy')
        assert events == [
            f'{_ABNORMAL} one or more nodes terminated. Transitioned to INIT '
            '(details: cluster is unhealthy).'
        ]


class TestNodeTaints:
    """_check_nodes_health, Kubernetes API path."""

    @staticmethod
    def _node(ready, taints=(), unschedulable=False):
        now = datetime.datetime.now(datetime.timezone.utc)
        return SimpleNamespace(status=SimpleNamespace(conditions=[
            SimpleNamespace(type='Ready', status='True' if ready else 'Unknown')
        ]),
                               spec=SimpleNamespace(unschedulable=unschedulable,
                                                    taints=[
                                                        SimpleNamespace(
                                                            key=k,
                                                            value=v,
                                                            effect='NoExecute',
                                                            time_added=now)
                                                        for k, v in taints
                                                    ]))

    def _check(self, monkeypatch, node, tolerations=()):
        api = mock.Mock()
        api.read_node.return_value = node
        monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                            lambda *a, **kw: api)
        monkeypatch.setattr(k8s_instance.plugin_extensions.NodeInfoSource,
                            'get', staticmethod(lambda context: None))
        return k8s_instance._check_nodes_health(
            'ctx', {'n1'},
            tolerations=list(tolerations),
            since=datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(hours=1))

    def test_a_notready_node_names_its_evicting_taint(self, monkeypatch):
        assert self._check(monkeypatch,
                           self._node(False, [(_TAINT, None)])) == {
                               'n1': f'NotReady (NoExecute taint {_TAINT})'
                           }

    def test_a_cordoned_node_names_its_evicting_taint(self, monkeypatch):
        node = self._node(True, [('drain', 'now')], unschedulable=True)
        assert self._check(monkeypatch, node) == {
            'n1': 'cordoned (NoExecute taint drain=now)'
        }

    def test_a_tolerated_taint_is_not_named(self, monkeypatch):
        node = self._node(True, [('nvidia.com/gpu', 'present')])
        assert self._check(monkeypatch, node, [{
            'key': 'nvidia.com/gpu',
            'operator': 'Exists',
            'effect': 'NoExecute'
        }]) == {}

    def test_an_untolerated_taint_on_a_ready_node(self, monkeypatch):
        node = self._node(True, [('drain', 'now')])
        assert self._check(monkeypatch, node) == {
            'n1': 'tainted (NoExecute taint drain=now)'
        }


class TestDisruptionNamesTheNode:

    @staticmethod
    def _pod(condition_type, node='n1'):
        return SimpleNamespace(metadata=SimpleNamespace(name='c-head'),
                               spec=SimpleNamespace(node_name=node),
                               status=SimpleNamespace(
                                   start_time=None,
                                   reason=None,
                                   message=None,
                                   container_statuses=None,
                                   conditions=[
                                       SimpleNamespace(
                                           type=condition_type,
                                           reason='DeletionByTaintManager',
                                           message='Taint manager: deleting',
                                           last_transition_time=None)
                                   ]))

    def test_disruption_target(self, monkeypatch):
        monkeypatch.setattr(k8s_instance.global_user_state, 'add_cluster_event',
                            mock.Mock())
        reason = k8s_instance._get_pod_termination_reason(
            self._pod('DisruptionTarget'), 'c')
        assert reason.startswith('Disrupted: DeletionByTaintManager (Taint '
                                 'manager: deleting) on node n1.\n')
        assert k8s_instance.pod_reason_identifies_cause(reason)

    def test_the_condensed_reason_names_it_too(self):
        assert kubernetes_utils.disruption_reason(
            'DeletionByTaintManager', '', self._pod('DisruptionTarget')) == (
                'Disrupted: DeletionByTaintManager on node n1')

    def test_an_unscheduled_pod_has_no_node(self):
        assert kubernetes_utils.disruption_reason(
            'X', 'm', self._pod('DisruptionTarget',
                                node=None)) == ('Disrupted: X (m)')

    def test_kueue_text_is_untouched(self, monkeypatch):
        monkeypatch.setattr(k8s_instance.global_user_state, 'add_cluster_event',
                            mock.Mock())
        reason = k8s_instance._get_pod_termination_reason(
            self._pod('TerminationTarget'), 'c')
        assert reason.startswith('Preempted by Kueue: DeletionByTaintManager '
                                 '(Taint manager: deleting).\n')
