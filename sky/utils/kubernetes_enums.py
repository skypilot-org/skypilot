"""Kubernetes enums for SkyPilot."""
import enum


class KubernetesNetworkingMode(enum.Enum):
    """Enum for the different types of networking modes for accessing
    jump pods.
    """
    NODEPORT = 'nodeport'
    PORTFORWARD = 'portforward'

    @classmethod
    def from_str(cls, mode: str) -> 'KubernetesNetworkingMode':
        """Returns the enum value for the given string."""
        if mode.lower() == cls.NODEPORT.value:
            return cls.NODEPORT
        elif mode.lower() == cls.PORTFORWARD.value:
            return cls.PORTFORWARD
        else:
            raise ValueError(f'Unsupported kubernetes networking mode: '
                             f'{mode}. The mode must be either '
                             f'\'{cls.PORTFORWARD.value}\' or '
                             f'\'{cls.NODEPORT.value}\'. ')


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
    GENERIC = 'generic'

    @classmethod
    def from_str(cls, autoscaler: str) -> 'KubernetesAutoscalerType':
        """Returns the enum value for the given string."""
        if autoscaler.lower() == cls.GKE.value:
            return cls.GKE
        elif autoscaler.lower() == cls.KARPENTER.value:
            return cls.KARPENTER
        elif autoscaler.lower() == cls.GENERIC.value:
            return cls.GENERIC
        else:
            raise ValueError(f'Unsupported kubernetes autoscaler type: '
                             f'{autoscaler}. The autoscaler must be either '
                             f'\'{cls.GKE.value}\', '
                             f'\'{cls.KARPENTER.value}\', or '
                             f'\'{cls.GENERIC.value}\'. ')
