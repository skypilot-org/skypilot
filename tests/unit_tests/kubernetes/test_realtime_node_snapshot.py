"""Tests for realtime GPU availability read from a node snapshot."""
import functools
import io
import json
from typing import Any, Callable, Dict, List, Optional

import pytest

from sky.catalog import kubernetes_catalog
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import annotations
from sky.utils import plugin_extensions

_CONTEXT = 'test-ctx'


def _node(name: str,
          labels: Optional[Dict[str, str]] = None,
          capacity: Optional[Dict[str, str]] = None,
          allocatable: Optional[Dict[str, str]] = None,
          ready: bool = True,
          unschedulable: bool = False,
          taints: Optional[List[Dict[str, str]]] = None,
          ip: str = '10.0.0.1') -> Dict[str, Any]:
    capacity = {'cpu': '8', 'memory': '32Gi', **(capacity or {})}
    allocatable = {**capacity, **(allocatable or {})}
    return {
        'metadata': {
            'name': name,
            'labels': {
                'kubernetes.io/hostname': name,
                **(labels or {})
            }
        },
        'spec': {
            'unschedulable': unschedulable,
            'taints': taints or []
        },
        'status': {
            'capacity': capacity,
            'allocatable': allocatable,
            'addresses': [{
                'type': 'InternalIP',
                'address': ip
            }],
            'conditions': [{
                'type': 'MemoryPressure',
                'status': 'False'
            }, {
                'type': 'Ready',
                'status': 'True' if ready else 'False'
            }],
        },
    }


def _pod(name: str,
         node: Optional[str],
         requests: Dict[str, str],
         phase: str = 'Running',
         namespace: str = 'default') -> Dict[str, Any]:
    return {
        'metadata': {
            'name': name,
            'namespace': namespace
        },
        'status': {
            'phase': phase
        },
        'spec': {
            'nodeName': node,
            'containers': [{
                'resources': {
                    'requests': {
                        'cpu': '1',
                        **requests
                    }
                }
            }],
        },
    }


class _Response(io.BytesIO):
    """A streamed Kubernetes API response (_preload_content=False)."""

    def release_conn(self):
        pass


class _CoreApi:
    """Serves list calls from fixed node and pod objects, counting calls."""

    def __init__(self, nodes: List[Dict[str, Any]], pods: List[Dict[str, Any]]):
        self.nodes = nodes
        self.pods = pods
        self.calls: List[str] = []

    def list_node(self, **kwargs):
        del kwargs
        self.calls.append('list_node')
        return _Response(json.dumps({'items': self.nodes}).encode())

    def list_pod_for_all_namespaces(self, field_selector: str, **kwargs):
        del kwargs
        self.calls.append('list_pod_for_all_namespaces')
        excluded = {f.split('!=')[1] for f in field_selector.split(',')}
        pods = [p for p in self.pods if p['status']['phase'] not in excluded]
        return _Response(json.dumps({'items': pods}).encode())


_h100 = functools.partial(
    _node,
    labels={'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'},
    capacity={'nvidia.com/gpu': '8'})

# H100 nodes labeled by GPU Feature Discovery, in every scheduling state.
_GFD_NODES = [
    _h100('h100-free', ip='10.0.0.1'),
    _h100('h100-busy', ip='10.0.0.2'),
    _h100('h100-degraded', allocatable={'nvidia.com/gpu': '6'}, ip='10.0.0.3'),
    _h100('h100-cordoned', unschedulable=True, ip='10.0.0.4'),
    _h100('h100-not-ready', ready=False, ip='10.0.0.5'),
    _h100('h100-tainted',
          taints=[{
              'key': 'team',
              'value': 'research',
              'effect': 'NoSchedule'
          }],
          ip='10.0.0.6'),
    _h100('h100-control-plane',
          taints=[{
              'key': 'node-role.kubernetes.io/control-plane',
              'effect': 'NoSchedule'
          }],
          ip='10.0.0.7'),
    _node('a100',
          labels={
              'nvidia.com/gpu.product': 'NVIDIA-A100-SXM4-80GB',
              'pool': 'a100'
          },
          capacity={'nvidia.com/gpu': '4'},
          ip='10.0.1.1'),
    _node('cpu', labels={'pool': 'cpu'}, ip='10.0.2.1'),
]

_GFD_PODS = [
    _pod('busy-1', 'h100-busy', {'nvidia.com/gpu': '4'}),
    _pod('busy-2', 'h100-busy', {'nvidia.com/gpu': '2'}, phase='Pending'),
    _pod('done', 'h100-busy', {'nvidia.com/gpu': '2'}, phase='Succeeded'),
    _pod('verification',
         'h100-free', {'nvidia.com/gpu': '8'},
         namespace='cw-hpc-verification'),
    _pod('unscheduled', None, {'nvidia.com/gpu': '8'}, phase='Pending'),
    _pod('a100-1', 'a100', {'nvidia.com/gpu': '1'}),
    _pod('cpu-only', 'cpu', {}),
]

_GKE_TPU_NODES = [
    _node('tpu-single-host',
          labels={
              'cloud.google.com/gke-tpu-accelerator': 'tpu-v5-lite-podslice',
              'cloud.google.com/gke-tpu-topology': '2x2',
              'cloud.google.com/gke-accelerator-count': '4',
          },
          capacity={'google.com/tpu': '4'}),
    _node('tpu-multi-host',
          labels={
              'cloud.google.com/gke-tpu-accelerator': 'tpu-v5-lite-podslice',
              'cloud.google.com/gke-tpu-topology': '4x4',
              'cloud.google.com/gke-accelerator-count': '4',
          },
          capacity={'google.com/tpu': '4'}),
    _node('gke-l4',
          labels={'cloud.google.com/gke-accelerator': 'nvidia-l4'},
          capacity={'nvidia.com/gpu': '1'}),
]

_GKE_TPU_PODS = [
    _pod('tpu-job', 'tpu-multi-host', {'google.com/tpu': '4'}),
]

_NEURON_NODES = [
    _node('trn1',
          labels={'karpenter.k8s.aws/instance-accelerator-name': 'trainium'},
          capacity={'aws.amazon.com/neuron': '16'}),
]

_NEURON_PODS = [
    _pod('trn-job', 'trn1', {'aws.amazon.com/neuron': '8'}),
]

_AMD_NODES = [
    _node('mi300',
          labels={'skypilot.co/accelerator': 'mi300'},
          capacity={'amd.com/gpu': '8'}),
]

_AMD_PODS = [
    _pod('amd-job', 'mi300', {'amd.com/gpu': '3'}),
]

_CUSTOM_KEY_NODES = [
    _node('custom',
          labels={'skypilot.co/accelerator': 'h100'},
          capacity={'example.com/gpu': '8'}),
]

_CUSTOM_KEY_PODS = [
    _pod('custom-job', 'custom', {'example.com/gpu': '5'}),
]

_CPU_NODES = [_node('cpu-1'), _node('cpu-2', ip='10.0.0.2')]

_TOLERATION = [{
    'key': 'team',
    'operator': 'Equal',
    'value': 'research',
    'effect': 'NoSchedule'
}]

_CLUSTERS = {
    'gfd': (_GFD_NODES, _GFD_PODS),
    'gke-tpu': (_GKE_TPU_NODES, _GKE_TPU_PODS),
    'neuron': (_NEURON_NODES, _NEURON_PODS),
    'amd': (_AMD_NODES, _AMD_PODS),
    'custom-key': (_CUSTOM_KEY_NODES, _CUSTOM_KEY_PODS),
    'cpu-only': (_CPU_NODES, []),
}


@pytest.fixture
def cluster_env(monkeypatch):
    """Patches everything but the Kubernetes API and the snapshot provider."""
    monkeypatch.setattr('sky.check.get_cached_enabled_clouds_or_refresh',
                        lambda *args, **kwargs: [])
    monkeypatch.setattr('sky.clouds.cloud_in_iterable',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr('sky.provision.kubernetes.utils.check_credentials',
                        lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(plugin_extensions.NodeInfoSource,
                        '_snapshot_provider_func', None)

    def configure(allowed_nodes: Optional[Dict[str, Any]] = None,
                  tolerations: Optional[List[Dict[str, Any]]] = None):
        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_allowed_nodes_config',
            lambda context=None: allowed_nodes)
        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_configured_tolerations',
            lambda context=None: tolerations)

    configure()
    return configure


_Provider = Callable[[str, List[str]],
                     Optional[plugin_extensions.KubernetesNodeSnapshot]]


def _serve(monkeypatch, core_api: _CoreApi,
           provider: Optional[_Provider]) -> None:
    """Starts a request served by core_api and the snapshot provider."""
    annotations.clear_request_level_cache()
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda context=None: core_api)
    monkeypatch.setattr(plugin_extensions.NodeInfoSource,
                        '_snapshot_provider_func', provider)


def _list_realtime(monkeypatch,
                   core_api: _CoreApi,
                   provider: Optional[_Provider] = None,
                   name_filter: Optional[str] = None,
                   quantity_filter: Optional[int] = None):
    _serve(monkeypatch, core_api, provider)
    qtys, capacity, available = kubernetes_catalog.list_accelerators_realtime(
        gpus_only=True,
        name_filter=name_filter,
        region_filter=_CONTEXT,
        quantity_filter=quantity_filter,
        case_sensitive=False)
    counts = {
        name: sorted(info.accelerator_count for info in infos)
        for name, infos in qtys.items()
    }
    return counts, capacity, available


def _assert_snapshot_matches_api(monkeypatch, cluster: str, **list_kwargs):
    nodes, pods = _CLUSTERS[cluster]
    api = _CoreApi(nodes, pods)
    expected = _list_realtime(monkeypatch, api, **list_kwargs)
    assert api.calls, 'the direct path must read the Kubernetes API'

    requested_keys: List[str] = []

    def provider(context, label_keys):
        # Keeps only the requested labels and the Ready condition, as a cache
        # serving the snapshot may.
        if context != _CONTEXT:
            return None
        requested_keys.extend(label_keys)
        trimmed = [
            dict(node,
                 metadata={
                     'name': node['metadata']['name'],
                     'labels': {
                         k: v
                         for k, v in node['metadata']['labels'].items()
                         if k in label_keys
                     },
                 },
                 status=dict(node['status'],
                             conditions=[
                                 c
                                 for c in node['status']['conditions']
                                 if c['type'] == 'Ready'
                             ]))
            for node in nodes
        ]
        return plugin_extensions.KubernetesNodeSnapshot(nodes=trimmed,
                                                        pods=pods)

    snapshot_api = _CoreApi(nodes, pods)
    actual = _list_realtime(monkeypatch,
                            snapshot_api,
                            provider=provider,
                            **list_kwargs)
    assert snapshot_api.calls == []
    assert 'kubernetes.io/hostname' not in requested_keys
    assert actual == expected
    return actual


@pytest.mark.parametrize('cluster', sorted(_CLUSTERS))
def test_snapshot_matches_api(monkeypatch, cluster_env, cluster):
    _assert_snapshot_matches_api(monkeypatch, cluster)


@pytest.mark.parametrize('cluster', sorted(_CLUSTERS))
def test_snapshot_matches_api_with_custom_gpu_resource_key(
        monkeypatch, cluster_env, cluster):
    monkeypatch.setenv('CUSTOM_GPU_RESOURCE_KEY', 'example.com/gpu')
    _assert_snapshot_matches_api(monkeypatch, cluster)


@pytest.mark.parametrize('allowed_nodes', [
    {
        'names': ['h100-busy', 'a100']
    },
    {
        'label_selector': {
            'pool': 'a100'
        }
    },
    {
        'ips': ['10.0.0.1', '10.0.2.1']
    },
])
def test_snapshot_matches_api_with_allowed_nodes(monkeypatch, cluster_env,
                                                 allowed_nodes):
    cluster_env(allowed_nodes=allowed_nodes)
    _assert_snapshot_matches_api(monkeypatch, 'gfd')


def test_snapshot_matches_api_with_tolerations(monkeypatch, cluster_env):
    cluster_env(tolerations=_TOLERATION)
    _assert_snapshot_matches_api(monkeypatch, 'gfd')


@pytest.mark.parametrize('name_filter,quantity_filter', [
    ('H100', None),
    (None, 2),
    ('A100', 4),
])
def test_snapshot_matches_api_with_filters(monkeypatch, cluster_env,
                                           name_filter, quantity_filter):
    _assert_snapshot_matches_api(monkeypatch,
                                 'gfd',
                                 name_filter=name_filter,
                                 quantity_filter=quantity_filter)


def test_snapshot_counts(monkeypatch, cluster_env):
    counts, capacity, available = _assert_snapshot_matches_api(
        monkeypatch, 'gfd')
    assert capacity == {'H100-80GB': 56, 'A100-80GB': 4}
    # h100-free (8; verification pods excluded), h100-busy (8 - 6 Running or
    # Pending), h100-degraded (6 allocatable); cordoned, not-ready and
    # untolerated-taint nodes add none.
    assert available == {'H100-80GB': 16, 'A100-80GB': 3}
    assert counts == {'H100-80GB': [1, 2, 4, 8], 'A100-80GB': [1, 2, 4]}


def _unserved(context, label_keys):
    del context, label_keys
    return None


def _failing(context, label_keys):
    del label_keys
    raise RuntimeError(f'cache for {context} unavailable')


@pytest.mark.parametrize('provider', [_unserved, _failing])
def test_unserved_context_falls_back_to_api(monkeypatch, cluster_env, provider):
    nodes, pods = _CLUSTERS['gfd']
    expected = _list_realtime(monkeypatch, _CoreApi(nodes, pods))
    api = _CoreApi(nodes, pods)
    assert _list_realtime(monkeypatch, api, provider=provider) == expected
    assert 'list_pod_for_all_namespaces' in api.calls


def test_non_realtime_listing_ignores_snapshot(monkeypatch, cluster_env):
    nodes, pods = _CLUSTERS['gfd']
    api = _CoreApi(nodes, pods)
    snapshot = plugin_extensions.KubernetesNodeSnapshot(nodes=[], pods=[])
    _serve(monkeypatch, api, lambda context, label_keys: snapshot)
    qtys = kubernetes_catalog.list_accelerators(gpus_only=True,
                                                name_filter=None,
                                                region_filter=_CONTEXT,
                                                quantity_filter=None)
    assert set(qtys) == {'H100-80GB', 'A100-80GB'}
    assert 'list_node' in api.calls


def test_snapshot_label_keys(cluster_env):
    cluster_env(allowed_nodes={'label_selector': {'pool': 'a100'}})
    keys = set(kubernetes_utils.get_node_snapshot_label_keys(_CONTEXT))
    for formatter in kubernetes_utils.LABEL_FORMATTER_REGISTRY:
        assert set(formatter.get_label_keys()) <= keys
    assert {
        'cloud.google.com/gke-tpu-topology',
        'cloud.google.com/gke-accelerator-count',
        'pool',
    } <= keys
