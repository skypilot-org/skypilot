"""Tests for the Kubernetes read helpers built on reads.list_objects()."""
# pylint: disable=redefined-outer-name
import datetime
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from sky.provision import constants as provision_constants
from sky.provision.kubernetes import instance
from sky.provision.kubernetes import reads
from sky.provision.kubernetes import utils
from sky.utils import plugin_extensions
from sky.utils import status_lib

_CONTEXT = 'ctx'
_HEAD = 'c-head'


def _node(name: str, gpus: str = '') -> Dict[str, Any]:
    capacity = {'cpu': '8', 'memory': '32Gi'}
    labels = {'kubernetes.io/hostname': name}
    if gpus:
        capacity['nvidia.com/gpu'] = gpus
        labels['nvidia.com/gpu.product'] = 'NVIDIA-H100-80GB-HBM3'
    return {
        'metadata': {
            'name': name,
            'labels': labels
        },
        'spec': {},
        'status': {
            'capacity': capacity,
            'allocatable': capacity,
            'addresses': [],
            'conditions': [{
                'type': 'Ready',
                'status': 'True'
            }],
        },
    }


def _pod(name: str = _HEAD,
         phase: str = 'Running',
         ready: bool = True,
         deleting: bool = False,
         component: Optional[str] = None) -> Dict[str, Any]:
    metadata = {
        'name': name,
        'namespace': 'default',
        'labels': {
            provision_constants.TAG_SKYPILOT_CLUSTER_NAME: 'c',
            'component': component or name,
        },
        'creationTimestamp': '2026-11-19T00:00:00Z',
    }
    if deleting:
        metadata['deletionTimestamp'] = '2026-11-19T00:01:00Z'
    return {
        'metadata': metadata,
        'spec': {
            'containers': [{
                'name': 'ray-node'
            }]
        },
        'status': {
            'phase': phase,
            'startTime': '2026-11-19T00:00:10Z',
            'conditions': [{
                'type': 'Ready',
                'status': 'True' if ready else 'False',
                'reason': None if ready else 'ContainersNotReady',
            }],
        },
    }


@pytest.fixture
def serve(monkeypatch):
    """Answers reads.list_objects() with nodes and, in turn, pod lists.

    A Cached read is answered from the cache when `cached`, otherwise from
    the API server. The last pod list answers all later reads.
    """

    def install(nodes: Optional[List[Dict[str, Any]]] = None,
                pods: Optional[List[List[Dict[str, Any]]]] = None,
                cached: bool = True) -> mock.Mock:
        pod_answers = list(pods or [[]])

        def list_objects(context,
                         resource,
                         *,
                         consistency,
                         convert=None,
                         **kwargs):
            del context, kwargs
            if resource == reads.NODES:
                items = nodes or [_node('gpu', gpus='8')]
            else:
                items = (pod_answers.pop(0)
                         if len(pod_answers) > 1 else pod_answers[0])
            from_cache = cached and isinstance(consistency, reads.Cached)
            return reads.ReadResult(
                items=[convert(item) for item in items] if convert else items,
                resource_version='1',
                staleness_seconds=0,
                reason=None if from_cache else reads.REASON_NO_SOURCE)

        fake = mock.Mock(side_effect=list_objects)
        monkeypatch.setattr(reads, 'list_objects', fake)
        return fake

    return install


@pytest.fixture
def cache_source(monkeypatch):
    monkeypatch.setattr(plugin_extensions.ClusterStateSource, '_provider_func',
                        mock.Mock())


def _consistencies(fake: mock.Mock) -> List[reads.Consistency]:
    return [call.kwargs['consistency'] for call in fake.call_args_list]


def test_nodes_are_read_from_the_api_server_by_default(serve):
    fake = serve([_node('n1')])
    nodes = utils.get_kubernetes_nodes(context=_CONTEXT)
    assert [node.metadata.name for node in nodes] == ['n1']
    assert fake.call_args.args == (_CONTEXT, reads.NODES)
    assert _consistencies(fake) == [reads.FRESH]


@pytest.mark.usefixtures('cache_source')
def test_request_cache_keeps_consistencies_apart(serve):
    fake = serve([_node('n1')])
    for _ in range(2):
        utils.get_kubernetes_nodes(context=_CONTEXT)
        utils.get_kubernetes_nodes(context=_CONTEXT, consistency=reads.CACHED)
    assert _consistencies(fake) == [reads.FRESH, reads.CACHED]


def test_without_a_source_one_node_list_serves_every_read(serve):
    fake = serve([_node('n1')])
    utils.get_kubernetes_nodes(context=_CONTEXT)
    utils.get_kubernetes_nodes(context=_CONTEXT, consistency=reads.CACHED)
    assert fake.call_count == 1


@pytest.mark.usefixtures('cache_source')
@pytest.mark.parametrize('read', [
    lambda: utils.detect_accelerator_resource(_CONTEXT),
    lambda: utils.detect_gpu_label_formatter(_CONTEXT),
    lambda: utils.get_gpu_resource_key(_CONTEXT),
    lambda: utils.get_spot_label(_CONTEXT),
    lambda: utils.get_unlabeled_accelerator_nodes(_CONTEXT),
])
def test_read_only_node_reads_accept_a_cache(serve, read):
    fake = serve([_node('gpu', gpus='8'), _node('cpu')])
    read()
    assert _consistencies(fake) == [reads.CACHED]


def test_gpu_resource_key_comes_from_node_capacity(serve):
    serve([_node('gpu', gpus='8')])
    assert utils.get_gpu_resource_key(_CONTEXT) == 'nvidia.com/gpu'


def test_gpu_resource_key_without_a_context_reads_the_default_target(serve):
    fake = serve([_node('gpu', gpus='8')])
    utils.get_gpu_resource_key()
    assert fake.call_args.args == (None, reads.NODES)


def test_allocated_resources_read_pods_holding_resources(serve):
    pod = _pod()
    pod['spec'] = {
        'nodeName': 'gpu',
        'containers': [{
            'name': 'ray-node',
            'resources': {
                'requests': {
                    'cpu': '2',
                    'nvidia.com/gpu': '1'
                }
            }
        }]
    }
    fake = serve(pods=[[pod]])
    gpus, cpu_memory = utils.get_allocated_resources_by_node(
        context=_CONTEXT, consistency=reads.CACHED)
    assert gpus == {'gpu': 1}
    assert cpu_memory == {'gpu': (2.0, 0.0)}
    call = next(
        call for call in fake.call_args_list if call.args[1] == reads.PODS)
    assert call.kwargs['consistency'] == reads.CACHED
    assert 'status.phase!=Succeeded' in call.kwargs['field_selector']
    assert 'status.phase!=Running' not in call.kwargs['field_selector']


def test_skypilot_pods_are_read_from_a_cache(serve):
    fake = serve(pods=[[_pod()]])
    pods = utils.get_skypilot_pods(_CONTEXT)
    assert fake.call_args.args == (_CONTEXT, reads.PODS)
    assert fake.call_args.kwargs['label_selector'] == (
        provision_constants.TAG_SKYPILOT_CLUSTER_NAME)
    assert fake.call_args.kwargs['consistency'] == reads.CACHED
    assert fake.call_args.kwargs['attempts'] == 1
    assert pods[0].metadata.name == _HEAD
    assert pods[0].status.start_time == datetime.datetime(
        2026, 11, 19, 0, 0, 10, tzinfo=datetime.timezone.utc)


def _query_instances(monkeypatch,
                     non_terminated_only: bool = True,
                     pods: Optional[List[str]] = None) -> Dict[str, Any]:
    """Queries a cluster whose services select `pods` (by default the head),
    as kubernetes-ray.yml.j2 gives each node a service."""
    monkeypatch.setattr(instance, '_get_pod_failure_reason_from_events',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(instance, '_get_pod_missing_reason',
                        lambda *args, **kwargs: None)
    services = [{
        'spec': {
            'selector': {
                'component': pod
            }
        }
    } for pod in pods or [_HEAD]]
    return instance.query_instances('c',
                                    'c',
                                    provider_config={
                                        'namespace': 'default',
                                        'context': _CONTEXT,
                                        'services': services,
                                    },
                                    non_terminated_only=non_terminated_only)


def test_a_healthy_cluster_is_read_from_a_cache(monkeypatch, serve):
    fake = serve(pods=[[_pod()]])
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.UP, None)
    }
    assert _consistencies(fake) == [reads.CACHED]
    call = fake.call_args
    assert call.kwargs['namespace'] == 'default'
    assert call.kwargs['label_selector'] == (
        f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}=c')
    assert call.kwargs['attempts'] == 1


def test_a_healthy_deployment_pod_is_read_from_a_cache(monkeypatch, serve):
    pod = _pod(name='c-deployment-7d9f8-x2x4k', component=_HEAD)
    fake = serve(pods=[[pod]])
    assert _query_instances(monkeypatch) == {
        'c-deployment-7d9f8-x2x4k': (status_lib.ClusterStatus.UP, None)
    }
    assert _consistencies(fake) == [reads.CACHED]


@pytest.mark.parametrize('cached', [
    [],
    [_pod(phase='Pending')],
    [_pod(ready=False)],
    [_pod(deleting=True)],
    [_pod(name='c-worker')],
])
def test_the_api_server_confirms_an_unhealthy_cached_answer(
        monkeypatch, serve, cached):
    fake = serve(pods=[cached, [_pod()]])
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.UP, None)
    }
    assert _consistencies(fake) == [reads.CACHED, reads.FRESH]


def test_a_cached_answer_missing_a_worker_is_read_again(monkeypatch, serve):
    fake = serve(pods=[[_pod()], [_pod(), _pod(name='c-worker1')]])
    assert _query_instances(monkeypatch, pods=[_HEAD, 'c-worker1']) == {
        _HEAD: (status_lib.ClusterStatus.UP, None),
        'c-worker1': (status_lib.ClusterStatus.UP, None),
    }
    assert _consistencies(fake) == [reads.CACHED, reads.FRESH]


def test_a_teardown_check_reads_the_api_server(monkeypatch, serve):
    fake = serve(pods=[[_pod()]])
    assert _query_instances(monkeypatch, non_terminated_only=False) == {
        _HEAD: (status_lib.ClusterStatus.UP, None)
    }
    assert _consistencies(fake) == [reads.FRESH]


def test_an_answer_from_the_api_server_is_read_once(monkeypatch, serve):
    fake = serve(pods=[[_pod(phase='Pending')]], cached=False)
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.INIT, None)
    }
    assert _consistencies(fake) == [reads.CACHED]
