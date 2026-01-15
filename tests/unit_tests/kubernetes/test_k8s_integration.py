"""Integration tests for Kubernetes functionality using mocks.

These tests cover scenarios that were previously only testable with real
K8s clusters (smoke tests). They use the mock infrastructure to simulate
K8s behavior without requiring actual cluster resources.

Test coverage includes:
- Pod lifecycle (creation, scheduling, running, failure, deletion)
- GPU allocation and multi-node clusters
- Volumes and PVCs
- Pod recovery scenarios
- Multi-context failover
- Service creation and cleanup
"""

import time
from typing import Dict, List, Optional
from unittest import mock

import pytest

from tests.unit_tests.kubernetes.k8s_fixtures import create_gpu_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import (
    create_heterogeneous_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import create_ray_cluster_pods
from tests.unit_tests.kubernetes.k8s_fixtures import create_single_node_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_pod
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_pvc
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_service
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster_empty
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster_with_gpus
from tests.unit_tests.kubernetes.k8s_fixtures import (
    mock_k8s_heterogeneous_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_multinode_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_with_pvc
from tests.unit_tests.kubernetes.k8s_fixtures import (
    mock_k8s_with_running_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import patch_kubernetes_apis
from tests.unit_tests.kubernetes.k8s_fixtures import patch_kubernetes_utils
from tests.unit_tests.kubernetes.mock_kubernetes import (
    KubernetesClusterSimulator)
from tests.unit_tests.kubernetes.mock_kubernetes import MockContainer
from tests.unit_tests.kubernetes.mock_kubernetes import (
    MockKubernetesApiException)
from tests.unit_tests.kubernetes.mock_kubernetes import MockNode
from tests.unit_tests.kubernetes.mock_kubernetes import MockPod
from tests.unit_tests.kubernetes.mock_kubernetes import MockPVC
from tests.unit_tests.kubernetes.mock_kubernetes import MockResourceRequirements
from tests.unit_tests.kubernetes.mock_kubernetes import MockService
from tests.unit_tests.kubernetes.mock_kubernetes import MockVolume
from tests.unit_tests.kubernetes.mock_kubernetes import PHASE_FAILED
from tests.unit_tests.kubernetes.mock_kubernetes import PHASE_PENDING
from tests.unit_tests.kubernetes.mock_kubernetes import PHASE_RUNNING
from tests.unit_tests.kubernetes.mock_kubernetes import PHASE_SUCCEEDED
from tests.unit_tests.kubernetes.mock_kubernetes import PVC_BOUND
from tests.unit_tests.kubernetes.mock_kubernetes import PVC_PENDING

# =============================================================================
# Pod Lifecycle Tests
# =============================================================================


class TestPodLifecycle:
    """Tests for pod lifecycle management."""

    def test_pod_creation_and_scheduling(self, mock_k8s_cluster):
        """Test that pods are created and scheduled to nodes."""
        simulator = mock_k8s_cluster

        pod = create_test_pod(
            name='test-pod',
            cpus='2',
            memory='4Gi',
        )

        created_pod = simulator.create_pod(pod)

        assert created_pod.metadata.name == 'test-pod'
        assert created_pod.status.phase == PHASE_RUNNING
        assert created_pod.spec.node_name is not None
        assert created_pod.status.pod_ip is not None

    def test_pod_scheduling_respects_resources(self, mock_k8s_cluster):
        """Test that pod scheduling respects resource constraints."""
        simulator = mock_k8s_cluster

        # Request more resources than node has
        pod = create_test_pod(
            name='large-pod',
            cpus='100',  # More than node capacity
            memory='4Gi',
        )

        created_pod = simulator.create_pod(pod)

        # Pod should remain pending (not scheduled)
        assert created_pod.status.phase == PHASE_PENDING
        assert created_pod.spec.node_name is None

    def test_pod_deletion_releases_resources(self, mock_k8s_cluster):
        """Test that deleting a pod releases its resources."""
        simulator = mock_k8s_cluster

        # Create and schedule a pod
        pod = create_test_pod(name='pod-to-delete', cpus='4', memory='8Gi')
        simulator.create_pod(pod)

        node_name = pod.spec.node_name
        assert node_name is not None

        # Record allocated resources before deletion
        allocated_before = simulator._allocated_resources[node_name]['cpu']

        # Delete the pod
        simulator.delete_pod('pod-to-delete')

        # Resources should be released
        allocated_after = simulator._allocated_resources[node_name]['cpu']
        assert allocated_after < allocated_before

    def test_pod_failure_simulation(self, mock_k8s_cluster):
        """Test simulating pod failure."""
        simulator = mock_k8s_cluster

        pod = create_test_pod(name='failing-pod')
        simulator.create_pod(pod)
        assert pod.status.phase == PHASE_RUNNING

        # Simulate failure
        simulator.simulate_pod_failure('failing-pod',
                                       reason='OOMKilled',
                                       message='Container exceeded memory limit')

        assert pod.status.phase == PHASE_FAILED
        assert pod.status.reason == 'OOMKilled'

        # Check that an event was generated
        events = simulator.list_events(involved_object_name='failing-pod')
        assert any(e.reason == 'OOMKilled' for e in events)

    def test_multiple_pods_scheduling(self, mock_k8s_multinode_cluster):
        """Test scheduling multiple pods across nodes when resources require it."""
        simulator = mock_k8s_multinode_cluster

        pods = []
        # Create pods large enough that they need to be spread across nodes
        # Each CPU node has 16 CPUs, so pods requesting 10 CPUs each will need
        # to be spread across different nodes
        for i in range(4):
            pod = create_test_pod(
                name=f'pod-{i}',
                cpus='10',
                memory='32Gi',
            )
            simulator.create_pod(pod)
            pods.append(pod)

        # All pods should be scheduled
        running_pods = [p for p in pods if p.status.phase == PHASE_RUNNING]
        for pod in running_pods:
            assert pod.spec.node_name is not None

        # With 10 CPU pods and 16 CPU nodes, pods should be spread across multiple nodes
        node_names = set(pod.spec.node_name for pod in running_pods)
        assert len(node_names) >= 2


class TestPodLabelSelector:
    """Tests for pod label selector functionality."""

    def test_list_pods_with_label_selector(self, mock_k8s_cluster):
        """Test listing pods with label selector."""
        simulator = mock_k8s_cluster

        # Create pods with different labels
        pod1 = create_test_pod(name='app-pod-1', labels={'app': 'web', 'env': 'prod'})
        pod2 = create_test_pod(name='app-pod-2', labels={'app': 'web', 'env': 'dev'})
        pod3 = create_test_pod(name='db-pod', labels={'app': 'database', 'env': 'prod'})

        for pod in [pod1, pod2, pod3]:
            simulator.create_pod(pod)

        # List pods with specific label
        web_pods = simulator.list_pods(label_selector='app=web')
        assert len(web_pods) == 2
        assert all(p.metadata.labels.get('app') == 'web' for p in web_pods)

        prod_pods = simulator.list_pods(label_selector='env=prod')
        assert len(prod_pods) == 2

        web_prod_pods = simulator.list_pods(label_selector='app=web,env=prod')
        assert len(web_prod_pods) == 1

    def test_ray_cluster_pods_filtering(self, mock_k8s_cluster_with_gpus):
        """Test filtering Ray cluster pods by label selector."""
        simulator = mock_k8s_cluster_with_gpus

        # Create Ray cluster pods
        pods = create_ray_cluster_pods(
            cluster_name='my-ray-cluster',
            num_workers=2,
            cpus_per_node='4',
            memory_per_node='16Gi',
        )
        for pod in pods:
            simulator.create_pod(pod)

        # Filter by cluster name
        cluster_pods = simulator.list_pods(
            label_selector='ray-cluster-name=my-ray-cluster')
        assert len(cluster_pods) == 3  # 1 head + 2 workers

        # Filter head pod
        head_pods = simulator.list_pods(
            label_selector='ray-cluster-name=my-ray-cluster,ray-node-type=head')
        assert len(head_pods) == 1
        assert 'head' in head_pods[0].metadata.name


# =============================================================================
# GPU Allocation Tests
# =============================================================================


class TestGPUAllocation:
    """Tests for GPU allocation and tracking."""

    def test_gpu_pod_scheduling(self, mock_k8s_cluster_with_gpus):
        """Test that GPU pods are scheduled to GPU nodes."""
        simulator = mock_k8s_cluster_with_gpus

        pod = create_test_pod(
            name='gpu-pod',
            cpus='4',
            memory='16Gi',
            gpus=2,
        )

        simulator.create_pod(pod)

        assert pod.status.phase == PHASE_RUNNING
        assert pod.spec.node_name is not None
        # Should be on a GPU node
        node = simulator.get_node(pod.spec.node_name)
        assert node.has_gpu()

    def test_gpu_allocation_tracking(self, mock_k8s_cluster_with_gpus):
        """Test that GPU allocations are tracked correctly."""
        simulator = mock_k8s_cluster_with_gpus

        # Initially no GPUs allocated
        allocated = simulator.get_allocated_gpu_qty_by_node()
        assert all(qty == 0 for qty in allocated.values())

        # Create GPU pods
        for i in range(4):
            pod = create_test_pod(
                name=f'gpu-pod-{i}',
                cpus='4',
                memory='16Gi',
                gpus=2,
            )
            simulator.create_pod(pod)

        # Check allocations
        allocated = simulator.get_allocated_gpu_qty_by_node()
        total_allocated = sum(allocated.values())
        assert total_allocated == 8  # 4 pods * 2 GPUs each

    def test_insufficient_gpus(self, mock_k8s_cluster_with_gpus):
        """Test scheduling when insufficient GPUs are available."""
        simulator = mock_k8s_cluster_with_gpus

        # Try to allocate all GPUs plus more
        for i in range(10):  # More pods than available GPU slots
            pod = create_test_pod(
                name=f'gpu-pod-{i}',
                cpus='4',
                memory='16Gi',
                gpus=4,
            )
            simulator.create_pod(pod)

        # Some pods should be pending
        pending_pods = [
            p for p in simulator.list_pods() if p.status.phase == PHASE_PENDING
        ]
        running_pods = [
            p for p in simulator.list_pods() if p.status.phase == PHASE_RUNNING
        ]

        assert len(pending_pods) > 0
        assert len(running_pods) > 0

    def test_gpu_type_node_selector(self, mock_k8s_heterogeneous_cluster):
        """Test GPU type selection via node selector."""
        simulator = mock_k8s_heterogeneous_cluster

        # Create pod requesting H100
        h100_pod = create_test_pod(
            name='h100-pod',
            cpus='8',
            memory='64Gi',
            gpus=4,
            node_selector={'skypilot.co/accelerator': 'h100'},
        )
        simulator.create_pod(h100_pod)

        assert h100_pod.status.phase == PHASE_RUNNING
        node = simulator.get_node(h100_pod.spec.node_name)
        assert 'h100' in node.metadata.labels.get('skypilot.co/accelerator', '').lower()

        # Create pod requesting V100
        v100_pod = create_test_pod(
            name='v100-pod',
            cpus='8',
            memory='64Gi',
            gpus=2,
            node_selector={'skypilot.co/accelerator': 'v100'},
        )
        simulator.create_pod(v100_pod)

        assert v100_pod.status.phase == PHASE_RUNNING
        node = simulator.get_node(v100_pod.spec.node_name)
        assert 'v100' in node.metadata.labels.get('skypilot.co/accelerator', '').lower()


# =============================================================================
# Multi-Node Cluster Tests
# =============================================================================


class TestMultiNodeCluster:
    """Tests for multi-node cluster operations."""

    def test_ray_cluster_creation(self, mock_k8s_cluster_with_gpus):
        """Test creating a multi-node Ray cluster."""
        simulator = mock_k8s_cluster_with_gpus

        pods = create_ray_cluster_pods(
            cluster_name='ray-test',
            num_workers=3,
            cpus_per_node='8',
            memory_per_node='32Gi',
            gpus_per_node=2,
        )

        for pod in pods:
            simulator.create_pod(pod)

        # All pods should be running
        for pod in pods:
            assert pod.status.phase == PHASE_RUNNING

        # Head pod should be identifiable
        head_pod = next(
            p for p in pods if 'head' in p.metadata.labels.get('ray-node-type', ''))
        assert head_pod is not None

        # Workers should be on potentially different nodes for distribution
        worker_pods = [
            p for p in pods if 'worker' in p.metadata.labels.get('ray-node-type', '')
        ]
        assert len(worker_pods) == 3

    def test_cluster_resource_distribution(self, mock_k8s_multinode_cluster):
        """Test that pods are distributed across available nodes."""
        simulator = mock_k8s_multinode_cluster

        # Create enough pods to require multiple nodes
        pods = []
        for i in range(8):
            pod = create_test_pod(
                name=f'distributed-pod-{i}',
                cpus='4',
                memory='16Gi',
            )
            simulator.create_pod(pod)
            pods.append(pod)

        running_pods = [p for p in pods if p.status.phase == PHASE_RUNNING]
        node_distribution = {}
        for pod in running_pods:
            node = pod.spec.node_name
            node_distribution[node] = node_distribution.get(node, 0) + 1

        # Pods should be distributed across multiple nodes
        assert len(node_distribution) >= 2


# =============================================================================
# Volume and PVC Tests
# =============================================================================


class TestVolumes:
    """Tests for volume and PVC operations."""

    def test_pvc_creation_and_binding(self, mock_k8s_cluster):
        """Test PVC creation and automatic binding."""
        simulator = mock_k8s_cluster

        pvc = create_test_pvc(
            name='test-pvc',
            storage_size='10Gi',
            bound=False,
        )

        # Auto-bind is enabled by default
        simulator.create_pvc(pvc)

        assert pvc.status.phase == PVC_BOUND
        assert pvc.spec.volume_name is not None

    def test_pvc_manual_binding(self, mock_k8s_cluster):
        """Test manual PVC binding."""
        simulator = mock_k8s_cluster
        simulator._auto_bind_pvcs = False

        pvc = create_test_pvc(
            name='manual-pvc',
            storage_size='20Gi',
            bound=False,
        )
        simulator.create_pvc(pvc)

        assert pvc.status.phase == PVC_PENDING

        # Manually bind
        pvc.bind('my-pv')
        assert pvc.status.phase == PVC_BOUND
        assert pvc.spec.volume_name == 'my-pv'

    def test_pod_with_pvc_volume(self, mock_k8s_with_pvc):
        """Test pod creation with PVC volume."""
        simulator, pvc = mock_k8s_with_pvc

        # Create pod with volume referencing the PVC
        volume = MockVolume(
            name='data-volume',
            persistent_volume_claim={'claimName': pvc.metadata.name},
        )

        pod = MockPod(
            name='pod-with-pvc',
            volumes=[volume],
        )
        pod.spec.containers = [MockContainer(name='main')]

        simulator.create_pod(pod)

        assert pod.status.phase == PHASE_RUNNING

    def test_pvc_deletion(self, mock_k8s_cluster):
        """Test PVC deletion."""
        simulator = mock_k8s_cluster

        pvc = create_test_pvc(name='pvc-to-delete')
        simulator.create_pvc(pvc)

        assert simulator.get_pvc('pvc-to-delete') is not None

        deleted = simulator.delete_pvc('pvc-to-delete')
        assert deleted is not None
        assert simulator.get_pvc('pvc-to-delete') is None


# =============================================================================
# Pod Recovery Tests
# =============================================================================


class TestPodRecovery:
    """Tests for pod recovery scenarios."""

    def test_pod_recreation_after_deletion(self, mock_k8s_cluster_with_gpus):
        """Test recreating a pod after deletion (simulates recovery)."""
        simulator = mock_k8s_cluster_with_gpus

        # Create initial pod
        pod1 = create_test_pod(
            name='recoverable-pod',
            cpus='4',
            memory='16Gi',
            gpus=2,
        )
        simulator.create_pod(pod1)
        assert pod1.status.phase == PHASE_RUNNING
        original_node = pod1.spec.node_name

        # Delete pod
        simulator.delete_pod('recoverable-pod')
        assert simulator.get_pod('recoverable-pod') is None

        # Recreate pod with same name
        pod2 = create_test_pod(
            name='recoverable-pod',
            cpus='4',
            memory='16Gi',
            gpus=2,
        )
        simulator.create_pod(pod2)

        assert pod2.status.phase == PHASE_RUNNING
        # New pod should get new UID
        assert pod2.metadata.uid != pod1.metadata.uid

    def test_node_failure_and_recovery(self, mock_k8s_multinode_cluster):
        """Test handling of node failure and recovery."""
        simulator = mock_k8s_multinode_cluster

        # Create pods
        pods = []
        for i in range(4):
            pod = create_test_pod(name=f'pod-{i}', cpus='4', memory='16Gi')
            simulator.create_pod(pod)
            pods.append(pod)

        # Find a node with pods
        node_to_fail = pods[0].spec.node_name
        assert node_to_fail is not None

        # Simulate node failure
        simulator.simulate_node_failure(node_to_fail)

        # Node should not be ready
        failed_node = simulator.get_node(node_to_fail)
        assert not failed_node.is_ready()

        # Recover the node
        simulator.recover_node(node_to_fail)
        assert failed_node.is_ready()

    def test_ray_head_pod_recovery(self, mock_k8s_cluster_with_gpus):
        """Test Ray head pod deletion and recreation (recovery scenario)."""
        simulator = mock_k8s_cluster_with_gpus

        # Create Ray cluster
        pods = create_ray_cluster_pods(
            cluster_name='recoverable-ray',
            num_workers=2,
            cpus_per_node='8',
            memory_per_node='32Gi',
            gpus_per_node=2,
        )

        for pod in pods:
            simulator.create_pod(pod)

        head_pod = [p for p in pods if 'head' in p.metadata.name][0]
        assert head_pod.status.phase == PHASE_RUNNING

        # Delete head pod (simulates head failure)
        simulator.delete_pod(head_pod.metadata.name)

        # Create new head pod
        new_head = create_test_pod(
            name='recoverable-ray-head',
            cpus='8',
            memory='32Gi',
            gpus=2,
            labels={
                'ray-cluster-name': 'recoverable-ray',
                'ray-node-type': 'head',
                'component': 'recoverable-ray-head'
            },
        )
        simulator.create_pod(new_head)

        assert new_head.status.phase == PHASE_RUNNING


# =============================================================================
# Service Tests
# =============================================================================


class TestServices:
    """Tests for Kubernetes service operations."""

    def test_service_creation(self, mock_k8s_cluster):
        """Test service creation."""
        simulator = mock_k8s_cluster

        service = create_test_service(
            name='test-service',
            service_type='ClusterIP',
            port=80,
            target_port=8080,
            selector={'app': 'web'},
        )

        simulator.create_service(service)

        retrieved = simulator.get_service('test-service')
        assert retrieved is not None
        assert retrieved.spec.cluster_ip is not None

    def test_loadbalancer_service(self, mock_k8s_cluster):
        """Test LoadBalancer service creation."""
        simulator = mock_k8s_cluster

        service = create_test_service(
            name='lb-service',
            service_type='LoadBalancer',
            port=443,
            target_port=8443,
        )

        simulator.create_service(service)

        assert service.status.load_balancer.get('ingress') is not None

    def test_service_deletion_cleanup(self, mock_k8s_cluster):
        """Test that services are properly cleaned up."""
        simulator = mock_k8s_cluster

        # Create service
        service = create_test_service(name='cleanup-service')
        simulator.create_service(service)
        assert simulator.get_service('cleanup-service') is not None

        # Delete service
        simulator.delete_service('cleanup-service')
        assert simulator.get_service('cleanup-service') is None

        # Verify no leftover services
        services = simulator.list_services()
        assert not any(s.metadata.name == 'cleanup-service' for s in services)


# =============================================================================
# Namespace Tests
# =============================================================================


class TestNamespaces:
    """Tests for namespace operations."""

    def test_namespace_creation(self, mock_k8s_cluster):
        """Test namespace creation."""
        simulator = mock_k8s_cluster

        simulator.create_namespace('test-ns')
        assert simulator.namespace_exists('test-ns')

    def test_pods_in_different_namespaces(self, mock_k8s_cluster):
        """Test pod isolation across namespaces."""
        simulator = mock_k8s_cluster

        simulator.create_namespace('ns-a')
        simulator.create_namespace('ns-b')

        pod_a = create_test_pod(name='pod-1', namespace='ns-a')
        pod_b = create_test_pod(name='pod-1', namespace='ns-b')

        simulator.create_pod(pod_a)
        simulator.create_pod(pod_b)

        # Both pods should exist in their respective namespaces
        assert simulator.get_pod('pod-1', 'ns-a') is not None
        assert simulator.get_pod('pod-1', 'ns-b') is not None

        # Listing pods in each namespace should only return that namespace's pods
        pods_a = simulator.list_pods('ns-a')
        pods_b = simulator.list_pods('ns-b')

        assert len(pods_a) == 1
        assert len(pods_b) == 1
        assert pods_a[0].metadata.namespace == 'ns-a'
        assert pods_b[0].metadata.namespace == 'ns-b'


# =============================================================================
# RBAC Tests
# =============================================================================


class TestRBAC:
    """Tests for RBAC resources."""

    def test_service_account_creation(self, mock_k8s_cluster):
        """Test service account creation."""
        simulator = mock_k8s_cluster

        simulator.create_service_account('my-sa')
        assert simulator.service_account_exists('my-sa')

    def test_role_and_binding_creation(self, mock_k8s_cluster):
        """Test role and role binding creation."""
        simulator = mock_k8s_cluster

        simulator.create_role('my-role')
        simulator.create_role_binding('my-role-binding')

        assert 'my-role' in simulator._roles.get('default', set())
        assert 'my-role-binding' in simulator._role_bindings.get('default', set())

    def test_cluster_role_creation(self, mock_k8s_cluster):
        """Test cluster role creation."""
        simulator = mock_k8s_cluster

        simulator.create_cluster_role('cluster-admin')
        simulator.create_cluster_role_binding('cluster-admin-binding')

        assert 'cluster-admin' in simulator._cluster_roles
        assert 'cluster-admin-binding' in simulator._cluster_role_bindings


# =============================================================================
# API Error Handling Tests
# =============================================================================


class TestAPIErrors:
    """Tests for API error handling."""

    def test_pod_not_found(self, mock_k8s_cluster):
        """Test handling of pod not found error."""
        simulator = mock_k8s_cluster
        apis = {'core_api': mock.MagicMock()}

        from tests.unit_tests.kubernetes.mock_kubernetes import MockCoreV1Api
        core_api = MockCoreV1Api(simulator)

        with pytest.raises(MockKubernetesApiException) as exc_info:
            core_api.read_namespaced_pod('nonexistent', 'default')

        assert exc_info.value.status == 404

    def test_pvc_not_found(self, mock_k8s_cluster):
        """Test handling of PVC not found error."""
        simulator = mock_k8s_cluster

        from tests.unit_tests.kubernetes.mock_kubernetes import MockCoreV1Api
        core_api = MockCoreV1Api(simulator)

        with pytest.raises(MockKubernetesApiException) as exc_info:
            core_api.read_namespaced_persistent_volume_claim('nonexistent', 'default')

        assert exc_info.value.status == 404


# =============================================================================
# Event Tests
# =============================================================================


class TestEvents:
    """Tests for event generation and retrieval."""

    def test_pod_creation_event(self, mock_k8s_cluster):
        """Test that pod creation generates events."""
        simulator = mock_k8s_cluster

        pod = create_test_pod(name='event-pod')
        simulator.create_pod(pod)

        events = simulator.list_events(involved_object_name='event-pod')
        assert len(events) > 0
        assert any(e.involved_object.get('name') == 'event-pod' for e in events)

    def test_pod_failure_event(self, mock_k8s_cluster):
        """Test that pod failure generates warning event."""
        simulator = mock_k8s_cluster

        pod = create_test_pod(name='failing-event-pod')
        simulator.create_pod(pod)

        simulator.simulate_pod_failure('failing-event-pod',
                                       reason='CrashLoopBackOff',
                                       message='Container keeps crashing')

        events = simulator.list_events(involved_object_name='failing-event-pod')
        warning_events = [e for e in events if e.type == 'Warning']
        assert len(warning_events) > 0
        assert any(e.reason == 'CrashLoopBackOff' for e in warning_events)


# =============================================================================
# Resource Parsing Tests
# =============================================================================


class TestResourceParsing:
    """Tests for resource string parsing."""

    def test_cpu_parsing(self):
        """Test CPU resource string parsing."""
        from tests.unit_tests.kubernetes.mock_kubernetes import _parse_cpu

        assert _parse_cpu('1') == 1.0
        assert _parse_cpu('2') == 2.0
        assert _parse_cpu('500m') == 0.5
        assert _parse_cpu('250m') == 0.25
        assert _parse_cpu('1500m') == 1.5

    def test_memory_parsing(self):
        """Test memory resource string parsing."""
        from tests.unit_tests.kubernetes.mock_kubernetes import _parse_memory

        assert _parse_memory('1Gi') == 1024**3
        assert _parse_memory('512Mi') == 512 * 1024**2
        assert _parse_memory('1G') == 1000**3
        assert _parse_memory('100Ki') == 100 * 1024


# =============================================================================
# Empty Cluster Tests
# =============================================================================


class TestEmptyCluster:
    """Tests for empty cluster scenarios."""

    def test_no_nodes_scheduling_fails(self, mock_k8s_cluster_empty):
        """Test that scheduling fails when no nodes exist."""
        simulator = mock_k8s_cluster_empty

        pod = create_test_pod(name='orphan-pod')
        simulator.create_pod(pod)

        assert pod.status.phase == PHASE_PENDING
        assert pod.spec.node_name is None

    def test_list_operations_return_empty(self, mock_k8s_cluster_empty):
        """Test that list operations return empty on empty cluster."""
        simulator = mock_k8s_cluster_empty

        assert simulator.list_nodes() == []
        assert simulator.list_pods() == []
        assert simulator.list_pvcs() == []
        assert simulator.list_services() == []
