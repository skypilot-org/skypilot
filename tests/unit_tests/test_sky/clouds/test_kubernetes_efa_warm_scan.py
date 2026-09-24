"""Warm-node EFA scan must respect the requested accelerator type.

`Kubernetes._detect_network_type` derives the `vpc.amazonaws.com/efa` count for a
`network_tier: best` request. It has two paths: scan a running node and use its
own EFA allocatable (warm), or size from the instance-type catalog (cold).

The warm scan used to accept any node that merely carried the accelerator label
KEY, without comparing its VALUE to the requested accelerator. On a
heterogeneous cluster every GPU node carries the key, so an `L4:8` request
matched a warm `p5.48xlarge` (label `h100`, 8 GPUs, 32 EFA) and derived
`floor(8/8*32) = 32`. The pod then requested 32 EFA interfaces on a node type
that has none and stayed Pending. Upstream bug from #8557.

These tests pin all three behaviours, because fixing only the first one is easy
to over-correct into skipping every warm node -- which still "works" (the cold
catalog path is safe) and would therefore ship green.
"""
from unittest import mock

import pytest

from sky.clouds import kubernetes as ck
from sky.utils import resources_utils

_GPU_LABEL = 'karpenter.k8s.aws/instance-gpu-name'
_GPU_RESOURCE = 'nvidia.com/gpu'
_EFA_RESOURCE = 'vpc.amazonaws.com/efa'


def _node(gpu_name, gpu_count, efa_count):
    """A warm AWS GPU node carrying Karpenter's gpu-name label."""
    node = mock.MagicMock()
    node.metadata.labels = {
        _GPU_LABEL: gpu_name,
        # Any AWS node carries these; this is what sets saw_aws_efa_node.
        'k8s.io/cloud-provider-aws': 'true',
    }
    allocatable = {_GPU_RESOURCE: str(gpu_count)}
    if efa_count is not None:
        allocatable[_EFA_RESOURCE] = str(efa_count)
    node.status.allocatable = allocatable
    return node


def _detect(nodes, acc_type, acc_count, autoscaler=None):
    with mock.patch.object(ck.kubernetes_utils,
                           'get_kubernetes_nodes',
                           return_value=nodes), \
         mock.patch.object(ck.skypilot_config,
                           'get_effective_region_config',
                           return_value=autoscaler):
        return ck.Kubernetes._detect_network_type(
            'ctx',
            resources_utils.NetworkTier.BEST,
            _GPU_LABEL,
            _GPU_RESOURCE,
            acc_count=acc_count,
            acc_type=acc_type)


def test_warm_node_of_a_different_accelerator_is_not_used():
    """L4:8 must not inherit a warm h100 node's 32 EFA interfaces.

    This is the regression: 32 on an L4 node is unschedulable.
    """
    _, meta = _detect([_node('h100', 8, 32)], acc_type='L4', acc_count=8)
    assert meta != {
        'efa_count': 32
    }, ('warm h100 node leaked its EFA count into an L4 request')
    assert meta is None or meta.get('efa_count') != 32


def test_warm_node_of_the_matching_accelerator_is_still_used():
    """The guard must not skip EVERYTHING -- catches an over-correction.

    A naive raw-string compare (label 'h100' != acc_type 'H100') would skip this
    node too, silently falling through to the catalog. That still schedules, so
    it would pass a test that only checked the mismatch case.
    """
    _, meta = _detect([_node('h100', 8, 32)], acc_type='H100', acc_count=8)
    assert meta == {
        'efa_count': 32
    }, (f'matching warm node should supply its own EFA count, got {meta}')


def test_no_accelerator_requested_keeps_previous_behaviour():
    """acc_type=None is a legitimate caller; the guard must be a no-op there."""
    _, meta = _detect([_node('h100', 8, 32)], acc_type=None, acc_count=8)
    assert meta == {'efa_count': 32}


def test_mismatched_warm_node_falls_through_to_the_catalog():
    """With an autoscaler configured, the type-aware cold path takes over.

    That is the whole point of skipping the mismatched node: the catalog knows
    L4's real per-accelerator EFA ratio, the warm p5 node does not.
    """
    with mock.patch.object(ck.Kubernetes,
                           '_derive_efa_count_from_catalog',
                           return_value=1) as derived:
        net, meta = _detect([_node('h100', 8, 32)],
                            acc_type='L4',
                            acc_count=8,
                            autoscaler='karpenter')
    derived.assert_called_once_with('L4', 8)
    assert meta == {'efa_count': 1}
    assert net == ck.KubernetesHighPerformanceNetworkType.AWS_EFA


def test_matching_accelerator_among_several_warm_nodes():
    """A mismatched node earlier in the list must not shadow a matching one."""
    nodes = [_node('h100', 8, 32), _node('l4', 8, 1)]
    _, meta = _detect(nodes, acc_type='L4', acc_count=8)
    assert meta == {
        'efa_count': 1
    }, (f'should have used the l4 node, not the h100 one; got {meta}')


@pytest.mark.parametrize('label_value,requested', [
    ('h100', 'H100'),
    ('H100', 'h100'),
    ('l4', 'L4'),
])
def test_label_value_case_does_not_matter(label_value, requested):
    """Karpenter/SkyPilot label values are lowercase; acc_type is canonical."""
    _, meta = _detect([_node(label_value, 8, 32)],
                      acc_type=requested,
                      acc_count=8)
    assert meta == {'efa_count': 32}


def test_node_with_an_empty_accelerator_label_is_still_used():
    """Skip only on a POSITIVE mismatch -- "cannot tell" must not skip.

    An empty label value is legitimate on heterogeneous clusters, and elsewhere
    in the codebase it means "unknown" rather than "different". Uses the REAL
    helper (no mock): an earlier version of this test mocked
    _node_accelerator_name to return None and so asserted behaviour the code did
    not actually have -- the None branch was unreachable because the helper
    returned the raw value for unrecognised labels.
    """
    node = _node('', 8, 32)
    # The pre-existing key check requires the key to be PRESENT, which it is.
    _, meta = _detect([node], acc_type='L4', acc_count=8)
    assert meta == {
        'efa_count': 32
    }, (f'an undeterminable accelerator must not cause a skip; got {meta}')


def test_gfd_labelled_p5_still_matches_a_plain_h100_request():
    """The regression a plain string compare would have introduced.

    GPU-Feature-Discovery (NVIDIA GPU Operator, common on EKS) labels a p5 node
    `nvidia.com/gpu.product: NVIDIA-H100-80GB-HBM3`, which normalizes to
    'H100-80GB'. The catalog's canonical name -- what a user types and what
    Resources canonicalizes to -- is 'H100'. An equality test would skip
    p5.48xlarge, the richest EFA SKU on AWS (32 interfaces), and silently lose
    the warm path on every GFD-labelled cluster.
    """
    gfd_key = 'nvidia.com/gpu.product'
    node = mock.MagicMock()
    node.metadata.labels = {
        gfd_key: 'NVIDIA-H100-80GB-HBM3',
        'k8s.io/cloud-provider-aws': 'true',
    }
    node.status.allocatable = {_GPU_RESOURCE: '8', _EFA_RESOURCE: '32'}
    with mock.patch.object(ck.kubernetes_utils,
                           'get_kubernetes_nodes',
                           return_value=[node]), \
         mock.patch.object(ck.skypilot_config,
                           'get_effective_region_config',
                           return_value=None):
        _, meta = ck.Kubernetes._detect_network_type(
            'ctx',
            resources_utils.NetworkTier.BEST,
            gfd_key,
            _GPU_RESOURCE,
            acc_count=8,
            acc_type='H100')
    assert meta == {
        'efa_count': 32
    }, (f'GFD-labelled p5 must satisfy an H100 request; got {meta}')
