import logging
from threading import RLock
from sky import authentication as auth
from sky import authentication as auth
from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils import common_utils
import os
from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME
from sky.skylet.providers.fluidstack import fluidstack_utils
import json
import time

logger = logging.getLogger(__name__)


def synchronized(f):

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return f(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class FluidstackNodeProvider(NodeProvider):
    """Node Provider for FluffyCloud."""

    def __init__(self, provider_config, cluster_name):
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self.cached_nodes = {}
        self.fluidstack_client = fluidstack_utils.FluidstackClient()
        # Load credentials
        public_key_path = os.path.expanduser(auth.PUBLIC_SSH_KEY_PATH)
        self.ssh_public_key = None
        with open(public_key_path, "r") as f:
            self.ssh_public_key = f.read().strip()
        assert self.ssh_public_key is not None, "Failed to load public key."

    def _get_filtered_nodes(self, tag_filters):
        running_instances = self.fluidstack_client.list_instances()
        self.cached_nodes = {}
        if not tag_filters:
            for instance in running_instances:
                self.cached_nodes[instance["id"]] = instance
            return self.cached_nodes

        for instance in running_instances:
            for key, value in tag_filters.items():
                if type(instance["tags"]) == str:
                    instance["tags"] = json.loads(instance["tags"])
                if instance["tags"].get(key, None) == value:
                    self.cached_nodes[instance["id"]] = instance
        return self.cached_nodes

    def non_terminated_nodes(self, tag_filters):
        """Return a list of node ids filtered by the specified tags dict.

        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes()
        must be called again to refresh results.
        """
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)
        return [
            k for k, instance in nodes.items()
            if instance['status'] not in ["Terminated", "Error Creating"]
        ]

    def is_running(self, node_id):
        """Return whether the specified node is running."""
        return (self._get_cached_node(node_id=node_id) is not None and
                self._get_cached_node(node_id=node_id)["status"] == "Running")

    def is_terminated(self, node_id):
        """Return whether the specified node is terminated."""
        return self._get_cached_node(node_id=node_id) is None

    def node_tags(self, node_id):
        """Returns the tags of the given node (string dict)."""
        return self._get_cached_node(node_id=node_id)["tags"]

    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        ip = self._get_cached_node(node_id=node_id)["ip"]
        if ip.lower() in ["pending", "provisioning"] or not ip:
            return None
        return ip

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        ip = self._get_cached_node(node_id=node_id)["ip"]
        if ip.lower() in ["pending", "provisioning"] or not ip:
            return None

        return ip

    def create_node(self, node_config, tags, count):
        """Creates a number of nodes within the namespace."""
        assert count == 1, count  # Only support 1-node clusters for now

        # Get the tags
        config_tags = node_config.get("tags", {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create node
        ttype = node_config["InstanceType"]
        region = self.provider_config["region"]
        vm_id = self.fluidstack_client.create_instance(
            instance_type=ttype, region=region, ssh_pub_key=self.ssh_public_key)

        if vm_id is None:
            raise fluidstack_utils.FluidstackAPIError(
                "Failed to launch instance.")

        self.fluidstack_client.add_tags(vm_id, config_tags)
        instances = self.fluidstack_client.list_instances()
        for instance in instances:
            if instance["id"] == vm_id:
                instance["tags"] = config_tags
                self.cached_nodes[vm_id] = instance
        while True:
            time.sleep(30)
            instance = self.fluidstack_client.info(vm_id)
            if instance['status'] == 'Error Creating':
                raise fluidstack_utils.FluidstackAPIError(
                    "Failed to launch instance")
            if instance['status'] == "Running":
                break

        ########
        # TODO #
        ########
        # May need to poll list_instances() to wait for booting
        # to finish before returning.

    @synchronized
    def set_node_tags(self, node_id, tags):
        """Sets the tag values (string dict) for the specified node."""
        self.fluidstack_client.add_tags(node_id, tags)

    def terminate_node(self, node_id):
        """Terminates the specified node."""
        self.fluidstack_client.delete(node_id)

    def _get_node(self, node_id):
        self.fluidstack_client.info(node_id)

    def _get_cached_node(self, node_id):
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
