"""Common data structures for provisioning"""
import abc
import dataclasses
import functools
import os
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.utils import env_options
from sky.utils import resources_utils

# NOTE: we can use pydantic instead of dataclasses or namedtuples, because
# pydantic provides more features like validation or parsing from
# nested dictionaries. This makes our API more extensible and easier
# to integrate with other frameworks like FastAPI etc.

# -------------------- input data model -------------------- #

InstanceId = str
_START_TITLE = '\n' + '-' * 20 + 'Start: {} ' + '-' * 20
_END_TITLE = '-' * 20 + 'End:   {} ' + '-' * 20 + '\n'

logger = sky_logging.init_logger(__name__)


class ProvisionerError(RuntimeError):
    """Exception for provisioner."""
    errors: List[Dict[str, str]]


class StopFailoverError(Exception):
    """Exception for stopping failover.

    It will be raised when failed to cleaning up resources after a failed
    provision, so the caller should stop the failover process and raise.
    """


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
    # Optional ports to open on launch of the cluster.
    ports_to_open_on_launch: Optional[List[int]]


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
    ssh_port: int = 22

    def get_feasible_ip(self) -> str:
        """Get the most feasible IPs of the instance. This function returns
        the public IP if it exist, otherwise it returns a private IP."""
        if self.external_ip is not None:
            return self.external_ip
        return self.internal_ip


@dataclasses.dataclass
class ClusterInfo:
    """Cluster Information."""
    instances: Dict[InstanceId, List[InstanceInfo]]
    # The unique identifier of the head instance, i.e., the
    # `instance_info.instance_id` of the head node.
    head_instance_id: Optional[InstanceId]
    # Provider related information.
    provider_name: str
    provider_config: Optional[Dict[str, Any]] = None

    docker_user: Optional[str] = None
    # Override the ssh_user from the cluster config.
    ssh_user: Optional[str] = None
    custom_ray_options: Optional[Dict[str, Any]] = None

    @property
    def num_instances(self) -> int:
        """Get the number of instances in the cluster."""
        return sum(len(instances) for instances in self.instances.values())

    def get_head_instance(self) -> Optional[InstanceInfo]:
        """Get the instance metadata of the head node"""
        if self.head_instance_id is None:
            return None
        if self.head_instance_id not in self.instances:
            raise ValueError('Head instance ID not in the cluster metadata. '
                             f'ClusterInfo: {self.__dict__}')
        return self.instances[self.head_instance_id][0]

    def get_worker_instances(self) -> List[InstanceInfo]:
        """Get all worker instances."""
        worker_instances = []
        for inst_id, instances in self.instances.items():
            if inst_id == self.head_instance_id:
                worker_instances.extend(instances[1:])
            else:
                worker_instances.extend(instances)
        return worker_instances

    def ip_tuples(self) -> List[Tuple[str, Optional[str]]]:
        """Get IP tuples of all instances. Make sure that list always
        starts with head node IP, if head node exists.

        Returns:
            A list of tuples (internal_ip, external_ip) of all instances.
        """
        head_instance = self.get_head_instance()
        if head_instance is None:
            head_instance_ip = []
        else:
            head_instance_ip = [(head_instance.internal_ip,
                                 head_instance.external_ip)]
        other_ips = []
        for instance in self.get_worker_instances():
            pair = (instance.internal_ip, instance.external_ip)
            other_ips.append(pair)
        return head_instance_ip + other_ips

    def instance_ids(self) -> List[str]:
        """Return the instance ids in the same order of ip_tuples."""
        id_list = []
        if self.head_instance_id is not None:
            id_list.append(self.head_instance_id + '-0')
        for inst_id, instances in self.instances.items():
            start_idx = 0
            if inst_id == self.head_instance_id:
                start_idx = 1
            id_list.extend(
                [f'{inst_id}-{i}' for i in range(start_idx, len(instances))])
        return id_list

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
        """Get internal or external IPs depends on the settings."""
        use_internal_ips = (self.provider_config is not None and
                            self.provider_config.get('use_internal_ips', False))
        return self._get_ips(use_internal_ips or not self.has_external_ips() or
                             force_internal_ips)

    def get_ssh_ports(self) -> List[int]:
        """Get the SSH port of all the instances."""
        head_instance = self.get_head_instance()

        head_instance_port = []
        if head_instance is not None:
            head_instance_port = [head_instance.ssh_port]

        worker_instances = self.get_worker_instances()
        worker_instance_ports = [
            instance.ssh_port for instance in worker_instances
        ]
        return head_instance_port + worker_instance_ports


class Endpoint:
    """Base class for endpoints."""
    pass

    @abc.abstractmethod
    def url(self, override_ip: Optional[str] = None) -> str:
        raise NotImplementedError


@dataclasses.dataclass
class SocketEndpoint(Endpoint):
    """Socket endpoint accessible via a host and a port."""
    port: Optional[int]
    host: str = ''

    def url(self, override_ip: Optional[str] = None) -> str:
        host = override_ip if override_ip else self.host
        if env_options.Options.RUNNING_IN_BUILDKITE.get(
        ) and 'localhost' in host:
            # In Buildkite CI, we run a kind (Kubernetes in Docker) cluster.
            # The controller pod runs inside this kind cluster, which itself
            # runs in a container. When the pod tries to access 'localhost',
            # it can't reach the host machine's localhost. Using
            # 'host.docker.internal' allows the pod to properly communicate
            # with services running on the host machine's localhost.
            host = 'host.docker.internal'
        return f'{host}{":" + str(self.port) if self.port else ""}'


@dataclasses.dataclass
class HTTPEndpoint(SocketEndpoint):
    """HTTP endpoint accessible via a url."""
    path: str = ''

    def url(self, override_ip: Optional[str] = None) -> str:
        host = override_ip if override_ip else self.host
        return f'http://{os.path.join(super().url(host), self.path)}'


@dataclasses.dataclass
class HTTPSEndpoint(SocketEndpoint):
    """HTTPS endpoint accessible via a url."""
    path: str = ''

    def url(self, override_ip: Optional[str] = None) -> str:
        host = override_ip if override_ip else self.host
        return f'https://{os.path.join(super().url(host), self.path)}'


def query_ports_passthrough(
    ports: List[str],
    head_ip: Optional[str],
) -> Dict[int, List[Endpoint]]:
    """Common function to get endpoints for AWS, GCP and Azure.

    Returns a list of socket endpoint using head_ip and ports."""
    assert head_ip is not None, head_ip
    ports = list(resources_utils.port_ranges_to_set(ports))
    result: Dict[int, List[Endpoint]] = {}
    for port in ports:
        result[port] = [SocketEndpoint(port=port, host=head_ip)]
    return result


def log_function_start_end(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(_START_TITLE.format(func.__name__))
        try:
            return func(*args, **kwargs)
        finally:
            logger.info(_END_TITLE.format(func.__name__))

    return wrapper
