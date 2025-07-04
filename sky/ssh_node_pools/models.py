"""Data Models for SSH Node Pools."""

import dataclasses
from enum import Enum
from typing import Any, Dict, List, Optional

import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy_json import mutable_json_type

SSHBase = declarative_base()


class SSHClusterStatus(Enum):
    PENDING = 'pending'
    STARTING = 'starting'
    ACTIVE = 'active'
    TERMINATING = 'terminating'


@dataclasses.dataclass
class SSHNode:
    """Node Data in SSH Cluster"""
    ip: str
    user: str
    identity_file: str
    password: str
    use_ssh_config: bool

    def __repr__(self):
        return f'<SSHNode(ip={self.ip})>'

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage"""
        return {
            'ip': self.ip,
            'user': self.user,
            'identity_file': self.identity_file,
            'password': self.password,
            'use_ssh_config': self.use_ssh_config,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SSHNode':
        """Create SSHNode from Configuration Dict"""
        return cls(
            ip=data['ip'],
            user=data['user'],
            identity_file=data['identity_file'],
            password=data['password'],
            use_ssh_config=data['use_ssh_config'],
        )

    def __hash__(self):
        return hash((self.ip, self.user, self.identity_file, self.password,
                     self.use_ssh_config))


@dataclasses.dataclass
class SSHCluster(SSHBase):  # type: ignore[valid-type, misc]
    """SSH Node Pool"""
    __tablename__ = 'ssh_clusters'

    name: Mapped[str] = mapped_column(sqlalchemy.Text, primary_key=True)
    status: Mapped[SSHClusterStatus] = mapped_column(
        sqlalchemy.Enum(SSHClusterStatus,
                        name='cluster_status_enum',
                        native_enum=False),
        nullable=False,
        default=SSHClusterStatus.PENDING)
    _head_node_ip: Mapped[Optional[str]] = mapped_column(sqlalchemy.Text,
                                                         nullable=True)

    _current_state: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        mutable_json_type(dbtype=sqlalchemy.JSON, nested=True), nullable=True)
    _update_state: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        mutable_json_type(dbtype=sqlalchemy.JSON, nested=True), nullable=True)

    @property
    def current_nodes(self) -> List[SSHNode]:
        return self._get_nodes(True)

    @property
    def update_nodes(self) -> List[SSHNode]:
        return self._get_nodes(False)

    @property
    def num_current_nodes(self) -> int:
        return len(self.current_nodes)

    @property
    def num_update_nodes(self) -> int:
        return len(self.update_nodes)

    @property
    def head_node_ip(self) -> Optional[str]:
        return self._head_node_ip

    def __repr__(self):
        return f'<SSHCluster(name={self.name})>'

    def _get_nodes(self, current: bool) -> List[SSHNode]:
        state = self._current_state if current else self._update_state
        state_name = 'current' if current else 'update'
        if state is None:
            return []
        if self.head_node_ip is None:
            raise ValueError(f'SSHCluster {state_name} state has '
                             f'null head node ip {self.name}')
        found_head, nodes = False, []
        for j in state:
            node = SSHNode.from_dict(j)
            nodes.append(node)
            if node.ip == self.head_node_ip:
                found_head = True
        if not found_head:
            raise ValueError(f'SSHCluster {self.name} {state_name} state '
                             'does not have configured head node ip '
                             f'{self.head_node_ip}')
        return nodes

    def set_head_node_ip(self, ip: str):
        """Set head node ip for SSHCluster. Note that head node ip is
        immutable, so we can only set it once."""
        if self.head_node_ip is not None and self.head_node_ip != ip:
            raise ValueError('Attempting to override head node ip '
                             f'{self._head_node_ip} on SSHCluster '
                             f'{self.name} to {ip}')
        self._head_node_ip = ip

    def commit(self):
        """After update is applied, remove previous configurations
        and update current state"""
        self._current_state = self._update_state
        self._update_state = None

    def set_update_nodes(self, nodes: List['SSHNode']):
        """Set SSHCluster with update configurations. Note that current
        state can only be set using the `commit` function, which transfers
        the update state to the current state."""
        if self.head_node_ip is None:
            raise ValueError('Attempting to set update node configurations '
                             f'on SSHCluster {self.name} without specifying '
                             'head node')
        self._update_state, found_head = [], False
        for node in nodes:
            if node.ip == self.head_node_ip:
                found_head = True
            self._update_state.append(node.to_dict())
        if not found_head:
            raise ValueError(f'Update nodes list for SSHCluster {self.name} '
                             f'does not include head node {self.head_node_ip}')

    def get_current_ips(self) -> List[str]:
        """Get ip addresses for current SSH nodes"""
        return [node.ip for node in self.current_nodes]

    def get_update_ips(self) -> List[str]:
        """Get ip addresses for updating SSH nodes"""
        return [node.ip for node in self.update_nodes]
