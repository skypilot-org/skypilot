"""Data Models for SkyPilot."""

import collections
import dataclasses
import json
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class User:
    """Dataclass to store user information."""
    # User hash
    id: str
    # Display name of the user
    name: Optional[str] = None
    # Role of the user
    # default role is admin, to keep backward compatibility
    role: str = 'admin'

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'name': self.name, 'role': self.role}


@dataclasses.dataclass
class Role:
    """Dataclass to store role information."""
    name: str
    black_list: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
        return {'name': self.name, 'black_list': json.dumps(self.black_list)}

    def has_permission(self, path: str, method: str) -> bool:
        for item in self.black_list:
            if item['path'] == path and item['method'] == method:
                return False
        return True


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


@dataclasses.dataclass
class KubernetesNodesInfo:
    """Dataclass to store Kubernetes node info map."""
    # The nodes in the cluster, keyed by node name.
    node_info_dict: Dict[str, KubernetesNodeInfo]
    # Additional hint for the node info.
    hint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            'node_info_dict': {
                node_name: dataclasses.asdict(node_info)
                for node_name, node_info in self.node_info_dict.items()
            },
            'hint': self.hint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KubernetesNodesInfo':
        return cls(
            node_info_dict={
                node_name: KubernetesNodeInfo(**node_info)
                for node_name, node_info in data['node_info_dict'].items()
            },
            hint=data['hint'],
        )
