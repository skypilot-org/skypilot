"""Node reads for requests that share a process and filter nodes differently."""
import contextvars
import io
import json
from typing import Any, Dict, List

import pytest

from sky import skypilot_config
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import annotations
from sky.utils import config_utils
from sky.utils import context

_CONTEXT = 'test-ctx'


def _node(name: str, labels: Dict[str, str], gpus: str) -> Dict[str, Any]:
    capacity = {'cpu': '8', 'memory': '32Gi'}
    if gpus:
        capacity['nvidia.com/gpu'] = gpus
    return {
        'metadata': {
            'name': name,
            'labels': {
                'kubernetes.io/hostname': name,
                **labels
            }
        },
        'spec': {},
        'status': {
            'capacity': capacity,
            'allocatable': capacity,
            'addresses': [{
                'type': 'InternalIP',
                'address': '10.0.0.1'
            }],
        },
    }


_NODES = [
    _node('gpu-node', {'nvidia.com/gpu.product': 'NVIDIA-H100-80GB-HBM3'}, '8'),
    _node('cpu-node', {}, ''),
]


class _Response(io.BytesIO):
    """A streamed Kubernetes API response (_preload_content=False)."""

    def release_conn(self):
        pass


class _CoreApi:

    def __init__(self, nodes: List[Dict[str, Any]]):
        self.nodes = nodes
        self.list_node_calls = 0

    def list_node(self, **kwargs):
        del kwargs
        self.list_node_calls += 1
        return _Response(json.dumps({'items': self.nodes}).encode())


def _request(allowed_node: str) -> contextvars.Context:
    """A request whose client config allows only `allowed_node`."""
    request = contextvars.Context()

    def start():
        context.initialize()
        # pylint: disable-next=protected-access
        skypilot_config._set_loaded_config(
            config_utils.Config(
                {'kubernetes': {
                    'allowed_nodes': {
                        'names': [allowed_node]
                    }
                }}))

    request.run(start)
    return request


@pytest.fixture
def core_api(monkeypatch):
    api = _CoreApi(_NODES)
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda context=None: api)
    annotations.clear_request_level_cache()
    yield api
    annotations.clear_request_level_cache()


def test_each_request_gets_its_own_node_filter(core_api):
    gpu_request = _request('gpu-node')
    cpu_request = _request('cpu-node')

    def node_names():
        return [
            n.metadata.name
            for n in kubernetes_utils.get_kubernetes_nodes(context=_CONTEXT)
        ]

    assert gpu_request.run(node_names) == ['gpu-node']
    assert cpu_request.run(node_names) == ['cpu-node']
    assert gpu_request.run(node_names) == ['gpu-node']
    assert core_api.list_node_calls == 1


@pytest.mark.usefixtures('core_api')
def test_accelerator_detection_follows_each_request_node_filter():
    gpu_request = _request('gpu-node')
    cpu_request = _request('cpu-node')

    def has_accelerator():
        has_gpus, _ = kubernetes_utils.detect_accelerator_resource(_CONTEXT)
        return has_gpus

    def label_formatter():
        formatter, node_labels = kubernetes_utils.detect_gpu_label_formatter(
            _CONTEXT)
        return type(formatter).__name__, sorted(node_labels)

    assert gpu_request.run(has_accelerator)
    assert not cpu_request.run(has_accelerator)
    assert gpu_request.run(has_accelerator)

    assert gpu_request.run(label_formatter) == ('GFDLabelFormatter',
                                                ['gpu-node'])
    assert cpu_request.run(label_formatter) == ('NoneType', ['cpu-node'])
    assert gpu_request.run(label_formatter) == ('GFDLabelFormatter',
                                                ['gpu-node'])
