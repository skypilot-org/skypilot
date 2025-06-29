"""Data Models for SSH Node Pools."""

import dataclasses
from typing import List, Optional

# TODO (kyuds): secure storage for ssh passwords.
# main problem is we can't just hash them with salt.

@dataclasses.dataclass
class SSHPool:
    """SSH Node Pool"""
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
        return (f'<SSHPool(name={self.name}({self.alias}), '
                f'head={self.head_node_name}, num_nodes={self.num_nodes})>')

    def get_detailed_name(self):
        """Return pool name with alias if one exists."""
        if self.alias is None:
            return self.name
        return f'{self.name}({self.alias})'


@dataclasses.dataclass
class SSHNode:
    """Node Data in Individual Node Pools"""
    id: Optional[str] # uuid
    ip: str
    user: Optional[str]
    identity_file: Optional[str]
    password: Optional[str]

    pool_name: str
    pool: Optional['SSHPool'] = dataclasses.field(
        default=None, init=False, repr=False)

    def __repr__(self):
        pool_name = (self.pool.get_detailed_name() 
                     if self.pool is not None else self.pool_name)
        return (f'<SSHNode(ip={self.ip}, '
                f'user={"default" if self.user is None else self.user}, '
                f'pool={pool_name})>')
