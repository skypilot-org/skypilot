"""Tests for Ironwood (TPU7x) GKE TPUs on Kubernetes."""
import pytest

from sky import resources as resources_lib
from sky.adaptors import gcp
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
