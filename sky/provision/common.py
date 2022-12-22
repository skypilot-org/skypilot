"""Common data structure for provisioning"""
from typing import Dict, Any, Optional, List, Tuple

import pydantic

# NOTE: we use pydantic instead of dataclasses or namedtuples, because
# pydantic provides more features like validation or parsing from
# nested dictionaries. This makes our API more extensible and easier
# to integrate with other frameworks like FastAPI etc.

# -------------------- input data model -------------------- #


class InstanceConfig(pydantic.BaseModel):
    """Metadata for instance configuration."""
    provider_config: Dict[str, Any]
    authentication_config: Dict[str, Any]
    node_config: Dict[str, Any]
    count: int
    tags: Dict[str, str]
    resume_stopped_nodes: bool


# -------------------- output data model -------------------- #


class ProvisionMetadata(pydantic.BaseModel):
    """Metadata from provisioning."""
    provider_name: str
    region: str
    zone: str
    cluster_name: str
    head_instance_id: str
    resumed_instance_ids: List[str]
    created_instance_ids: List[str]

    def is_instance_just_booted(self, instance_id: str) -> bool:
        """Is an instance just booted,
        so that there are no services running?"""
        return (instance_id in self.resumed_instance_ids or
                instance_id in self.created_instance_ids)


class InstanceMetadata(pydantic.BaseModel):
    """Metadata from querying a cloud instance."""
    instance_id: str
    private_ip: str
    public_ip: Optional[str]
    tags: Dict[str, str]

    def get_feasible_ip(self) -> str:
        """Get the most feasible IPs of the instance. This function returns
        the public IP if it exist, otherwise it returns a private IP."""
        if self.public_ip is not None:
            return self.public_ip
        return self.private_ip


class ClusterMetadata(pydantic.BaseModel):
    """Metadata from querying a cluster."""
    instances: Dict[str, InstanceMetadata]
    head_instance_id: Optional[str]

    def ip_tuples(self) -> List[Tuple[str, Optional[str]]]:
        """Get IP tuples of all instances. Make sure that list always
        starts with head node IP, if head node exists.
        """
        head_node_ip, other_ips = [], []
        for inst in self.instances.values():
            pair = (inst.private_ip, inst.public_ip)
            if inst.instance_id == self.head_instance_id:
                head_node_ip.append(pair)
            else:
                other_ips.append(pair)
        return head_node_ip + other_ips

    def has_public_ips(self) -> bool:
        """True if the cluster has public IP."""
        ip_tuples = self.ip_tuples()
        if not ip_tuples:
            return False
        return ip_tuples[0][1] is not None

    def get_feasible_ips(self) -> List[str]:
        """Get the most feasible IPs of the cluster. This function returns
        public IPs if they exist, otherwise it returns private IPs.
        If the private IPs are 'None's, this means this cluster does not have
        any IPs."""
        ip_tuples = self.ip_tuples()
        if not ip_tuples:
            return []
        if ip_tuples[0][1] is not None:
            ip_list = []
            for pair in ip_tuples:
                public_ip = pair[1]
                if public_ip is None:
                    raise ValueError('Inconsistent public IPs of the cluster')
                ip_list.append(public_ip)
            return ip_list
        return [pair[0] for pair in ip_tuples]

    def get_head_instance(self) -> Optional[InstanceMetadata]:
        """Get the instance metadata of the head node"""
        if self.head_instance_id is None:
            return None
        return self.instances[self.head_instance_id]
