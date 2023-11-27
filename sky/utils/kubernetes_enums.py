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
