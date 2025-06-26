"""Data Models for SSH Node Pools."""

import dataclasses
from typing import List, Optional

# TODO (kyuds): secure storage for ssh passwords.
# main problem is we can't just hash them with salt.

@dataclasses.dataclass
class SSHCluster:
    """SSH Node Pool Cluster"""
    name: str # pk
    alias: Optional[str]
    num_nodes: int
    head_node_name: str
    head_node_id: str # uuid
    default_user: Optional[str]
    default_identity_file: Optional[str]
    default_password: Optional[str]

    nodes: List['SSHNode'] = dataclasses.field(
        default_factory=list, init=False, repr=False)

    def __repr__(self):
        return (f'<SSHCluster(name={self.name}({self.alias}), '
                f'head={self.head_node_name}, num_nodes={self.num_nodes})>')

    def get_detailed_name(self):
        """Return cluster name with alias if one exists."""
        if self.alias is None:
            return self.name
        return f'{self.name}({self.alias})'


@dataclasses.dataclass
class SSHNode:
    """Node Data in Individual Clusters"""
    id: Optional[str] # uuid
    ip: str
    user: Optional[str]
    identity_file: Optional[str]
    password: Optional[str]

    cluster_name: str
    cluster: Optional['SSHCluster'] = dataclasses.field(
        default=None, init=False, repr=False)

    def __repr__(self):
        cluster_name = (self.cluster.get_detailed_name() 
                        if self.cluster is not None else self.cluster_name)
        return (f'<SSHNode(ip={self.ip}, '
                f'user={"default" if self.user is None else self.user}, '
                f'cluster={cluster_name})>')
