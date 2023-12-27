import logging
import os
from threading import RLock
import time
from urllib.parse import urlparse

from ray.autoscaler._private.command_runner import SSHCommandRunner
from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME

from sky import authentication as auth
from sky.exceptions import ResourcesUnavailableError

from .ovhai_client import OVHCloudClient

logger = logging.getLogger(__name__)


class OVHCloudError(Exception):
    pass


def synchronized(f):

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return f(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class OVHCloudNodeProvider(NodeProvider):
    """Node Provider for OVHCloud."""

    def __init__(self, provider_config, cluster_name):
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self.cached_nodes = {}
        self.region = provider_config.get("region")
        self.zones = provider_config.get('availability_zone').split(',')
        self.client = OVHCloudClient(self.region)
        self.serviceName = self.client.os_tenant_id
        self.ssh_key_name = "sky-key"
        self.get_or_create_ssh_key()

    def get_or_create_ssh_key(self):
        if self.ssh_key_name not in [
                key.name for key in self.client.list_key_pairs()
        ]:
            print(f"Creating key {self.ssh_key_name}")
            with open(os.path.expanduser(auth.PUBLIC_SSH_KEY_PATH), "r") as _f:
                key_content = _f.read()
            self.client.create_key_pair(self.ssh_key_name, key_content)

    @synchronized
    def get_filtered_nodes(self, tag_filters):
        nodes = self.client.get_filtered_nodes(tag_filters)
        possible_names = [
            f'{self.cluster_name}-head', f'{self.cluster_name}-worker'
        ]
        cluster_instances = {
            node_id: node
            for node_id, node in nodes.items()
            if node.name in possible_names
        }
        return cluster_instances

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

    def is_running(self, node_id):
        """Return whether the specified node is running."""
        return self._get_cached_node(node_id=node_id) is not None

    def is_terminated(self, node_id):
        """Return whether the specified node is terminated."""
        # print(f"Check terminanted {node_id} {self._get_cached_node(node_id=node_id)}")
        return self._get_cached_node(node_id=node_id) is None

    def node_tags(self, node_id):
        """Returns the tags of the given node (string dict)."""
        node = self.client.get_node_details(node_id)
        tags = node.extra['metadata']
        # print("Returning tags:", tags)
        return tags

    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        # print(f"Node if {node_id}")
        # print(self._get_cached_node(node_id=node_id))
        node_status_data = self._get_cached_node(node_id=node_id)
        print(f"Public IP returned is {node_status_data.public_ips[0]}")
        return node_status_data.public_ips[0]

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_cached_node(node_id=node_id).public_ips[0]

    def create_node(self, node_config, tags, count):
        """Creates a number of nodes within the namespace."""
        target_network = self.client.find_instance_network()
        target_size = self.client.find_instance_size(
            node_config['InstanceType'])
        target_image = self.client.find_instance_image()
        if not target_size or not target_image:
            raise ResourcesUnavailableError

        nodes = []
        for _ in range(count):
            node = self.client.create_node(
                f"{self.cluster_name}-{tags['ray-node-type']}",
                size=target_size,
                image=target_image,
                networks=target_network,
                ex_keyname="sky-key",
                ex_metadata=tags)
            nodes.append(node)
        for node in nodes:
            while node.state not in ['running', 'error']:
                node = self.client.get_node_details(node.id)
                time.sleep(2)
            if node.state == 'error':
                if 'not enough hosts available' in node.extra['fault'][
                        'message']:
                    raise ResourcesUnavailableError
                raise RuntimeError("Unable to launch instance")
            config_tags = node_config.get('tags', {}).copy()
            config_tags.update(tags)
            config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

            self.cached_nodes[node.id] = node

            if node is None:
                raise OVHCloudError('Failed to launch instance.')

            self.set_node_tags(node.id, tags=tags)

    @synchronized
    def set_node_tags(self, node_id, tags):
        """Sets the tag values (string dict) for the specified node."""
        node = self.client.get_node_details(node_id)
        metadata = node.extra['metadata']
        metadata.update(tags)
        self.client.set_metadata(node, metadata)

    def get_command_runner(
        self,
        log_prefix,
        node_id,
        auth_config,
        cluster_name,
        process_runner,
        use_internal_ip,
        docker_config=None,
    ):
        common_args = {
            "log_prefix": log_prefix,
            "node_id": node_id,
            "provider": self,
            "auth_config": auth_config,
            "cluster_name": cluster_name,
            "process_runner": process_runner,
            "use_internal_ip": use_internal_ip,
        }
        return SSHCommandRunner(**common_args)

    def terminate_node(self, node_id):
        """Terminates the specified node."""
        print(f"Destroying node {node_id}")
        node = self.client.get_node_details(node_id)
        self.client.destroy_node(node)

    def _get_node(self, node_id):
        node = self.client.get_node_details(node_id)
        return node

    def _get_cached_node(self, node_id):
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
