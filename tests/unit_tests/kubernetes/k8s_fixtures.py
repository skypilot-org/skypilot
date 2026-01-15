"""Pytest fixtures for Kubernetes integration testing.

This module provides pytest fixtures for common K8s test scenarios that
can be used to test SkyPilot's K8s functionality without requiring a
real K8s cluster.

Usage:
    from tests.unit_tests.kubernetes.k8s_fixtures import (
        mock_k8s_cluster,
        mock_k8s_cluster_with_gpus,
        mock_k8s_multinode_cluster,
    )

    def test_my_k8s_feature(mock_k8s_cluster):
        # The fixture provides a simulator and patches the K8s APIs
        simulator = mock_k8s_cluster
        # ... test code ...
"""

import functools
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple
from unittest import mock

import pytest

from tests.unit_tests.kubernetes.mock_kubernetes import (
    create_mock_kubernetes_api)
from tests.unit_tests.kubernetes.mock_kubernetes import (
    KubernetesClusterSimulator)
from tests.unit_tests.kubernetes.mock_kubernetes import MockContainer
from tests.unit_tests.kubernetes.mock_kubernetes import MockContainerPort
from tests.unit_tests.kubernetes.mock_kubernetes import MockCoreV1Api
from tests.unit_tests.kubernetes.mock_kubernetes import (
    MockKubernetesApiException)
from tests.unit_tests.kubernetes.mock_kubernetes import MockNode
from tests.unit_tests.kubernetes.mock_kubernetes import MockPod
from tests.unit_tests.kubernetes.mock_kubernetes import MockPVC
from tests.unit_tests.kubernetes.mock_kubernetes import MockResourceRequirements
from tests.unit_tests.kubernetes.mock_kubernetes import MockService
from tests.unit_tests.kubernetes.mock_kubernetes import MockServicePort
from tests.unit_tests.kubernetes.mock_kubernetes import MockVolume
from tests.unit_tests.kubernetes.mock_kubernetes import MockVolumeMount

# =============================================================================
# Cluster Configuration Presets
# =============================================================================


def create_single_node_cluster(
    context_name: str = 'mock-k8s',
    cpu: str = '16',
    memory: str = '64Gi',
) -> KubernetesClusterSimulator:
    """Create a single-node CPU-only cluster."""
    sim = KubernetesClusterSimulator(context_name=context_name)
    sim.add_node(
        name='node-1',
        cpu=cpu,
        memory=memory,
    )
    return sim


def create_gpu_cluster(
    context_name: str = 'mock-k8s-gpu',
    num_nodes: int = 2,
    gpus_per_node: int = 8,
    gpu_type: str = 'H100',
    cpu_per_node: str = '96',
    memory_per_node: str = '768Gi',
) -> KubernetesClusterSimulator:
    """Create a multi-node GPU cluster."""
    sim = KubernetesClusterSimulator(context_name=context_name)
    for i in range(num_nodes):
        sim.add_node(
            name=f'gpu-node-{i+1}',
            cpu=cpu_per_node,
            memory=memory_per_node,
            gpu_count=gpus_per_node,
            gpu_type=gpu_type,
        )
    return sim


def create_heterogeneous_cluster(
    context_name: str = 'mock-k8s-hetero',) -> KubernetesClusterSimulator:
    """Create a heterogeneous cluster with mixed node types."""
    sim = KubernetesClusterSimulator(context_name=context_name)

    # CPU-only nodes
    for i in range(2):
        sim.add_node(
            name=f'cpu-node-{i+1}',
            cpu='16',
            memory='64Gi',
        )

    # V100 GPU nodes
    for i in range(2):
        sim.add_node(
            name=f'v100-node-{i+1}',
            cpu='48',
            memory='256Gi',
            gpu_count=4,
            gpu_type='V100',
        )

    # H100 GPU nodes
    for i in range(2):
        sim.add_node(
            name=f'h100-node-{i+1}',
            cpu='96',
            memory='768Gi',
            gpu_count=8,
            gpu_type='H100',
        )

    return sim


def create_tpu_cluster(
    context_name: str = 'mock-k8s-tpu',
    num_nodes: int = 1,
    tpu_count: int = 8,
    tpu_type: str = 'tpu-v4-podslice',
) -> KubernetesClusterSimulator:
    """Create a TPU cluster."""
    sim = KubernetesClusterSimulator(context_name=context_name)
    for i in range(num_nodes):
        sim.add_node(
            name=f'tpu-node-{i+1}',
            cpu='96',
            memory='384Gi',
            tpu_count=tpu_count,
            tpu_type=tpu_type,
        )
    return sim


# =============================================================================
# API Patching Utilities
# =============================================================================


def patch_kubernetes_apis(
    simulator: KubernetesClusterSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> Dict[str, Any]:
    """Patch all Kubernetes APIs with mock implementations.

    Args:
        simulator: The cluster simulator to use
        monkeypatch: pytest monkeypatch fixture

    Returns:
        Dictionary of mock API instances
    """
    apis = create_mock_kubernetes_api(simulator)

    # Patch the adaptor module functions
    monkeypatch.setattr('sky.adaptors.kubernetes.core_api',
                        lambda *args, **kwargs: apis['core_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.auth_api',
                        lambda *args, **kwargs: apis['auth_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.apps_api',
                        lambda *args, **kwargs: apis['apps_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.networking_api',
                        lambda *args, **kwargs: apis['networking_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.storage_api',
                        lambda *args, **kwargs: apis['storage_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.batch_api',
                        lambda *args, **kwargs: apis['batch_api'])
    monkeypatch.setattr('sky.adaptors.kubernetes.watch',
                        lambda *args, **kwargs: apis['watch'])

    # Patch config loading to be a no-op
    monkeypatch.setattr('sky.adaptors.kubernetes._load_config',
                        lambda *args, **kwargs: None)

    # Patch context listing
    def mock_list_contexts():
        return ([{'name': simulator.context_name}], {'name': simulator.context_name})

    monkeypatch.setattr('sky.adaptors.kubernetes.list_kube_config_contexts',
                        mock_list_contexts)

    # Patch utility functions
    def mock_get_nodes(context=None):
        return simulator.list_nodes()

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        mock_get_nodes)

    def mock_get_all_context_names():
        return [simulator.context_name]

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_all_kube_context_names',
                        mock_get_all_context_names)

    def mock_is_incluster():
        return False

    monkeypatch.setattr('sky.provision.kubernetes.utils.is_incluster_config_available',
                        mock_is_incluster)

    # Patch API exception
    monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                        lambda: MockKubernetesApiException)

    return apis


def patch_kubernetes_utils(
    simulator: KubernetesClusterSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch Kubernetes utility functions.

    This patches higher-level utility functions that are commonly
    used in SkyPilot's K8s integration.
    """
    from sky.provision.kubernetes import utils as k8s_utils

    # Patch GPU label formatter detection
    def mock_detect_gpu_label_formatter(*args, **kwargs):
        return [k8s_utils.SkyPilotLabelFormatter, {}]

    monkeypatch.setattr('sky.provision.kubernetes.utils.detect_gpu_label_formatter',
                        mock_detect_gpu_label_formatter)

    # Patch accelerator resource detection
    def mock_detect_accelerator_resource(*args, **kwargs):
        return (True, [])

    monkeypatch.setattr('sky.provision.kubernetes.utils.detect_accelerator_resource',
                        mock_detect_accelerator_resource)

    # Patch instance fits check
    def mock_check_instance_fits(context, instance_type, *args, **kwargs):
        # Parse the instance type to get requirements
        # Format: {n}CPU--{k}GB[--{type}:{count}]
        import re
        match = re.match(r'(\d+(?:\.\d+)?)?CPU--(\d+(?:\.\d+)?)?GB(?:--([^:]+):(\d+))?',
                         instance_type)
        if not match:
            return (True, '')

        cpus = float(match.group(1) or 0)
        memory_gb = float(match.group(2) or 0)
        acc_type = match.group(3)
        acc_count = int(match.group(4) or 0) if match.group(4) else 0

        # Check if any node can fit this
        for node in simulator.list_nodes():
            node_cpus = node.get_allocatable_cpu()
            node_memory = node.get_allocatable_memory() / (1024**3)  # Convert to GB
            node_gpus = node.get_allocatable_gpus()

            allocated = simulator._allocated_resources.get(node.metadata.name, {})
            avail_cpus = node_cpus - allocated.get('cpu', 0)
            avail_memory = node_memory - allocated.get('memory', 0) / (1024**3)
            avail_gpus = node_gpus - int(allocated.get('nvidia.com/gpu', 0))

            if cpus <= avail_cpus and memory_gb <= avail_memory:
                if acc_count == 0 or avail_gpus >= acc_count:
                    # Check GPU type if specified
                    if acc_type:
                        node_gpu_type = node.metadata.labels.get(
                            'skypilot.co/accelerator', '').upper()
                        if node_gpu_type != acc_type.upper():
                            continue
                    return (True, '')

        return (False, f'No node can fit instance type {instance_type}')

    monkeypatch.setattr('sky.provision.kubernetes.utils.check_instance_fits',
                        mock_check_instance_fits)

    # Patch spot label retrieval
    def mock_get_spot_label(*args, **kwargs):
        return (None, None)

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_spot_label',
                        mock_get_spot_label)

    # Patch kubeconfig exec auth check
    def mock_is_kubeconfig_exec_auth(*args, **kwargs):
        return (False, None)

    monkeypatch.setattr('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth',
                        mock_is_kubeconfig_exec_auth)

    # Patch allocated GPU quantity by node
    def mock_get_allocated_gpu_qty_by_node(context=None):
        return simulator.get_allocated_gpu_qty_by_node()

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_allocated_gpu_qty_by_node',
                        mock_get_allocated_gpu_qty_by_node)


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_cluster(monkeypatch) -> Generator[KubernetesClusterSimulator, None, None]:
    """Fixture providing a single-node CPU cluster.

    Usage:
        def test_something(mock_k8s_cluster):
            simulator = mock_k8s_cluster
            # simulator has one CPU node
    """
    simulator = create_single_node_cluster()
    patch_kubernetes_apis(simulator, monkeypatch)
    patch_kubernetes_utils(simulator, monkeypatch)
    yield simulator


@pytest.fixture
def mock_k8s_cluster_with_gpus(
        monkeypatch) -> Generator[KubernetesClusterSimulator, None, None]:
    """Fixture providing a 2-node H100 GPU cluster.

    Each node has 8 H100 GPUs, 96 CPUs, 768GB memory.

    Usage:
        def test_gpu_feature(mock_k8s_cluster_with_gpus):
            simulator = mock_k8s_cluster_with_gpus
            # simulator has 2 GPU nodes with 8 H100s each
    """
    simulator = create_gpu_cluster(num_nodes=2, gpus_per_node=8, gpu_type='H100')
    patch_kubernetes_apis(simulator, monkeypatch)
    patch_kubernetes_utils(simulator, monkeypatch)
    yield simulator


@pytest.fixture
def mock_k8s_multinode_cluster(
        monkeypatch) -> Generator[KubernetesClusterSimulator, None, None]:
    """Fixture providing a 4-node cluster (2 CPU, 2 GPU).

    Usage:
        def test_multinode_feature(mock_k8s_multinode_cluster):
            simulator = mock_k8s_multinode_cluster
    """
    simulator = KubernetesClusterSimulator(context_name='mock-k8s-multinode')
    # CPU nodes
    simulator.add_node(name='cpu-node-1', cpu='16', memory='64Gi')
    simulator.add_node(name='cpu-node-2', cpu='16', memory='64Gi')
    # GPU nodes
    simulator.add_node(name='gpu-node-1',
                       cpu='48',
                       memory='256Gi',
                       gpu_count=4,
                       gpu_type='V100')
    simulator.add_node(name='gpu-node-2',
                       cpu='48',
                       memory='256Gi',
                       gpu_count=4,
                       gpu_type='V100')

    patch_kubernetes_apis(simulator, monkeypatch)
    patch_kubernetes_utils(simulator, monkeypatch)
    yield simulator


@pytest.fixture
def mock_k8s_heterogeneous_cluster(
        monkeypatch) -> Generator[KubernetesClusterSimulator, None, None]:
    """Fixture providing a heterogeneous cluster with mixed GPU types.

    Includes: 2 CPU nodes, 2 V100 nodes, 2 H100 nodes

    Usage:
        def test_heterogeneous_feature(mock_k8s_heterogeneous_cluster):
            simulator = mock_k8s_heterogeneous_cluster
    """
    simulator = create_heterogeneous_cluster()
    patch_kubernetes_apis(simulator, monkeypatch)
    patch_kubernetes_utils(simulator, monkeypatch)
    yield simulator


@pytest.fixture
def mock_k8s_cluster_empty(
        monkeypatch) -> Generator[KubernetesClusterSimulator, None, None]:
    """Fixture providing an empty cluster (no nodes).

    Useful for testing error cases when no resources are available.

    Usage:
        def test_no_resources_error(mock_k8s_cluster_empty):
            simulator = mock_k8s_cluster_empty
            # Expect resource errors
    """
    simulator = KubernetesClusterSimulator(context_name='mock-k8s-empty')
    patch_kubernetes_apis(simulator, monkeypatch)
    patch_kubernetes_utils(simulator, monkeypatch)
    yield simulator


@pytest.fixture
def k8s_cluster_factory(
        monkeypatch
) -> Generator[Callable[..., KubernetesClusterSimulator], None, None]:
    """Factory fixture for creating custom cluster configurations.

    Usage:
        def test_custom_cluster(k8s_cluster_factory):
            simulator = k8s_cluster_factory(
                num_nodes=3,
                gpus_per_node=4,
                gpu_type='A100',
            )
    """
    simulators: List[KubernetesClusterSimulator] = []

    def factory(
        num_nodes: int = 1,
        cpus_per_node: str = '16',
        memory_per_node: str = '64Gi',
        gpus_per_node: int = 0,
        gpu_type: Optional[str] = None,
        context_name: str = 'mock-k8s-custom',
    ) -> KubernetesClusterSimulator:
        simulator = KubernetesClusterSimulator(context_name=context_name)
        for i in range(num_nodes):
            simulator.add_node(
                name=f'node-{i+1}',
                cpu=cpus_per_node,
                memory=memory_per_node,
                gpu_count=gpus_per_node,
                gpu_type=gpu_type,
            )
        patch_kubernetes_apis(simulator, monkeypatch)
        patch_kubernetes_utils(simulator, monkeypatch)
        simulators.append(simulator)
        return simulator

    yield factory


# =============================================================================
# Helper Functions for Test Setup
# =============================================================================


def create_test_pod(
    name: str,
    namespace: str = 'default',
    cpus: str = '1',
    memory: str = '1Gi',
    gpus: int = 0,
    labels: Optional[Dict[str, str]] = None,
    node_selector: Optional[Dict[str, str]] = None,
    volumes: Optional[List[MockVolume]] = None,
    image: str = 'ubuntu:latest',
) -> MockPod:
    """Create a test pod with specified resources.

    Args:
        name: Pod name
        namespace: Kubernetes namespace
        cpus: CPU request (e.g., '1', '0.5', '2')
        memory: Memory request (e.g., '1Gi', '512Mi')
        gpus: Number of GPUs requested
        labels: Pod labels
        node_selector: Node selector labels
        volumes: Volumes to mount
        image: Container image

    Returns:
        MockPod instance
    """
    resources = MockResourceRequirements(
        requests={
            'cpu': cpus,
            'memory': memory
        },
        limits={
            'cpu': cpus,
            'memory': memory
        },
    )
    if gpus > 0:
        resources.requests['nvidia.com/gpu'] = str(gpus)
        resources.limits['nvidia.com/gpu'] = str(gpus)

    container = MockContainer(
        name='main',
        image=image,
        resources=resources,
    )

    return MockPod(
        name=name,
        namespace=namespace,
        labels=labels or {},
        containers=[container],
        node_selector=node_selector,
        volumes=volumes or [],
    )


def create_ray_cluster_pods(
    cluster_name: str,
    num_workers: int = 1,
    namespace: str = 'default',
    cpus_per_node: str = '4',
    memory_per_node: str = '16Gi',
    gpus_per_node: int = 0,
    gpu_type: Optional[str] = None,
) -> List[MockPod]:
    """Create pods for a Ray cluster (head + workers).

    Args:
        cluster_name: Name of the Ray cluster
        num_workers: Number of worker pods
        namespace: Kubernetes namespace
        cpus_per_node: CPU per pod
        memory_per_node: Memory per pod
        gpus_per_node: GPUs per pod
        gpu_type: Type of GPU (for node selector)

    Returns:
        List of MockPod instances [head, worker1, worker2, ...]
    """
    pods = []

    # Common labels for Ray cluster
    base_labels = {
        'ray-cluster-name': cluster_name,
        'skypilot-cluster-name': cluster_name,
    }

    node_selector = {}
    if gpu_type:
        node_selector['skypilot.co/accelerator'] = gpu_type.lower()

    # Create head pod
    head_labels = {
        **base_labels, 'ray-node-type': 'head',
        'component': f'{cluster_name}-head'
    }
    head_pod = create_test_pod(
        name=f'{cluster_name}-head',
        namespace=namespace,
        cpus=cpus_per_node,
        memory=memory_per_node,
        gpus=gpus_per_node,
        labels=head_labels,
        node_selector=node_selector if node_selector else None,
    )
    pods.append(head_pod)

    # Create worker pods
    for i in range(num_workers):
        worker_labels = {**base_labels, 'ray-node-type': 'worker'}
        worker_pod = create_test_pod(
            name=f'{cluster_name}-worker-{i}',
            namespace=namespace,
            cpus=cpus_per_node,
            memory=memory_per_node,
            gpus=gpus_per_node,
            labels=worker_labels,
            node_selector=node_selector if node_selector else None,
        )
        pods.append(worker_pod)

    return pods


def create_test_pvc(
    name: str,
    namespace: str = 'default',
    storage_size: str = '10Gi',
    storage_class: str = 'standard',
    access_modes: Optional[List[str]] = None,
    bound: bool = True,
) -> MockPVC:
    """Create a test PVC.

    Args:
        name: PVC name
        namespace: Kubernetes namespace
        storage_size: Storage size (e.g., '10Gi')
        storage_class: Storage class name
        access_modes: Access modes (default: ReadWriteOnce)
        bound: Whether to mark PVC as bound

    Returns:
        MockPVC instance
    """
    pvc = MockPVC(
        name=name,
        namespace=namespace,
        storage_class=storage_class,
        storage_size=storage_size,
        access_modes=access_modes,
    )
    if bound:
        pvc.bind()
    return pvc


def create_test_service(
    name: str,
    namespace: str = 'default',
    service_type: str = 'ClusterIP',
    port: int = 80,
    target_port: int = 8080,
    selector: Optional[Dict[str, str]] = None,
) -> MockService:
    """Create a test service.

    Args:
        name: Service name
        namespace: Kubernetes namespace
        service_type: Service type (ClusterIP, NodePort, LoadBalancer)
        port: Service port
        target_port: Target port on pods
        selector: Pod selector

    Returns:
        MockService instance
    """
    return MockService(
        name=name,
        namespace=namespace,
        service_type=service_type,
        ports=[MockServicePort(port=port, target_port=target_port)],
        selector=selector or {},
    )


# =============================================================================
# Scenario-Based Fixtures
# =============================================================================


@pytest.fixture
def mock_k8s_with_running_cluster(
    mock_k8s_cluster_with_gpus
) -> Generator[Tuple[KubernetesClusterSimulator, List[MockPod]], None, None]:
    """Fixture with a cluster and already-running Ray pods.

    Provides a tuple of (simulator, [head_pod, worker_pods...])

    Usage:
        def test_existing_cluster(mock_k8s_with_running_cluster):
            simulator, pods = mock_k8s_with_running_cluster
            head_pod = pods[0]
            worker_pods = pods[1:]
    """
    simulator = mock_k8s_cluster_with_gpus

    # Create and schedule Ray cluster pods
    pods = create_ray_cluster_pods(
        cluster_name='test-cluster',
        num_workers=2,
        cpus_per_node='8',
        memory_per_node='32Gi',
        gpus_per_node=2,
    )

    for pod in pods:
        simulator.create_pod(pod)

    yield simulator, pods


@pytest.fixture
def mock_k8s_with_pvc(
    mock_k8s_cluster
) -> Generator[Tuple[KubernetesClusterSimulator, MockPVC], None, None]:
    """Fixture with a cluster and a bound PVC.

    Usage:
        def test_with_pvc(mock_k8s_with_pvc):
            simulator, pvc = mock_k8s_with_pvc
    """
    simulator = mock_k8s_cluster

    pvc = create_test_pvc(
        name='test-pvc',
        storage_size='20Gi',
        bound=True,
    )
    simulator.create_pvc(pvc)

    yield simulator, pvc


@pytest.fixture
def mock_k8s_multi_context(
        monkeypatch) -> Generator[Dict[str, KubernetesClusterSimulator], None, None]:
    """Fixture with multiple K8s contexts.

    Provides a dict mapping context names to simulators.

    Usage:
        def test_multi_context(mock_k8s_multi_context):
            contexts = mock_k8s_multi_context
            prod_sim = contexts['prod-cluster']
            dev_sim = contexts['dev-cluster']
    """
    simulators = {
        'prod-cluster':
            create_gpu_cluster(
                context_name='prod-cluster',
                num_nodes=4,
                gpus_per_node=8,
                gpu_type='H100',
            ),
        'dev-cluster':
            create_single_node_cluster(
                context_name='dev-cluster',
                cpu='8',
                memory='32Gi',
            ),
        'staging-cluster':
            create_gpu_cluster(
                context_name='staging-cluster',
                num_nodes=2,
                gpus_per_node=4,
                gpu_type='V100',
            ),
    }

    # Patch context listing to return all contexts
    def mock_list_contexts():
        contexts = [{'name': name} for name in simulators.keys()]
        return (contexts, contexts[0])

    monkeypatch.setattr('sky.adaptors.kubernetes.list_kube_config_contexts',
                        mock_list_contexts)

    def mock_get_all_context_names():
        return list(simulators.keys())

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_all_kube_context_names',
                        mock_get_all_context_names)

    # Patch the main simulator APIs (use first context by default)
    primary_sim = list(simulators.values())[0]
    patch_kubernetes_apis(primary_sim, monkeypatch)
    patch_kubernetes_utils(primary_sim, monkeypatch)

    yield simulators
