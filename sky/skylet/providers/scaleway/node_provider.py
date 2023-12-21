import logging
from threading import RLock
import time
from types import ModuleType
from typing import Any, Dict, List, Optional

from ray.autoscaler._private.command_runner import DockerCommandRunner
from ray.autoscaler._private.command_runner import SSHCommandRunner
from ray.autoscaler.command_runner import CommandRunnerInterface
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME
from ray.util.annotations import DeveloperAPI
import scaleway
from scaleway.instance.v1 import CreateServerResponse
from scaleway.instance.v1 import InstanceV1API

logger = logging.getLogger(__name__)


class ScalewayError(Exception):
    pass


def synchronized(f):

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return f(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


def get_node_id_from_node(node: CreateServerResponse):
    return "{zone}/{instance_id}".format(zone=node.server.zone,
                                         instance_id=node.server.id)


@DeveloperAPI
class ScalewayNodeProvider:
    """Interface for getting and returning nodes from a Cloud.

    **Important**: This is an INTERNAL API that is only exposed for the purpose
    of implementing custom node providers. It is not allowed to call into
    NodeProvider methods from any Ray package outside the autoscaler, only to
    define new implementations of NodeProvider for use with the "external" node
    provider option.

    NodeProviders are namespaced by the `cluster_name` parameter; they only
    operate on nodes within that namespace.

    Nodes may be in one of three states: {pending, running, terminated}. Nodes
    appear immediately once started by `create_node`, and transition
    immediately to terminated when `terminate_node` is called.
    """

    def __init__(self, provider_config: Dict[str, Any],
                 cluster_name: str) -> None:
        self.lock = RLock()
        self.provider_config = provider_config
        self.cluster_name = cluster_name
        self._internal_ip_cache: Dict[str, str] = {}
        self._external_ip_cache: Dict[str, str] = {}
        self.client = scaleway.Client.from_config_file_and_env()
        self.instance = InstanceV1API(client=self.client)
        self.commercial_type = "GPU-3070-S"
        self.zone = "fr-par-2"
        self.image = "ubuntu_jammy_gpu_os_12"

    @staticmethod
    def is_readonly() -> bool:
        """Returns whether this provider is readonly.

        Readonly node providers do not allow nodes to be created or terminated.
        """
        return False

    def non_terminated_nodes(self, tag_filters):
        """Return a list of node ids filtered by the specified tags dict.
        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes()
        must be called again to refresh results.
        """
        nodes = self.get_filtered_nodes(tag_filters=tag_filters)
        return [node_id for node_id in nodes.keys()]

    def is_running(self, node_id: str) -> bool:
        """Return whether the specified node is running."""
        return self._get_cached_node(node_id=node_id) is not None

    def is_terminated(self, node_id: str) -> bool:
        """Return whether the specified node is terminated."""
        return self._get_cached_node(node_id=node_id) is None

    def node_tags(self, node_id: str) -> Dict[str, str]:
        """Returns the tags of the given node (string dict)."""
        node = self._get_cached_node(node_id=node_id)
        if node is None:
            return {}
        return node['tags']

    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        # print(f"Node if {node_id}")
        # print(self._get_cached_node(node_id=node_id))
        node_status_data = self._get_cached_node(node_id=node_id)
        print(f"Public IP returned is {node_status_data.public_ips[0]}")
        return node_status_data.public_ips[0]

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self.instance.get_server(server_id=node_id,
                                        zone=self.zone).server.private_ip

    def get_node_id(self,
                    ip_address: str,
                    use_internal_ip: bool = False) -> str:
        """Returns the node_id given an IP address.

        Assumes ip-address is unique per node.

        Args:
            ip_address: Address of node.
            use_internal_ip: Whether the ip address is
                public or private.

        Raises:
            ValueError if not found.
        """

        def find_node_id():
            if use_internal_ip:
                return self._internal_ip_cache.get(ip_address)
            else:
                return self._external_ip_cache.get(ip_address)

        if not find_node_id():
            all_nodes = self.non_terminated_nodes({})
            ip_func = self.internal_ip if use_internal_ip else self.external_ip
            ip_cache = (self._internal_ip_cache
                        if use_internal_ip else self._external_ip_cache)
            for node_id in all_nodes:
                ip_cache[ip_func(node_id)] = node_id

        if not find_node_id():
            if use_internal_ip:
                known_msg = f"Worker internal IPs: {list(self._internal_ip_cache)}"
            else:
                known_msg = f"Worker external IP: {list(self._external_ip_cache)}"
            raise ValueError(f"ip {ip_address} not found. " + known_msg)

        return find_node_id()

    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str],
                    count: int) -> Optional[Dict[str, Any]]:
        """Creates a number of nodes within the namespace.

        Optionally returns a mapping from created node ids to node metadata.

        Optionally may throw a
        ray.autoscaler.node_launch_exception.NodeLaunchException which the
        autoscaler may use to provide additional functionality such as
        observability.

        """
        nodes = {}
        for _ in range(count):
            node = self.instance._create_server(
                name=f"{self.cluster_name}-{tags['ray-node-type']}",
                commercial_type=self.commercial_type,
                image=self.image,
                tags=[
                    "{key}:{value}".format(key=key, value=value)
                    for key, value in tags.items()
                ],
                enable_ipv6=True)
            node_id = get_node_id_from_node(node)
            nodes[node_id] = node
        for node in nodes:
            while node.server.state not in ['running', 'error']:
                node = self.instance.get_server(zone=node.server.zone,
                                                server_id=node.server.id)
                time.sleep(2)
            if node.server.state == 'error':
                raise RuntimeError("Unable to launch instance")
            config_tags = node_config.get('tags', {}).copy()
            config_tags.update(tags)
            config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

            if node is None:
                raise ScalewayError('Failed to launch instance.')

        return nodes

    def create_node_with_resources_and_labels(
        self,
        node_config: Dict[str, Any],
        tags: Dict[str, str],
        count: int,
        resources: Dict[str, float],
        labels: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """Create nodes with a given resource and label config.

        This is the method actually called by the autoscaler. Prefer to
        implement this when possible directly, otherwise it delegates to the
        create_node() implementation.

        Optionally may throw a ray.autoscaler.node_launch_exception.NodeLaunchException.
        """
        return self.create_node(node_config, tags, count)

    @synchronized
    def set_node_tags(self, node_id, tags):
        """Sets the tag values (string dict) for the specified node."""
        zone, server_id = node_id.split("/")
        self.instance._update_server(zone=zone, server_id=server_id, tags=tags)

    def terminate_node(self, node_id: str) -> None:
        """Terminates the specified node.

        Optionally return a mapping from deleted node ids to node
        metadata.
        """
        zone, server_id = node_id.split("/")
        self.instance.delete_server(server_id=server_id, zone=zone)

    def terminate_nodes(self, node_ids: List[str]) -> Optional[Dict[str, Any]]:
        """Terminates a set of nodes.

        May be overridden with a batch method, which optionally may return a
        mapping from deleted node ids to node metadata.
        """
        for node_id in node_ids:
            logger.info("NodeProvider: {}: Terminating node".format(node_id))
            self.terminate_node(node_id)
        return None

    @property
    def max_terminate_nodes(self) -> Optional[int]:
        """The maximum number of nodes which can be terminated in one single
        API request. By default, this is "None", which means that the node
        provider's underlying API allows infinite requests to be terminated
        with one request.

        For example, AWS only allows 1000 nodes to be terminated
        at once; to terminate more, we must issue multiple separate API
        requests. If the limit is infinity, then simply set this to None.

        This may be overridden. The value may be useful when overriding the
        "terminate_nodes" method.
        """
        return None

    @staticmethod
    def bootstrap_config(cluster_config: Dict[str, Any]) -> Dict[str, Any]:
        """Bootstraps the cluster config by adding env defaults if needed."""
        return cluster_config

    def get_command_runner(
        self,
        log_prefix: str,
        node_id: str,
        auth_config: Dict[str, Any],
        cluster_name: str,
        process_runner: ModuleType,
        use_internal_ip: bool,
        docker_config: Optional[Dict[str, Any]] = None,
    ) -> CommandRunnerInterface:
        """Returns the CommandRunner class used to perform SSH commands.

        Args:
        log_prefix: stores "NodeUpdater: {}: ".format(<node_id>). Used
            to print progress in the CommandRunner.
        node_id: the node ID.
        auth_config: the authentication configs from the autoscaler
            yaml file.
        cluster_name: the name of the cluster.
        process_runner: the module to use to run the commands
            in the CommandRunner. E.g., subprocess.
        use_internal_ip: whether the node_id belongs to an internal ip
            or external ip.
        docker_config: If set, the docker information of the docker
            container that commands should be run on.
        """
        common_args = {
            "log_prefix": log_prefix,
            "node_id": node_id,
            "provider": self,
            "auth_config": auth_config,
            "cluster_name": cluster_name,
            "process_runner": process_runner,
            "use_internal_ip": use_internal_ip,
        }
        if docker_config and docker_config["container_name"] != "":
            return DockerCommandRunner(docker_config, **common_args)
        else:
            return SSHCommandRunner(**common_args)

    def prepare_for_head_node(self,
                              cluster_config: Dict[str, Any]) -> Dict[str, Any]:
        """Returns a new cluster config with custom configs for head node."""
        return cluster_config

    @staticmethod
    def fillout_available_node_types_resources(
            cluster_config: Dict[str, Any]) -> Dict[str, Any]:
        """Fills out missing "resources" field for available_node_types."""
        return cluster_config

    def safe_to_scale(self) -> bool:
        """Optional condition to determine if it's safe to proceed with an autoscaling
        update. Can be used to wait for convergence of state managed by an external
        cluster manager.

        Called by the autoscaler immediately after non_terminated_nodes().
        If False is returned, the autoscaler will abort the update.
        """
        return True

    def post_process(self) -> None:
        """This optional method is executed at the end of
        StandardAutoscaler._update().
        """
        pass

    def _get_cached_node(self, node_id):
        """Return node info from cache if possible, otherwise fetches it."""
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]

        return self._get_node(node_id)
