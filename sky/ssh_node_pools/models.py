"""Data Models for SSH Node Pools."""

import dataclasses
from typing import Any, Dict, List, Optional


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
class SSHCluster:
    """SSH Node Pool"""
    name: str  # pk
    head_node_ip: str
    alias: Optional[str] = None
    _node_json: List[Dict[str, Any]] = dataclasses.field(default_factory=list)

    @property
    def num_nodes(self):
        return len(self._node_json)

    @property
    def nodes(self) -> List['SSHNode']:
        return [SSHNode.from_dict(j) for j in self._node_json]

    @property
    def node_json(self) -> List[Dict[str, Any]]:
        return self._node_json

    def set_nodes(self, nodes: List[SSHNode]):
        self._node_json = []
        found_head = False
        for node in nodes:
            if node.ip == self.head_node_ip:
                found_head = True
            self._node_json.append(node.to_dict())
        if not found_head:
            raise ValueError('updating nodes list for ssh cluster '
                             f'<{self.get_detailed_name()}> '
                             'that doesn\'t include head node '
                             f'{self.head_node_ip}')

    def __repr__(self):
        return (f'<SSHCluster(name={self.name}, '
                f'num_nodes={self.num_nodes}, '
                f'head_node={self.head_node_ip})>')

    def get_detailed_name(self):
        if self.alias is None:
            return self.name
        return f'{self.name}({self.alias})'

    def get_head_node(self) -> SSHNode:
        for node in self.nodes:
            if node.ip == self.head_node_ip:
                return node
        raise ValueError(f'head node not found for {self.name}')

    def get_worker_nodes(self) -> List[SSHNode]:
        workers = []
        for node in self.nodes:
            if node.ip != self.head_node_ip:
                workers.append(node)
        return workers
