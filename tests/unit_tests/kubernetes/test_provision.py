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
        1] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky gpus list --infra kubernetes\' to see the available GPUs.'

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
        2] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky gpus list --infra kubernetes\' to see the available GPUs.'

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
        1] == '└── Cluster does not have sufficient GPUs for your request: Run \'sky gpus list --infra kubernetes\' to see the available GPUs. Verify if any node matching label nvidia-tesla-a100 and sufficient resource nvidia.com/gpu is available in the cluster.'

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


def test_pod_termination_reason_null_finished_at(monkeypatch):
    """Test _get_pod_termination_reason with null finished_at timestamp.

    When pods are in certain failed states (e.g., Unknown status due to
    ephemeral storage issues), terminated.finished_at can be None.
    This should not cause a TypeError.

    Regression test for SKY-4423.
    """
    import datetime

    now = datetime.datetime(2025, 1, 1, 0, 0, 0)

    pod = mock.MagicMock()
    pod.metadata.name = 'test-pod'
    pod.status.start_time = now

    # Ready condition
    ready_condition = mock.MagicMock()
    ready_condition.type = 'Ready'
    ready_condition.reason = 'PodFailed'
    ready_condition.message = ''
    ready_condition.last_transition_time = now

    pod.status.conditions = [ready_condition]

    # Container with terminated state but null finished_at
    container_status = mock.MagicMock()
    container_status.name = 'ray-node'
    container_status.state.terminated = mock.MagicMock()
    container_status.state.terminated.exit_code = 137
    container_status.state.terminated.reason = 'Unknown'
    container_status.state.terminated.finished_at = None

    pod.status.container_statuses = [container_status]

    monkeypatch.setattr('sky.provision.kubernetes.instance.global_user_state',
                        mock.MagicMock())

    # Should not raise TypeError
    reason = instance._get_pod_termination_reason(pod, 'test-cluster')

    expected = ('Terminated unexpectedly.\n'
                'Last known state: PodFailed.\n'
                'Container errors: Unknown')
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


def test_get_pvc_binding_status_no_volumes(monkeypatch):
    """Test _get_pvc_binding_status with no volumes."""
    pod = mock.MagicMock()
    pod.spec.volumes = None

    result = instance._get_pvc_binding_status('test-namespace', 'test-context',
                                              pod)
    assert result is None


def test_get_pvc_binding_status_no_pvc(monkeypatch):
    """Test _get_pvc_binding_status with volumes but no PVC."""
    pod = mock.MagicMock()
    volume = mock.MagicMock()
    volume.persistent_volume_claim = None
    pod.spec.volumes = [volume]

    result = instance._get_pvc_binding_status('test-namespace', 'test-context',
                                              pod)
    assert result is None


def test_get_pvc_binding_status_bound_pvc(monkeypatch):
    """Test _get_pvc_binding_status with a bound PVC."""
    pod = mock.MagicMock()
    pvc_claim = mock.MagicMock()
    pvc_claim.claim_name = 'test-pvc'
    volume = mock.MagicMock()
    volume.persistent_volume_claim = pvc_claim
    pod.spec.volumes = [volume]

    # Mock the PVC as bound
    pvc = mock.MagicMock()
    pvc.status.phase = 'Bound'

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_persistent_volume_claim.return_value = pvc

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    result = instance._get_pvc_binding_status('test-namespace', 'test-context',
                                              pod)
    assert result is None


def test_get_pvc_binding_status_pending_pvc(monkeypatch):
    """Test _get_pvc_binding_status with a pending PVC."""
    pod = mock.MagicMock()
    pvc_claim = mock.MagicMock()
    pvc_claim.claim_name = 'test-pvc'
    volume = mock.MagicMock()
    volume.persistent_volume_claim = pvc_claim
    pod.spec.volumes = [volume]

    # Mock the PVC as pending
    pvc = mock.MagicMock()
    pvc.status.phase = 'Pending'

    # Mock the events for the PVC
    pvc_event = mock.MagicMock()
    pvc_event.type = 'Warning'
    pvc_event.reason = 'ProvisioningFailed'
    pvc_event.message = 'storageclass does not support ReadWriteMany'
    pvc_events = mock.MagicMock()
    pvc_events.items = [pvc_event]

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_persistent_volume_claim.return_value = pvc
    core_api_mock.list_namespaced_event.return_value = pvc_events

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    result = instance._get_pvc_binding_status('test-namespace', 'test-context',
                                              pod)
    assert result is not None
    assert 'test-pvc' in result
    assert 'Pending' in result
    assert 'ProvisioningFailed' in result
    assert 'storageclass does not support ReadWriteMany' in result
    assert 'kubectl describe pvc' in result
    assert 'test-namespace' in result


def test_raise_pod_scheduling_errors_pvc_unbound(monkeypatch):
    """Test that _raise_pod_scheduling_errors surfaces PVC binding issues."""
    error_message = '0/3 nodes are available: 3 pod has unbound immediate PersistentVolumeClaims.'

    namespace = 'test-namespace'
    context = 'test-context'

    new_node = mock.MagicMock()
    new_node.metadata = mock.MagicMock()
    new_node.metadata.name = 'test-node'
    new_node.status = mock.MagicMock()
    new_node.status.phase = 'Pending'

    # Mock the pod with a PVC
    pvc_claim = mock.MagicMock()
    pvc_claim.claim_name = 'test-pvc'
    volume = mock.MagicMock()
    volume.persistent_volume_claim = pvc_claim

    read_namespaced_pod_mock = mock.MagicMock()
    read_namespaced_pod_mock.status.phase = 'Pending'
    read_namespaced_pod_mock.spec.node_selector = None
    read_namespaced_pod_mock.spec.volumes = [volume]

    # Mock the PVC as pending
    pvc = mock.MagicMock()
    pvc.status.phase = 'Pending'

    # Mock the events for the PVC
    pvc_event = mock.MagicMock()
    pvc_event.type = 'Warning'
    pvc_event.reason = 'ProvisioningFailed'
    pvc_event.message = 'storageclass does not support ReadWriteMany'
    pvc_events = mock.MagicMock()
    pvc_events.items = [pvc_event]

    # Mock the pod scheduling event
    test_event = mock.MagicMock()
    test_event.metadata = mock.MagicMock()
    test_event.metadata.creation_timestamp = '2021-01-01T00:00:00Z'
    test_event.reason = 'FailedScheduling'
    test_event.message = error_message

    events_mock = mock.MagicMock()
    events_mock.items = [test_event]

    core_api_mock = mock.MagicMock()
    core_api_mock.read_namespaced_pod.return_value = read_namespaced_pod_mock
    core_api_mock.read_namespaced_persistent_volume_claim.return_value = pvc
    core_api_mock.list_namespaced_event.side_effect = [events_mock, pvc_events]

    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: core_api_mock)

    with pytest.raises(config_lib.KubernetesError) as exc_info:
        instance._raise_pod_scheduling_errors(namespace, context, [new_node])

    error_str = str(exc_info.value)
    # Verify that PVC binding issue is mentioned in the error
    assert 'PVC binding issue' in error_str or 'unbound' in error_str
    assert 'test-pvc' in error_str or 'PersistentVolumeClaims' in error_str


# ---------- RBAC 409 Conflict Handling Tests ----------


class FakeApiException(Exception):
    """A real exception that mimics kubernetes.client.rest.ApiException."""

    def __init__(self, status, reason='', body=''):
        super().__init__(status, reason, body)
        self.status = status
        self.reason = reason
        self.body = body


def _make_api_exception(status, reason='', body=''):
    """Create a fake Kubernetes ApiException with the given status code."""
    return FakeApiException(status, reason, body)


def _make_provider_config_for_rbac():
    """Return a minimal provider_config with all RBAC fields populated."""
    return {
        'autoscaler_service_account': {
            'metadata': {
                'name': 'skypilot-service-account',
                'namespace': 'default',
            },
        },
        'autoscaler_role': {
            'metadata': {
                'name': 'skypilot-service-account-role',
                'namespace': 'default',
            },
            'rules': [{
                'apiGroups': [''],
                'resources': ['pods'],
                'verbs': ['get', 'list'],
            }],
        },
        'autoscaler_role_binding': {
            'metadata': {
                'name': 'skypilot-service-account-role-binding',
                'namespace': 'default',
            },
            'roleRef': {
                'apiGroup': 'rbac.authorization.k8s.io',
                'kind': 'Role',
                'name': 'skypilot-service-account-role',
            },
            'subjects': [{
                'kind': 'ServiceAccount',
                'name': 'skypilot-service-account',
                'namespace': 'default',
            }],
        },
        'autoscaler_cluster_role': {
            'metadata': {
                'name': 'skypilot-service-account-cluster-role',
                'namespace': 'default',
            },
            'rules': [{
                'apiGroups': [''],
                'resources': ['nodes'],
                'verbs': ['get', 'list'],
            }],
        },
        'autoscaler_cluster_role_binding': {
            'metadata': {
                'name': 'skypilot-service-account-cluster-role-binding',
                'namespace': 'default',
            },
            'roleRef': {
                'apiGroup': 'rbac.authorization.k8s.io',
                'kind': 'ClusterRole',
                'name': 'skypilot-service-account-cluster-role',
            },
            'subjects': [{
                'kind': 'ServiceAccount',
                'name': 'skypilot-service-account',
                'namespace': 'default',
            }],
        },
    }


class TestRbac409ConflictHandling:
    """Tests that RBAC resource creation handles 409 Conflict gracefully.

    When two concurrent cluster launches both find RBAC resources missing
    and try to create them, the second one gets a 409 Conflict. The fix
    catches this, re-reads the resource, and falls through to compare/patch
    (upsert semantics).
    """

    @pytest.fixture(autouse=True)
    def mock_api_client(self, monkeypatch):
        """Mock api_client so dict_to_k8s_object works without kubeconfig."""
        import kubernetes as k8s_lib
        bare_client = k8s_lib.client.ApiClient(k8s_lib.client.Configuration())
        monkeypatch.setattr('sky.adaptors.kubernetes.api_client',
                            lambda *args, **kwargs: bare_client)

    @staticmethod
    def _make_existing_role(rules):
        """Create a mock existing role with the given rules."""
        existing = mock.MagicMock()
        existing.rules = rules
        return existing

    @staticmethod
    def _make_existing_binding(role_ref, subjects):
        """Create a mock existing binding with the given role_ref/subjects."""
        existing = mock.MagicMock()
        existing.role_ref = role_ref
        existing.subjects = subjects
        return existing

    def test_service_account_409_handled(self, monkeypatch):
        """Test 409 on create_namespaced_service_account re-reads and succeeds.
        """
        api_exc = _make_api_exception(409, 'Conflict')
        existing_sa = mock.MagicMock()

        core_api_mock = mock.MagicMock()
        # First list returns empty -> "not found", second list (after 409)
        # returns the concurrently-created resource.
        core_api_mock.list_namespaced_service_account.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_sa]),
        ]
        core_api_mock.create_namespaced_service_account.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                            lambda *args, **kwargs: core_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        provider_config = _make_provider_config_for_rbac()
        # Should not raise
        config_lib._configure_autoscaler_service_account(
            'default', None, provider_config)

    def test_service_account_other_error_raised(self, monkeypatch):
        """Test that non-409 errors are still raised."""
        api_exc = _make_api_exception(500, 'Internal Server Error')

        core_api_mock = mock.MagicMock()
        core_api_mock.list_namespaced_service_account.return_value = (
            mock.MagicMock(items=[]))
        core_api_mock.create_namespaced_service_account.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                            lambda *args, **kwargs: core_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: FakeApiException)

        provider_config = _make_provider_config_for_rbac()
        with pytest.raises(FakeApiException):
            config_lib._configure_autoscaler_service_account(
                'default', None, provider_config)

    def test_role_409_handled(self, monkeypatch):
        """Test 409 on create_namespaced_role re-reads and succeeds."""
        from sky.provision.kubernetes import utils as kubernetes_utils

        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        # Build the expected k8s role object so we can match its rules.
        new_role = kubernetes_utils.dict_to_k8s_object(
            provider_config['autoscaler_role'], 'V1Role')
        existing_role = self._make_existing_role(new_role.rules)

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_namespaced_role.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_role]),
        ]
        auth_api_mock.create_namespaced_role.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_role('default', None, provider_config,
                                              'autoscaler_role')
        # Rules match, so patch should NOT be called.
        auth_api_mock.patch_namespaced_role.assert_not_called()

    def test_role_409_then_patch(self, monkeypatch):
        """Test 409 on create_namespaced_role re-reads and patches when rules
        differ."""
        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        # Existing role has different rules than what we want.
        existing_role = self._make_existing_role(rules=['stale-rules'])

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_namespaced_role.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_role]),
        ]
        auth_api_mock.create_namespaced_role.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_role('default', None, provider_config,
                                              'autoscaler_role')
        # Rules differ, so patch SHOULD be called.
        auth_api_mock.patch_namespaced_role.assert_called_once()

    def test_role_binding_409_handled(self, monkeypatch):
        """Test 409 on create_namespaced_role_binding re-reads and succeeds."""
        from sky.provision.kubernetes import utils as kubernetes_utils

        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        new_rb = kubernetes_utils.dict_to_k8s_object(
            provider_config['autoscaler_role_binding'], 'V1RoleBinding')
        existing_rb = self._make_existing_binding(new_rb.role_ref,
                                                  new_rb.subjects)

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_namespaced_role_binding.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_rb]),
        ]
        auth_api_mock.create_namespaced_role_binding.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_role_binding(
            'default', None, provider_config, 'autoscaler_role_binding')
        auth_api_mock.patch_namespaced_role_binding.assert_not_called()

    def test_role_binding_409_then_patch(self, monkeypatch):
        """Test 409 on create_namespaced_role_binding re-reads and patches when
        binding differs."""
        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        # Existing binding has different subjects.
        existing_rb = self._make_existing_binding(role_ref='stale-role-ref',
                                                  subjects=['stale-subject'])

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_namespaced_role_binding.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_rb]),
        ]
        auth_api_mock.create_namespaced_role_binding.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_role_binding(
            'default', None, provider_config, 'autoscaler_role_binding')
        auth_api_mock.patch_namespaced_role_binding.assert_called_once()

    def test_cluster_role_409_handled(self, monkeypatch):
        """Test 409 on create_cluster_role re-reads and succeeds."""
        from sky.provision.kubernetes import utils as kubernetes_utils

        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        new_cr = kubernetes_utils.dict_to_k8s_object(
            provider_config['autoscaler_cluster_role'], 'V1ClusterRole')
        existing_cr = self._make_existing_role(new_cr.rules)

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_cluster_role.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_cr]),
        ]
        auth_api_mock.create_cluster_role.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_cluster_role('default', None,
                                                      provider_config)
        auth_api_mock.patch_cluster_role.assert_not_called()

    def test_cluster_role_409_then_patch(self, monkeypatch):
        """Test 409 on create_cluster_role re-reads and patches when rules
        differ."""
        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        existing_cr = self._make_existing_role(rules=['stale-rules'])

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_cluster_role.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_cr]),
        ]
        auth_api_mock.create_cluster_role.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_cluster_role('default', None,
                                                      provider_config)
        auth_api_mock.patch_cluster_role.assert_called_once()

    def test_cluster_role_binding_409_handled(self, monkeypatch):
        """Test 409 on create_cluster_role_binding re-reads and succeeds."""
        from sky.provision.kubernetes import utils as kubernetes_utils

        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        new_binding = kubernetes_utils.dict_to_k8s_object(
            provider_config['autoscaler_cluster_role_binding'],
            'V1ClusterRoleBinding')
        existing_binding = self._make_existing_binding(new_binding.role_ref,
                                                       new_binding.subjects)

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_cluster_role_binding.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_binding]),
        ]
        auth_api_mock.create_cluster_role_binding.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_cluster_role_binding(
            'default', None, provider_config)
        auth_api_mock.patch_cluster_role_binding.assert_not_called()

    def test_cluster_role_binding_409_then_patch(self, monkeypatch):
        """Test 409 on create_cluster_role_binding re-reads and patches when
        binding differs."""
        api_exc = _make_api_exception(409, 'Conflict')
        provider_config = _make_provider_config_for_rbac()

        existing_binding = self._make_existing_binding(
            role_ref='stale-role-ref', subjects=['stale-subject'])

        auth_api_mock = mock.MagicMock()
        auth_api_mock.list_cluster_role_binding.side_effect = [
            mock.MagicMock(items=[]),
            mock.MagicMock(items=[existing_binding]),
        ]
        auth_api_mock.create_cluster_role_binding.side_effect = api_exc

        monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                            lambda *args, **kwargs: auth_api_mock)
        monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                            lambda *args, **kwargs: type(api_exc))

        config_lib._configure_autoscaler_cluster_role_binding(
            'default', None, provider_config)
        auth_api_mock.patch_cluster_role_binding.assert_called_once()


class TestRuntimeClassOverride:
    """Tests for runtimeClassName override behavior in GPU pod specs.

    When nvidia runtime exists and GPU pods are requested, SkyPilot
    auto-sets runtimeClassName to 'nvidia'. pod_config should be able
    to override or remove this setting.
    """

    @staticmethod
    def _apply_runtime_class_logic(pod_spec, nvidia_runtime_exists,
                                   needs_gpus_nvidia):
        """Replicates the runtimeClassName logic from _create_pods."""
        if nvidia_runtime_exists and needs_gpus_nvidia:
            if 'runtimeClassName' not in pod_spec['spec']:
                pod_spec['spec']['runtimeClassName'] = 'nvidia'
            elif not pod_spec['spec']['runtimeClassName']:
                del pod_spec['spec']['runtimeClassName']

    def test_default_sets_nvidia_runtime(self):
        """No runtimeClassName in pod_config -> auto-set to 'nvidia'."""
        pod_spec = {'spec': {'containers': [{}]}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=True,
                                        needs_gpus_nvidia=True)
        assert pod_spec['spec']['runtimeClassName'] == 'nvidia'

    def test_pod_config_overrides_runtime(self):
        """pod_config sets a custom runtimeClassName -> respected."""
        pod_spec = {'spec': {'containers': [{}], 'runtimeClassName': 'custom'}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=True,
                                        needs_gpus_nvidia=True)
        assert pod_spec['spec']['runtimeClassName'] == 'custom'

    def test_pod_config_null_removes_runtime(self):
        """pod_config sets runtimeClassName to None -> field removed."""
        pod_spec = {'spec': {'containers': [{}], 'runtimeClassName': None}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=True,
                                        needs_gpus_nvidia=True)
        assert 'runtimeClassName' not in pod_spec['spec']

    def test_pod_config_empty_string_removes_runtime(self):
        """pod_config sets runtimeClassName to '' -> field removed."""
        pod_spec = {'spec': {'containers': [{}], 'runtimeClassName': ''}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=True,
                                        needs_gpus_nvidia=True)
        assert 'runtimeClassName' not in pod_spec['spec']

    def test_no_nvidia_runtime_no_change(self):
        """nvidia runtime doesn't exist -> no runtimeClassName set."""
        pod_spec = {'spec': {'containers': [{}]}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=False,
                                        needs_gpus_nvidia=True)
        assert 'runtimeClassName' not in pod_spec['spec']

    def test_no_gpu_no_change(self):
        """No GPU requested -> no runtimeClassName set."""
        pod_spec = {'spec': {'containers': [{}]}}
        self._apply_runtime_class_logic(pod_spec,
                                        nvidia_runtime_exists=True,
                                        needs_gpus_nvidia=False)
        assert 'runtimeClassName' not in pod_spec['spec']


class TestWaitForPodsToScheduleAutoscaleTimeout:
    """Tests for the autoscaler-aware timeout extension in
    _wait_for_pods_to_schedule.

    The production bug: when an autoscaler is configured, node scale-up can
    take 10+ minutes, but the default provision_timeout (10s) is tuned for
    normal scheduling latency. Tests verify that once autoscaling is
    detected, the deadline is extended from the detection moment.
    """

    class _FakeClock:
        """Deterministic clock that advances only when sleep() is called.

        Replaces time.time()/time.sleep() in the instance module so the
        while loop in _wait_for_pods_to_schedule is driven by simulated
        time rather than wall-clock time.
        """

        def __init__(self):
            self.now = 0.0

        def time(self):
            return self.now

        def sleep(self, secs):
            self.now += secs

    @staticmethod
    def _make_node(name: str, cluster_name_on_cloud: str):
        """Build a mock new_node (used to derive expected pod names)."""
        from sky.provision import constants as prov_constants
        node = mock.MagicMock()
        node.metadata.name = name
        node.metadata.labels = {
            prov_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud
        }
        return node

    @staticmethod
    def _make_pending_pod(name: str, cluster_name_on_cloud: str):
        """Build a pod that is Pending with no container_statuses.

        This represents a pod that has not yet been scheduled — the loop
        should keep waiting for it.
        """
        from sky.provision import constants as prov_constants
        pod = mock.MagicMock()
        pod.metadata.name = name
        pod.metadata.labels = {
            prov_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud
        }
        pod.status.phase = 'Pending'
        pod.status.container_statuses = None
        return pod

    def _setup(self, monkeypatch, autoscaler_type, autoscale_detected):
        """Wire up all mocks. Returns (clock, raise_errors_mock)."""

        # 1. Config lookup — return the autoscaler type when asked.
        def mock_config(cloud, region, keys, default_value=None, **kwargs):
            if keys == ('autoscaler',):
                return autoscaler_type
            return default_value

        monkeypatch.setattr('sky.skypilot_config.get_effective_region_config',
                            mock_config)

        # 2. k8s core API — always return the same pending pod.
        cluster_name_on_cloud = 'my-cluster'
        pod = self._make_pending_pod('pod-0', cluster_name_on_cloud)
        pods_list = mock.MagicMock()
        pods_list.items = [pod]
        core_api = mock.MagicMock()
        core_api.list_namespaced_pod.return_value = pods_list
        monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                            lambda *a, **kw: core_api)

        # 3. Autoscale detection — return caller-supplied flag.
        monkeypatch.setattr(instance, '_cluster_had_autoscale_event',
                            lambda *a, **kw: autoscale_detected)
        monkeypatch.setattr(instance, '_cluster_maybe_autoscaling',
                            lambda *a, **kw: autoscale_detected)

        # 4. Replace the slow error-surfacing path with a simple marker
        #    so we can cheaply detect that the timeout path fired.
        raise_errors = mock.MagicMock(
            side_effect=config_lib.KubernetesError('simulated-timeout'))
        monkeypatch.setattr(instance, '_raise_pod_scheduling_errors',
                            raise_errors)

        # 5. Deterministic clock — advances only via sleep().
        clock = self._FakeClock()
        monkeypatch.setattr(instance.time, 'time', clock.time)
        monkeypatch.setattr(instance.time, 'sleep', clock.sleep)

        # 6. No-op spinner update to avoid rich_utils side effects.
        monkeypatch.setattr('sky.utils.rich_utils.force_update_status',
                            lambda *a, **kw: None)

        return clock, raise_errors, cluster_name_on_cloud

    def test_timeout_fires_without_autoscaler(self, monkeypatch):
        """Without any autoscaler configured, the original timeout is
        enforced — the function should exit the loop and raise once the
        user-specified timeout elapses."""
        _, raise_errors, cluster_name_on_cloud = self._setup(
            monkeypatch, autoscaler_type=None, autoscale_detected=False)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError,
                           match='simulated-timeout'):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=5,
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        assert raise_errors.called, (
            'Without autoscaler, timeout=5s must trigger the error path.')

    def test_autoscale_detection_extends_deadline(self, monkeypatch):
        """When autoscaling is detected, the deadline is extended from the
        detection moment by _AUTOSCALE_DETECTED_TIMEOUT_SECONDS. A short
        user timeout alone would exit in seconds, but the extension keeps
        the loop alive for much longer."""
        clock, raise_errors, cluster_name_on_cloud = self._setup(
            monkeypatch, autoscaler_type='gke', autoscale_detected=True)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=5,  # far shorter than the 900s extension
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        # The loop sleeps 1s per iteration via the fake clock. If the
        # extension did NOT apply we would exit after ~5s of simulated
        # time. It must run for at least the extension window instead.
        assert clock.now >= instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS, (
            f'Expected simulated time >= '
            f'{instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS}s after '
            f'autoscale detection, but got {clock.now}s — the extension '
            f'did not take effect.')
        assert raise_errors.called

    def test_autoscale_extension_does_not_shorten_user_timeout(
            self, monkeypatch):
        """If the user set a provision_timeout longer than the extension
        window, their value must still be honored (max of the two)."""
        clock, _, cluster_name_on_cloud = self._setup(monkeypatch,
                                                      autoscaler_type='gke',
                                                      autoscale_detected=True)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        long_timeout = instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS + 600
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=long_timeout,
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        # The function should run at least until the longer user timeout
        # elapses, even though the extension window expired earlier.
        assert clock.now >= long_timeout, (
            f'User-specified timeout of {long_timeout}s must not be '
            f'shortened by the autoscale extension; loop ran for only '
            f'{clock.now}s.')

    def test_karpenter_heuristic_does_not_extend_deadline(self, monkeypatch):
        """Karpenter does not emit TriggeredScaleUp; the code falls back
        to heuristic FailedScheduling detection. That signal is NOT
        reliable enough (same event fires for oversized requests,
        taints, PVC binding errors, etc.) to extend the deadline by
        15 min, so the heuristic path must only update the spinner
        message and leave the deadline alone.

        The autoscaler-configured initial minimum timeout still applies,
        but nothing beyond that.
        """
        clock, _, cluster_name_on_cloud = self._setup(
            monkeypatch, autoscaler_type='karpenter', autoscale_detected=True)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=5,
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        # Initial timeout is bumped to the autoscaler minimum (60s), but
        # the 15 min extension must NOT apply under the heuristic path.
        assert clock.now >= instance._AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS, (
            f'Expected at least the autoscaler initial minimum '
            f'({instance._AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS}s) of '
            f'waiting, got {clock.now}s.')
        assert clock.now < instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS, (
            f'Heuristic FailedScheduling detection must NOT extend the '
            f'deadline by the full 15 min window, but loop ran for '
            f'{clock.now}s.')

    def test_autoscaler_configured_bumps_short_timeout_to_minimum(
            self, monkeypatch):
        """The default provision_timeout (10s) is shorter than the
        Cluster Autoscaler scan interval (~10s), so with a vanilla
        config the loop would exit before any TriggeredScaleUp could
        be emitted. When an autoscaler is configured, the initial
        timeout must be bumped to at least
        _AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS so detection has a
        chance to run."""
        clock, _, cluster_name_on_cloud = self._setup(monkeypatch,
                                                      autoscaler_type='gke',
                                                      autoscale_detected=False)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=10,  # default; shorter than CA scan interval
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        # No detection → no 15 min extension. But the initial timeout
        # must have been bumped to the autoscaler minimum, so the loop
        # should run for at least that long.
        assert clock.now >= instance._AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS, (
            f'Autoscaler-configured timeout should be bumped to >= '
            f'{instance._AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS}s, but '
            f'loop ran only {clock.now}s.')
        assert clock.now < instance._AUTOSCALE_DETECTED_TIMEOUT_SECONDS, (
            f'No TriggeredScaleUp detected → 15 min extension must not '
            f'apply, but loop ran for {clock.now}s.')

    def test_no_autoscaler_does_not_bump_timeout(self, monkeypatch):
        """Without an autoscaler configured, the initial-minimum bump
        must NOT apply — a user who explicitly sets a short timeout on
        a non-autoscaling cluster expects it to be honored."""
        clock, _, cluster_name_on_cloud = self._setup(monkeypatch,
                                                      autoscaler_type=None,
                                                      autoscale_detected=False)

        node = self._make_node('pod-0', cluster_name_on_cloud)
        import datetime  # pylint: disable=import-outside-toplevel

        with pytest.raises(config_lib.KubernetesError):
            instance._wait_for_pods_to_schedule(
                namespace='ns',
                context='test-context',
                new_nodes=[node],
                timeout=5,
                cluster_name='cn',
                create_pods_start=datetime.datetime.utcnow())

        # Without autoscaler, the 5s timeout must be honored (not bumped
        # to the 60s autoscaler minimum). Loop should exit shortly after
        # 5s — generously below the autoscaler minimum.
        assert clock.now < instance._AUTOSCALE_INITIAL_MIN_TIMEOUT_SECONDS, (
            f'No autoscaler configured → short user timeout must not be '
            f'bumped, but loop ran for {clock.now}s.')
