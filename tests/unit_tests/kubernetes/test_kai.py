"""Tests for KAI Scheduler support."""
import asyncio
import copy
import datetime
import pickle
from types import SimpleNamespace

from kubernetes.client import V1Container
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

    def create_namespaced_custom_object(self, group, version, namespace, plural,
                                        body, **kwargs):
        del group, version, namespace, plural, kwargs
        name = body['metadata']['name']
        self.calls.append(('create', name))
        if name in self.pod_groups:
            raise FakeApiException(409)
        self.pod_groups[name] = copy.deepcopy(body)

    def delete_namespaced_custom_object(self, group, version, namespace, plural,
                                        name, **kwargs):
        del group, version, namespace, plural, kwargs
        self.calls.append(('delete', name))
        if self.pod_groups.pop(name, None) is None:
            raise FakeApiException(404)

    def patch_namespaced_custom_object(self, group, version, namespace, plural,
                                       name, body, **kwargs):
        del group, version, namespace, plural, kwargs
        self.calls.append(('patch', name))
        self.pod_groups[name]['spec'].update(body['spec'])

    def get_namespaced_custom_object(self, group, version, namespace, plural,
                                     name, **kwargs):
        del group, version, namespace, plural, kwargs
        if self.read_errors:
            raise self.read_errors.pop(0)
        if name not in self.pod_groups:
            raise FakeApiException(404)
        return copy.deepcopy(self.pod_groups[name])

    def get_cluster_custom_object(self, group, version, plural, name, **kwargs):
        del group, version, plural, kwargs
        return {'spec': copy.deepcopy(self.queues[name])}


@pytest.fixture
def cluster(monkeypatch):
    fake = FakeKai()
    monkeypatch.setattr(
        kai, 'kubernetes',
        SimpleNamespace(custom_objects_api=lambda context: fake,
                        api_exception=lambda: FakeApiException,
                        urllib3_http_error=kubernetes.urllib3_http_error,
                        parse_quantity=kubernetes.parse_quantity,
                        API_TIMEOUT=kubernetes.API_TIMEOUT))
    monkeypatch.setattr(kubernetes_utils, 'filter_pods',
                        lambda *args: fake.live_pods)
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: True)
    return fake


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


def test_ensure_pod_group_creates_gang_from_pod_fields(cluster):
    kai.ensure_pod_group(
        CONTEXT, NAMESPACE, CLUSTER,
        _node_config(**{
            'kai.scheduler/preemptibility': 'preemptible',
            'shard': 'a'
        }), 4)

    pod_group = cluster.pod_groups[CLUSTER]
    assert pod_group['spec'] == {
        'minMember': 4,
        'queue': 'q',
        'priorityClassName': 'batch',
        'preemptibility': 'preemptible',
    }
    assert pod_group['metadata']['labels'] == {
        'kai.scheduler/queue': 'q',
        'kai.scheduler/preemptibility': 'preemptible',
        'shard': 'a',
        'skypilot-cluster-name': CLUSTER,
    }


def test_ensure_pod_group_omits_unset_priority(cluster):
    node_config = _node_config()
    del node_config['spec']['priorityClassName']

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, node_config, 1)

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


def test_ensure_pod_group_resizes_running_cluster(cluster):
    cluster.pod_groups[CLUSTER] = _running_gang()
    cluster.live_pods = {CLUSTER: object()}

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, _node_config(), 2)

    assert cluster.pod_groups[CLUSTER] == _running_gang(minMember=2)
    assert ('delete', CLUSTER) not in cluster.calls


@pytest.mark.parametrize('spec', [{
    'queue': 'other'
}, {
    'priorityClassName': 'other'
}, {
    'preemptibility': 'preemptible'
}])
def test_ensure_pod_group_refuses_changed_settings(cluster, spec):
    cluster.pod_groups[CLUSTER] = _running_gang(**spec)
    cluster.live_pods = {CLUSTER: object()}

    with pytest.raises(exceptions.ProvisionUnsupportedError,
                       match=f'Cannot change KAI {next(iter(spec))}'):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, _node_config(), 2)
    assert cluster.pod_groups[CLUSTER] == _running_gang(**spec)


def test_ensure_pod_group_replaces_stale_group(cluster):
    cluster.pod_groups[CLUSTER] = {'spec': {}, 'status': 'stale'}

    kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, _node_config(), 1)

    assert cluster.calls == [('delete', CLUSTER), ('create', CLUSTER)]
    assert 'status' not in cluster.pod_groups[CLUSTER]


def test_ensure_pod_group_fails_if_stale_group_survives(cluster, monkeypatch):
    cluster.pod_groups[CLUSTER] = {'spec': {}}
    monkeypatch.setattr(cluster, 'delete_namespaced_custom_object',
                        lambda *args, **kwargs: None)

    with pytest.raises(FakeApiException):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, _node_config(), 1)


def test_ensure_pod_group_rejects_deployments(cluster):
    with pytest.raises(exceptions.ProvisionUnsupportedError):
        kai.ensure_pod_group(CONTEXT, NAMESPACE, CLUSTER, {
            **_node_config(), 'deployment_spec': {}
        }, 1)
    assert not cluster.calls


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


def _wait_for_pods_to_schedule(monkeypatch, gated=False, scheduled=False):
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
    monkeypatch.setattr(global_user_state, 'add_cluster_event',
                        lambda *args, **kwargs: None)

    def check_once(*args):
        kai._check_scheduling_wait(*args)  # pylint: disable=protected-access
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
    assert 'sky down c' in e.value.hint
    assert e.value.continue_condition == kai.GangBound(context=CONTEXT,
                                                       namespace=NAMESPACE,
                                                       pod_group=CLUSTER)


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


def test_wait_checker_rate_limits(monkeypatch):
    now = [0.0]
    checks = []
    monkeypatch.setattr(kai.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(kai, '_check_scheduling_wait',
                        lambda *args: checks.append(now[0]))
    check = kai.scheduling_wait_checker()

    for now[0] in (0, 1, 9.9, 10, 15, 20):
        check(CONTEXT, NAMESPACE, CLUSTER, 'c')

    assert checks == [0, 10, 20]


def _resume(cluster, states, cancel_after=None):
    """Drives GangBound through a sequence of PodGroup states."""
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
    condition = kai.GangBound(context=CONTEXT,
                              namespace=NAMESPACE,
                              pod_group=CLUSTER)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(kai.asyncio, 'sleep', sleep)
        resumed = asyncio.run(
            condition.wait_async(is_cancelled=is_cancelled,
                                 fallback_wait_seconds=1,
                                 update_status_msg=update_status_msg))
    return resumed, reported


def test_gang_bound_resumes_when_bound(cluster):
    resumed, reported = _resume(
        cluster, [_queued(WAIT_FOR_QUOTA),
                  _queued(WAIT_FOR_NODES),
                  _bound()])

    assert resumed
    assert [r.split(':')[0] for r in reported
           ] == ['NonPreemptibleOverQuota', 'PodSchedulingErrors']


@pytest.mark.parametrize('outcome',
                         [None, _queued(_quota_reason('OverLimit', '16'))],
                         ids=['deleted', 'never-fits'])
def test_gang_bound_resumes_to_fail(cluster, outcome):
    assert _resume(cluster, [_queued(WAIT_FOR_QUOTA), outcome])[0]


def test_gang_bound_survives_transport_error(cluster):
    cluster.read_errors = [urllib3.exceptions.ProtocolError('reset')]

    assert _resume(cluster, [_queued(WAIT_FOR_QUOTA), _bound()])[0]


def test_gang_bound_stops_when_cancelled(cluster):
    assert not _resume(cluster, [_queued(WAIT_FOR_QUOTA)], cancel_after=3)[0]


def test_gang_bound_is_picklable():
    condition = kai.GangBound(context=CONTEXT,
                              namespace=NAMESPACE,
                              pod_group=CLUSTER)
    assert pickle.loads(pickle.dumps(condition)) == condition
