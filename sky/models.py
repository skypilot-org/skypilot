"""Data Models for SkyPilot."""

import collections
import dataclasses
import getpass
import os
from typing import Any, Dict, Optional

import pydantic

from sky.skylet import constants
from sky.utils import common_utils


@dataclasses.dataclass
class User:
    """Dataclass to store user information."""
    # User hash
    id: str
    # Display name of the user
    name: Optional[str] = None
    password: Optional[str] = None
    created_at: Optional[int] = None

    def __init__(
            self,
            id: str,  # pylint: disable=redefined-builtin
            name: Optional[str] = None,
            password: Optional[str] = None,
            created_at: Optional[int] = None):
        self.id = id.strip().lower()
        self.name = name
        self.password = password
        self.created_at = created_at

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'name': self.name}

    def to_env_vars(self) -> Dict[str, Any]:
        return {
            constants.USER_ID_ENV_VAR: self.id,
            constants.USER_ENV_VAR: self.name,
        }

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
    name: str
    type: str
    cloud: str
    region: Optional[str]
    zone: Optional[str]
    name_on_cloud: str
    size: Optional[str]
    config: Dict[str, Any] = {}
    labels: Optional[Dict[str, str]] = None
