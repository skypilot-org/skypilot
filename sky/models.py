"""Data Models for SkyPilot."""

import collections
import dataclasses
import getpass
import os
import time
from typing import Any, Dict, Optional

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

    @property
    def is_service_account(self) -> bool:
        """Check if this user is a service account."""
        return False


@dataclasses.dataclass
class ServiceAccount(User):
    """Dataclass to represent a service account (inherits from User)."""
    # Creator who created this service account
    creator_user_hash: str = ""
    # Service account specific metadata
    service_account_name: Optional[str] = None
    created_at: Optional[int] = None

    def __post_init__(self):
        """Set default values after initialization."""
        if self.created_at is None:
            self.created_at = int(time.time())
        if self.service_account_name is None:
            self.service_account_name = self.name

    @property
    def is_service_account(self) -> bool:
        """Check if this user is a service account."""
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'creator_user_hash': self.creator_user_hash,
            'service_account_name': self.service_account_name,
            'created_at': self.created_at,
            'is_service_account': True
        }

    @classmethod
    def generate_service_account_id(cls, service_account_name: str, creator_user_id: str) -> str:
        """Generate a deterministic service account user ID."""
        import re
        # Create a deterministic ID based on creator and service account name
        # Use sa- prefix followed by first 8 chars of creator ID and service account name
        # No random suffix to ensure same service account gets same ID
        safe_sa_name = re.sub(r'[^a-zA-Z0-9]', '', service_account_name)[:12]
        creator_prefix = creator_user_id[:8]
        service_account_id = f'sa-{creator_prefix}-{safe_sa_name}'
        return service_account_id

    @classmethod
    def create_service_account(cls, service_account_name: str, creator_user_hash: str) -> 'ServiceAccount':
        """Create a new service account with deterministic ID."""
        service_account_id = cls.generate_service_account_id(service_account_name, creator_user_hash)
        return cls(
            id=service_account_id,
            name=service_account_name,
            creator_user_hash=creator_user_hash,
            service_account_name=service_account_name
        )


@dataclasses.dataclass
class ServiceAccountToken:
    """Dataclass to store service account token information."""
    token_id: str
    token_name: str
    token_hash: str
    service_account: ServiceAccount
    created_at: int
    last_used_at: Optional[int] = None
    expires_at: Optional[int] = None

    @property
    def creator_user_hash(self) -> str:
        """Get the creator user hash from the service account."""
        return self.service_account.creator_user_hash

    @property
    def service_account_user_id(self) -> str:
        """Get the service account user ID."""
        return self.service_account.id

    @property
    def service_account_name(self) -> str:
        """Get the service account name."""
        return self.service_account.service_account_name or self.service_account.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            'token_id': self.token_id,
            'token_name': self.token_name,
            'created_at': self.created_at,
            'last_used_at': self.last_used_at,
            'expires_at': self.expires_at,
            'creator_user_hash': self.creator_user_hash,
            'service_account_user_id': self.service_account_user_id,
            'service_account_name': self.service_account_name,
            'service_account': self.service_account.to_dict(),
        }

    def is_expired(self) -> bool:
        """Check if the token is expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @classmethod
    def from_db_record(cls, db_record: Dict[str, Any], service_account: ServiceAccount) -> 'ServiceAccountToken':
        """Create ServiceAccountToken from database record and ServiceAccount."""
        return cls(
            token_id=db_record['token_id'],
            token_name=db_record['token_name'],
            token_hash=db_record['token_hash'],
            service_account=service_account,
            created_at=db_record['created_at'],
            last_used_at=db_record.get('last_used_at'),
            expires_at=db_record.get('expires_at'),
        )


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
