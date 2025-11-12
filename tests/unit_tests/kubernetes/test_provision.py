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
        1] == '└── Cluster does not have sufficient Memory for your request: Run \'kubectl get nodes -o custom-columns=NAME:.metadata.name,MEMORY:.status.allocatable.memory\' to check the available memory on the node.'

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

    monkeypatch.setattr(clouds, 'Kubernetes',
                        lambda *args, **kwargs: kubernetes_cloud)

    requested_resources_str = f'{requested_resources}'
    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, insufficient_resources) ==
        f'Failed to acquire resources (CPUs, Memory) in {zone} for {requested_resources_str}. '
    )

    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, None) ==
        f'Failed to acquire resources in {zone} for {requested_resources_str}. '
    )

    to_provision.zone = None
    assert (
        retrying_vm_prosioner._insufficient_resources_msg(
            to_provision, requested_resources, insufficient_resources) ==
        f'Failed to acquire resources (CPUs, Memory) in context {region} for {requested_resources_str}. '
    )


def test_pod_termination_reason_start_error(monkeypatch):
    """Test _get_pod_termination_reason with StartError (like busybox).

    Pod is in Failed state with container terminated due to StartError.
    """
    import datetime

    now = datetime.datetime(2025, 1, 1, 0, 0, 0)

    pod = mock.MagicMock()
    pod.metadata.name = 'test-pod'
    pod.status.start_time = now

    # Ready condition showing PodFailed
    ready_condition = mock.MagicMock()
    ready_condition.type = 'Ready'
    ready_condition.reason = 'PodFailed'
    ready_condition.message = ''
    ready_condition.last_transition_time = now

    pod.status.conditions = [ready_condition]

    # Container with StartError
    container_status = mock.MagicMock()
    container_status.name = 'ray-node'
    container_status.state.terminated = mock.MagicMock()
    container_status.state.terminated.exit_code = 128
    container_status.state.terminated.reason = 'StartError'
    container_status.state.terminated.finished_at = now

    pod.status.container_statuses = [container_status]

    monkeypatch.setattr('sky.provision.kubernetes.instance.global_user_state',
                        mock.MagicMock())

    reason = instance._get_pod_termination_reason(pod, 'test-cluster')

    expected = ('Terminated unexpectedly.\n'
                'Last known state: PodFailed.\n'
                'Container errors: StartError')
    assert reason == expected


def test_pod_termination_reason_kueue_preemption(monkeypatch):
    """Test _get_pod_termination_reason with Kueue preemption.

    Pod is being terminated by Kueue due to PodsReady timeout.
    Includes both the TerminationTarget condition (preemption) and
    Ready condition (container status), as seen in real API responses.
    """
    import datetime

    now = datetime.datetime(2025, 1, 1, 0, 0, 0)

    pod = mock.MagicMock()
    pod.metadata.name = 'test-pod'
    pod.status.start_time = now

    ready_condition = mock.MagicMock()
    ready_condition.type = 'Ready'
    ready_condition.reason = 'ContainersNotReady'
    ready_condition.message = 'containers with unready status: [ray-node]'
    ready_condition.last_transition_time = now

    # Taken from an actual Pod that got preempted by Kueue.
    termination_condition = mock.MagicMock()
    termination_condition.type = 'TerminationTarget'
    termination_condition.reason = 'WorkloadEvictedDueToPodsReadyTimeout'
    termination_condition.message = 'Exceeded the PodsReady timeout default/test-pod'
    termination_condition.last_transition_time = now

    pod.status.conditions = [ready_condition, termination_condition]

    # Container still creating (not terminated)
    container_status = mock.MagicMock()
    container_status.state.terminated = None
    pod.status.container_statuses = [container_status]

    monkeypatch.setattr('sky.provision.kubernetes.instance.global_user_state',
                        mock.MagicMock())

    reason = instance._get_pod_termination_reason(pod, 'test-cluster')

    expected = (
        'Preempted by Kueue: WorkloadEvictedDueToPodsReadyTimeout '
        '(Exceeded the PodsReady timeout default/test-pod).\n'
        'Last known state: ContainersNotReady (containers with unready status: [ray-node]).'
    )
    assert reason == expected


def test_list_namespaced_pod_success(monkeypatch):
    """Test that list_namespaced_pod returns pods from the API response."""
    mock_pod1 = mock.MagicMock()
    mock_pod1.metadata.name = 'test-pod-1'
    mock_pod1.status.phase = 'Running'

    mock_pod2 = mock.MagicMock()
    mock_pod2.metadata.name = 'test-pod-2'
    mock_pod2.status.phase = 'Pending'

    mock_response = mock.MagicMock()
    mock_response.items = [mock_pod1, mock_pod2]
    mock_response.api_version = 'v1'
    mock_response.kind = 'PodList'
    mock_response.metadata = mock.MagicMock()

    core_api_mock = mock.MagicMock()
    core_api_mock.list_namespaced_pod.return_value = mock_response

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    pods = instance.list_namespaced_pod(
        context='test-context',
        namespace='test-namespace',
        cluster_name_on_cloud='test-cluster',
        is_ssh=False,
        identity='Kubernetes cluster',
        label_selector='skypilot-cluster-name=test-cluster')

    assert len(pods) == 2
    assert pods[0].metadata.name == 'test-pod-1'
    assert pods[1].metadata.name == 'test-pod-2'


def test_query_instances_uses_correct_label_selector(monkeypatch):
    """Test that query_instances uses constants.TAG_SKYPILOT_CLUSTER_NAME."""
    from sky.provision import constants

    captured_label_selector = []

    def mock_list_namespaced_pod(context, namespace, cluster_name_on_cloud,
                                 is_ssh, identity, label_selector):
        captured_label_selector.append(label_selector)
        mock_pod = mock.MagicMock()
        mock_pod.metadata.name = 'test-pod'
        mock_pod.status.phase = 'Running'

        mock_response = mock.MagicMock()
        mock_response.items = [mock_pod]
        mock_response.api_version = 'v1'
        mock_response.kind = 'PodList'
        mock_response.metadata = mock.MagicMock()
        return [mock_pod]

    monkeypatch.setattr('sky.provision.kubernetes.instance.list_namespaced_pod',
                        mock_list_namespaced_pod)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_namespace_from_config',
        lambda *args: 'default')
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_context_from_config',
        lambda *args: 'test-context')

    instance.query_instances(cluster_name='test-cluster',
                             cluster_name_on_cloud='test-cluster-on-cloud',
                             provider_config={'namespace': 'default'},
                             retry_if_missing=False)

    # Verify the label selector was constructed correctly
    expected_label = f'{constants.TAG_SKYPILOT_CLUSTER_NAME}=test-cluster-on-cloud'
    assert captured_label_selector[0] == expected_label


def test_query_instances_retry_if_missing(monkeypatch):
    """Test that query_instances retries when retry_if_missing=True and pods are empty."""
    call_count = [0]

    def mock_list_namespaced_pod(*args, **kwargs):
        call_count[0] += 1
        # Return empty on first call, non-empty on second
        if call_count[0] == 1:
            return []
        else:
            mock_pod = mock.MagicMock()
            mock_pod.metadata.name = 'test-pod'
            mock_pod.status.phase = 'Running'
            return [mock_pod]

    monkeypatch.setattr('sky.provision.kubernetes.instance.list_namespaced_pod',
                        mock_list_namespaced_pod)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_namespace_from_config',
        lambda *args: 'default')
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_context_from_config',
        lambda *args: 'test-context')
    # Mock time.sleep to speed up test
    monkeypatch.setattr('time.sleep', lambda *args: None)

    result = instance.query_instances(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-on-cloud',
        provider_config={'namespace': 'default'},
        retry_if_missing=True)

    # Should have retried once
    assert call_count[0] == 2
    assert len(result) == 1


def test_query_instances_retry_exhausted(monkeypatch):
    """Test that query_instances stops after max retries and returns empty dict."""
    call_count = [0]

    def mock_list_namespaced_pod(*args, **kwargs):
        call_count[0] += 1
        # Always return empty to exhaust retries
        return []

    monkeypatch.setattr('sky.provision.kubernetes.instance.list_namespaced_pod',
                        mock_list_namespaced_pod)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_namespace_from_config',
        lambda *args: 'default')
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_context_from_config',
        lambda *args: 'test-context')
    # Mock time.sleep to speed up test
    monkeypatch.setattr('time.sleep', lambda *args: None)

    result = instance.query_instances(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-on-cloud',
        provider_config={'namespace': 'default'},
        retry_if_missing=True)

    # Should have called 1 (initial) + _MAX_QUERY_INSTANCES_RETRIES times
    assert call_count[0] == 1 + instance._MAX_QUERY_INSTANCES_RETRIES
    # Should return empty dict when no pods found
    assert result == {}
