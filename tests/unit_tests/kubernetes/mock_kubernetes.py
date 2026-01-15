"""Mock Kubernetes infrastructure for integration testing.

This module provides a comprehensive mocking infrastructure that allows
smoke tests that currently require real K8s clusters with GPUs to run
without actual K8s infrastructure.

Key components:
- MockNode: Simulates K8s nodes with CPU/GPU/memory resources
- MockPod: Simulates pods with lifecycle states
- MockPVC: Simulates persistent volume claims
- MockService: Simulates K8s services
- KubernetesClusterSimulator: Stateful cluster simulator
- MockKubernetesAPI: Mock API client that uses the simulator
"""

import copy
from dataclasses import dataclass
from dataclasses import field
import datetime
import enum
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from unittest import mock
import uuid

# Pod phases
PHASE_PENDING = 'Pending'
PHASE_RUNNING = 'Running'
PHASE_SUCCEEDED = 'Succeeded'
PHASE_FAILED = 'Failed'
PHASE_UNKNOWN = 'Unknown'

# PVC phases
PVC_BOUND = 'Bound'
PVC_PENDING = 'Pending'
PVC_LOST = 'Lost'

# Default resource allocatable on nodes
DEFAULT_CPU_ALLOCATABLE = '16'
DEFAULT_MEMORY_ALLOCATABLE = '64Gi'


class PodConditionType(enum.Enum):
    """Pod condition types."""
    POD_SCHEDULED = 'PodScheduled'
    CONTAINERS_READY = 'ContainersReady'
    INITIALIZED = 'Initialized'
    READY = 'Ready'


@dataclass
class MockMetadata:
    """Mock Kubernetes metadata object."""
    name: str
    namespace: str = 'default'
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    creation_timestamp: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow)
    deletion_timestamp: Optional[datetime.datetime] = None
    finalizers: List[str] = field(default_factory=list)
    resource_version: str = '1'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'namespace': self.namespace,
            'uid': self.uid,
            'labels': self.labels,
            'annotations': self.annotations,
            'creation_timestamp': self.creation_timestamp.isoformat(),
            'deletion_timestamp': (
                self.deletion_timestamp.isoformat() if self.deletion_timestamp else None
            ),
            'finalizers': self.finalizers,
            'resource_version': self.resource_version,
        }


@dataclass
class MockResourceRequirements:
    """Mock resource requirements for containers."""
    requests: Dict[str, str] = field(default_factory=dict)
    limits: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'requests': self.requests,
            'limits': self.limits,
        }


@dataclass
class MockContainerPort:
    """Mock container port."""
    container_port: int
    name: Optional[str] = None
    protocol: str = 'TCP'


@dataclass
class MockVolumeMount:
    """Mock volume mount."""
    name: str
    mount_path: str
    read_only: bool = False
    sub_path: Optional[str] = None


@dataclass
class MockContainer:
    """Mock container spec."""
    name: str
    image: str = 'ubuntu:latest'
    command: Optional[List[str]] = None
    args: Optional[List[str]] = None
    resources: MockResourceRequirements = field(
        default_factory=MockResourceRequirements)
    ports: List[MockContainerPort] = field(default_factory=list)
    volume_mounts: List[MockVolumeMount] = field(default_factory=list)
    env: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'image': self.image,
            'command': self.command,
            'args': self.args,
            'resources': self.resources.to_dict(),
            'ports': [{
                'container_port': p.container_port,
                'name': p.name,
                'protocol': p.protocol
            } for p in self.ports],
            'volume_mounts': [{
                'name': m.name,
                'mount_path': m.mount_path,
                'read_only': m.read_only
            } for m in self.volume_mounts],
            'env': self.env,
        }


@dataclass
class MockContainerStatus:
    """Mock container status."""
    name: str
    ready: bool = False
    started: bool = False
    restart_count: int = 0
    image: str = 'ubuntu:latest'
    image_id: str = ''
    container_id: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=lambda: {'waiting': {}})
    last_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockPodCondition:
    """Mock pod condition."""
    type: str
    status: str = 'True'
    last_transition_time: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow)
    reason: Optional[str] = None
    message: Optional[str] = None


@dataclass
class MockVolume:
    """Mock volume spec."""
    name: str
    persistent_volume_claim: Optional[Dict[str, str]] = None
    config_map: Optional[Dict[str, Any]] = None
    secret: Optional[Dict[str, Any]] = None
    empty_dir: Optional[Dict[str, Any]] = None
    host_path: Optional[Dict[str, str]] = None


@dataclass
class MockPodSpec:
    """Mock pod spec."""
    containers: List[MockContainer] = field(default_factory=list)
    init_containers: List[MockContainer] = field(default_factory=list)
    volumes: List[MockVolume] = field(default_factory=list)
    node_name: Optional[str] = None
    node_selector: Dict[str, str] = field(default_factory=dict)
    service_account_name: str = 'default'
    restart_policy: str = 'Always'
    tolerations: List[Dict[str, Any]] = field(default_factory=list)
    affinity: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'containers': [c.to_dict() for c in self.containers],
            'init_containers': [c.to_dict() for c in self.init_containers],
            'volumes': [{
                'name': v.name,
                'persistent_volume_claim': v.persistent_volume_claim,
                'config_map': v.config_map,
                'secret': v.secret,
                'empty_dir': v.empty_dir
            } for v in self.volumes],
            'node_name': self.node_name,
            'node_selector': self.node_selector,
            'service_account_name': self.service_account_name,
            'restart_policy': self.restart_policy,
        }


@dataclass
class MockPodStatus:
    """Mock pod status."""
    phase: str = PHASE_PENDING
    host_ip: Optional[str] = None
    pod_ip: Optional[str] = None
    start_time: Optional[datetime.datetime] = None
    conditions: List[MockPodCondition] = field(default_factory=list)
    container_statuses: List[MockContainerStatus] = field(default_factory=list)
    init_container_statuses: List[MockContainerStatus] = field(default_factory=list)
    reason: Optional[str] = None
    message: Optional[str] = None


class MockPod:
    """Mock Kubernetes Pod object.

    Simulates a K8s pod with realistic behavior including:
    - Lifecycle transitions (Pending -> Running -> Succeeded/Failed)
    - Resource requirements and limits
    - Volume mounts and PVC references
    - Container states
    """

    def __init__(
        self,
        name: str,
        namespace: str = 'default',
        labels: Optional[Dict[str, str]] = None,
        containers: Optional[List[MockContainer]] = None,
        node_name: Optional[str] = None,
        node_selector: Optional[Dict[str, str]] = None,
        volumes: Optional[List[MockVolume]] = None,
    ):
        self.metadata = MockMetadata(
            name=name,
            namespace=namespace,
            labels=labels or {},
        )
        self.spec = MockPodSpec(
            containers=containers or [MockContainer(name='main')],
            node_name=node_name,
            node_selector=node_selector or {},
            volumes=volumes or [],
        )
        self.status = MockPodStatus()
        self._scheduled_time: Optional[float] = None
        self._startup_delay: float = 0.5  # Simulated startup time

    def schedule_to_node(self, node_name: str, host_ip: str) -> None:
        """Schedule this pod to a node."""
        self.spec.node_name = node_name
        self.status.host_ip = host_ip
        self.status.pod_ip = f'10.{hash(self.metadata.name) % 256}.{hash(node_name) % 256}.{hash(self.metadata.uid) % 256}'
        self.status.conditions.append(
            MockPodCondition(
                type=PodConditionType.POD_SCHEDULED.value,
                status='True',
                reason='Scheduled',
            ))
        self._scheduled_time = time.time()

    def start_running(self) -> None:
        """Transition pod to running state."""
        if self.spec.node_name is None:
            raise ValueError('Pod must be scheduled before running')
        self.status.phase = PHASE_RUNNING
        self.status.start_time = datetime.datetime.utcnow()
        for container in self.spec.containers:
            container_status = MockContainerStatus(
                name=container.name,
                ready=True,
                started=True,
                image=container.image,
                container_id=f'containerd://{uuid.uuid4()}',
                state={
                    'running': {
                        'started_at': datetime.datetime.utcnow().isoformat()
                    }
                },
            )
            self.status.container_statuses.append(container_status)
        self.status.conditions.extend([
            MockPodCondition(type=PodConditionType.INITIALIZED.value, status='True'),
            MockPodCondition(type=PodConditionType.CONTAINERS_READY.value,
                             status='True'),
            MockPodCondition(type=PodConditionType.READY.value, status='True'),
        ])

    def fail(self, reason: str = 'Error', message: str = 'Container failed') -> None:
        """Mark the pod as failed."""
        self.status.phase = PHASE_FAILED
        self.status.reason = reason
        self.status.message = message
        for cs in self.status.container_statuses:
            cs.ready = False
            cs.state = {'terminated': {'exit_code': 1, 'reason': reason}}

    def succeed(self) -> None:
        """Mark the pod as succeeded."""
        self.status.phase = PHASE_SUCCEEDED
        for cs in self.status.container_statuses:
            cs.ready = False
            cs.state = {'terminated': {'exit_code': 0, 'reason': 'Completed'}}

    def get_resource_requests(self) -> Dict[str, float]:
        """Get total resource requests for the pod."""
        requests: Dict[str, float] = {'cpu': 0, 'memory': 0}
        for container in self.spec.containers:
            if container.resources.requests:
                if 'cpu' in container.resources.requests:
                    requests['cpu'] += _parse_cpu(container.resources.requests['cpu'])
                if 'memory' in container.resources.requests:
                    requests['memory'] += _parse_memory(
                        container.resources.requests['memory'])
                if 'nvidia.com/gpu' in container.resources.requests:
                    requests['nvidia.com/gpu'] = requests.get(
                        'nvidia.com/gpu', 0) + int(
                            container.resources.requests['nvidia.com/gpu'])
        return requests

    def matches_selector(self, selector: Dict[str, str]) -> bool:
        """Check if pod matches the given label selector."""
        for key, value in selector.items():
            if self.metadata.labels.get(key) != value:
                return False
        return True


@dataclass
class MockNodeAddress:
    """Mock node address."""
    type: str
    address: str


@dataclass
class MockNodeCondition:
    """Mock node condition."""
    type: str
    status: str
    last_heartbeat_time: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow)
    last_transition_time: datetime.datetime = field(
        default_factory=datetime.datetime.utcnow)
    reason: str = ''
    message: str = ''


@dataclass
class MockNodeStatus:
    """Mock node status."""
    capacity: Dict[str, str] = field(default_factory=dict)
    allocatable: Dict[str, str] = field(default_factory=dict)
    conditions: List[MockNodeCondition] = field(default_factory=list)
    addresses: List[MockNodeAddress] = field(default_factory=list)
    node_info: Dict[str, str] = field(default_factory=dict)


class MockNode:
    """Mock Kubernetes Node object.

    Simulates a K8s node with:
    - CPU/memory/GPU capacity and allocatable resources
    - Labels for GPU type, instance type, etc.
    - Node conditions (Ready, MemoryPressure, etc.)
    """

    def __init__(
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None,
        cpu: str = DEFAULT_CPU_ALLOCATABLE,
        memory: str = DEFAULT_MEMORY_ALLOCATABLE,
        gpu_count: int = 0,
        gpu_type: Optional[str] = None,
        tpu_count: int = 0,
        tpu_type: Optional[str] = None,
    ):
        self.metadata = MockMetadata(
            name=name,
            labels=labels or {},
        )

        # Set up capacity and allocatable resources
        capacity = {
            'cpu': cpu,
            'memory': memory,
            'pods': '110',
            'ephemeral-storage': '100Gi',
        }
        allocatable = copy.deepcopy(capacity)

        if gpu_count > 0:
            capacity['nvidia.com/gpu'] = str(gpu_count)
            allocatable['nvidia.com/gpu'] = str(gpu_count)
            if gpu_type:
                self.metadata.labels['skypilot.co/accelerator'] = gpu_type.lower()
                self.metadata.labels[
                    'cloud.google.com/gke-accelerator'] = gpu_type.lower()
                self.metadata.labels['cloud.google.com/gke-accelerator-count'] = str(
                    gpu_count)

        if tpu_count > 0:
            capacity['google.com/tpu'] = str(tpu_count)
            allocatable['google.com/tpu'] = str(tpu_count)
            if tpu_type:
                self.metadata.labels['cloud.google.com/gke-tpu-accelerator'] = tpu_type
                self.metadata.labels['cloud.google.com/gke-tpu-topology'] = '2x2'

        self.status = MockNodeStatus(
            capacity=capacity,
            allocatable=allocatable,
            conditions=[
                MockNodeCondition(type='Ready',
                                  status='True',
                                  reason='KubeletReady',
                                  message='kubelet is ready'),
                MockNodeCondition(type='MemoryPressure', status='False'),
                MockNodeCondition(type='DiskPressure', status='False'),
                MockNodeCondition(type='PIDPressure', status='False'),
            ],
            addresses=[
                MockNodeAddress(type='InternalIP',
                                address=f'10.0.{hash(name) % 256}.{hash(name) % 100}'),
                MockNodeAddress(type='Hostname', address=name),
            ],
            node_info={
                'kubelet_version': 'v1.28.0',
                'container_runtime_version': 'containerd://1.7.0',
                'os_image': 'Ubuntu 22.04 LTS',
                'architecture': 'amd64',
            },
        )

    def is_ready(self) -> bool:
        """Check if node is ready."""
        for condition in self.status.conditions:
            if condition.type == 'Ready':
                return condition.status == 'True'
        return False

    def has_gpu(self, gpu_type: Optional[str] = None) -> bool:
        """Check if node has GPU (optionally of specific type)."""
        gpu_count = int(self.status.allocatable.get('nvidia.com/gpu', '0'))
        if gpu_count == 0:
            return False
        if gpu_type:
            node_gpu_type = self.metadata.labels.get('skypilot.co/accelerator', '')
            return node_gpu_type.lower() == gpu_type.lower()
        return True

    def get_allocatable_gpus(self) -> int:
        """Get number of allocatable GPUs."""
        return int(self.status.allocatable.get('nvidia.com/gpu', '0'))

    def get_allocatable_cpu(self) -> float:
        """Get allocatable CPU cores."""
        return _parse_cpu(self.status.allocatable.get('cpu', '0'))

    def get_allocatable_memory(self) -> float:
        """Get allocatable memory in bytes."""
        return _parse_memory(self.status.allocatable.get('memory', '0'))


@dataclass
class MockPVCSpec:
    """Mock PVC spec."""
    access_modes: List[str] = field(default_factory=lambda: ['ReadWriteOnce'])
    resources: Dict[str, Dict[str, str]] = field(
        default_factory=lambda: {'requests': {
            'storage': '10Gi'
        }})
    storage_class_name: Optional[str] = 'standard'
    volume_name: Optional[str] = None
    volume_mode: str = 'Filesystem'


@dataclass
class MockPVCStatus:
    """Mock PVC status."""
    phase: str = PVC_PENDING
    access_modes: List[str] = field(default_factory=list)
    capacity: Dict[str, str] = field(default_factory=dict)


class MockPVC:
    """Mock PersistentVolumeClaim object."""

    def __init__(
        self,
        name: str,
        namespace: str = 'default',
        storage_class: str = 'standard',
        storage_size: str = '10Gi',
        access_modes: Optional[List[str]] = None,
    ):
        self.metadata = MockMetadata(
            name=name,
            namespace=namespace,
        )
        self.spec = MockPVCSpec(
            access_modes=access_modes or ['ReadWriteOnce'],
            resources={'requests': {
                'storage': storage_size
            }},
            storage_class_name=storage_class,
        )
        self.status = MockPVCStatus(phase=PVC_PENDING,)

    def bind(self, volume_name: Optional[str] = None) -> None:
        """Bind the PVC to a volume."""
        self.status.phase = PVC_BOUND
        self.status.access_modes = self.spec.access_modes
        self.status.capacity = self.spec.resources.get('requests', {})
        self.spec.volume_name = volume_name or f'pv-{self.metadata.uid}'


@dataclass
class MockServicePort:
    """Mock service port."""
    port: int
    target_port: Union[int, str]
    protocol: str = 'TCP'
    name: Optional[str] = None
    node_port: Optional[int] = None


@dataclass
class MockServiceSpec:
    """Mock service spec."""
    ports: List[MockServicePort] = field(default_factory=list)
    selector: Dict[str, str] = field(default_factory=dict)
    type: str = 'ClusterIP'
    cluster_ip: Optional[str] = None
    external_ips: List[str] = field(default_factory=list)
    load_balancer_ip: Optional[str] = None


@dataclass
class MockServiceStatus:
    """Mock service status."""
    load_balancer: Dict[str, Any] = field(default_factory=dict)


class MockService:
    """Mock Kubernetes Service object."""

    def __init__(
        self,
        name: str,
        namespace: str = 'default',
        service_type: str = 'ClusterIP',
        ports: Optional[List[MockServicePort]] = None,
        selector: Optional[Dict[str, str]] = None,
    ):
        self.metadata = MockMetadata(
            name=name,
            namespace=namespace,
        )
        self.spec = MockServiceSpec(
            ports=ports or [],
            selector=selector or {},
            type=service_type,
            cluster_ip=f'10.96.{hash(name) % 256}.{hash(name) % 100}',
        )
        self.status = MockServiceStatus()

        if service_type == 'LoadBalancer':
            self.status.load_balancer = {
                'ingress': [{
                    'ip': f'34.{hash(name) % 256}.{hash(name) % 256}.{hash(name) % 100}'
                }]
            }


@dataclass
class MockEvent:
    """Mock Kubernetes Event object."""
    metadata: MockMetadata
    involved_object: Dict[str, str]
    reason: str
    message: str
    type: str = 'Normal'  # Normal or Warning
    count: int = 1
    first_timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    last_timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    source: Dict[str, str] = field(default_factory=lambda: {'component': 'kubelet'})


# Helper functions for parsing resource strings


def _parse_cpu(cpu_str: str) -> float:
    """Parse CPU string to number of cores."""
    if isinstance(cpu_str, (int, float)):
        return float(cpu_str)
    cpu_str = str(cpu_str)
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000
    return float(cpu_str)


def _parse_memory(memory_str: str) -> float:
    """Parse memory string to bytes."""
    if isinstance(memory_str, (int, float)):
        return float(memory_str)
    memory_str = str(memory_str)
    units = {
        'Ki': 1024,
        'Mi': 1024**2,
        'Gi': 1024**3,
        'Ti': 1024**4,
        'K': 1000,
        'M': 1000**2,
        'G': 1000**3,
        'T': 1000**4,
    }
    for suffix, multiplier in units.items():
        if memory_str.endswith(suffix):
            return float(memory_str[:-len(suffix)]) * multiplier
    return float(memory_str)


class KubernetesClusterSimulator:
    """Stateful Kubernetes cluster simulator.

    Maintains cluster state and handles API operations:
    - Node management
    - Pod scheduling and lifecycle
    - PVC binding
    - Service creation
    - Event generation
    """

    def __init__(
        self,
        context_name: str = 'mock-k8s-context',
        namespace: str = 'default',
    ):
        self.context_name = context_name
        self.namespace = namespace
        self._lock = threading.RLock()

        # Cluster state
        self._nodes: Dict[str, MockNode] = {}
        self._pods: Dict[str, Dict[str, MockPod]] = {}  # namespace -> name -> pod
        self._pvcs: Dict[str, Dict[str, MockPVC]] = {}  # namespace -> name -> pvc
        self._services: Dict[str,
                             Dict[str,
                                  MockService]] = {}  # namespace -> name -> service
        self._events: List[MockEvent] = []
        self._service_accounts: Dict[str, Set[str]] = {}  # namespace -> names
        self._roles: Dict[str, Set[str]] = {}  # namespace -> names
        self._role_bindings: Dict[str, Set[str]] = {}  # namespace -> names
        self._cluster_roles: Set[str] = set()
        self._cluster_role_bindings: Set[str] = set()
        self._namespaces: Set[str] = {'default', 'kube-system'}
        self._daemon_sets: Dict[str, Dict[str,
                                          Any]] = {}  # namespace -> name -> daemonset
        self._config_maps: Dict[str, Dict[str,
                                          Any]] = {}  # namespace -> name -> configmap
        self._secrets: Dict[str, Dict[str, Any]] = {}  # namespace -> name -> secret

        # Resource tracking
        self._allocated_resources: Dict[str, Dict[str, float]] = {
        }  # node_name -> resource -> amount

        # Simulation settings
        self._auto_schedule = True  # Auto-schedule pods to available nodes
        self._auto_bind_pvcs = True  # Auto-bind PVCs
        self._startup_delay = 0.0  # Simulated pod startup delay

    def add_node(
        self,
        name: str,
        cpu: str = DEFAULT_CPU_ALLOCATABLE,
        memory: str = DEFAULT_MEMORY_ALLOCATABLE,
        gpu_count: int = 0,
        gpu_type: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> MockNode:
        """Add a node to the cluster."""
        with self._lock:
            node = MockNode(
                name=name,
                cpu=cpu,
                memory=memory,
                gpu_count=gpu_count,
                gpu_type=gpu_type,
                labels=labels,
            )
            self._nodes[name] = node
            self._allocated_resources[name] = {
                'cpu': 0.0,
                'memory': 0.0,
                'nvidia.com/gpu': 0.0,
            }
            return node

    def remove_node(self, name: str) -> None:
        """Remove a node from the cluster."""
        with self._lock:
            if name in self._nodes:
                del self._nodes[name]
            if name in self._allocated_resources:
                del self._allocated_resources[name]

    def get_node(self, name: str) -> Optional[MockNode]:
        """Get a node by name."""
        return self._nodes.get(name)

    def list_nodes(self) -> List[MockNode]:
        """List all nodes."""
        return list(self._nodes.values())

    def create_pod(
        self,
        pod: MockPod,
        namespace: Optional[str] = None,
    ) -> MockPod:
        """Create a pod in the cluster."""
        ns = namespace or pod.metadata.namespace
        with self._lock:
            if ns not in self._pods:
                self._pods[ns] = {}
            self._pods[ns][pod.metadata.name] = pod

            # Auto-schedule if enabled
            if self._auto_schedule:
                self._try_schedule_pod(pod)

            # Add creation event
            self._add_event(
                name=f'{pod.metadata.name}.{uuid.uuid4().hex[:8]}',
                namespace=ns,
                involved_object={
                    'kind': 'Pod',
                    'name': pod.metadata.name,
                    'namespace': ns,
                    'uid': pod.metadata.uid,
                },
                reason='Scheduled' if pod.spec.node_name else 'Pending',
                message=f'Pod {pod.metadata.name} created',
            )

            return pod

    def _try_schedule_pod(self, pod: MockPod) -> bool:
        """Try to schedule a pod to an available node."""
        if pod.spec.node_name:
            return True  # Already scheduled

        requests = pod.get_resource_requests()

        for node_name, node in self._nodes.items():
            if not node.is_ready():
                continue

            # Check node selector
            if pod.spec.node_selector:
                matches = True
                for key, value in pod.spec.node_selector.items():
                    if node.metadata.labels.get(key) != value:
                        matches = False
                        break
                if not matches:
                    continue

            # Check available resources
            allocated = self._allocated_resources.get(node_name, {})
            available_cpu = node.get_allocatable_cpu() - allocated.get('cpu', 0)
            available_memory = node.get_allocatable_memory() - allocated.get(
                'memory', 0)
            available_gpu = node.get_allocatable_gpus() - allocated.get(
                'nvidia.com/gpu', 0)

            if (requests.get('cpu', 0) <= available_cpu and
                    requests.get('memory', 0) <= available_memory and
                    requests.get('nvidia.com/gpu', 0) <= available_gpu):
                # Schedule to this node
                host_ip = next((addr.address
                                for addr in node.status.addresses
                                if addr.type == 'InternalIP'), '10.0.0.1')
                pod.schedule_to_node(node_name, host_ip)

                # Update allocated resources
                self._allocated_resources[node_name]['cpu'] = (allocated.get('cpu', 0) +
                                                               requests.get('cpu', 0))
                self._allocated_resources[node_name]['memory'] = (
                    allocated.get('memory', 0) + requests.get('memory', 0))
                self._allocated_resources[node_name]['nvidia.com/gpu'] = (
                    allocated.get('nvidia.com/gpu', 0) +
                    requests.get('nvidia.com/gpu', 0))

                # Simulate startup
                pod.start_running()
                return True

        return False

    def delete_pod(
        self,
        name: str,
        namespace: str = 'default',
        grace_period: int = 30,
    ) -> Optional[MockPod]:
        """Delete a pod."""
        with self._lock:
            if namespace in self._pods and name in self._pods[namespace]:
                pod = self._pods[namespace][name]

                # Release resources
                if pod.spec.node_name:
                    requests = pod.get_resource_requests()
                    allocated = self._allocated_resources.get(pod.spec.node_name, {})
                    for resource, amount in requests.items():
                        if resource in allocated:
                            allocated[resource] = max(0, allocated[resource] - amount)

                del self._pods[namespace][name]
                return pod
        return None

    def get_pod(self, name: str, namespace: str = 'default') -> Optional[MockPod]:
        """Get a pod by name."""
        return self._pods.get(namespace, {}).get(name)

    def list_pods(
        self,
        namespace: str = 'default',
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
    ) -> List[MockPod]:
        """List pods in a namespace."""
        pods = list(self._pods.get(namespace, {}).values())

        if label_selector:
            # Parse label selector (simple key=value format)
            selector = {}
            for part in label_selector.split(','):
                if '=' in part:
                    key, value = part.split('=', 1)
                    selector[key.strip()] = value.strip()
            pods = [p for p in pods if p.matches_selector(selector)]

        return pods

    def create_pvc(
        self,
        pvc: MockPVC,
        namespace: Optional[str] = None,
    ) -> MockPVC:
        """Create a PVC."""
        ns = namespace or pvc.metadata.namespace
        with self._lock:
            if ns not in self._pvcs:
                self._pvcs[ns] = {}
            self._pvcs[ns][pvc.metadata.name] = pvc

            if self._auto_bind_pvcs:
                pvc.bind()

            return pvc

    def delete_pvc(self, name: str, namespace: str = 'default') -> Optional[MockPVC]:
        """Delete a PVC."""
        with self._lock:
            if namespace in self._pvcs and name in self._pvcs[namespace]:
                pvc = self._pvcs[namespace][name]
                del self._pvcs[namespace][name]
                return pvc
        return None

    def get_pvc(self, name: str, namespace: str = 'default') -> Optional[MockPVC]:
        """Get a PVC by name."""
        return self._pvcs.get(namespace, {}).get(name)

    def list_pvcs(self, namespace: str = 'default') -> List[MockPVC]:
        """List PVCs in a namespace."""
        return list(self._pvcs.get(namespace, {}).values())

    def create_service(
        self,
        service: MockService,
        namespace: Optional[str] = None,
    ) -> MockService:
        """Create a service."""
        ns = namespace or service.metadata.namespace
        with self._lock:
            if ns not in self._services:
                self._services[ns] = {}
            self._services[ns][service.metadata.name] = service
            return service

    def delete_service(self,
                       name: str,
                       namespace: str = 'default') -> Optional[MockService]:
        """Delete a service."""
        with self._lock:
            if namespace in self._services and name in self._services[namespace]:
                service = self._services[namespace][name]
                del self._services[namespace][name]
                return service
        return None

    def get_service(self,
                    name: str,
                    namespace: str = 'default') -> Optional[MockService]:
        """Get a service by name."""
        return self._services.get(namespace, {}).get(name)

    def list_services(self, namespace: str = 'default') -> List[MockService]:
        """List services in a namespace."""
        return list(self._services.get(namespace, {}).values())

    def _add_event(
        self,
        name: str,
        namespace: str,
        involved_object: Dict[str, str],
        reason: str,
        message: str,
        event_type: str = 'Normal',
    ) -> MockEvent:
        """Add an event."""
        event = MockEvent(
            metadata=MockMetadata(name=name, namespace=namespace),
            involved_object=involved_object,
            reason=reason,
            message=message,
            type=event_type,
        )
        self._events.append(event)
        return event

    def list_events(
        self,
        namespace: str = 'default',
        involved_object_name: Optional[str] = None,
    ) -> List[MockEvent]:
        """List events."""
        events = [e for e in self._events if e.metadata.namespace == namespace]
        if involved_object_name:
            events = [
                e for e in events
                if e.involved_object.get('name') == involved_object_name
            ]
        return events

    def create_namespace(self, name: str) -> None:
        """Create a namespace."""
        self._namespaces.add(name)

    def namespace_exists(self, name: str) -> bool:
        """Check if a namespace exists."""
        return name in self._namespaces

    def create_service_account(self, name: str, namespace: str = 'default') -> None:
        """Create a service account."""
        if namespace not in self._service_accounts:
            self._service_accounts[namespace] = set()
        self._service_accounts[namespace].add(name)

    def service_account_exists(self, name: str, namespace: str = 'default') -> bool:
        """Check if a service account exists."""
        return name in self._service_accounts.get(namespace, set())

    def create_role(self, name: str, namespace: str = 'default') -> None:
        """Create a role."""
        if namespace not in self._roles:
            self._roles[namespace] = set()
        self._roles[namespace].add(name)

    def create_role_binding(self, name: str, namespace: str = 'default') -> None:
        """Create a role binding."""
        if namespace not in self._role_bindings:
            self._role_bindings[namespace] = set()
        self._role_bindings[namespace].add(name)

    def create_cluster_role(self, name: str) -> None:
        """Create a cluster role."""
        self._cluster_roles.add(name)

    def create_cluster_role_binding(self, name: str) -> None:
        """Create a cluster role binding."""
        self._cluster_role_bindings.add(name)

    def get_allocated_gpu_qty_by_node(self) -> Dict[str, int]:
        """Get allocated GPU quantity by node."""
        result = {}
        for node_name, allocated in self._allocated_resources.items():
            result[node_name] = int(allocated.get('nvidia.com/gpu', 0))
        return result

    def simulate_pod_failure(
        self,
        name: str,
        namespace: str = 'default',
        reason: str = 'Error',
        message: str = 'Container crashed',
    ) -> None:
        """Simulate a pod failure."""
        pod = self.get_pod(name, namespace)
        if pod:
            pod.fail(reason, message)
            self._add_event(
                name=f'{name}.{uuid.uuid4().hex[:8]}',
                namespace=namespace,
                involved_object={
                    'kind': 'Pod',
                    'name': name,
                    'namespace': namespace,
                },
                reason=reason,
                message=message,
                event_type='Warning',
            )

    def simulate_node_failure(self, node_name: str) -> None:
        """Simulate a node failure."""
        node = self.get_node(node_name)
        if node:
            for condition in node.status.conditions:
                if condition.type == 'Ready':
                    condition.status = 'False'
                    condition.reason = 'KubeletNotReady'
                    condition.message = 'Node is not ready'

    def recover_node(self, node_name: str) -> None:
        """Recover a failed node."""
        node = self.get_node(node_name)
        if node:
            for condition in node.status.conditions:
                if condition.type == 'Ready':
                    condition.status = 'True'
                    condition.reason = 'KubeletReady'
                    condition.message = 'kubelet is ready'


class MockKubernetesApiException(Exception):
    """Mock Kubernetes API exception."""

    def __init__(self, status: int = 404, reason: str = 'Not Found', body: str = ''):
        self.status = status
        self.reason = reason
        self.body = body
        super().__init__(f'{status} {reason}: {body}')


class MockCoreV1Api:
    """Mock Kubernetes CoreV1Api.

    Provides mock implementations of the CoreV1Api methods
    used by SkyPilot.
    """

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator

    def list_node(self, **kwargs) -> mock.MagicMock:
        """List nodes."""
        nodes = self._sim.list_nodes()
        result = mock.MagicMock()
        result.items = nodes
        return result

    def read_node(self, name: str, **kwargs) -> MockNode:
        """Read a node."""
        node = self._sim.get_node(name)
        if not node:
            raise MockKubernetesApiException(404, 'Not Found', f'Node {name} not found')
        return node

    def list_namespaced_pod(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        **kwargs,
    ) -> mock.MagicMock:
        """List pods in a namespace."""
        pods = self._sim.list_pods(namespace, label_selector, field_selector)
        result = mock.MagicMock()
        result.items = pods
        return result

    def read_namespaced_pod(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> MockPod:
        """Read a pod."""
        pod = self._sim.get_pod(name, namespace)
        if not pod:
            raise MockKubernetesApiException(404, 'Not Found', f'Pod {name} not found')
        return pod

    def create_namespaced_pod(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> MockPod:
        """Create a pod."""
        # Convert body to MockPod if needed
        if isinstance(body, MockPod):
            pod = body
        else:
            pod = MockPod(
                name=body.get('metadata', {}).get('name',
                                                  f'pod-{uuid.uuid4().hex[:8]}'),
                namespace=namespace,
                labels=body.get('metadata', {}).get('labels', {}),
            )
        return self._sim.create_pod(pod, namespace)

    def delete_namespaced_pod(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """Delete a pod."""
        pod = self._sim.delete_pod(name, namespace)
        if not pod:
            raise MockKubernetesApiException(404, 'Not Found', f'Pod {name} not found')
        result = mock.MagicMock()
        result.status = 'Success'
        return result

    def patch_namespaced_pod(
        self,
        name: str,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> MockPod:
        """Patch a pod."""
        pod = self._sim.get_pod(name, namespace)
        if not pod:
            raise MockKubernetesApiException(404, 'Not Found', f'Pod {name} not found')
        # Apply patches (simplified)
        if isinstance(body, dict):
            if 'metadata' in body and 'labels' in body['metadata']:
                pod.metadata.labels.update(body['metadata']['labels'])
        return pod

    def list_namespaced_persistent_volume_claim(
        self,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """List PVCs."""
        pvcs = self._sim.list_pvcs(namespace)
        result = mock.MagicMock()
        result.items = pvcs
        return result

    def read_namespaced_persistent_volume_claim(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> MockPVC:
        """Read a PVC."""
        pvc = self._sim.get_pvc(name, namespace)
        if not pvc:
            raise MockKubernetesApiException(404, 'Not Found', f'PVC {name} not found')
        return pvc

    def create_namespaced_persistent_volume_claim(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> MockPVC:
        """Create a PVC."""
        if isinstance(body, MockPVC):
            pvc = body
        else:
            pvc = MockPVC(
                name=body.get('metadata', {}).get('name',
                                                  f'pvc-{uuid.uuid4().hex[:8]}'),
                namespace=namespace,
            )
        return self._sim.create_pvc(pvc, namespace)

    def delete_namespaced_persistent_volume_claim(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """Delete a PVC."""
        pvc = self._sim.delete_pvc(name, namespace)
        if not pvc:
            raise MockKubernetesApiException(404, 'Not Found', f'PVC {name} not found')
        result = mock.MagicMock()
        result.status = 'Success'
        return result

    def list_namespaced_service(
        self,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """List services."""
        services = self._sim.list_services(namespace)
        result = mock.MagicMock()
        result.items = services
        return result

    def read_namespaced_service(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> MockService:
        """Read a service."""
        service = self._sim.get_service(name, namespace)
        if not service:
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'Service {name} not found')
        return service

    def create_namespaced_service(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> MockService:
        """Create a service."""
        if isinstance(body, MockService):
            service = body
        else:
            service = MockService(
                name=body.get('metadata', {}).get('name',
                                                  f'svc-{uuid.uuid4().hex[:8]}'),
                namespace=namespace,
            )
        return self._sim.create_service(service, namespace)

    def delete_namespaced_service(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """Delete a service."""
        service = self._sim.delete_service(name, namespace)
        if not service:
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'Service {name} not found')
        result = mock.MagicMock()
        result.status = 'Success'
        return result

    def list_namespaced_event(
        self,
        namespace: str,
        field_selector: Optional[str] = None,
        **kwargs,
    ) -> mock.MagicMock:
        """List events."""
        involved_object_name = None
        if field_selector and 'involvedObject.name=' in field_selector:
            match = re.search(r'involvedObject\.name=([^,]+)', field_selector)
            if match:
                involved_object_name = match.group(1)
        events = self._sim.list_events(namespace, involved_object_name)
        result = mock.MagicMock()
        result.items = events
        return result

    def list_namespace(self, **kwargs) -> mock.MagicMock:
        """List namespaces."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=ns))
            for ns in self._sim._namespaces
        ]
        return result

    def create_namespace(self, body: Any, **kwargs) -> mock.MagicMock:
        """Create a namespace."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_namespace(name)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name)
        return result

    def read_namespace(self, name: str, **kwargs) -> mock.MagicMock:
        """Read a namespace."""
        if not self._sim.namespace_exists(name):
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'Namespace {name} not found')
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name)
        return result

    def list_namespaced_service_account(
        self,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """List service accounts."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name, namespace=namespace))
            for name in self._sim._service_accounts.get(namespace, set())
        ]
        return result

    def create_namespaced_service_account(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> mock.MagicMock:
        """Create a service account."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_service_account(name, namespace)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def read_namespaced_config_map(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """Read a configmap."""
        if namespace not in self._sim._config_maps or name not in self._sim._config_maps[
                namespace]:
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'ConfigMap {name} not found')
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        result.data = self._sim._config_maps[namespace][name].get('data', {})
        return result

    def create_namespaced_config_map(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> mock.MagicMock:
        """Create a configmap."""
        if namespace not in self._sim._config_maps:
            self._sim._config_maps[namespace] = {}
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim._config_maps[namespace][name] = body if isinstance(body, dict) else {
            'data': {}
        }
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def read_namespaced_secret(
        self,
        name: str,
        namespace: str,
        **kwargs,
    ) -> mock.MagicMock:
        """Read a secret."""
        if namespace not in self._sim._secrets or name not in self._sim._secrets[
                namespace]:
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'Secret {name} not found')
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        result.data = self._sim._secrets[namespace][name].get('data', {})
        return result

    def create_namespaced_secret(
        self,
        namespace: str,
        body: Any,
        **kwargs,
    ) -> mock.MagicMock:
        """Create a secret."""
        if namespace not in self._sim._secrets:
            self._sim._secrets[namespace] = {}
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim._secrets[namespace][name] = body if isinstance(body, dict) else {
            'data': {}
        }
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result


class MockRbacAuthorizationV1Api:
    """Mock RbacAuthorizationV1Api."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator

    def list_namespaced_role(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List roles."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name, namespace=namespace))
            for name in self._sim._roles.get(namespace, set())
        ]
        return result

    def create_namespaced_role(self, namespace: str, body: Any,
                               **kwargs) -> mock.MagicMock:
        """Create a role."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_role(name, namespace)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def list_namespaced_role_binding(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List role bindings."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name, namespace=namespace))
            for name in self._sim._role_bindings.get(namespace, set())
        ]
        return result

    def create_namespaced_role_binding(self, namespace: str, body: Any,
                                       **kwargs) -> mock.MagicMock:
        """Create a role binding."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_role_binding(name, namespace)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def list_cluster_role(self, **kwargs) -> mock.MagicMock:
        """List cluster roles."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name))
            for name in self._sim._cluster_roles
        ]
        return result

    def create_cluster_role(self, body: Any, **kwargs) -> mock.MagicMock:
        """Create a cluster role."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_cluster_role(name)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name)
        return result

    def list_cluster_role_binding(self, **kwargs) -> mock.MagicMock:
        """List cluster role bindings."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name))
            for name in self._sim._cluster_role_bindings
        ]
        return result

    def create_cluster_role_binding(self, body: Any, **kwargs) -> mock.MagicMock:
        """Create a cluster role binding."""
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim.create_cluster_role_binding(name)
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name)
        return result


class MockAppsV1Api:
    """Mock AppsV1Api."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator

    def list_namespaced_deployment(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List deployments."""
        result = mock.MagicMock()
        result.items = []
        return result

    def list_namespaced_daemon_set(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List daemonsets."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name, namespace=namespace))
            for name in self._sim._daemon_sets.get(namespace, {}).keys()
        ]
        return result

    def read_namespaced_daemon_set(self, name: str, namespace: str,
                                   **kwargs) -> mock.MagicMock:
        """Read a daemonset."""
        if namespace not in self._sim._daemon_sets or name not in self._sim._daemon_sets[
                namespace]:
            raise MockKubernetesApiException(404, 'Not Found',
                                             f'DaemonSet {name} not found')
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def create_namespaced_daemon_set(self, namespace: str, body: Any,
                                     **kwargs) -> mock.MagicMock:
        """Create a daemonset."""
        if namespace not in self._sim._daemon_sets:
            self._sim._daemon_sets[namespace] = {}
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._sim._daemon_sets[namespace][name] = body
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result

    def patch_namespaced_daemon_set(self, name: str, namespace: str, body: Any,
                                    **kwargs) -> mock.MagicMock:
        """Patch a daemonset."""
        if namespace not in self._sim._daemon_sets:
            self._sim._daemon_sets[namespace] = {}
        self._sim._daemon_sets[namespace][name] = body
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result


class MockNetworkingV1Api:
    """Mock NetworkingV1Api."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator
        self._ingresses: Dict[str, Dict[str, Any]] = {}
        self._ingress_classes: List[Any] = []

    def list_ingress_class(self, **kwargs) -> mock.MagicMock:
        """List ingress classes."""
        result = mock.MagicMock()
        result.items = self._ingress_classes
        return result

    def list_namespaced_ingress(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List ingresses."""
        result = mock.MagicMock()
        result.items = list(self._ingresses.get(namespace, {}).values())
        return result

    def create_namespaced_ingress(self, namespace: str, body: Any,
                                  **kwargs) -> mock.MagicMock:
        """Create an ingress."""
        if namespace not in self._ingresses:
            self._ingresses[namespace] = {}
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._ingresses[namespace][name] = body
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result


class MockStorageV1Api:
    """Mock StorageV1Api."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator
        self._storage_classes = ['standard', 'premium-rwo']

    def list_storage_class(self, **kwargs) -> mock.MagicMock:
        """List storage classes."""
        result = mock.MagicMock()
        result.items = [
            mock.MagicMock(metadata=MockMetadata(name=name))
            for name in self._storage_classes
        ]
        return result


class MockBatchV1Api:
    """Mock BatchV1Api."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def list_namespaced_job(self, namespace: str, **kwargs) -> mock.MagicMock:
        """List jobs."""
        result = mock.MagicMock()
        result.items = list(self._jobs.get(namespace, {}).values())
        return result

    def create_namespaced_job(self, namespace: str, body: Any,
                              **kwargs) -> mock.MagicMock:
        """Create a job."""
        if namespace not in self._jobs:
            self._jobs[namespace] = {}
        name = body.get('metadata', {}).get('name') if isinstance(
            body, dict) else body.metadata.name
        self._jobs[namespace][name] = body
        result = mock.MagicMock()
        result.metadata = MockMetadata(name=name, namespace=namespace)
        return result


class MockWatch:
    """Mock Kubernetes Watch object."""

    def __init__(self, simulator: KubernetesClusterSimulator):
        self._sim = simulator

    def stream(self, func: Callable, *args, **kwargs):
        """Stream events (yields immediately for tests)."""
        # In tests, we don't actually stream - just return empty
        return iter([])


def create_mock_kubernetes_api(simulator: KubernetesClusterSimulator) -> Dict[str, Any]:
    """Create a complete set of mock Kubernetes APIs.

    Returns a dictionary mapping API names to mock API instances.
    """
    return {
        'core_api': MockCoreV1Api(simulator),
        'auth_api': MockRbacAuthorizationV1Api(simulator),
        'apps_api': MockAppsV1Api(simulator),
        'networking_api': MockNetworkingV1Api(simulator),
        'storage_api': MockStorageV1Api(simulator),
        'batch_api': MockBatchV1Api(simulator),
        'watch': MockWatch(simulator),
    }
