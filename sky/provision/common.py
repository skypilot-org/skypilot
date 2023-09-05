"""Common data structures for provisioning"""
from typing import Any, Dict, List, Optional, Tuple

import pydantic

# NOTE: we use pydantic instead of dataclasses or namedtuples, because
# pydantic provides more features like validation or parsing from
# nested dictionaries. This makes our API more extensible and easier
# to integrate with other frameworks like FastAPI etc.

# -------------------- input data model -------------------- #


class InstanceConfig(pydantic.BaseModel):
    """Metadata for instance configuration."""
    # Global configurations for the cloud provider.
    provider_config: Dict[str, Any]
    # Configurations for the authentication.
    authentication_config: Dict[str, Any]
    # Configurations for each instance.
    node_config: Dict[str, Any]
    # Number of instances to start.
    count: int
    # Tags for the instances.
    tags: Dict[str, str]
    # Whether or not to resume stopped instances.
    resume_stopped_nodes: bool


# -------------------- output data model -------------------- #


class ProvisionMetadata(pydantic.BaseModel):
    """Metadata from provisioning."""
    # The name of the cloud provider.
    provider_name: str
    # The name of the region.
    region: str
    # The name of the sub-zone in the region. It must be a single zone.
    # It can also be None if the cloud provider does not support zones.
    zone: Optional[str]
    # The name of the cluster.
    cluster_name: str
    # The head node ID.
    head_instance_id: str
    # The IDs of all just resumed instances.
    resumed_instance_ids: List[str]
    # The IDs of all just created instances.
    created_instance_ids: List[str]

    def is_instance_just_booted(self, instance_id: str) -> bool:
        """Whether or not the instance is just booted.

        Is an instance just booted,  so that there are no services running?
        """
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

        Returns:
            A list of tuples (private_ip, public_ip) of all instances.
        """
        head_node_ip, other_ips = [], []
        for instance in self.instances.values():
            pair = (instance.private_ip, instance.public_ip)
            if instance.instance_id == self.head_instance_id:
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

    def get_ips(self, use_internal_ips: bool) -> List[str]:
        """Get public or private/internal IPs of all instances.

        It returns the IP of the head node first.
        """
        ip_tuples = self.ip_tuples()
        ip_list = []
        if use_internal_ips:
            for pair in ip_tuples:
                private_ip = pair[0]
                if private_ip is None:
                    raise ValueError('Not all instances have private IPs')
                ip_list.append(private_ip)
        else:
            for pair in ip_tuples:
                public_ip = pair[1]
                if public_ip is None:
                    raise ValueError('Not all instances have public IPs')
                ip_list.append(public_ip)
        return ip_list

    def get_head_instance(self) -> Optional[InstanceMetadata]:
        """Get the instance metadata of the head node"""
        if self.head_instance_id is None:
            return None
        if self.head_instance_id not in self.instances:
            raise ValueError('Head instance ID not in the cluster metadata.')
        return self.instances[self.head_instance_id]
