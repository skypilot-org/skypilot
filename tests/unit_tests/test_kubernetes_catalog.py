"""Tests for the Kubernetes accelerator catalog.

Covers the aggregation over all allowed contexts (issue #9267): without a
region filter, the accelerator listing must query every context the active
workspace allows and report each entry under the context it came from,
matching the table output of ``sky gpus list --cloud kubernetes``.
"""
import collections
import json

import pytest

from sky import check as sky_check
from sky import clouds
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.catalog import kubernetes_catalog
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.kubernetes.utils import SkyPilotLabelFormatter
from sky.provision.kubernetes.utils import V1Node

_CONTEXT_A = 'ctx-a'
_CONTEXT_B = 'ctx-b'


def _gpu_node(name: str, accelerator_label: str, gpu_count: int) -> V1Node:
    return V1Node.from_dict({
        'metadata': {
            'name': name,
            'labels': {
                SkyPilotLabelFormatter.LABEL_KEY: accelerator_label
            },
        },
        'status': {
            'allocatable': {
                'nvidia.com/gpu': str(gpu_count),
                'cpu': '32',
                'memory': '200Gi'
            },
            'capacity': {
                'nvidia.com/gpu': str(gpu_count),
                'cpu': '32',
                'memory': '200Gi'
            },
            'conditions': [{
                'type': 'Ready',
                'status': 'True'
            }],
        },
        'spec': {
            'unschedulable': False,
            'taints': []
        },
    })


# An 8xA100 node in ctx-a and a 4xH100 node in ctx-b.
_NODES_BY_CONTEXT = {
    _CONTEXT_A: [_gpu_node('node-a1', 'a100-80gb', 8)],
    _CONTEXT_B: [_gpu_node('node-b1', 'h100', 4)],
}


def _patch_cluster_calls(monkeypatch, nodes_by_context) -> list:
    """Patches every catalog call that reaches a cluster.

    Returns the list the contexts' credential checks are recorded in.
    """
    monkeypatch.setattr(sky_check, 'get_cached_enabled_clouds_or_refresh',
                        lambda *_, **__: [clouds.Kubernetes()])
    checked = []

    def check_credentials(context, **_):
        checked.append(context)
        return True, None

    monkeypatch.setattr(kubernetes_utils, 'check_credentials',
                        check_credentials)
    monkeypatch.setattr(kubernetes_utils, 'detect_accelerator_resource',
                        lambda context: (True, {'nvidia.com/gpu'}))
    monkeypatch.setattr(kubernetes_utils, 'detect_gpu_label_formatter',
                        lambda context: (SkyPilotLabelFormatter(), {}))
    monkeypatch.setattr(
        kubernetes_utils,
        'get_kubernetes_nodes',
        lambda context=None: list(nodes_by_context.get(context, [])))
    monkeypatch.setattr(kubernetes_utils,
                        'get_gpu_resource_key',
                        lambda context=None: 'nvidia.com/gpu')
    # The real get_allocated_gpu_qty_by_node returns a defaultdict.
    monkeypatch.setattr(kubernetes_utils,
                        'get_allocated_gpu_qty_by_node',
                        lambda context=None: collections.defaultdict(int))
    return checked


@pytest.fixture
def checked_contexts(monkeypatch, tmp_path):
    """Two allowed contexts with valid credentials, isolated config."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    config = tmp_path / 'config.yaml'
    config.write_text(json.dumps({}))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(config))
    skypilot_config.reload_config()
    checked = _patch_cluster_calls(monkeypatch, _NODES_BY_CONTEXT)
    monkeypatch.setattr(clouds.Kubernetes,
                        'existing_allowed_contexts',
                        lambda silent=False: [_CONTEXT_A, _CONTEXT_B])
    return checked


def _regions(result, accelerator) -> set:
    return {item.region for item in result[accelerator]}


def test_list_accelerators_merges_all_allowed_contexts(checked_contexts):
    """Without a region filter, every allowed context is listed (#9267)."""
    _, capacity, _ = kubernetes_catalog.list_accelerators_realtime(
        gpus_only=True,
        name_filter=None,
        region_filter=None,
        quantity_filter=None)
    result = kubernetes_catalog.list_accelerators(gpus_only=True,
                                                  name_filter=None,
                                                  region_filter=None,
                                                  quantity_filter=None)

    # The contexts are queried in parallel: compare as a set.
    assert set(checked_contexts) == {_CONTEXT_A, _CONTEXT_B}
    # ctx-a's A100s and ctx-b's H100s are both reported, each under the
    # context it came from.
    assert _regions(result, 'A100-80GB') == {_CONTEXT_A}
    assert _regions(result, 'H100') == {_CONTEXT_B}
    # The quantity ladder: powers of two up to the per-node count.
    assert sorted(
        item.accelerator_count for item in result['A100-80GB']) == [1, 2, 4, 8]
    assert sorted(
        item.accelerator_count for item in result['H100']) == [1, 2, 4]
    # The per-context capacities add up across the contexts.
    assert capacity == {'A100-80GB': 8, 'H100': 4}


def test_list_accelerators_region_filter_queries_only_that_context(
        checked_contexts):
    """An explicit region filter keeps querying a single context."""
    result = kubernetes_catalog.list_accelerators(gpus_only=True,
                                                  name_filter=None,
                                                  region_filter=_CONTEXT_B,
                                                  quantity_filter=None)

    assert checked_contexts == [_CONTEXT_B]
    assert list(result) == ['H100']
    assert _regions(result, 'H100') == {_CONTEXT_B}


@pytest.mark.usefixtures('checked_contexts')
def test_list_accelerators_skips_context_with_invalid_credentials(monkeypatch):
    checked = []

    def check_credentials(context, **_):
        checked.append(context)
        if context == _CONTEXT_A:
            return False, 'Invalid credentials'
        return True, None

    monkeypatch.setattr(kubernetes_utils, 'check_credentials',
                        check_credentials)

    result = kubernetes_catalog.list_accelerators(gpus_only=True,
                                                  name_filter=None,
                                                  region_filter=None,
                                                  quantity_filter=None)

    # ctx-a is skipped, but it does not abort the listing of ctx-b.
    assert set(checked) == {_CONTEXT_A, _CONTEXT_B}
    assert list(result) == ['H100']
    assert _regions(result, 'H100') == {_CONTEXT_B}


def test_list_accelerators_without_allowed_contexts(checked_contexts,
                                                    monkeypatch):
    """No allowed context (and no in-cluster fallback) lists nothing."""
    monkeypatch.setattr(clouds.Kubernetes,
                        'existing_allowed_contexts',
                        lambda silent=False: [])

    result, capacity, available = kubernetes_catalog.list_accelerators_realtime(
        gpus_only=True,
        name_filter=None,
        region_filter=None,
        quantity_filter=None)

    assert (result, capacity, available) == ({}, {}, {})
    assert checked_contexts == []


def test_list_accelerators_falls_back_to_in_cluster_context(
        monkeypatch, tmp_path):
    """A pod with no kubeconfig context lists the in-cluster context.

    Goes through the real existing_allowed_contexts() so its in-cluster
    fallback is exercised.
    """
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.delenv(kubernetes.IN_CLUSTER_CONTEXT_NAME_ENV_VAR,
                       raising=False)
    config = tmp_path / 'config.yaml'
    config.write_text(json.dumps({}))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(config))
    skypilot_config.reload_config()
    # No nodes behind the in-cluster context: the point is which context the
    # catalog queries, not what it finds there.
    checked = _patch_cluster_calls(monkeypatch, {})

    in_cluster = kubernetes.in_cluster_context_name()
    # What the real kubeconfig readers return inside a pod whose kubeconfig
    # was not uploaded: only the in-cluster context exists.
    monkeypatch.setattr(kubernetes_utils, 'get_all_kube_context_names',
                        lambda: [in_cluster])
    monkeypatch.setattr(kubernetes_utils,
                        'get_current_kube_config_context_name', lambda: None)
    monkeypatch.setattr(kubernetes_utils, 'is_incluster_config_available',
                        lambda: True)

    result, capacity, available = kubernetes_catalog.list_accelerators_realtime(
        gpus_only=True,
        name_filter=None,
        region_filter=None,
        quantity_filter=None)

    assert checked == [in_cluster]
    assert (result, capacity, available) == ({}, {}, {})


@pytest.mark.usefixtures('checked_contexts')
def test_list_accelerators_realtime_sums_across_contexts(monkeypatch):
    """Realtime capacity and availability add up over the contexts."""
    monkeypatch.setattr(kubernetes_utils,
                        'get_allocated_gpu_qty_by_node',
                        lambda context=None: collections.defaultdict(
                            int, {'node-a1': 3}
                            if context == _CONTEXT_A else {'node-b1': 1}))

    _, capacity, available = kubernetes_catalog.list_accelerators_realtime(
        gpus_only=True,
        name_filter=None,
        region_filter=None,
        quantity_filter=None)

    assert capacity == {'A100-80GB': 8, 'H100': 4}
    # ctx-a has 8-3=5 A100s free, ctx-b has 4-1=3 H100s free.
    assert available == {'A100-80GB': 5, 'H100': 3}


@pytest.mark.usefixtures('checked_contexts')
def test_list_accelerators_all_regions_keeps_per_context_entries(monkeypatch):
    """--all-regions keeps one entry per context for a shared GPU type."""
    # Give ctx-b an A100 node too, so both contexts list A100-80GB.
    nodes_by_context = {
        _CONTEXT_A: _NODES_BY_CONTEXT[_CONTEXT_A][:],
        _CONTEXT_B: _NODES_BY_CONTEXT[_CONTEXT_A][:]
    }
    monkeypatch.setattr(
        kubernetes_utils,
        'get_kubernetes_nodes',
        lambda context=None: list(nodes_by_context.get(context, [])))

    result = kubernetes_catalog.list_accelerators(gpus_only=True,
                                                  name_filter=None,
                                                  region_filter=None,
                                                  quantity_filter=None,
                                                  all_regions=True)

    assert _regions(result, 'A100-80GB') == {_CONTEXT_A, _CONTEXT_B}
