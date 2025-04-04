"""Data Models for SkyPilot."""

import collections
import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass
class User:
    # User hash
    id: str
    # Display name of the user
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'name': self.name}


RealtimeGpuAvailability = collections.namedtuple(
    'RealtimeGpuAvailability', ['gpu', 'counts', 'capacity', 'available'])


@dataclasses.dataclass
class KubernetesNodeInfo:
    """Dataclass to store Kubernetes node information."""
    name: str
    accelerator_type: Optional[str]
    # Resources available on the node. E.g., {'nvidia.com/gpu': '2'}
    total: Dict[str, int]
    free: Dict[str, int]
