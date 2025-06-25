"""Data Models for SkyPilot."""

import collections
import dataclasses
import getpass
import hashlib
import json
import os
import re
import secrets
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

    def is_service_account(self) -> bool:
        """Check if this user is a service account."""
        return self.id.startswith('sa-')


@dataclasses.dataclass
class ServiceAccount(User):
    """Service Account class that inherits from User with additional metadata."""
    _creator_user_hash: Optional[str] = dataclasses.field(default=None, init=False)
    _service_account_name: Optional[str] = dataclasses.field(default=None, init=False) 
    _created_at: Optional[int] = dataclasses.field(default=None, init=False)

    def __post_init__(self):
        """Ensure service account has proper metadata."""
        if self._created_at is None:
            self._created_at = int(time.time())
        
        # Store service account metadata in password field as JSON
        if self.password and self.password.startswith('{'):
            # Password field contains JSON metadata
            try:
                metadata = json.loads(self.password)
                self._creator_user_hash = metadata.get('creator_user_hash')
                self._service_account_name = metadata.get('service_account_name')
                if 'created_at' in metadata:
                    self._created_at = metadata['created_at']
            except (json.JSONDecodeError, KeyError):
                pass

    @classmethod
    def generate_service_account_id(cls, token_name: str, creator_user_id: str) -> str:
        """Generate a unique service account ID."""
        random_suffix = secrets.token_hex(8)
        safe_token_name = re.sub(r'[^a-zA-Z0-9]', '', token_name)[:8]
        creator_prefix = creator_user_id[:8]
        return f'sa-{creator_prefix}-{safe_token_name}-{random_suffix}'

    @classmethod
    def create_service_account(cls, 
                             token_name: str, 
                             creator_user_hash: str) -> 'ServiceAccount':
        """Create a new service account."""
        service_account_id = cls.generate_service_account_id(token_name, creator_user_hash)
        created_at = int(time.time())
        
        # Store metadata in password field as JSON
        metadata = {
            'creator_user_hash': creator_user_hash,
            'service_account_name': token_name,
            'created_at': created_at
        }
        
        service_account = cls(
            id=service_account_id,
            name=token_name,
            password=json.dumps(metadata)
        )
        service_account._creator_user_hash = creator_user_hash
        service_account._service_account_name = token_name
        service_account._created_at = created_at
        return service_account

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        base_dict = super().to_dict()
        base_dict.update({
            'creator_user_hash': self.creator_user_hash,
            'service_account_name': self.service_account_name,
            'created_at': self.created_at,
            'is_service_account': True
        })
        return base_dict

    @classmethod
    def from_db_record(cls, db_record) -> 'ServiceAccount':
        """Create ServiceAccount from database record."""
        service_account = cls(
            id=db_record.id,
            name=db_record.name,
            password=db_record.password
        )
        return service_account

    @property
    def creator_user_hash(self) -> Optional[str]:
        """Get the creator user hash."""
        return self._creator_user_hash

    @creator_user_hash.setter
    def creator_user_hash(self, value: Optional[str]):
        """Set the creator user hash."""
        self._creator_user_hash = value

    @property
    def service_account_name(self) -> Optional[str]:
        """Get the service account name."""
        return self._service_account_name

    @service_account_name.setter
    def service_account_name(self, value: Optional[str]):
        """Set the service account name."""
        self._service_account_name = value

    @property
    def created_at(self) -> Optional[int]:
        """Get the creation timestamp."""
        return self._created_at

    @created_at.setter
    def created_at(self, value: Optional[int]):
        """Set the creation timestamp."""
        self._created_at = value

    @property
    def service_account_user_id(self) -> str:
        """Get the service account user ID (same as id)."""
        return self.id


@dataclasses.dataclass
class ServiceAccountToken:
    """Dataclass to store service account token information."""
    token_id: str
    user_hash: str  # Creator user hash
    token_name: str
    token_hash: str
    created_at: int
    service_account: Optional[ServiceAccount] = None
    last_used_at: Optional[int] = None
    expires_at: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'token_id': self.token_id,
            'user_hash': self.user_hash,
            'token_name': self.token_name,
            'created_at': self.created_at,
            'last_used_at': self.last_used_at,
            'expires_at': self.expires_at,
            'service_account_user_id': self.service_account.id if self.service_account else None,
            'creator_user_hash': self.user_hash,
        }

    def is_expired(self) -> bool:
        """Check if the token is expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @classmethod
    def from_db_record(cls, db_record, service_account: Optional[ServiceAccount] = None) -> 'ServiceAccountToken':
        """Create ServiceAccountToken from database record."""
        return cls(
            token_id=db_record.token_id,
            user_hash=db_record.creator_user_hash,
            token_name=db_record.token_name,
            token_hash=db_record.token_hash,
            created_at=db_record.created_at,
            last_used_at=db_record.last_used_at,
            expires_at=db_record.expires_at,
            service_account=service_account
        )

    def get_service_account_user_id(self) -> Optional[str]:
        """Get the service account user ID."""
        return self.service_account.id if self.service_account else None


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
