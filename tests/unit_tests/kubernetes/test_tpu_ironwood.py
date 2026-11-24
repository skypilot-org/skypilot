"""Tests for Ironwood (TPU7x) GKE TPUs on Kubernetes."""
from unittest import mock

import pytest

from sky import resources as resources_lib
from sky.adaptors import gcp
from sky.catalog import kubernetes_catalog
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import accelerator_registry

_FMT = kubernetes_utils.GKELabelFormatter
_GKE = kubernetes_utils.GKEAutoscaler


def test_tpu7x_is_a_gke_tpu():
    assert kubernetes_utils.is_tpu_on_gke('tpu7x')
    assert kubernetes_utils.is_tpu_on_gke('tpu7x', normalize=False)
    # Google's TensorCore-count names are not accepted: `tpu7x-8` is 4 chips
    # in Google's naming and 8 in the `tpu-v6e-8` style, so neither is
    # assumed.
    assert not kubernetes_utils.is_tpu_on_gke('tpu7x-8')


def test_tpu7x_label_key_and_value():
    assert _FMT.get_label_key('tpu7x') == _FMT.TPU_LABEL_KEY
    assert _FMT.get_label_values('tpu7x') == ['tpu7x']
    assert kubernetes_utils.get_gke_accelerator_name('tpu7x') == 'tpu7x'
    assert _FMT.get_tpu_topology_label_value('tpu7x', 4) == '2x2x1'
    assert _FMT.get_accelerator_from_label_value('tpu7x') == 'tpu7x'


def test_tpu7x_name_is_canonical():
    assert accelerator_registry.canonicalize_accelerator_name('TPU7x',
                                                              None) == 'tpu7x'


@pytest.mark.parametrize('count,expected', [
    (4, None),
    (8, ('2x2x2', 2)),
    (16, ('2x2x4', 4)),
    (32, ('2x4x4', 8)),
    (64, ('4x4x4', 16)),
    (128, ('4x4x8', 32)),
    (256, ('4x8x8', 64)),
    (2048, ('8x16x16', 512)),
    (12, None),
])
def test_tpu7x_multi_host_shape(count, expected):
    # Ironwood has only 4-chip VMs, so 8 chips and up is multi-host without
    # `accelerator_args.multi_host`.
    shape = kubernetes_utils.get_tpu_multi_host_shape('tpu7x', count, None)
    if expected is None:
        assert shape is None
        return
    assert shape is not None
    assert shape.accelerator == 'tpu7x'
    assert shape.chips_per_vm == 4
    assert (shape.topology, shape.vms_per_slice) == expected
    assert kubernetes_utils.reduce_tpu_topology(shape.topology) == count


def test_tpu7x_multi_host_opt_in_is_rejected():
    """`multi_host: true` is only for the 8-chip v6e size with two VM shapes."""
    r = resources_lib.Resources(infra='kubernetes',
                                accelerators='tpu7x:8',
                                accelerator_args={'multi_host': True})
    with pytest.raises(ValueError, match='multi_host'):
        r.validate()


@pytest.mark.parametrize('machine_type,chips', [
    ('tpu7x-standard-4t', 4),
    ('tpu7x-standard-1t', 1),
    ('ct6e-standard-8t', 8),
    ('ct5lp-hightpu-4t', 4),
    ('e2-standard-8', 0),
])
def test_chip_count_from_machine_type(machine_type, chips):
    assert _GKE._tpu_chip_count_from_instance_type(machine_type) == chips


def _ironwood_pool(topology=None):
    """A tpu7x node pool as GKE describes it (gcloud, 2026-11-23).

    GKE labels Ironwood pools `single-host` even when a workload policy makes
    them a multi-host slice.
    """
    pool = {
        'name': 'pool',
        'config': {
            'machineType': 'tpu7x-standard-4t',
            'resourceLabels': {
                'goog-gke-accelerator-type': 'tpu7x',
                'goog-gke-node-pool-provisioning-model': 'spot',
                'goog-gke-tpu-node-pool-type': 'single-host',
            },
        },
    }
    if topology is not None:
        pool['placementPolicy'] = {
            'policyName': f'tpu7x-{topology}',
            'tpuTopology': topology,
        }
    return pool


def test_ironwood_slice_pool_is_multi_host():
    assert _GKE._node_pool_is_multi_host_tpu(_ironwood_pool('2x2x2'))
    assert not _GKE._node_pool_is_multi_host_tpu(_ironwood_pool())
    # A topology that fits in one VM is still single-host.
    assert not _GKE._node_pool_is_multi_host_tpu(_ironwood_pool('2x2x1'))


def test_ironwood_autoscaler_matches_pools_to_requests():
    shape_8 = kubernetes_utils.get_tpu_multi_host_shape('tpu7x', 8, None)
    shape_16 = kubernetes_utils.get_tpu_multi_host_shape('tpu7x', 16, None)
    slice_pool = _ironwood_pool('2x2x2')
    vm_pool = _ironwood_pool()
    assert _GKE._node_pool_is_tpu_slice(slice_pool, shape_8)
    assert not _GKE._node_pool_is_tpu_slice(slice_pool, shape_16)
    assert not _GKE._node_pool_is_tpu_slice(vm_pool, shape_8)
    fits = _GKE._check_instance_fits_gke_autoscaler_node_pool
    # A single-host request fits the single-host pool, not one VM of a slice.
    assert fits('8CPU--64GB--tpu7x:4', vm_pool)
    assert not fits('8CPU--64GB--tpu7x:4', slice_pool)
    assert fits('8CPU--64GB--tpu7x:8', slice_pool, shape_8)
    assert not fits('8CPU--64GB--tpu7x:8', vm_pool, shape_8)


def test_gke_api_error_falls_back_to_optimistic_scheduling(monkeypatch):
    """A refused GKE API call (no permission on the project) logs the error
    and schedules optimistically instead of crashing the launch."""

    class _Resp(dict):
        status = 403
        reason = 'Forbidden'

    def _raise(*args, **kwargs):
        raise gcp.http_error_exception()(_Resp(),
                                         b'{"error": "PERMISSION_DENIED"}')

    monkeypatch.setattr(gcp, 'build', _raise)
    ctx = 'gke_my-project_us-central1-c_my-cluster'
    assert _GKE.can_create_new_instance_of_type(ctx, '8CPU--64GB--tpu7x:4')
    assert _GKE.get_available_machine_types(ctx) == []


def _k8s(accelerators, accelerator_args=None):
    return resources_lib.Resources(infra='kubernetes',
                                   accelerators=accelerators,
                                   accelerator_args=accelerator_args)


@pytest.mark.parametrize(
    'requested,task_nodes,launched,launched_nodes,ok',
    [
        # A single-VM request never runs on a slice.
        ('tpu7x:4', 1, 'tpu7x:8', 1, False),
        # A slice request does not fit a single-VM cluster.
        ('tpu7x:8', 1, 'tpu7x:4', 1, False),
        # Different slice shapes.
        ('tpu7x:8', 1, 'tpu7x:16', 1, False),
        ('tpu-v6e-16', 1, 'tpu-v6e-32', 1, False),
        # Same slice, every slice of the cluster.
        ('tpu7x:8', 1, 'tpu7x:8', 1, True),
        ('tpu7x:8', 2, 'tpu7x:8', 2, True),
        ('tpu-v6e-16', 1, 'tpu-v6e-16', 1, True),
        # One slice of a two-slice cluster.
        ('tpu7x:8', 1, 'tpu7x:8', 2, False),
        # Single-host clusters keep the existing rules.
        ('tpu7x:4', 1, 'tpu7x:4', 2, True),
        ('tpu-v6e-slice:4', 1, 'tpu-v6e-slice:8', 1, True),
        # Tasks without a TPU request are not restricted.
        (None, 1, 'tpu7x:8', 2, True),
    ])
def test_tpu_slice_fit_error(requested, task_nodes, launched, launched_nodes,
                             ok):
    error = kubernetes_utils.tpu_slice_fit_error(_k8s(requested), task_nodes,
                                                 _k8s(launched), launched_nodes)
    assert (error is None) == ok, error


def test_v6e_8_opt_in_slice_must_match():
    slice_8 = _k8s('tpu-v6e-8', {'multi_host': True})
    vm_8 = _k8s('tpu-v6e-8')
    assert kubernetes_utils.tpu_slice_fit_error(vm_8, 1, slice_8, 1)
    assert kubernetes_utils.tpu_slice_fit_error(slice_8, 1, vm_8, 1)
    assert kubernetes_utils.tpu_slice_fit_error(slice_8, 1, slice_8, 1) is None


@pytest.mark.parametrize('name', ['TPU7x', 'Tpu7X'])
def test_mixed_case_ironwood_goes_to_kubernetes(name):
    r = resources_lib.Resources(accelerators=f'{name}:4')
    assert r.cloud is not None and r.cloud.canonical_name() == 'kubernetes'


def test_show_gpus_lists_a_slice_vm_by_slice_size():
    """A VM of a 2x2x2 slice is requested as the whole slice (8 chips)."""

    def _node(name, topology):
        node = mock.MagicMock()
        node.metadata.name = name
        node.metadata.labels = {
            _FMT.TPU_LABEL_KEY: 'tpu7x',
            _FMT.TPU_TOPOLOGY_LABEL_KEY: topology,
            _FMT.ACCELERATOR_COUNT_LABEL_KEY: '4',
        }
        node.status.allocatable = {'google.com/tpu': '4'}
        node.status.capacity = {'google.com/tpu': '4'}
        node.is_ready.return_value = True
        node.is_cordoned.return_value = False
        node.get_taints.return_value = []
        return node

    nodes = [_node('s1', '2x2x2'), _node('s2', '2x2x2'), _node('v1', '2x2x1')]
    p = 'sky.provision.kubernetes.utils.'
    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch(p + 'get_current_kube_config_context_name', return_value='ctx'), \
         mock.patch('sky.clouds.Kubernetes.existing_allowed_contexts', return_value=['ctx']), \
         mock.patch(p + 'check_credentials', return_value=[True]), \
         mock.patch(p + 'detect_accelerator_resource', return_value=True), \
         mock.patch(p + 'detect_gpu_label_formatter', return_value=[_FMT(), None]), \
         mock.patch(p + 'get_kubernetes_nodes', return_value=nodes), \
         mock.patch(p + 'get_allocated_resources_by_node', return_value=({'s1': 0, 's2': 0, 'v1': 0}, {})), \
         mock.patch(p + 'get_gpu_resource_key', return_value='nvidia.com/gpu'):
        counts, capacity, _ = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)
    assert sorted(int(i.accelerator_count) for i in counts['tpu7x']) == [4, 8]
    assert capacity['tpu7x'] == 12


def test_slice_rule_skips_native_gcp_tpu_vms():
    """Native GCP TPU VM pods keep their own node rules."""
    gcp_cluster = resources_lib.Resources(infra='gcp',
                                          accelerators='tpu-v6e-32')
    task = resources_lib.Resources(infra='gcp', accelerators='tpu-v6e-32')
    assert kubernetes_utils.tpu_slice_fit_error(task, 1, gcp_cluster, 2) is None


def _tpu_node(name, accelerator, topology, chips='4', pool='pool'):
    node = mock.MagicMock()
    node.metadata.name = name
    node.metadata.labels = {
        _FMT.TPU_LABEL_KEY: accelerator,
        _FMT.TPU_TOPOLOGY_LABEL_KEY: topology,
        _FMT.ACCELERATOR_COUNT_LABEL_KEY: chips,
        'cloud.google.com/gke-nodepool': pool,
    }
    node.status.allocatable = {'google.com/tpu': chips}
    node.status.capacity = {'google.com/tpu': chips}
    node.is_ready.return_value = True
    node.is_cordoned.return_value = False
    node.get_taints.return_value = []
    return node


def _show_gpus(nodes, quantity_filter=None, allocated=None):
    p = 'sky.provision.kubernetes.utils.'
    allocated = allocated or {}
    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch(p + 'get_current_kube_config_context_name', return_value='ctx'), \
         mock.patch('sky.clouds.Kubernetes.existing_allowed_contexts', return_value=['ctx']), \
         mock.patch(p + 'check_credentials', return_value=[True]), \
         mock.patch(p + 'detect_accelerator_resource', return_value=True), \
         mock.patch(p + 'detect_gpu_label_formatter', return_value=[_FMT(), None]), \
         mock.patch(p + 'get_kubernetes_nodes', return_value=nodes), \
         mock.patch(p + 'get_allocated_resources_by_node', return_value=({n.metadata.name: allocated.get(n.metadata.name, 0) for n in nodes}, {})), \
         mock.patch(p + 'get_gpu_resource_key', return_value='nvidia.com/gpu'):
        return kubernetes_catalog.list_accelerators_realtime(
            True, None, None, quantity_filter)


def test_show_gpus_slice_filter_counts_whole_slice_vms():
    nodes = [
        _tpu_node('s1', 'tpu7x', '2x2x2'),
        _tpu_node('s2', 'tpu7x', '2x2x2')
    ]
    _, capacity, available = _show_gpus(nodes, quantity_filter=8)
    assert capacity['tpu7x'] == 8
    assert available['tpu7x'] == 8
    # A busy VM makes its whole slice unavailable.
    _, _, available = _show_gpus(nodes, quantity_filter=8, allocated={'s1': 4})
    assert available['tpu7x'] == 0


def test_show_gpus_slice_filter_matches_slice_size_exactly():
    """An 8-chip request can only use an 8-chip slice."""
    nodes = [
        _tpu_node('a1', 'tpu7x', '2x2x2', pool='a'),
        _tpu_node('a2', 'tpu7x', '2x2x2', pool='a')
    ]
    nodes += [_tpu_node(f'b{i}', 'tpu7x', '2x2x4', pool='b') for i in range(4)]
    _, capacity, available = _show_gpus(nodes, quantity_filter=8)
    assert (capacity['tpu7x'], available['tpu7x']) == (8, 8)
    _, capacity, available = _show_gpus(nodes, quantity_filter=16)
    assert (capacity['tpu7x'], available['tpu7x']) == (16, 16)
    _, capacity, available = _show_gpus(nodes)
    assert (capacity['tpu7x'], available['tpu7x']) == (24, 24)


def test_show_gpus_skips_slices_skypilot_cannot_launch():
    """TPU v4 has no multi-host shape in SkyPilot."""
    nodes = [
        _tpu_node('v4', 'tpu-v4-podslice', '4x4'),
        _tpu_node('s1', 'tpu7x', '2x2x2')
    ]
    counts, _, _ = _show_gpus(nodes)
    assert 'tpu-v4-podslice' not in counts
    assert [int(i.accelerator_count) for i in counts['tpu7x']] == [8]


@pytest.mark.parametrize('tolerated,free', [(False, 0), (True, 8)])
def test_show_gpus_slice_taint(tolerated, free):
    """An untolerated taint on one VM makes its whole slice busy; a taint
    the configured tolerations cover does not."""
    nodes = [
        _tpu_node('a1', 'tpu7x', '2x2x2', pool='a'),
        _tpu_node('a2', 'tpu7x', '2x2x2', pool='a')
    ]
    nodes[0].get_taints.return_value = [{
        'key': 'maintenance',
        'value': 'true',
        'effect': 'NoSchedule',
        'tolerated': tolerated,
    }]
    _, _, available = _show_gpus(nodes, quantity_filter=8)
    assert available['tpu7x'] == free
