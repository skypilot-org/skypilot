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


class KubernetesAutoscalerType(enum.Enum):
    """Enum for the different types of cluster autoscalers for Kubernetes."""
    GKE = 'gke'
    KARPENTER = 'karpenter'
    COREWEAVE = 'coreweave'
    GENERIC = 'generic'

    def emits_autoscale_event(self) -> bool:
        """Returns whether specific autoscaler emits the event reason
        TriggeredScaleUp."""
        return self not in {self.KARPENTER}
