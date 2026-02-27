"""Data Models for SkyPilot."""

import collections
import dataclasses
import enum
import getpass
import os
from typing import Any, ClassVar, Dict, List, Optional

import pydantic

from sky.skylet import constants
from sky.utils import common_utils


class UserType(enum.Enum):
    """Enum for user types."""
    # Internal system users (SERVER_ID, SKYPILOT_SYSTEM_USER_ID)
    SYSTEM = 'system'
    # Users authenticated by basic auth on the API server that have a password
    BASIC = 'basic'
    # Service accounts
    SA = 'sa'
    # Users authenticated via SSO
    SSO = 'sso'
    # Users authenticated by basic auth on the ingress that have no password
    LEGACY = 'legacy'


@dataclasses.dataclass
class User:
    """Dataclass to store user information."""
    # User hash
    id: str
    # Display name of the user
    name: Optional[str] = None
    password: Optional[str] = None
    created_at: Optional[int] = None
    user_type: Optional[str] = None

    def __init__(
        self,
        id: str,  # pylint: disable=redefined-builtin
        name: Optional[str] = None,
        password: Optional[str] = None,
        created_at: Optional[int] = None,
        user_type: Optional[str] = None,
    ):
        self.id = id.strip().lower()
        self.name = name
        self.password = password
        self.created_at = created_at
        self.user_type = user_type

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'name': self.name, 'user_type': self.user_type}

    @classmethod
    def get_current_user(cls) -> 'User':
        """Returns the current user."""
        user_name = os.getenv(constants.USER_ENV_VAR, getpass.getuser())
        user_hash = common_utils.get_user_hash()
        return User(id=user_hash, name=user_name)

    def is_service_account(self) -> bool:
        """Check if the user is a service account."""
        return self.id.lower().startswith('sa-')


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
    # IP address of the node (external IP preferred, fallback to internal IP)
    ip_address: Optional[str] = None
    # CPU count (total CPUs available on the node)
    cpu_count: Optional[float] = None
    # Memory in GB (total memory available on the node)
    memory_gb: Optional[float] = None
    # Free CPU count (free CPUs available on the node after pod allocations)
    cpu_free: Optional[float] = None
    # Free memory in GB (free memory available on the node after pod
    # allocations)
    memory_free_gb: Optional[float] = None
    # Whether the node is ready (all conditions are satisfied)
    is_ready: bool = True
    # Whether the node is cordoned (spec.unschedulable is true)
    is_cordoned: bool = False
    # List of taints on the node, each taint is a dict with 'key', 'value',
    # 'effect'
    taints: Optional[List[Dict[str, Any]]] = None


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


class VolumeConfig(pydantic.BaseModel):
    """Configuration for creating a volume."""
    # If any fields changed, increment the version. For backward compatibility,
    # modify the __setstate__ method to handle the old version.
    _VERSION: ClassVar[int] = 1

    _version: int
    name: str
    type: str
    cloud: str
    region: Optional[str]
    zone: Optional[str]
    name_on_cloud: str
    size: Optional[str]
    config: Dict[str, Any] = {}
    labels: Optional[Dict[str, str]] = None
    id_on_cloud: Optional[str] = None

    def __getstate__(self) -> Dict[str, Any]:
        state = super().__getstate__()
        state['_version'] = self._VERSION
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        """Set state from pickled state, for backward compatibility."""
        super().__setstate__(state)
        version = state.pop('_version', None)
        if version is None:
            version = -1

        if version < 0:
            state['id_on_cloud'] = None

        state['_version'] = self._VERSION
        self.__dict__.update(state)
