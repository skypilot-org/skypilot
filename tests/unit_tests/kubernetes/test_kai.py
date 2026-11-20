"""Tests for KAI Scheduler support."""
import copy
import datetime
from types import SimpleNamespace

from kubernetes.client import V1ContainerState
from kubernetes.client import V1ContainerStateRunning
from kubernetes.client import V1ContainerStateTerminated
from kubernetes.client import V1ContainerStatus
from kubernetes.client import V1ObjectMeta
from kubernetes.client import V1Pod
from kubernetes.client import V1PodStatus
import pytest
import urllib3

from sky import exceptions
from sky import global_user_state
from sky.adaptors import kubernetes
from sky.provision.kubernetes import instance
from sky.provision.kubernetes import kai
from sky.provision.kubernetes import utils as kubernetes_utils

CONTEXT = 'ctx'
NAMESPACE = 'ns'
CLUSTER = 'c-head'


class FakeApiException(Exception):

    def __init__(self, status):
        super().__init__(status)
        self.status = status


class FakeKai:
    """Fake PodGroups and Topologies served through the custom objects API."""

    def __init__(self):
        self.pod_groups = {}
        self.live_pods = {}
        self.calls = []
        self.pod_group_events = []
        self.event_lists = 0
        # Plurals SkyPilot may not read, and whether PodGroups are served.
        self.forbidden = set()
        self.serves_pod_groups = True
        # KAI orders levels broadest first.
        self.topologies = {
            'fabric': {
                'spec': {
                    'levels': [
                        {
                            'nodeLabel': 'topology.kubernetes.io/zone',
                            'alias': 'zone'
                        },
                        {
                            'nodeLabel': 'topology.kubernetes.io/rack'
                        },
                        {
                            'nodeLabel': 'kubernetes.io/hostname'
                        },
                    ]
                }
            }
        }

    def create_namespaced_custom_object(self, group, version, namespace, plural,
                                        body, **kwargs):
        del group, version, namespace, plural, kwargs
        name = body['metadata']['name']
        self.calls.append(('create', name))
        if not self.serves_pod_groups:
            raise FakeApiException(404)
        if name in self.pod_groups:
            raise FakeApiException(409)
        self.pod_groups[name] = copy.deepcopy(body)

    def delete_namespaced_custom_object(self, group, version, namespace, plural,
                                        name, **kwargs):
        del group, version, namespace, plural, kwargs
        self.calls.append(('delete', name))
        if self.pod_groups.pop(name, None) is None:
            raise FakeApiException(404)

    def get_namespaced_custom_object(self, group, version, namespace, plural,
                                     name, **kwargs):
        del group, version, namespace, plural, kwargs
        if name not in self.pod_groups:
            raise FakeApiException(404)
        return copy.deepcopy(self.pod_groups[name])

    def patch_namespaced_custom_object(self, group, version, namespace, plural,
                                       name, body, **kwargs):
        del group, version, namespace, plural, kwargs
        self.calls.append(('patch', name, body))
        self.pod_groups[name]['spec'].update(body['spec'])

    def get_cluster_custom_object(self, group, version, plural, name, **kwargs):
        del group, version, kwargs
        if plural in self.forbidden:
            raise FakeApiException(403)
        if name not in self.topologies:
            raise FakeApiException(404)
        return copy.deepcopy(self.topologies[name])

    def list_namespaced_event(self, namespace, field_selector, **kwargs):
        del namespace, kwargs
        self.event_lists += 1
        fields = dict(term.split('=') for term in field_selector.split(','))
        return SimpleNamespace(items=[
            event for event in self.pod_group_events
            if event.involved_object.kind == fields['involvedObject.kind'] and
            event.reason == fields['reason']
        ])


@pytest.fixture
def cluster(monkeypatch):
    fake = FakeKai()
    monkeypatch.setattr(
        kai, 'kubernetes',
        SimpleNamespace(custom_objects_api=lambda context: fake,
                        core_api=lambda context: fake,
                        api_exception=lambda: FakeApiException,
                        urllib3_http_error=kubernetes.urllib3_http_error,
                        API_TIMEOUT=kubernetes.API_TIMEOUT))
    monkeypatch.setattr(kubernetes_utils, 'filter_pods',
                        lambda *args: fake.live_pods)
    return fake


PROVIDER_CONFIG = {'kai_queue': 'q', 'kai_priority_class': 'batch'}


def _node_config(**labels):
    return {
        'metadata': {
            'labels': {
                'kai.scheduler/queue': 'q',
                **labels
            }
        },
        'spec': {
            'priorityClassName': 'batch'
        },
    }


def test_ensure_pod_group_creates_gang_from_config(cluster):
    # pod_config may override the pod's own KAI fields; the gang ignores them.
    node_config = _node_config(**{'kai.scheduler/queue': 'other', 'shard': 'a'})
    node_config['spec']['priorityClassName'] = 'other'

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, {
        **PROVIDER_CONFIG, 'kai_preemptibility': 'preemptible'
    }, node_config, 4)

    pod_group = cluster.pod_groups[CLUSTER]
    assert pod_group['spec'] == {
        'minMember': 4,
        'queue': 'q',
        'priorityClassName': 'batch',
        'preemptibility': 'preemptible',
    }
    assert pod_group['metadata']['labels'] == {
        'kai.scheduler/queue': 'other',
        'shard': 'a',
        'skypilot-cluster-name': CLUSTER,
    }


def test_ensure_pod_group_constrains_gang_to_topology(cluster):
    # A level is named by its alias or its node label.
    kai.ensure_pod_group(
        CONTEXT, NAMESPACE, CLUSTER, {
            **PROVIDER_CONFIG, 'kai_topology': 'fabric',
            'kai_required_topology_level': 'zone',
            'kai_preferred_topology_level': 'topology.kubernetes.io/rack'
        }, _node_config(), 2)

    assert cluster.pod_groups[CLUSTER]['spec']['topologyConstraint'] == {
        'topology': 'fabric',
        'requiredTopologyLevel': 'zone',
        'preferredTopologyLevel': 'topology.kubernetes.io/rack',
    }


@pytest.mark.parametrize(
    'constraint,error', [
        ({
            'kai_topology': 'missing',
            'kai_preferred_topology_level': 'zone'
        }, "'missing' does not exist"),
        ({
            'kai_topology': 'fabric',
            'kai_required_topology_level': 'rack'
        }, "has no level 'rack'"),
        ({
            'kai_topology': 'fabric',
            'kai_required_topology_level': 'topology.kubernetes.io/rack',
            'kai_preferred_topology_level': 'zone'
        }, 'must be at or below'),
    ],
    ids=['missing-topology', 'unknown-level', 'preferred-above-required'])
def test_ensure_pod_group_refuses_topology_kai_would_only_log(
        cluster, constraint, error):
    with pytest.raises(exceptions.ProvisionUnsupportedError, match=error):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, {
            **PROVIDER_CONFIG,
            **constraint
        }, _node_config(), 2)
    assert CLUSTER not in cluster.pod_groups


def test_unreadable_topology_fails_with_the_missing_permission(cluster):
    cluster.forbidden = {'topologies'}

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='permission to get topologies.kai.scheduler'):
        kai.ensure_pod_group(
            CONTEXT, NAMESPACE, CLUSTER, {
                **PROVIDER_CONFIG, 'kai_topology': 'fabric',
                'kai_preferred_topology_level': 'zone'
            }, _node_config(), 2)


def test_ensure_pod_group_omits_unset_priority(cluster):
    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, {'kai_queue': 'q'},
                         _node_config(), 1)

    assert cluster.pod_groups[CLUSTER]['spec'] == {'minMember': 1, 'queue': 'q'}


def _running_gang(**spec):
    return {
        'spec': {
            'minMember': 4,
            'queue': 'q',
            'priorityClassName': 'batch',
            **spec
        },
        'status': 'kept'
    }


def test_ensure_pod_group_keeps_running_cluster_gang(cluster):
    # A resize leaves minMember to set_gang_size, once its new pods are bound.
    cluster.pod_groups[CLUSTER] = _running_gang()
    cluster.live_pods = {CLUSTER: object()}

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                         _node_config(), 2)

    assert cluster.pod_groups[CLUSTER] == _running_gang()
    assert ('delete', CLUSTER) not in cluster.calls


def test_ensure_pod_group_explains_a_context_without_kai(cluster):
    cluster.serves_pod_groups = False

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='does not serve the KAI PodGroup API'):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                             _node_config(), 1)


@pytest.mark.parametrize('spec', [{
    'queue': 'other'
}, {
    'priorityClassName': 'other'
}, {
    'preemptibility': 'preemptible'
}, {
    'topologyConstraint': {
        'topology': 'fabric',
        'preferredTopologyLevel': 'zone'
    }
}])
def test_ensure_pod_group_refuses_changed_settings(cluster, spec):
    cluster.pod_groups[CLUSTER] = _running_gang(**spec)
    cluster.live_pods = {CLUSTER: object()}

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match=f'Cannot change KAI {next(iter(spec))}'):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                             _node_config(), 2)
    assert cluster.pod_groups[CLUSTER] == _running_gang(**spec)


def test_ensure_pod_group_replaces_stale_group(cluster):
    cluster.pod_groups[CLUSTER] = {'spec': {}, 'status': 'stale'}

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                         _node_config(), 1)

    assert cluster.calls == [('delete', CLUSTER), ('create', CLUSTER)]
    assert 'status' not in cluster.pod_groups[CLUSTER]


def test_ensure_pod_group_fails_if_stale_group_survives(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'spec': {}}
    monkeypatch.setattr(cluster, 'delete_namespaced_custom_object',
                        lambda *args, **kwargs: None)

    with pytest.raises(FakeApiException):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                             _node_config(), 1)


@pytest.mark.parametrize('kai_queue', ['q', None])
def test_cleanup_deletes_pod_group_for_kai_clusters(cluster, monkeypatch,
                                                    kai_queue):
    monkeypatch.setattr(instance, '_delete_cluster_services',
                        lambda *args: None)
    cluster.pod_groups[CLUSTER] = {}
    provider_config = {'context': CONTEXT, 'namespace': NAMESPACE}
    if kai_queue:
        provider_config['kai_queue'] = kai_queue

    instance.cleanup_cluster_resources(CLUSTER, provider_config)

    assert (CLUSTER in cluster.pod_groups) == (kai_queue is None)


def test_worker_only_termination_shrinks_the_gang_first(cluster, monkeypatch):
    # A scale-down removes every worker. Below minMember, KAI would evict the
    # head as well.
    cluster.pod_groups[CLUSTER] = _running_gang()
    cluster.live_pods = {
        name:
        V1Pod(metadata=V1ObjectMeta(name=name, labels={'ray-node-type': kind}))
        for name, kind in [('head', 'head'), ('worker', 'worker')]
    }
    monkeypatch.setattr(instance, 'is_high_availability_cluster_by_kubectl',
                        lambda *args: False)
    monkeypatch.setattr(
        instance, '_terminate_node',
        lambda namespace, context, pod_name, **kwargs: cluster.calls.append(
            ('terminate', pod_name)))

    provider_config = {
        **PROVIDER_CONFIG, 'context': CONTEXT,
        'namespace': NAMESPACE
    }
    instance.terminate_instances(CLUSTER, provider_config, worker_only=True)

    # The patch lands before any worker is deleted.
    assert [call[0] for call in cluster.calls] == ['patch', 'terminate']
    assert cluster.calls[1] == ('terminate', 'worker')
    assert cluster.pod_groups[CLUSTER]['spec']['minMember'] == 1


POD = f'{CLUSTER}-worker1'
RECLAIMED = (f'KAI evicted the pod (reclaim): Pod {NAMESPACE}/{POD} was '
             f'preempted by workload {NAMESPACE}/other')


def _pod_group_event(action, message, uid='pg-uid', at=None):
    return SimpleNamespace(
        reason='Evict',
        message=message,
        last_timestamp=at,
        metadata=SimpleNamespace(annotations={'evictor-action-type': action}),
        involved_object=SimpleNamespace(kind='PodGroup', name=CLUSTER, uid=uid))


def _evict_event(pod_name, uid='pg-uid', at=None):
    # A reclaim as KAI records it: the first line names the pod, and
    # one line per affected queue follows.
    return _pod_group_event(
        'reclaim', (f'Pod {NAMESPACE}/{pod_name} was preempted by workload '
                    f'{NAMESPACE}/other.\nq (entitlement 4 GPU) uses <GPU: 8>'),
        uid=uid,
        at=at)


POD_CREATED = datetime.datetime(2026, 10, 21, tzinfo=datetime.timezone.utc)


def _refresh_reason(monkeypatch):
    """The reason a status refresh gives for POD while it is deleted."""
    pod = V1Pod(metadata=V1ObjectMeta(name=POD,
                                      creation_timestamp=POD_CREATED,
                                      deletion_timestamp=POD_CREATED),
                status=V1PodStatus(phase='Running'))
    monkeypatch.setattr(instance, 'list_namespaced_pod', lambda *args: [pod])
    monkeypatch.setattr(instance, '_get_pod_failure_reason_from_events',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(global_user_state, 'add_cluster_event',
                        lambda *args, **kwargs: None)
    provider_config = {
        'context': CONTEXT,
        'namespace': NAMESPACE,
        'kai_queue': 'q',
        'services': [{
            'spec': {
                'selector': {
                    'component': POD
                }
            }
        }],
    }
    statuses = instance.query_instances('c',
                                        CLUSTER,
                                        provider_config,
                                        non_terminated_only=False)
    return statuses[POD][1]


def test_status_refresh_names_kai_eviction(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(f'{POD}0'), _evict_event(POD)]

    assert RECLAIMED in _refresh_reason(monkeypatch)


def test_status_refresh_names_a_stale_gang_eviction(cluster, monkeypatch):
    # KAI evicts the rest of a gang that fell below minMember, in a message
    # that ends with the pod's name.
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [
        SimpleNamespace(
            reason='Evict',
            message=('Workload doesn\'t meet the minimum required number of '
                     f'pods (2), evicting remaining pod: {NAMESPACE}/{POD}'),
            metadata=SimpleNamespace(
                annotations={'evictor-action-type': 'stalegangeviction'}),
            involved_object=SimpleNamespace(kind='PodGroup',
                                            name=CLUSTER,
                                            uid='pg-uid'))
    ]

    assert ('KAI evicted the pod (stalegangeviction): Workload doesn\'t meet '
            'the minimum required number of pods (2), evicting remaining pod: '
            f'{NAMESPACE}/{POD}') in _refresh_reason(monkeypatch)


@pytest.mark.parametrize(
    'event',
    [
        _evict_event(POD, uid='earlier-pod-group'),
        _evict_event(f'{POD}0'),
        # The same PodGroup, but before this pod existed.
        _evict_event(POD, at=POD_CREATED - datetime.timedelta(hours=1)),
    ],
    ids=['earlier-pod-group', 'other-pod', 'before-the-pod'])
def test_status_refresh_ignores_other_kai_evictions(cluster, monkeypatch,
                                                    event):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [event]

    assert 'KAI' not in _refresh_reason(monkeypatch)


HEAD = f'{CLUSTER}-head'
# What the kubelet leaves on a pod KAI deletes.
KILLING = SimpleNamespace(reason='Killing',
                          message='Stopping container ray-node')


def _termination_cause(monkeypatch, pod_events=None, kai_queue='q'):
    """Why a status refresh says the head and POD are gone."""

    def get_pod_events(context, namespace, pod_name):
        del context, namespace
        return (pod_events or {}).get(pod_name, [KILLING])

    monkeypatch.setattr(instance, '_get_pod_events', get_pod_events)
    provider_config = {'context': CONTEXT, 'namespace': NAMESPACE}
    if kai_queue is not None:
        provider_config['kai_queue'] = kai_queue
    # Neither pod's node is known, so no node is read.
    pod_nodes = {HEAD: None, POD: None}
    return instance.get_cluster_termination_cause(provider_config,
                                                  pod_nodes,
                                                  tolerations=[],
                                                  since=POD_CREATED)


# Messages as KAI Scheduler v0.18.7 words them.
@pytest.mark.parametrize(
    'action,message,expected',
    [
        ('preempt',
         f'Pod {NAMESPACE}/{HEAD} was preempted by higher priority workload '
         f'{NAMESPACE}/other',
         f'Pods preempted by KAI Scheduler: Pod {NAMESPACE}/{HEAD} was '
         f'preempted by higher priority workload {NAMESPACE}/other.'),
        ('reclaim',
         f'Pod {NAMESPACE}/{HEAD} was preempted by workload {NAMESPACE}/other.'
         '\nq uses <GPU: 8> with a quota of <GPU: 4>',
         'Pods reclaimed by KAI Scheduler for another queue: Pod '
         f'{NAMESPACE}/{HEAD} was preempted by workload {NAMESPACE}/other.'),
        ('consolidation',
         f'Pod {NAMESPACE}/{HEAD} was preempted and rescheduled due to bin '
         'packing (resource consolidation) procedure',
         'Pods evicted by KAI Scheduler for consolidation: Pod '
         f'{NAMESPACE}/{HEAD} was preempted and rescheduled due to bin '
         'packing (resource consolidation) procedure.'),
        # A worker's eviction explains the cluster as well as the head's.
        ('stalegangeviction',
         'Workload doesn\'t meet the minimum required number of pods (2), '
         f'evicting remaining pod: {NAMESPACE}/{POD}',
         'Pods evicted by KAI Scheduler: Workload doesn\'t meet the minimum '
         'required number of pods (2), evicting remaining pod: '
         f'{NAMESPACE}/{POD}.'),
    ],
    ids=['preempt', 'reclaim', 'consolidation', 'stalegangeviction'])
def test_termination_cause_names_kai_eviction(cluster, monkeypatch, action,
                                              message, expected):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_pod_group_event(action, message)]

    assert _termination_cause(monkeypatch) == expected


def test_termination_cause_off_kai_reads_no_pod_group_events(
        cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(HEAD)]

    assert _termination_cause(monkeypatch, kai_queue=None) is None
    assert cluster.event_lists == 0


def test_termination_cause_prefers_the_pods_own_deletion_event(
        cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(HEAD)]
    evicted = SimpleNamespace(reason='Evicted',
                              message='The node was low on resource: memory.',
                              reporting_component='kubelet',
                              involved_object=SimpleNamespace(name=POD))

    cause = _termination_cause(monkeypatch, {POD: [evicted]})

    assert cause == (
        'Pods evicted by kubelet: The node was low on resource: memory.')
    assert cluster.event_lists == 0


@pytest.mark.parametrize('method,error', [
    ('list_namespaced_event', FakeApiException(500)),
    ('list_namespaced_event',
     urllib3.exceptions.ReadTimeoutError(None, None, 'Read timed out.')),
    ('get_namespaced_custom_object', FakeApiException(500)),
],
                         ids=['list-error', 'list-timeout', 'pod-group-error'])
def test_termination_cause_survives_a_failed_kai_read(cluster, monkeypatch,
                                                      method, error):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(HEAD)]

    def _fail(*args, **kwargs):
        del args, kwargs
        raise error

    monkeypatch.setattr(cluster, method, _fail)

    assert _termination_cause(monkeypatch) is None


@pytest.mark.parametrize(
    'event',
    [
        _evict_event(HEAD, uid='earlier-pod-group'),
        _evict_event(f'{HEAD}0'),
        # The same PodGroup, but before the cluster launched.
        _evict_event(HEAD, at=POD_CREATED - datetime.timedelta(hours=1)),
    ],
    ids=['earlier-pod-group', 'other-pod', 'before-the-launch'])
def test_termination_cause_ignores_other_kai_evictions(cluster, monkeypatch,
                                                       event):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [event]

    assert _termination_cause(monkeypatch) is None


def _patch_pod_read(monkeypatch, pod):
    """Serves POD to kubernetes_utils, or a 404 when pod is None."""

    def read_namespaced_pod(*args, **kwargs):
        del args, kwargs
        if pod is None:
            raise FakeApiException(404)
        return pod

    monkeypatch.setattr(
        kubernetes_utils, 'kubernetes',
        SimpleNamespace(core_api=lambda context: SimpleNamespace(
            read_namespaced_pod=read_namespaced_pod,
            list_namespaced_event=lambda *args, **kwargs: SimpleNamespace(items=
                                                                          [])),
                        api_exception=lambda: FakeApiException,
                        API_TIMEOUT=kubernetes.API_TIMEOUT))


# A pod KAI is still deleting can report its container as running.
@pytest.mark.parametrize('container_state', [
    V1ContainerState(
        terminated=V1ContainerStateTerminated(exit_code=137, reason='Error')),
    V1ContainerState(running=V1ContainerStateRunning()),
],
                         ids=['killed', 'being-deleted'])
def test_exec_failure_on_an_evicted_pod_names_kai_eviction(
        cluster, monkeypatch, container_state):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(POD)]
    _patch_pod_read(
        monkeypatch,
        V1Pod(metadata=V1ObjectMeta(name=POD,
                                    annotations={'pod-group-name': CLUSTER}),
              status=V1PodStatus(phase='Running',
                                 container_statuses=[
                                     V1ContainerStatus(name='ray-node',
                                                       image='image',
                                                       image_id='image',
                                                       ready=False,
                                                       restart_count=0,
                                                       state=container_state)
                                 ])))

    assert kubernetes_utils.diagnose_terminated_pod(
        CONTEXT, NAMESPACE, POD) == f'Pod {POD} terminated: {RECLAIMED}.'


def test_exec_failure_on_a_deleted_pod_names_kai_eviction(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'metadata': {'uid': 'pg-uid'}}
    cluster.pod_group_events = [_evict_event(POD)]
    _patch_pod_read(monkeypatch, None)

    assert kubernetes_utils.diagnose_terminated_pod(
        CONTEXT, NAMESPACE, POD) == f'Pod {POD} was deleted: {RECLAIMED}.'
