"""Tests for Kubernetes provision."""

import re
from unittest import mock

import pytest

from sky import clouds
from sky import resources
from sky.backends import cloud_vm_ray_backend
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import instance
from sky.provision.kubernetes.instance import logger


def _remove_colorama_escape_codes(error_output):
    return [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in error_output]


def test_out_of_cpus(monkeypatch):
    """Test to check if the error message is correct when there is only CPU resource shortage."""

    error_message = '0/7 nodes are available: 3 Insufficient cpu, 4 node(s) didn\'t match Pod\'s node affinity/selector. preemption: 0/7 nodes are available: 3 No preemption victims found for incoming pod, 4 Preemption is not helpful for scheduling.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'
    new_node.status.conditions = [mock.MagicMock()]
    new_node.status.conditions[0].type = 'Ready'
    new_node.status.conditions[0].status = 'False'
    new_node.status.conditions[0].reason = 'InsufficientCPU'
    new_node.status.conditions[0].message = error_message

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = None

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock.list_namespaced_event.return_value = events_mock

    error_output = []

    def mock_warning(msg, *args, **kwargs):
        if args:
            msg = msg % args
        error_output.append(msg)

    monkeypatch.setattr(logger, 'error', mock_warning)

    with pytest.raises(config_lib.KubernetesError) as e:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_output = error_output[0].split('\n')
    error_output = _remove_colorama_escape_codes(error_output)

    assert error_output[0] == '⨯ Insufficient resource capacity on the cluster:'
    assert error_output[
        1] == '└── Cluster does not have sufficient CPUs for your request: Run \'kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu\' to check the available CPUs on the node.'

    assert len(error_output) == 2


def test_out_of_gpus(monkeypatch):
    """Test to check if the error message is correct when there is only GPU resource shortage."""

    error_message = '0/7 nodes are available: 3 Insufficient nvidia.com/gpu, 4 node(s) didn\'t match Pod\'s node affinity/selector. preemption: 0/7 nodes are available: 3 No preemption victims found for incoming pod, 4 Preemption is not helpful for scheduling.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'
    new_node.status.conditions = [mock.MagicMock()]
    new_node.status.conditions[0].type = 'Ready'
    new_node.status.conditions[0].status = 'False'
    new_node.status.conditions[0].reason = 'InsufficientCPU'
    new_node.status.conditions[0].message = error_message

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = None

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock.list_namespaced_event.return_value = events_mock

    error_output = []

    def mock_warning(msg, *args, **kwargs):
        if args:
            msg = msg % args
        error_output.append(msg)

    monkeypatch.setattr(logger, 'error', mock_warning)

    with pytest.raises(config_lib.KubernetesError) as e:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_output = error_output[0].split('\n')
    # Remove any colorama escape codes
    error_output = _remove_colorama_escape_codes(error_output)

    assert error_output[0] == '⨯ Insufficient resource capacity on the cluster:'
    assert error_output[
        1] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky show-gpus --infra kubernetes\' to see the available GPUs.'

    assert len(error_output) == 2


def test_out_of_both_cpus_and_gpus(monkeypatch):
    error_message = '0/7 nodes are available: 3 Insufficient cpu, 3 Insufficient nvidia.com/gpu, 4 node(s) didn\'t match Pod\'s node affinity/selector. preemption: 0/7 nodes are available: 3 No preemption victims found for incoming pod, 4 Preemption is not helpful for scheduling.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'
    new_node.status.conditions = [mock.MagicMock()]
    new_node.status.conditions[0].type = 'Ready'
    new_node.status.conditions[0].status = 'False'
    new_node.status.conditions[0].reason = 'InsufficientCPU'
    new_node.status.conditions[0].message = error_message

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = None

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock.list_namespaced_event.return_value = events_mock

    error_output = []

    def mock_warning(msg, *args, **kwargs):
        if args:
            msg = msg % args
        error_output.append(msg)

    monkeypatch.setattr(logger, 'error', mock_warning)

    with pytest.raises(config_lib.KubernetesError) as e:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_output = error_output[0].split('\n')
    # Remove any colorama escape codes
    error_output = _remove_colorama_escape_codes(error_output)

    assert error_output[0] == '⨯ Insufficient resource capacity on the cluster:'
    assert error_output[
        1] == '├── Cluster does not have sufficient CPUs for your request: Run \'kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu\' to check the available CPUs on the node.'
    assert error_output[
        2] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky show-gpus --infra kubernetes\' to see the available GPUs.'

    assert len(error_output) == 3


def test_out_of_gpus_and_node_selector_failed(monkeypatch):
    """Test to check if the error message is correct when there is GPU resource
    
    shortage and node selector failed to match.
    """

    error_message = '0/7 nodes are available: 3 Insufficient nvidia.com/gpu, 4 node(s) didn\'t match Pod\'s node affinity/selector. preemption: 0/7 nodes are available: 3 No preemption victims found for incoming pod, 4 Preemption is not helpful for scheduling.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'
    new_node.status.conditions = [mock.MagicMock()]
    new_node.status.conditions[0].type = 'Ready'
    new_node.status.conditions[0].status = 'False'
    new_node.status.conditions[0].reason = 'InsufficientCPU'
    new_node.status.conditions[0].message = error_message

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = {
        'cloud.google.com/gke-accelerator': 'nvidia-tesla-a100'
    }

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock.list_namespaced_event.return_value = events_mock

    error_output = []

    def mock_warning(msg, *args, **kwargs):
        if args:
            msg = msg % args
        error_output.append(msg)

    monkeypatch.setattr(logger, 'error', mock_warning)

    with pytest.raises(config_lib.KubernetesError) as e:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_output = error_output[0].split('\n')
    # Remove any colorama escape codes
    error_output = _remove_colorama_escape_codes(error_output)

    assert error_output[0] == '⨯ Insufficient resource capacity on the cluster:'
    assert error_output[
        1] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky show-gpus --infra kubernetes\' to see the available GPUs. Verify if any node matching label nvidia-tesla-a100 and sufficient resource nvidia.com/gpu is available in the cluster.'

    assert len(error_output) == 2


def test_out_of_memory(monkeypatch):
    """Test to check if the error message is correct when there is only CPU resource shortage."""

    error_message = '0/7 nodes are available: 3 Insufficient memory, 4 node(s) didn\'t match Pod\'s node affinity/selector. preemption: 0/7 nodes are available: 3 No preemption victims found for incoming pod, 4 Preemption is not helpful for scheduling.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'
    new_node.status.conditions = [mock.MagicMock()]
    new_node.status.conditions[0].type = 'Ready'
    new_node.status.conditions[0].status = 'False'
    new_node.status.conditions[0].reason = 'InsufficientCPU'
    new_node.status.conditions[0].message = error_message

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = None

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock.list_namespaced_event.return_value = events_mock

    error_output = []

    def mock_warning(msg, *args, **kwargs):
        if args:
            msg = msg % args
        error_output.append(msg)

    monkeypatch.setattr(logger, 'error', mock_warning)

    with pytest.raises(config_lib.KubernetesError) as e:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_output = error_output[0].split('\n')
    error_output = _remove_colorama_escape_codes(error_output)

    assert error_output[0] == '⨯ Insufficient resource capacity on the cluster:'
    assert error_output[
        1] == '└── Cluster does not have sufficient Memory for your request'

    assert len(error_output) == 2


def test_insufficient_resources_msg(monkeypatch):
    """Test to check if the error message is correct when there is only CPU resource shortage."""

    monkeypatch.setattr(
        cloud_vm_ray_backend.RetryingVmProvisioner,
        '__init__',
        lambda *args, **kwargs: None,
    )

    retrying_vm_prosioner = cloud_vm_ray_backend.RetryingVmProvisioner(
        log_dir=None,
        dag=None,
        optimize_target=None,
        requested_features=None,
        local_wheel_path=None,
        wheel_hash=None,
    )

    zone = "Test Zone"
    region = "Test Region"
    cloud = "Test Cloud"
    requested_resources = mock.MagicMock()
    insufficient_resources = mock.MagicMock()

    to_provision = mock.MagicMock()
    to_provision.zone = zone
    to_provision.region = region
    to_provision.cloud = cloud

    num_cpus = 10
    num_memory = 10
    requested_resources = resources.Resources(cpus=num_cpus, memory=num_memory)
    insufficient_resources = ["CPUs", "Memory"]

    ssh_cloud = mock.MagicMock()
    ssh_cloud.is_same_cloud = mock.MagicMock()
    ssh_cloud.is_same_cloud.return_value = False

    monkeypatch.setattr(clouds, 'SSH', lambda *args, **kwargs: ssh_cloud)

    kubernetes_cloud = mock.MagicMock()
    kubernetes_cloud.is_same_cloud = mock.MagicMock()
    kubernetes_cloud.is_same_cloud.return_value = True

    monkeypatch.setattr(
        clouds, 'Kubernetes', lambda *args, **kwargs: kubernetes_cloud
    )

    requested_resources_str = f'{requested_resources}'
    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, insufficient_resources
        )
        == f'Failed to acquire resources (CPUs, Memory) in {zone} for {requested_resources_str}. '
    )

    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, None
        )
        == f'Failed to acquire resources in {zone} for {requested_resources_str}. '
    )

    to_provision.zone = None
    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, insufficient_resources
        )
        == f'Failed to acquire resources (CPUs, Memory) in context {region} for {requested_resources_str}. '
    )
