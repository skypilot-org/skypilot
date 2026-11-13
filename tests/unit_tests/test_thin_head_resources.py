"""Tests for opt-in dedicated thin head (head_node_cpus / head_node_memory)."""

import copy

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import instance as k8s_instance


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
    k8s_instance._apply_head_resources_to_pod_spec(
        pod, {
            'cpus': '2',
            'memory': '4',
            'cpu_limit': '2',
            'memory_limit': '4',
        })
    assert pod['spec']['containers'][0]['resources']['requests']['cpu'] == '2'
    assert pod['spec']['containers'][0]['resources']['requests'][
        'memory'] == '4G'
    assert pod['spec']['containers'][0]['resources']['limits']['cpu'] == '2'
    assert pod['spec']['containers'][0]['resources']['limits'][
        'memory'] == '4G'
    # Worker clone untouched
    assert worker['spec']['containers'][0]['resources']['requests']['cpu'] == '8'


def test_apply_head_resources_defaults_limits_to_requests():
    """Limits match requests when cpu_limit/memory_limit are omitted."""
    pod = {
        'spec': {
            'containers': [{
                'resources': {
                    'requests': {'cpu': '8', 'memory': '32G'},
                    'limits': {'cpu': '8', 'memory': '32G'},
                }
            }]
        }
    }
    k8s_instance._apply_head_resources_to_pod_spec(
        pod, {'cpus': '0.5', 'memory': '16'})
    res = pod['spec']['containers'][0]['resources']
    assert res['requests'] == {'cpu': '0.5', 'memory': '16G'}
    assert res['limits'] == {'cpu': '0.5', 'memory': '16G'}


def test_separate_head_implies_ray_setup_token_cpu_contract():
    """Dedicated head: pod sized by head_node_*; Ray advertises setup-token CPU.

    Provision paths in clouds/kubernetes.py and
    provision/kubernetes/instance.py set custom_ray_options['num-cpus'] to
    THIN_HEAD_RAY_NUM_CPUS (0.0001, matching task_codegen._SETUP_CPUS) when
    head_resources / has_separate_head() is set. This test locks the
    Resources API side of that contract.
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
    # Worker shape unchanged; Ray exclusion is via setup-token num-cpus, not
    # by zeroing the pod request.
    assert r.cpus == '8'
    assert float(k8s_constants.THIN_HEAD_RAY_NUM_CPUS) == 0.0001


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
