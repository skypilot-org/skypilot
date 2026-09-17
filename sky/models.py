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
    # The user's preferred default workspace, if one has been set.
    # Resolution and RBAC validation live in sky/workspaces/; this field is
    # just the persisted value. None means "no preference set".
    preferred_workspace: Optional[str] = None

    def __init__(
        self,
        id: str,  # pylint: disable=redefined-builtin
        name: Optional[str] = None,
        password: Optional[str] = None,
        created_at: Optional[int] = None,
        user_type: Optional[str] = None,
        preferred_workspace: Optional[str] = None,
    ):
        self.id = id.strip().lower()
        self.name = name
        self.password = password
        self.created_at = created_at
        self.user_type = user_type
        self.preferred_workspace = preferred_workspace

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'user_type': self.user_type,
            'preferred_workspace': self.preferred_workspace,
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
    # 'effect', and optionally 'tolerated' (a bool indicating whether the
    # taint is matched by an entry in the configured
    # `kubernetes.pod_config.spec.tolerations`).
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


class VolumeResizeStatus(enum.Enum):
    """Where a resize has got to, in terms of what stands between it and done.

    Deliberately not the cloud's own vocabulary, which is both wordier and
    version-specific: Kubernetes alone names these differently on its
    conditions and on `allocatedResourceStatuses`, and another cloud would name
    them differently again.

    What the user should *do* is not part of the state: for the middle one it
    depends on whether anything is using the volume, which the state itself
    cannot say. See `volume.resize_display_message`.
    """
    # The storage backend is working on it; waiting is all that is needed.
    IN_PROGRESS = 'in_progress'
    # The new capacity is allocated, but the filesystem is grown by the node
    # that mounts the volume, so the volume keeps its old size until that
    # happens. For a volume in use it usually happens on its own, without
    # restarting anything; for one nothing is using, it waits indefinitely.
    PENDING_ON_NODE = 'pending_on_node'
    # The resize stopped short. The recorded size is still the real one.
    #
    # Only clusters that report a failed resize can produce this: where they
    # do not, a resize that can never succeed keeps reporting IN_PROGRESS.
    FAILED = 'failed'


@dataclasses.dataclass
class ObservedVolumeState:
    """The fields of a volume that the cloud owns, as the cloud reports them.

    ``VolumeConfig`` doubles as the request SkyPilot made and the state the
    cloud is in, and only the request is known when the volume is created --
    storage can be expanded, and a provisioner can round a request up. This
    carries what a later look at the cloud found, so the recorded config can be
    brought back in line with it.

    A field left None means the cloud reported nothing for it, which must never
    be read as "the recorded value is gone".
    """
    # The capacity the volume actually has, in the same units as
    # ``VolumeConfig.size``.
    size: Optional[str] = None
    storage_class_name: Optional[str] = None
    # Set while the volume is being resized to a size it does not have yet.
    # `size` stays the capacity that exists, so without these two a volume
    # whose expansion is in flight -- or stuck waiting on something the user
    # has to do -- looks indistinguishable from one that was never resized.
    resize_status: Optional[VolumeResizeStatus] = None
    resize_target_size: Optional[str] = None
    # How the cloud explains the state, in its own words. None when it offers
    # none, which is the usual case for everything but Kubernetes conditions.
    resize_message: Optional[str] = None


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
