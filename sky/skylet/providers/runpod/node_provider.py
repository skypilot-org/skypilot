"""RunPod Node Provider. A RunPod pod is a node within the Sky paradigm.

Node provider is called by the Ray Autoscaler to provision new compute resources.

To show debug messages, export SKYPILOT_DEBUG=1

Class definition: https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/node_provider.py
"""

import time
import logging
from threading import RLock
from typing import Any, Dict, List, Optional

from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME

import sky.skylet.providers.runpod.rp_helper as runpod_api

logger = logging.getLogger(__name__)


class RunPodError(Exception):
    pass


def synchronized(func):
    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return func(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class RunPodNodeProvider(NodeProvider):
    """ Node Provider for RunPod. """

    def __init__(self, provider_config: Dict[str, Any], cluster_name: str) -> None:
        """ Initialize the RunPodNodeProvider.
        cached_nodes | The list of nodes that have been cached for quick access.
        ssh_key_name | The name of the SSH key to use for the pods.
        """
        NodeProvider.__init__(self, provider_config, cluster_name)

        self.lock = RLock()
        self.cached_nodes: Dict[str, Dict[str, Any]] = {}

    def non_terminated_nodes(self, tag_filters: Dict[str, str]) -> List[str]:
        """Return a list of node ids filtered by the specified tags dict.

        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes()
        must be called again to refresh results.
        """
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)
        return [node_id for node_id, _ in nodes.items()]

    def is_running(self, node_id):
        """Return whether the specified node is running."""
        return self._get_node(node_id=node_id) is not None

    def is_terminated(self, node_id):
        """Return whether the specified node is terminated."""
        return self._get_node(node_id=node_id) is None

    def node_tags(self, node_id):
        """Returns the tags of the given node (string dict)."""
        return self._get_node(node_id=node_id)['tags']

    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        return self._get_node(node_id=node_id)['ip']

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_node(node_id=node_id)['ip']

    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str], count: int) -> Optional[Dict[str, Any]]:
        """Creates a number of nodes within the namespace."""
        # Get the tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create nodes
        ttype = node_config['InstanceType']
        region = self.provider_config['region']

        for _ in range(count):
            instance_id = runpod_api.launch(name=self.cluster_name,
                                            instance_type=ttype,
                                            region=region
                                            )

        if instance_id is None:
            raise RunPodError('Failed to launch instance.')

        runpod_api.set_tags(instance_id, config_tags)

        instance_status = "PENDING"
        while instance_status != "RUNNING":
            time.sleep(1)
            instance_status = runpod_api.list_instances()[instance_id]['status']

    @synchronized
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)
        runpod_api.set_tags(node_id, node['tags'])

    def terminate_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Terminates the specified node."""
        runpod_api.remove(node_id)

    @synchronized
    def _get_filtered_nodes(self, tag_filters: Dict[str, str]) -> Dict[str, Any]:
        """SkyPilot Method
        Caches the nodes with the given tag_filters.
        """
        instances = runpod_api.list_instances()

        filtered_nodes = {}
        for instance_id, instance in instances.items():
            if instance['status'] not in ['CREATED', 'RUNNING', 'RESTARTING', 'PAUSED']:
                continue
            if any(tag in instance['tags'] for tag in tag_filters):
                filtered_nodes[instance_id] = instance

        return filtered_nodes

    def _get_node(self, node_id: str):
        """ SkyPilot Method
        Returns the node with the given node_id, if it exists.
        """
        instances = runpod_api.list_instances()
        for instance_id, instance in instances.items():
            if instance_id == node_id:
                return instance

        return None
