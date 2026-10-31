"""Tests for KAI Scheduler support."""
import asyncio
import copy
import datetime
import pickle
from types import SimpleNamespace

from kubernetes.client import V1Container
from kubernetes.client import V1ContainerState
from kubernetes.client import V1ContainerStateRunning
from kubernetes.client import V1ContainerStateTerminated
from kubernetes.client import V1ContainerStatus
from kubernetes.client import V1ObjectMeta
from kubernetes.client import V1Pod
from kubernetes.client import V1PodSchedulingGate
from kubernetes.client import V1PodSpec
from kubernetes.client import V1PodStatus
import pytest
import urllib3

from sky import exceptions
from sky import global_user_state
from sky.adaptors import kubernetes
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import instance
from sky.provision.kubernetes import kai
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils

CONTEXT = 'ctx'
NAMESPACE = 'ns'
CLUSTER = 'c-head'


class FakeApiException(Exception):

    def __init__(self, status):
        super().__init__(status)
        self.status = status


def _queue(limit, quota, parent=''):
    return {
        'parentQueue': parent,
        'resources': {
            'gpu': {
                'quota': quota,
                'limit': limit
            }
        }
    }


class FakeKai:
    """Fake PodGroups and Queues served through the custom objects API."""

    def __init__(self):
        self.pod_groups = {}
        self.queues = {
            'parent': _queue(limit=8, quota=8),
            'q': _queue(limit=8, quota=4, parent='parent'),
            'no-quota': _queue(limit=8, quota=0, parent='parent'),
            'no-limit': _queue(limit=-1, quota=4, parent='parent'),
            'unlimited-parent': _queue(limit=-1, quota=-1),
            'unlimited': _queue(limit=-1, quota=0, parent='unlimited-parent'),
        }
        self.live_pods = {}
        self.calls = []
        self.read_errors = []
        self.pod_group_events = []
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
        if self.read_errors:
            raise self.read_errors.pop(0)
        if name not in self.pod_groups:
            raise FakeApiException(404)
        return copy.deepcopy(self.pod_groups[name])

    def get_cluster_custom_object(self, group, version, plural, name, **kwargs):
        del group, version, kwargs
        if plural in self.forbidden:
            raise FakeApiException(403)
        if plural == 'topologies':
            if name not in self.topologies:
                raise FakeApiException(404)
            return copy.deepcopy(self.topologies[name])
        if name not in self.queues:
            raise FakeApiException(404)
        return {'spec': copy.deepcopy(self.queues[name])}

    def list_namespaced_event(self, namespace, field_selector, **kwargs):
        del namespace, kwargs
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
                        parse_quantity=kubernetes.parse_quantity,
                        API_TIMEOUT=kubernetes.API_TIMEOUT))
    monkeypatch.setattr(kubernetes_utils, 'filter_pods',
                        lambda *args: fake.live_pods)
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: True)
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


def _queued(*reasons, queue='q'):
    return {
        'spec': {
            'minMember': 1,
            'queue': queue
        },
        'status': {
            'schedulingConditions': [{
                'reasons': list(reasons)
            }]
        },
    }


def _bound():
    return {
        'spec': {
            'queue': 'q'
        },
        'status': {
            'resourcesStatus': {
                'allocated': {
                    'nvidia.com/gpu': '4'
                }
            }
        }
    }


def _quota_reason(reason, gpus, cpu='1'):
    requested_field = {
        'OverLimit': 'podGroupRequestedResources',
        'NonPreemptibleOverQuota': 'podGroupRequestedNonPreemptibleResources',
    }[reason]
    return {
        'reason': reason,
        'message': 'over\n  quota',
        'details': {
            'queueDetails': {
                requested_field: {
                    'cpu': cpu,
                    'nvidia.com/gpu': gpus
                }
            }
        },
    }


WAIT_FOR_QUOTA = _quota_reason('NonPreemptibleOverQuota', '4')
WAIT_FOR_NODES = {'reason': 'PodSchedulingErrors', 'message': 'no nodes'}


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
    # A relaunch keeps the cluster's node count, so the gang stays as it is.
    cluster.pod_groups[CLUSTER] = _running_gang()
    cluster.live_pods = {CLUSTER: object()}

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, PROVIDER_CONFIG,
                         _node_config(), 4)

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


class CheckReturned(Exception):
    """The KAI check returned, so the wait loop would keep waiting."""


def _wait_for_pods_to_schedule(monkeypatch,
                               gated=False,
                               scheduled=False,
                               events=None):
    """Runs one iteration of the real wait loop with the KAI check."""
    pod = V1Pod(
        metadata=V1ObjectMeta(name=CLUSTER,
                              uid='uid',
                              labels={'skypilot-cluster-name': CLUSTER}),
        spec=V1PodSpec(containers=[V1Container(name='ray-node')],
                       node_name='node' if scheduled else None,
                       scheduling_gates=[V1PodSchedulingGate(
                           name='g')] if gated else None),
        status=V1PodStatus(phase='Pending'),
    )
    monkeypatch.setattr(
        instance.kubernetes, 'core_api', lambda context: SimpleNamespace(
            list_namespaced_pod=lambda *a, **k: SimpleNamespace(items=[pod])))
    monkeypatch.setattr(
        global_user_state, 'add_cluster_event',
        lambda *args, **kwargs: events.append(kwargs['reason'])
        if events is not None else None)

    def check_once(*args):
        kai._park_or_fail_if_queued(*args)  # pylint: disable=protected-access
        raise CheckReturned

    instance._wait_for_pods_to_schedule(  # pylint: disable=protected-access
        NAMESPACE,
        CONTEXT, [pod],
        60,
        'c',
        datetime.datetime.now(datetime.timezone.utc),
        scheduling_callback=check_once)


@pytest.mark.parametrize('gated', [False, True])
@pytest.mark.parametrize('reason', [WAIT_FOR_QUOTA, WAIT_FOR_NODES])
def test_queued_gang_parks_launch(cluster, monkeypatch, reason, gated):
    cluster.pod_groups[CLUSTER] = _queued(reason)

    with pytest.raises(exceptions.ExecutionPausedError) as e:
        _wait_for_pods_to_schedule(monkeypatch, gated=gated)

    assert reason['reason'] in str(e.value)
    assert '\n' not in str(e.value)
    assert e.value.continue_condition == _condition()


@pytest.mark.parametrize(
    'reason,queue',
    [
        (_quota_reason('OverLimit', '16'), 'q'),
        (_quota_reason('NonPreemptibleOverQuota', '8'), 'q'),
        (_quota_reason('NonPreemptibleOverQuota', '1'), 'no-quota'),
        # The parent's limit applies when the queue's own is unlimited.
        (_quota_reason('OverLimit', '16'), 'no-limit'),
        ({
            'reason': 'QueueDoesNotExist',
            'message': ''
        }, 'q'),
    ])
def test_gang_that_never_fits_fails(cluster, monkeypatch, reason, queue):
    cluster.pod_groups[CLUSTER] = _queued(reason, queue=queue)

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='KAI cannot schedule c'):
        _wait_for_pods_to_schedule(monkeypatch)


def test_unreadable_queue_fails_with_the_missing_permission(
        cluster, monkeypatch):
    cluster.forbidden = {'queues'}
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_QUOTA)

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='permission to get queues.scheduling.run.ai'):
        _wait_for_pods_to_schedule(monkeypatch)


def test_queue_cycle_ends_the_limit_walk(cluster, monkeypatch):
    # KAI's webhook does not reject a cycle of parent queues.
    cluster.queues['a'] = _queue(limit=8, quota=4, parent='b')
    cluster.queues['b'] = _queue(limit=8, quota=4, parent='a')
    cluster.pod_groups[CLUSTER] = _queued(_quota_reason('OverLimit', '16'),
                                          queue='a')

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match='KAI cannot schedule c'):
        _wait_for_pods_to_schedule(monkeypatch)


def test_quota_reason_without_gpus_parks(cluster, monkeypatch):
    reason = _quota_reason('OverLimit', '0')
    del reason['details']['queueDetails']['podGroupRequestedResources'][
        'nvidia.com/gpu']
    cluster.pod_groups[CLUSTER] = _queued(reason)

    with pytest.raises(exceptions.ExecutionPausedError):
        _wait_for_pods_to_schedule(monkeypatch)


@pytest.mark.parametrize(
    'reason',
    [
        _quota_reason('OverLimit', '64'),
        # CPU quota reasons always wait.
        _quota_reason('NonPreemptibleOverQuota', '0', cpu='10k'),
    ])
def test_gang_under_unlimited_queue_parks(cluster, monkeypatch, reason):
    cluster.pod_groups[CLUSTER] = _queued(reason, queue='unlimited')

    with pytest.raises(exceptions.ExecutionPausedError):
        _wait_for_pods_to_schedule(monkeypatch)


@pytest.mark.parametrize('pod_group', [{
    'spec': {
        'queue': 'q'
    }
}, None],
                         ids=['unevaluated', 'missing'])
def test_gang_without_kai_verdict_keeps_waiting(cluster, monkeypatch,
                                                pod_group):
    if pod_group is not None:
        cluster.pod_groups[CLUSTER] = pod_group

    with pytest.raises(CheckReturned):
        _wait_for_pods_to_schedule(monkeypatch)


def test_transport_error_keeps_waiting(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_QUOTA)
    cluster.read_errors = [urllib3.exceptions.ProtocolError('reset')]

    with pytest.raises(CheckReturned):
        _wait_for_pods_to_schedule(monkeypatch)


def test_launch_outside_request_keeps_waiting(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_QUOTA)
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: False)

    with pytest.raises(CheckReturned):
        _wait_for_pods_to_schedule(monkeypatch)


def test_scheduled_pods_are_not_parked(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_NODES)

    _wait_for_pods_to_schedule(monkeypatch, scheduled=True)


def test_volume_errors_are_raised_before_parking(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_NODES)

    def fail(*args, **kwargs):
        raise config_lib.KubernetesError('volume cannot bind')

    monkeypatch.setattr(instance._PendingVolumeProbe, 'probe', fail)  # pylint: disable=protected-access

    with pytest.raises(config_lib.KubernetesError, match='volume'):
        _wait_for_pods_to_schedule(monkeypatch)


def test_volume_wait_is_reported_before_parking(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = _queued(WAIT_FOR_NODES)
    monkeypatch.setattr(
        instance._PendingVolumeProbe,
        'probe',  # pylint: disable=protected-access
        lambda *args, **kwargs: 'waiting for volume data')
    events = []

    with pytest.raises(exceptions.ExecutionPausedError):
        _wait_for_pods_to_schedule(monkeypatch, events=events)

    assert 'Launching (waiting for volume data)' in events


def test_queued_launch_check_rate_limits(monkeypatch):
    now = [0.0]
    checks = []
    monkeypatch.setattr(kai.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(kai, '_park_or_fail_if_queued',
                        lambda *args: checks.append(now[0]))
    check = kai.queued_launch_check()

    for now[0] in (0, 1, 9.9, 10, 15, 20):
        check(CONTEXT, NAMESPACE, CLUSTER, 'c')

    assert checks == [0, 10, 20]


def _condition():
    return kai.PodGroupScheduledCondition(context=CONTEXT,
                                          namespace=NAMESPACE,
                                          cluster_name_on_cloud=CLUSTER,
                                          cluster_name='c')


def _resume(cluster, states, cancel_after=None):
    """Drives PodGroupScheduledCondition through a sequence of PodGroup states."""
    reported = []
    polls = 0

    async def is_cancelled():
        return cancel_after is not None and polls >= cancel_after

    async def update_status_msg(message):
        reported.append(message)

    async def sleep(seconds):
        del seconds
        nonlocal polls
        polls += 1
        state = states[min(polls, len(states) - 1)]
        if state is None:
            cluster.pod_groups.pop(CLUSTER, None)
        else:
            cluster.pod_groups[CLUSTER] = state

    cluster.pod_groups[CLUSTER] = states[0]
    condition = _condition()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(kai.asyncio, 'sleep', sleep)
        resumed = asyncio.run(
            condition.wait_async(is_cancelled=is_cancelled,
                                 fallback_wait_seconds=1,
                                 update_status_msg=update_status_msg))
    return resumed, reported


def test_condition_resumes_when_scheduled(cluster):
    resumed, reported = _resume(
        cluster, [_queued(WAIT_FOR_QUOTA),
                  _queued(WAIT_FOR_NODES),
                  _bound()])

    assert resumed
    # In the form of the message the launch parked with.
    assert reported == [
        'c is queued in KAI: NonPreemptibleOverQuota: over quota',
        'c is queued in KAI: PodSchedulingErrors: no nodes',
    ]


@pytest.mark.parametrize('outcome',
                         [None, _queued(_quota_reason('OverLimit', '16'))],
                         ids=['deleted', 'never-fits'])
def test_condition_resumes_to_fail(cluster, outcome):
    assert _resume(cluster, [_queued(WAIT_FOR_QUOTA), outcome])[0]


def test_condition_rechecks_queue_limits_only_when_kai_reason_changes(
        cluster, monkeypatch):
    # Each check reads every queue up the chain; a parked launch polls for
    # as long as KAI keeps it queued.
    checks = []
    never_fits = kai._never_fits  # pylint: disable=protected-access
    monkeypatch.setattr(kai, '_never_fits',
                        lambda *args: checks.append(1) or never_fits(*args))

    assert _resume(cluster, [_queued(WAIT_FOR_QUOTA)] * 3 +
                   [_queued(WAIT_FOR_NODES), _bound()])[0]
    assert len(checks) == 2


def test_condition_stops_when_cancelled(cluster):
    assert not _resume(cluster, [_queued(WAIT_FOR_QUOTA)], cancel_after=3)[0]


def test_condition_is_picklable():
    condition = _condition()
    assert pickle.loads(pickle.dumps(condition)) == condition


POD = f'{CLUSTER}-worker1'
RECLAIMED = (f'KAI evicted the pod (reclaim): Pod {NAMESPACE}/{POD} was '
             f'preempted by workload {NAMESPACE}/other')


def _evict_event(pod_name, uid='pg-uid', at=None):
    # A reclaim as KAI records it: the first line names the pod, and
    # one line per affected queue follows.
    return SimpleNamespace(
        reason='Evict',
        message=(f'Pod {NAMESPACE}/{pod_name} was preempted by workload '
                 f'{NAMESPACE}/other.\nq (entitlement 4 GPU) uses <GPU: 8>'),
        last_timestamp=at,
        metadata=SimpleNamespace(
            annotations={'evictor-action-type': 'reclaim'}),
        involved_object=SimpleNamespace(kind='PodGroup', name=CLUSTER, uid=uid))


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
