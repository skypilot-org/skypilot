"""Tests for opt-in dedicated thin head (head_node_cpus / head_node_memory)."""

import copy
from unittest import mock

import pytest

from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky import task as task_lib
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.provision import instance_setup
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes import utils as kubernetes_utils


def test_head_fields_require_pair():
    with pytest.raises(ValueError, match='together'):
        resources_lib.Resources(cpus=4, memory=16, head_node_cpus=2)
    with pytest.raises(ValueError, match='together'):
        resources_lib.Resources(cpus=4, memory=16, head_node_memory=4)


def test_separate_head_launch_count_and_yaml_roundtrip():
    r = resources_lib.Resources(
        cpus=8,
        memory=32,
        head_node_cpus=2,
        head_node_memory=4,
        infra='k8s',
    )
    assert r.has_separate_head()
    assert r.launch_node_count(10) == 11
    assert r.head_node_cpus == '2'
    cfg = r.to_yaml_config()
    assert cfg['head_node_cpus'] == '2'
    assert cfg['head_node_memory'] == '4'
    assert cfg['cpus'] == '8'
    restored = list(resources_lib.Resources.from_yaml_config(cfg))[0]
    assert restored.has_separate_head()
    assert restored.launch_node_count(3) == 4


def test_default_homogeneous_unchanged():
    r = resources_lib.Resources(cpus=8, memory=32, infra='k8s')
    assert not r.has_separate_head()
    assert r.launch_node_count(10) == 10
    cfg = r.to_yaml_config()
    assert 'head_node_cpus' not in cfg


def test_apply_head_resources_to_pod_spec():
    pod = {
        'spec': {
            'containers': [{
                'resources': {
                    'requests': {
                        'cpu': '8',
                        'memory': '32G',
                    },
                    'limits': {
                        'cpu': '8',
                        'memory': '32G',
                    },
                }
            }]
        }
    }
    worker = copy.deepcopy(pod)
    k8s_instance._apply_head_resources_to_pod_spec(pod, {
        'cpus': '2',
        'memory': '4',
        'cpu_limit': '2',
        'memory_limit': '4',
    })
    assert pod['spec']['containers'][0]['resources']['requests']['cpu'] == '2'
    assert pod['spec']['containers'][0]['resources']['requests'][
        'memory'] == '4G'
    assert pod['spec']['containers'][0]['resources']['limits']['cpu'] == '2'
    assert pod['spec']['containers'][0]['resources']['limits']['memory'] == '4G'
    # Worker clone untouched
    assert worker['spec']['containers'][0]['resources']['requests'][
        'cpu'] == '8'


def test_apply_head_resources_strips_accelerators_and_sidecars():
    """Thin head must not inherit worker GPU/TPU asks or GPUDirect sidecars."""
    pod = {
        'metadata': {
            'annotations': {
                'k8s.v1.cni.cncf.io/networks': 'rdma-net',
                'devices.gke.io/container.tcpx-daemon': '- path: /dev/nvidia0',
                'skypilot-user': 'alice',
            }
        },
        'spec': {
            'runtimeClassName': 'nvidia',
            'nodeSelector': {
                'cloud.google.com/gke-accelerator': 'nvidia-l4',
                'cloud.google.com/gke-flex-start': 'true',
                'topology.kubernetes.io/zone': 'us-central1-a',
            },
            'affinity': {
                'nodeAffinity': {
                    'requiredDuringSchedulingIgnoredDuringExecution': {
                        'nodeSelectorTerms': [{
                            'matchExpressions': [{
                                'key': 'cloud.google.com/gke-accelerator',
                                'operator': 'In',
                                'values': ['nvidia-l4'],
                            }]
                        }]
                    },
                    'preferredDuringSchedulingIgnoredDuringExecution': [{
                        'weight': 1,
                        'preference': {
                            'matchExpressions': [{
                                'key': 'skypilot-binpack',
                                'operator': 'In',
                                'values': ['gpu'],
                            }]
                        },
                    }],
                }
            },
            'containers': [
                {
                    'name': 'ray-node',
                    'resources': {
                        'requests': {
                            'cpu': '8',
                            'memory': '32G',
                            'nvidia.com/gpu': '1',
                            'nvidia.com/rdma_ib': '1',
                        },
                        'limits': {
                            'cpu': '8',
                            'memory': '32G',
                            'nvidia.com/gpu': '1',
                            'nvidia.com/rdma_ib': '1',
                        },
                    },
                },
                {
                    'name': 'tcpx-daemon',
                    'resources': {
                        'requests': {
                            'cpu': '1',
                            'nvidia.com/gpu': '1'
                        },
                    },
                },
            ],
        },
    }
    worker = copy.deepcopy(pod)
    k8s_instance._apply_head_resources_to_pod_spec(pod, {
        'cpus': '2',
        'memory': '4',
    })
    head_res = pod['spec']['containers'][0]['resources']
    assert head_res['requests'] == {'cpu': '2', 'memory': '4G'}
    assert head_res['limits'] == {'cpu': '2', 'memory': '4G'}
    assert len(pod['spec']['containers']) == 1
    assert 'runtimeClassName' not in pod['spec']
    assert pod['spec']['nodeSelector'] == {
        'topology.kubernetes.io/zone': 'us-central1-a',
    }
    node_aff = pod['spec']['affinity']['nodeAffinity']
    assert 'requiredDuringSchedulingIgnoredDuringExecution' not in node_aff
    assert 'preferredDuringSchedulingIgnoredDuringExecution' in node_aff
    assert pod['metadata']['annotations'] == {'skypilot-user': 'alice'}
    # Worker clone untouched (still has GPU + sidecar).
    assert worker['spec']['containers'][0]['resources']['requests'][
        'nvidia.com/gpu'] == '1'
    assert len(worker['spec']['containers']) == 2


def test_apply_head_resources_defaults_limits_to_requests():
    """Limits match requests when cpu_limit/memory_limit are omitted."""
    pod = {
        'spec': {
            'containers': [{
                'resources': {
                    'requests': {
                        'cpu': '8',
                        'memory': '32G'
                    },
                    'limits': {
                        'cpu': '8',
                        'memory': '32G'
                    },
                }
            }]
        }
    }
    k8s_instance._apply_head_resources_to_pod_spec(pod, {
        'cpus': '0.5',
        'memory': '16'
    })
    res = pod['spec']['containers'][0]['resources']
    assert res['requests'] == {'cpu': '0.5', 'memory': '16G'}
    assert res['limits'] == {'cpu': '0.5', 'memory': '16G'}


def test_separate_head_implies_ray_num_cpus_zero_contract():
    """Dedicated head: pod sized by head_node_*; Ray advertises num-cpus=0.

    Provision paths in clouds/kubernetes.py and
    provision/kubernetes/instance.py set custom_ray_options['num-cpus'] to
    THIN_HEAD_RAY_NUM_CPUS ('0') when head_resources / has_separate_head()
    is set. Setup places via node:<ip>, not a CPU bundle. This test locks
    the Resources API side of that contract.
    """
    r = resources_lib.Resources(
        cpus=8,
        memory=32,
        head_node_cpus=20,
        head_node_memory=16,
        infra='k8s',
    )
    assert r.has_separate_head()
    assert float(str(r.head_node_cpus).rstrip('+')) == 20.0
    # Worker shape unchanged; Ray exclusion is via num-cpus=0, not by
    # zeroing the pod request.
    assert r.cpus == '8'
    assert k8s_constants.THIN_HEAD_RAY_NUM_CPUS == '0'


def test_thin_head_ray_start_omits_accelerator_resources():
    """Head ray start must not advertise worker GPUs via --resources."""
    with_gpu = instance_setup.ray_head_start_command('{"GPU":1}', {
        'num-cpus': '0',
        'object-store-memory': 500000000
    })
    assert '--resources=' in with_gpu

    thin = instance_setup.ray_head_start_command(None, {
        'num-cpus': '0',
        'object-store-memory': 500000000
    })
    assert '--resources=' not in thin
    assert '--num-cpus=0' in thin


def test_num_task_nodes_excludes_thin_head():
    """Physical pods include the head; task slots are workers only."""
    r = resources_lib.Resources(
        cpus=8,
        memory=32,
        head_node_cpus=2,
        head_node_memory=4,
        infra='k8s',
    )
    assert kubernetes_utils.num_task_nodes(11, r) == 10
    assert kubernetes_utils.num_task_nodes(3, r) == 2


def test_check_resources_fit_cluster_rejects_extra_thin_head_slot():
    """sky exec asking for physical pod count must fail on thin head."""
    launched = resources_lib.Resources(
        cpus=8,
        memory=32,
        head_node_cpus=2,
        head_node_memory=4,
        infra='k8s/ctx',
    )
    handle = CloudVmRayResourceHandle.__new__(CloudVmRayResourceHandle)
    handle.cluster_name = 'thin-head-cluster'
    handle.launched_nodes = 3  # 2 workers + head
    handle.launched_resources = launched

    task = task_lib.Task(num_nodes=3)
    task.set_resources(launched)

    backend = CloudVmRayBackend()
    with mock.patch('sky.usage.usage_lib.messages'), \
         mock.patch('sky.global_user_state.get_status_from_cluster_name',
                    return_value=None), \
         pytest.raises(exceptions.ResourcesMismatchError, match='Requested'):
        backend.check_resources_fit_cluster(handle, task)

    # Exact worker count still fits.
    task_ok = task_lib.Task(num_nodes=2)
    task_ok.set_resources(launched)
    with mock.patch('sky.usage.usage_lib.messages'), \
         mock.patch('sky.global_user_state.get_status_from_cluster_name',
                    return_value=None):
        assert backend.check_resources_fit_cluster(handle, task_ok) is not None


def test_copy_preserves_head_fields():
    r = resources_lib.Resources(
        cpus=8,
        memory=32,
        head_node_cpus=1,
        head_node_memory=2,
        infra='kubernetes',
    )
    copied = r.copy(cpus=16)
    assert copied.cpus == '16'
    assert copied.has_separate_head()
    assert copied.head_node_cpus == '1'
    assert isinstance(copied.cloud, clouds.Kubernetes)
