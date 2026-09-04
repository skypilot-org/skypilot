"""Kubernetes enums for SkyPilot."""
import enum


# TODO(kevin): Remove this enum in v0.13.0.
class KubernetesNetworkingMode(enum.Enum):
    """Enum for the different types of networking modes for accessing pods.
    """
    NODEPORT = 'nodeport'
    PORTFORWARD = 'portforward'


class KubernetesServiceType(enum.Enum):
    """Enum for the different types of services."""
    NODEPORT = 'NodePort'
    CLUSTERIP = 'ClusterIP'


class KubernetesPortMode(enum.Enum):
    """Enum for the different types of modes supported for opening
    ports on Kubernetes.
    """
    INGRESS = 'ingress'
    LOADBALANCER = 'loadbalancer'
    PODIP = 'podip'


class KubernetesRdmaMode(enum.Enum):
    """How RDMA NICs are delivered to pods on an RDMA-capable cluster.

    The delivery models are mutually exclusive and each drives several pod-spec
    decisions at once, so they are expressed as one mode rather than as
    independent switches. Names match the models Oracle documents for OKE (see
    docs/using-rdma-network-interfaces-in-manifests.md). The mechanism is not
    OCI-specific, but it is currently only wired for the network type where it
    has a validated user.

    Only the non-default model needs naming: leaving this unset already means
    "share the node's network namespace and reach the RDMA devices through a
    /dev/infiniband hostPath", which is what SkyPilot has always done on an
    RDMA-capable cluster.
    """
    # Keep the pod's own network namespace; the RDMA NICs arrive as SR-IOV
    # virtual functions, requested as an extended resource and attached by
    # Multus.
    SRIOV = 'sriov'


class KubernetesAutoscalerType(enum.Enum):
    """Enum for the different types of cluster autoscalers for Kubernetes."""
    GKE = 'gke'
    KARPENTER = 'karpenter'
    COREWEAVE = 'coreweave'
    NEBIUS = 'nebius'
    GENERIC = 'generic'

    def emits_autoscale_event(self) -> bool:
        """Returns whether specific autoscaler emits the event reason
        TriggeredScaleUp."""
        return self not in {self.KARPENTER}
