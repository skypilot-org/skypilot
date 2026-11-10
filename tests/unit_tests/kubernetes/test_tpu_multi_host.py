"""Tests for multi-host GKE TPU slices on Kubernetes."""
import pickle
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.backends import cloud_vm_ray_backend
from sky.backends import task_codegen
from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes import utils as kubernetes_utils

_FMT = kubernetes_utils.GKELabelFormatter


@pytest.mark.parametrize(
    'accelerator,count,enforce,expected',
    [
        # Sizes with a single-host VM stay single-host by default.
        ('tpu-v6e-1', 1, None, None),
        ('tpu-v6e-4', 1, None, None),
        ('tpu-v6e-8', 1, None, None),
        ('tpu-v6e-slice', 8, None, None),
        # enforce_multi_host picks the two-VM 2x4 slice for 8 chips.
        ('tpu-v6e-8', 1, True, ('2x4', 2)),
        ('tpu-v6e-slice', 8, True, ('2x4', 2)),
        # No multi-host slice of 4 chips exists.
        ('tpu-v6e-4', 1, True, None),
        # 16 chips and up exist only as multi-host slices.
        ('tpu-v6e-16', 1, None, ('4x4', 4)),
        ('tpu-v6e-slice', 32, None, ('4x8', 8)),
        ('tpu-v6e-64', 1, None, ('8x8', 16)),
        ('tpu-v6e-128', 1, None, ('8x16', 32)),
        ('tpu-v6e-256', 1, None, ('16x16', 64)),
        ('tpu-v6e-512', 1, None, None),
        ('H100', 8, True, None),
        (None, None, True, None),
    ])
def test_get_tpu_multi_host_shape(accelerator, count, enforce, expected):
    shape = kubernetes_utils.get_tpu_multi_host_shape(accelerator, count,
                                                      enforce)
    if expected is None:
        assert shape is None
    else:
        assert shape is not None
        assert shape.accelerator == 'tpu-v6e-slice'
        assert shape.chips_per_vm == 4
        assert (shape.topology, shape.vms_per_slice) == expected


def _validate(**kwargs):
    r = resources_lib.Resources(**kwargs)
    r.validate()
    return r


@pytest.mark.parametrize('kwargs', [
    dict(accelerators='tpu-v6e-8'),
    dict(accelerators='tpu-v6e-8:1'),
    dict(accelerators='tpu-v6e-slice:8'),
    dict(accelerators='tpu-v6e-8', infra='k8s'),
])
def test_enforce_multi_host_accepted(kwargs):
    r = _validate(enforce_multi_host=True, **kwargs)
    # A GCP TPU name resolves to Kubernetes, the only cloud with the flag.
    assert isinstance(r.cloud, clouds.Kubernetes)
    assert kubernetes_utils.tpu_vms_per_slice(r) == 2


@pytest.mark.parametrize('kwargs', [
    dict(accelerators='tpu-v6e-8', enforce_multi_host=False),
    dict(accelerators='tpu-v6e-8:2', enforce_multi_host=True),
    dict(accelerators='tpu-v6e-4', enforce_multi_host=True),
    dict(accelerators='tpu-v6e-16', enforce_multi_host=True),
    dict(accelerators='H100:8', enforce_multi_host=True),
    dict(enforce_multi_host=True),
    dict(accelerators='tpu-v6e-8', enforce_multi_host=True, infra='gcp'),
])
def test_enforce_multi_host_rejected(kwargs):
    with pytest.raises(ValueError, match='enforce_multi_host'):
        _validate(**kwargs)


def test_enforce_multi_host_round_trip():
    r = resources_lib.Resources(accelerators='tpu-v6e-8',
                                enforce_multi_host=True)
    config = r.to_yaml_config()
    assert config['enforce_multi_host'] is True
    parsed = list(resources_lib.Resources.from_yaml_config(config))[0]
    assert parsed.enforce_multi_host is True
    assert resources_lib.Resources(accelerators='tpu-v6e-8').to_yaml_config(
    ).get('enforce_multi_host') is None
    assert r.copy().enforce_multi_host is True


def test_multi_host_and_single_host_clusters_do_not_match():
    multi = resources_lib.Resources(infra='k8s/ctx',
                                    accelerators='tpu-v6e-8',
                                    enforce_multi_host=True)
    single = resources_lib.Resources(infra='k8s/ctx', accelerators='tpu-v6e-8')
    assert not single.less_demanding_than(multi)
    assert not multi.less_demanding_than(single)


def test_cpu_only_request_fits_multi_host_cluster():
    multi = resources_lib.Resources(infra='k8s/ctx',
                                    accelerators='tpu-v6e-8',
                                    enforce_multi_host=True)
    cpu_only = resources_lib.Resources(infra='k8s/ctx')
    assert cpu_only.less_demanding_than(multi)


def test_enforce_multi_host_pickle_round_trip():
    r = resources_lib.Resources(accelerators='tpu-v6e-8',
                                enforce_multi_host=True)
    assert pickle.loads(pickle.dumps(r)).enforce_multi_host is True


def test_unpickle_resources_from_before_enforce_multi_host():
    # Resources pickled by a version without the field have no
    # _enforce_multi_host attribute.
    state = resources_lib.Resources(accelerators='tpu-v6e-8').__getstate__()
    state = dict(state)
    state.pop('_enforce_multi_host', None)
    state['_version'] = 36
    r = resources_lib.Resources.__new__(resources_lib.Resources)
    r.__setstate__(state)
    assert r.enforce_multi_host is None


def _multi_host_handle(launched_nodes):
    handle = mock.MagicMock()
    handle.launched_nodes = launched_nodes
    handle.launched_resources = resources_lib.Resources(
        infra='k8s/ctx', accelerators='tpu-v6e-8', enforce_multi_host=True)
    return handle


@pytest.mark.parametrize('slices', [2, 3])
def test_resize_multi_host_counts_pods_per_slice(slices):
    # Two 2-VM slices are four cluster nodes. Resizing to two or three slices
    # is not a scale-down, so no worker is terminated.
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.run_on_head = mock.MagicMock()
    task = mock.MagicMock()
    task.num_nodes = slices
    with mock.patch.object(cloud_vm_ray_backend.provision_lib,
                           'terminate_instances') as terminate:
        backend._handle_resize_pre_provision(_multi_host_handle(4), task, 'c')
    backend.run_on_head.assert_not_called()
    terminate.assert_not_called()


@pytest.mark.parametrize(
    'kwargs,expected',
    [
        (dict(accelerators='tpu-v6e-16', infra='k8s'), 4),
        (dict(accelerators='tpu-v6e-8', infra='k8s'), 1),
        (dict(accelerators='tpu-v6e-16'), 1),  # GCP TPU VM slice: one node.
        (dict(accelerators='H100:8', infra='k8s'), 1),
        (dict(infra='k8s'), 1),
    ])
def test_tpu_vms_per_slice(kwargs, expected):
    assert kubernetes_utils.tpu_vms_per_slice(
        resources_lib.Resources(**kwargs)) == expected


def test_pod_fields_multi_host_slice():
    fields = kubernetes_utils.get_pod_fields(
        acc_label_key=_FMT.TPU_LABEL_KEY,
        acc_label_values=['tpu-v6e-slice'],
        avoid_label_keys=None,
        topology_label_key=_FMT.TPU_TOPOLOGY_LABEL_KEY,
        topology_label_value='2x4',
        spot_label_key=None,
        spot_label_value=None,
        enable_flex_start=False,
        tpu_chips_per_vm=4,
        same_node_pool_cluster='c1')
    spec = fields['spec']
    assert spec['nodeSelector'] == {
        _FMT.TPU_TOPOLOGY_LABEL_KEY: '2x4',
        _FMT.ACCELERATOR_COUNT_LABEL_KEY: '4',
    }
    term = spec['affinity']['podAffinity'][
        'requiredDuringSchedulingIgnoredDuringExecution'][0]
    assert term == {
        'labelSelector': {
            'matchLabels': {
                'skypilot-cluster-name': 'c1'
            }
        },
        'topologyKey': 'cloud.google.com/gke-nodepool',
    }
    # Node affinity for the accelerator type is still there.
    assert 'nodeAffinity' in spec['affinity']


def _node(labels, ready=True):
    node = mock.MagicMock()
    node.metadata.labels = labels
    node.is_ready.return_value = ready
    node.status.capacity = {'cpu': '180', 'memory': '700Gi'}
    node.status.allocatable = node.status.capacity
    return node


def _tpu_labels(topology, chips):
    return {
        _FMT.TPU_LABEL_KEY: 'tpu-v6e-slice',
        _FMT.TPU_TOPOLOGY_LABEL_KEY: topology,
        _FMT.ACCELERATOR_COUNT_LABEL_KEY: str(chips),
    }


def test_check_instance_fits_multi_host():
    shape = kubernetes_utils.get_tpu_multi_host_shape('tpu-v6e-8', 1, True)
    single_8 = _node(_tpu_labels('2x4', 8))
    multi_vm = _node(_tpu_labels('2x4', 4))
    # An 8-chip single-host VM is not a VM of a multi-host 2x4 slice.
    with mock.patch.object(kubernetes_utils,
                           'get_kubernetes_nodes',
                           return_value=[single_8]):
        fits, reason = kubernetes_utils.check_instance_fits(
            'ctx', '16CPU--64GB--tpu-v6e-8:1', tpu_multi_host_shape=shape)
    assert not fits and 'multi-host' in reason
    with mock.patch.object(kubernetes_utils,
                           'get_kubernetes_nodes',
                           return_value=[single_8, multi_vm]):
        fits, reason = kubernetes_utils.check_instance_fits(
            'ctx', '16CPU--64GB--tpu-v6e-8:1', tpu_multi_host_shape=shape)
    assert fits, reason


def _node_pool(topology, machine_type, multi_host=True):
    return {
        'name': 'pool',
        'config': {
            'machineType': machine_type,
            'resourceLabels': {
                'goog-gke-tpu-node-pool-type': 'multi-host'
                                               if multi_host else 'single-host',
                'goog-gke-accelerator-type': 'tpu-v6e-slice',
            },
        },
        'placementPolicy': {
            'tpuTopology': topology
        } if topology else {},
    }


def test_gke_autoscaler_node_pool_is_tpu_slice():
    gke = kubernetes_utils.GKEAutoscaler
    shape_8 = kubernetes_utils.get_tpu_multi_host_shape('tpu-v6e-8', 1, True)
    shape_16 = kubernetes_utils.get_tpu_multi_host_shape('tpu-v6e-16', 1, None)
    pool_2x4 = _node_pool('2x4', 'ct6e-standard-4t')
    assert gke._node_pool_is_tpu_slice(pool_2x4, shape_8)
    assert not gke._node_pool_is_tpu_slice(pool_2x4, shape_16)
    assert gke._node_pool_is_tpu_slice(_node_pool('4x4', 'ct6e-standard-4t'),
                                       shape_16)
    # A single-host 8-chip pool is not a 2x4 multi-host slice.
    assert not gke._node_pool_is_tpu_slice(
        _node_pool(None, 'ct6e-standard-8t', multi_host=False), shape_8)
    # The instance-type check routes multi-host requests to that check.
    assert gke._check_instance_fits_gke_autoscaler_node_pool(
        '16CPU--64GB--tpu-v6e-8:1', pool_2x4, shape_8)
    assert not gke._check_instance_fits_gke_autoscaler_node_pool(
        '16CPU--64GB--tpu-v6e-8:1', pool_2x4)


def _pod(name, node_name, ip, topology='2x4', chips='4'):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={'skypilot-cluster': 'c'}),
        spec=SimpleNamespace(node_name=node_name,
                             node_selector={
                                 _FMT.TPU_TOPOLOGY_LABEL_KEY: topology,
                                 _FMT.ACCELERATOR_COUNT_LABEL_KEY: chips,
                             }),
        status=SimpleNamespace(pod_ip=ip))


def test_tag_tpu_multi_host_workers_groups_by_node_pool():
    """Pods are grouped into slices by the node pool they run on, not by
    their index: the scheduler picks which pool each pod lands on."""
    pods = {
        'c-head': _pod('c-head', 'gke-tpu-b-2', '10.0.0.4'),
        'c-worker1': _pod('c-worker1', 'gke-tpu-a-1', '10.0.0.1'),
        'c-worker2': _pod('c-worker2', 'gke-tpu-b-1', '10.0.0.3'),
        'c-worker3': _pod('c-worker3', 'gke-tpu-a-2', '10.0.0.2'),
    }
    node_pools = {
        'gke-tpu-a-1': 'pool-a',
        'gke-tpu-a-2': 'pool-a',
        'gke-tpu-b-1': 'pool-b',
        'gke-tpu-b-2': 'pool-b',
    }
    infos = {
        name: [mock.MagicMock(tags=dict(p.metadata.labels))]
        for name, p in pods.items()
    }
    core_api = mock.MagicMock()
    core_api.read_node.side_effect = lambda name, **_: SimpleNamespace(
        metadata=SimpleNamespace(
            labels={'cloud.google.com/gke-nodepool': node_pools[name]}))
    with mock.patch.object(k8s_instance.kubernetes,
                           'core_api',
                           return_value=core_api):
        k8s_instance._tag_tpu_multi_host_workers('c', 'ctx', pods, infos)

    def env(name):
        return kubernetes_utils.tpu_worker_env_vars(infos[name][0].tags)

    assert env('c-worker1') == {
        'TPU_WORKER_ID': '0',
        'TPU_WORKER_HOSTNAMES': '10.0.0.1,10.0.0.2',
        'TPU_NAME': 'c-pool-a',
    }
    assert env('c-worker3')['TPU_WORKER_ID'] == '1'
    assert env('c-worker2') == {
        'TPU_WORKER_ID': '0',
        'TPU_WORKER_HOSTNAMES': '10.0.0.3,10.0.0.4',
        'TPU_NAME': 'c-pool-b',
    }
    assert env('c-head')['TPU_WORKER_ID'] == '1'
    # The pod's own labels are left untouched.
    assert kubernetes_utils.TPU_WORKER_ID_TAG not in pods[
        'c-head'].metadata.labels


def test_tag_tpu_multi_host_workers_skips_single_host_pods():
    pods = {'c-head': _pod('c-head', 'n1', '10.0.0.1', '2x4', '8')}
    infos = {'c-head': [mock.MagicMock(tags={})]}
    with mock.patch.object(k8s_instance.kubernetes, 'core_api') as core_api:
        k8s_instance._tag_tpu_multi_host_workers('c', 'ctx', pods, infos)
    core_api.assert_not_called()
    assert kubernetes_utils.tpu_worker_env_vars(infos['c-head'][0].tags) == {}


def test_ray_codegen_adds_per_ip_env_vars():
    codegen = task_codegen.RayCodeGen()
    codegen.add_prologue(job_id=1)
    codegen.add_setup(2, {'CPU': 1},
                      stable_cluster_internal_ips=['10.0.0.1', '10.0.0.2'],
                      env_vars={},
                      log_dir='/tmp/log')
    codegen.set_per_ip_env_vars({'10.0.0.2': {'TPU_WORKER_ID': '1'}})
    codegen.add_task(2,
                     bash_script='echo hi',
                     task_name='t',
                     resources_dict={'CPU': 1},
                     log_dir='/tmp/log')
    codegen.add_epilogue()
    code = codegen.build()
    assert ("sky_env_vars_dict.update({'10.0.0.2': {'TPU_WORKER_ID': '1'}}"
            ".get(ip, {}))") in code
