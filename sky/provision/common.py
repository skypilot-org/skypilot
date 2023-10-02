"""Common data structures for provisioning"""
import dataclasses
from typing import Any, Dict, List, Optional, Tuple

# NOTE: we can use pydantic instead of dataclasses or namedtuples, because
# pydantic provides more features like validation or parsing from
# nested dictionaries. This makes our API more extensible and easier
# to integrate with other frameworks like FastAPI etc.

# -------------------- input data model -------------------- #

InstanceId = str


@dataclasses.dataclass
class ProvisionConfig:
    """Configuration for provisioning."""
    # Global configurations for the cloud provider.
    provider_config: Dict[str, Any]
    # Configurations for the authentication.
    authentication_config: Dict[str, Any]
    # Configurations for the docker container to be run on the instance.
    docker_config: Dict[str, Any]
    # Configurations for each instance.
    node_config: Dict[str, Any]
    # Number of instances to start.
    count: int
    # Tags for the instances.
    tags: Dict[str, str]
    # Whether or not to resume stopped instances.
    resume_stopped_nodes: bool


# -------------------- output data model -------------------- #


@dataclasses.dataclass
class ProvisionRecord:
    """Record for a provisioning process."""
    # The name of the cloud provider.
    provider_name: str
    # The name of the region.
    region: str
    # The name of the sub-zone in the region. It must be a single zone.
    # It can also be None if the cloud provider does not support zones.
    zone: Optional[str]
    # The name of the cluster.
    cluster_name: str
    # The unique identifier of the head instance, i.e., the
    # `instance_info.instance_id` of the head node.
    head_instance_id: InstanceId
    # The IDs of all just resumed instances.
    resumed_instance_ids: List[InstanceId]
    # The IDs of all just created instances.
    created_instance_ids: List[InstanceId]

    def is_instance_just_booted(self, instance_id: InstanceId) -> bool:
        """Whether or not the instance is just booted.

        Is an instance just booted,  so that there are no services running?
        """
        return (instance_id in self.resumed_instance_ids or
                instance_id in self.created_instance_ids)


@dataclasses.dataclass
class InstanceInfo:
    """Instance information."""
    instance_id: InstanceId
    internal_ip: str
    external_ip: Optional[str]
    tags: Dict[str, str]

    def get_feasible_ip(self) -> str:
        """Get the most feasible IPs of the instance. This function returns
        the public IP if it exist, otherwise it returns a private IP."""
        if self.external_ip is not None:
            return self.external_ip
        return self.internal_ip


@dataclasses.dataclass
class ClusterInfo:
    """Cluster Information."""
    instances: Dict[InstanceId, InstanceInfo]
    # The unique identifier of the head instance, i.e., the
    # `instance_info.instance_id` of the head node.
    head_instance_id: Optional[InstanceId]
    docker_user: Optional[str] = None

    def ip_tuples(self) -> List[Tuple[str, Optional[str]]]:
        """Get IP tuples of all instances. Make sure that list always
        starts with head node IP, if head node exists.

        Returns:
            A list of tuples (internal_ip, external_ip) of all instances.
        """
        head_node_ip, other_ips = [], []
        for instance in self.instances.values():
            pair = (instance.internal_ip, instance.external_ip)
            if instance.instance_id == self.head_instance_id:
                head_node_ip.append(pair)
            else:
                other_ips.append(pair)
        return head_node_ip + other_ips

    def has_external_ips(self) -> bool:
        """True if the cluster has external IP."""
        ip_tuples = self.ip_tuples()
        if not ip_tuples:
            return False
        return ip_tuples[0][1] is not None

    def _get_ips(self, use_internal_ips: bool) -> List[str]:
        """Get public or private/internal IPs of all instances.

        It returns the IP of the head node first.
        """
        ip_tuples = self.ip_tuples()
        ip_list = []
        if use_internal_ips:
            for pair in ip_tuples:
                internal_ip = pair[0]
                if internal_ip is None:
                    raise ValueError('Not all instances have private IPs')
                ip_list.append(internal_ip)
        else:
            for pair in ip_tuples:
                public_ip = pair[1]
                if public_ip is None:
                    raise ValueError('Not all instances have public IPs')
                ip_list.append(public_ip)
        return ip_list

    def get_feasible_ips(self, force_internal_ips: bool = False) -> List[str]:
        """Get external IPs if they exist, otherwise get internal ones."""
        return self._get_ips(not self.has_external_ips() or force_internal_ips)

    def get_head_instance(self) -> Optional[InstanceInfo]:
        """Get the instance metadata of the head node"""
        if self.head_instance_id is None:
            return None
        if self.head_instance_id not in self.instances:
            raise ValueError('Head instance ID not in the cluster metadata.')
        return self.instances[self.head_instance_id]
