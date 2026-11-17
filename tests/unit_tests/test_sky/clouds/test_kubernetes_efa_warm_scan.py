"""Warm-node EFA scan must only use nodes the pod can land on.

`Kubernetes._detect_network_type` derives the `vpc.amazonaws.com/efa` count
for a `network_tier: best` request. It has two paths: scan a running node and
use its own EFA allocatable (warm), or size from the instance-type catalog
(cold).

The warm scan used to accept any node that merely carried the accelerator label
KEY, without comparing its VALUE to the requested accelerator. On a
heterogeneous cluster every GPU node carries the key, so an `L4:8` request
matched a warm `p5.48xlarge` (label `h100`, 8 GPUs, 32 EFA) and derived
`floor(8/8*32) = 32`. The pod then requested 32 EFA interfaces on a node type
that has none and stayed Pending. Upstream bug from #8557.

The scan now keeps only nodes whose label value is one of
`k8s_acc_label_values`, the values the pod's required node affinity pins to,
as `_derive_rdma_nic_count` does. Besides the mismatch, these tests pin the
matching cases: an over-corrected filter that skipped every warm node would
still schedule through the cold catalog path, so a test of the mismatch alone
would pass it.
"""
from unittest import mock

import pytest

from sky.clouds import kubernetes as ck
from sky.utils import resources_utils

_GPU_LABEL = 'karpenter.k8s.aws/instance-gpu-name'
_GPU_RESOURCE = 'nvidia.com/gpu'
_EFA_RESOURCE = 'vpc.amazonaws.com/efa'


def _node(gpu_name, gpu_count, efa_count, label_key=_GPU_LABEL):
    """A warm AWS GPU node with an accelerator label (Karpenter's default)."""
    node = mock.MagicMock()
    node.metadata.labels = {
        label_key: gpu_name,
        # Any AWS node carries these; this is what sets saw_aws_efa_node.
        'k8s.io/cloud-provider-aws': 'true',
    }
    allocatable = {_GPU_RESOURCE: str(gpu_count)}
    if efa_count is not None:
        allocatable[_EFA_RESOURCE] = str(efa_count)
    node.status.allocatable = allocatable
    return node


def _detect(nodes,
            acc_type,
            acc_count,
            label_values,
            autoscaler=None,
            label_key=_GPU_LABEL):
    with mock.patch.object(ck.kubernetes_utils,
                           'get_kubernetes_nodes',
                           return_value=nodes), \
         mock.patch.object(ck.skypilot_config,
                           'get_effective_region_config',
                           return_value=autoscaler):
        return ck.Kubernetes._detect_network_type(
            'ctx',
            resources_utils.NetworkTier.BEST,
            label_key,
            _GPU_RESOURCE,
            acc_count=acc_count,
            acc_type=acc_type,
            k8s_acc_label_values=label_values)


def test_warm_node_of_a_different_accelerator_is_not_used():
    """L4:8 must not inherit a warm h100 node's 32 EFA interfaces.

    This is the regression: 32 on an L4 node is unschedulable.

    With no autoscaler (a static cluster) the skipped node leaves nothing to
    size from, so the result is (NONE, None) -- the same outcome as the
    existing skip of a warm node with too few GPUs.
    """
    net, meta = _detect([_node('h100', 8, 32)],
                        acc_type='L4',
                        acc_count=8,
                        label_values=['l4'])
    assert meta is None, (
        f'warm h100 node leaked its EFA count into an L4 request: {meta}')
    assert net == ck.KubernetesHighPerformanceNetworkType.NONE


def test_warm_node_of_the_matching_accelerator_is_still_used():
    """The filter must not skip EVERYTHING -- catches an over-correction."""
    _, meta = _detect([_node('h100', 8, 32)],
                      acc_type='H100',
                      acc_count=8,
                      label_values=['h100'])
    assert meta == {
        'efa_count': 32
    }, (f'matching warm node should supply its own EFA count, got {meta}')


def test_no_affinity_values_keeps_previous_behaviour():
    """With no affinity values (no accelerator pinned), nothing is filtered."""
    _, meta = _detect([_node('h100', 8, 32)],
                      acc_type=None,
                      acc_count=8,
                      label_values=None)
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
                            label_values=['l4'],
                            autoscaler='karpenter')
    derived.assert_called_once_with('L4', 8)
    assert meta == {'efa_count': 1}
    assert net == ck.KubernetesHighPerformanceNetworkType.AWS_EFA


def test_matching_accelerator_among_several_warm_nodes():
    """A mismatched node earlier in the list must not shadow a matching one."""
    nodes = [_node('h100', 8, 32), _node('l4', 8, 1)]
    _, meta = _detect(nodes, acc_type='L4', acc_count=8, label_values=['l4'])
    assert meta == {
        'efa_count': 1
    }, (f'should have used the l4 node, not the h100 one; got {meta}')


@pytest.mark.parametrize('label_value', ['', 'not-an-accelerator'])
def test_node_outside_the_affinity_is_not_used(label_value):
    """An empty or unrecognized label value is outside the affinity too.

    The pod cannot land on such a node, so it must not supply an EFA count.
    """
    net, meta = _detect([_node(label_value, 8, 32)],
                        acc_type='L4',
                        acc_count=8,
                        label_values=['l4'])
    assert meta is None
    assert net == ck.KubernetesHighPerformanceNetworkType.NONE


def test_gfd_labelled_p5_still_matches_a_plain_h100_request():
    """A GFD raw label value in the affinity keeps the warm path.

    GPU-Feature-Discovery labels a p5 node
    `nvidia.com/gpu.product: NVIDIA-H100-80GB-HBM3`. For an `H100` request the
    affinity pins that raw value, so the node, the richest EFA SKU on AWS
    (32 interfaces), must still be used.
    """
    gfd_key = 'nvidia.com/gpu.product'
    node = _node('NVIDIA-H100-80GB-HBM3', 8, 32, label_key=gfd_key)
    _, meta = _detect([node],
                      acc_type='H100',
                      acc_count=8,
                      label_values=['NVIDIA-H100-80GB-HBM3'],
                      label_key=gfd_key)
    assert meta == {
        'efa_count': 32
    }, (f'GFD-labelled p5 must satisfy an H100 request; got {meta}')
