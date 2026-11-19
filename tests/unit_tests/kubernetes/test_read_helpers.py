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
from sky.utils import annotations
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
         deleting: bool = False) -> Dict[str, Any]:
    metadata = {
        'name': name,
        'namespace': 'default',
        'labels': {
            provision_constants.TAG_SKYPILOT_CLUSTER_NAME: 'c'
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


class _Reads:
    """Records list_objects() calls; answers pod lists in turn."""

    def __init__(self, nodes: List[Dict[str, Any]],
                 pods: List[List[Dict[str, Any]]]):
        self.nodes = nodes
        self.pods = pods
        self.calls: List[Dict[str, Any]] = []

    def list_objects(self,
                     context: Optional[str],
                     resource: reads.Resource,
                     *,
                     namespace: Optional[str] = None,
                     label_selector: Optional[str] = None,
                     field_selector: Optional[str] = None,
                     consistency: reads.Consistency = reads.Cached()):
        self.calls.append({
            'context': context,
            'resource': resource,
            'namespace': namespace,
            'label_selector': label_selector,
            'field_selector': field_selector,
            'consistency': consistency,
        })
        if resource == reads.NODES:
            items = self.nodes
        else:
            items = self.pods[0] if len(self.pods) == 1 else self.pods.pop(0)
        return reads.ReadResult(items=items,
                                resource_version='1',
                                staleness_seconds=0,
                                reason=None)

    def consistencies(self) -> List[reads.Consistency]:
        return [call['consistency'] for call in self.calls]


@pytest.fixture
def fake_reads(monkeypatch):

    def install(nodes: Optional[List[Dict[str, Any]]] = None,
                *pods: List[Dict[str, Any]]) -> _Reads:
        fake = _Reads(nodes or [_node('gpu', gpus='8')], list(pods) or [[]])
        monkeypatch.setattr(reads, 'list_objects', fake.list_objects)
        return fake

    annotations.clear_request_level_cache()
    yield install
    annotations.clear_request_level_cache()


@pytest.fixture
def cache_source(monkeypatch):
    monkeypatch.setattr(plugin_extensions.ClusterStateSource, '_provider_func',
                        mock.Mock())


def test_nodes_are_read_from_the_api_server_by_default(fake_reads):
    fake = fake_reads([_node('n1')])
    nodes = utils.get_kubernetes_nodes(context=_CONTEXT)
    assert [node.metadata.name for node in nodes] == ['n1']
    assert fake.consistencies() == [reads.FRESH]
    assert fake.calls[0]['resource'] == reads.NODES


def test_request_cache_keeps_consistencies_apart(fake_reads):
    fake = fake_reads([_node('n1')])
    for _ in range(2):
        utils.get_kubernetes_nodes(context=_CONTEXT)
        utils.get_kubernetes_nodes(context=_CONTEXT, consistency=reads.Cached())
    assert fake.consistencies() == [reads.FRESH, reads.Cached()]


@pytest.mark.parametrize('read', [
    lambda: utils.detect_accelerator_resource(_CONTEXT),
    lambda: utils.detect_gpu_label_formatter(_CONTEXT),
    lambda: utils.get_gpu_resource_key(_CONTEXT),
    lambda: utils.get_spot_label(_CONTEXT),
    lambda: utils.get_unlabeled_accelerator_nodes(_CONTEXT),
])
def test_read_only_node_reads_accept_a_cache(fake_reads, read):
    fake = fake_reads([_node('gpu', gpus='8'), _node('cpu')])
    read()
    assert fake.consistencies() == [reads.Cached()]


def test_gpu_resource_key_comes_from_node_capacity(fake_reads):
    fake_reads([_node('gpu', gpus='8')])
    assert utils.get_gpu_resource_key(_CONTEXT) == 'nvidia.com/gpu'


def test_allocated_resources_read_pods_holding_resources(fake_reads):
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
    fake = fake_reads(None, [pod])
    gpus, cpu_memory = utils.get_allocated_resources_by_node(
        context=_CONTEXT, consistency=reads.Cached())
    assert gpus == {'gpu': 1}
    assert cpu_memory == {'gpu': (2.0, 0.0)}
    call = next(call for call in fake.calls if call['resource'] == reads.PODS)
    assert call['consistency'] == reads.Cached()
    assert 'status.phase!=Succeeded' in call['field_selector']
    assert 'status.phase!=Running' not in call['field_selector']


def test_skypilot_pods_are_read_from_a_cache(fake_reads):
    fake = fake_reads(None, [_pod()])
    pods = utils.get_skypilot_pods(_CONTEXT)
    assert fake.calls == [{
        'context': _CONTEXT,
        'resource': reads.PODS,
        'namespace': None,
        'label_selector': provision_constants.TAG_SKYPILOT_CLUSTER_NAME,
        'field_selector': None,
        'consistency': reads.Cached(),
    }]
    assert pods[0].metadata.name == _HEAD
    assert pods[0].status.start_time == datetime.datetime(
        2026, 11, 19, 0, 0, 10, tzinfo=datetime.timezone.utc)


def _query_instances(monkeypatch) -> Dict[str, Any]:
    monkeypatch.setattr(instance, '_get_pod_failure_reason_from_events',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(instance, '_get_pod_missing_reason',
                        lambda *args, **kwargs: None)
    return instance.query_instances('c',
                                    'c',
                                    provider_config={
                                        'namespace': 'default',
                                        'context': _CONTEXT,
                                        'services': [{
                                            'spec': {
                                                'selector': {
                                                    'component': _HEAD
                                                }
                                            }
                                        }],
                                    })


@pytest.mark.usefixtures('cache_source')
def test_a_healthy_cluster_is_read_from_a_cache(monkeypatch, fake_reads):
    fake = fake_reads(None, [_pod()])
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.UP, None)
    }
    assert fake.consistencies() == [reads.Cached()]
    assert fake.calls[0]['namespace'] == 'default'
    assert fake.calls[0]['label_selector'] == (
        f'{provision_constants.TAG_SKYPILOT_CLUSTER_NAME}=c')


@pytest.mark.usefixtures('cache_source')
@pytest.mark.parametrize('cached', [
    [],
    [_pod(phase='Pending')],
    [_pod(ready=False)],
    [_pod(deleting=True)],
    [_pod(name='c-worker')],
])
def test_the_api_server_confirms_an_unhealthy_answer(monkeypatch, fake_reads,
                                                     cached):
    fake = fake_reads(None, cached, [_pod()])
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.UP, None)
    }
    assert fake.consistencies() == [reads.Cached(), reads.FRESH]


def test_without_a_cache_an_unhealthy_answer_is_read_once(
        monkeypatch, fake_reads):
    fake = fake_reads(None, [_pod(phase='Pending')])
    assert _query_instances(monkeypatch) == {
        _HEAD: (status_lib.ClusterStatus.INIT, None)
    }
    assert fake.consistencies() == [reads.Cached()]
