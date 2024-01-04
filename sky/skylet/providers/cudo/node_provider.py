"""
Methods that start with an underscore (_) are specific to SkyPilot,
and not part of the ray NodeProvider class definition.
"""
import logging
import os
from threading import RLock
import time
from typing import Any, Dict, List, Optional

from cudo_compute import cudo_api
from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME

from sky import authentication as auth
from sky.skylet.providers.cudo.cudo_machine_type import get_spec_from_instance
import sky.skylet.providers.cudo.cudo_wrapper as cudo_wrapper
from sky.skylet.providers.oci import utils

logger = logging.getLogger(__name__)


class CudoError(Exception):
    pass


def synchronized(func):

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return func(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class CudoNodeProvider(NodeProvider):
    """Node Provider for Cudo."""

    def __init__(self, provider_config, cluster_name):
        NodeProvider.__init__(self, provider_config, cluster_name)

        self.lock = RLock()
        self.cached_nodes = {}

        api_key, error = cudo_api.get_api_key()
        self.api_key = api_key
        self.ssh_key_path = os.path.expanduser(auth.PUBLIC_SSH_KEY_PATH)
        self.ssh_key_name = None

    @utils.debug_enabled(logger=logger)
    def non_terminated_nodes(self, tag_filters: Dict[str, str]) -> List[str]:
        """Return a list of node ids filtered by the specified tags dict.

        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes()
        must be called again to refresh results.
        """
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)

        # TEMPLATE ACTION: Filter out terminated nodes

        return [node_id for node_id, _ in nodes.items()]

    @utils.debug_enabled(logger=logger)
    def is_running(self, node_id: str) -> bool:
        """Return whether the specified node is running."""
        return self._get_cached_node(node_id=node_id) is not None

    @utils.debug_enabled(logger=logger)
    def is_terminated(self, node_id: str) -> bool:
        """Return whether the specified node is terminated."""
        return self._get_cached_node(node_id=node_id) is None

    @utils.debug_enabled(logger=logger)
    def node_tags(self, node_id: str) -> Dict[str, str]:
        """Returns the tags of the given node (string dict)."""
        return self._get_cached_node(node_id=node_id)['tags']

    @utils.debug_enabled(logger=logger)
    def external_ip(self, node_id: str) -> str:
        """Returns the external ip of the given node."""
        return self._get_cached_node(node_id=node_id)['ip']

    @utils.debug_enabled(logger=logger)
    def internal_ip(self, node_id: str) -> str:
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_cached_node(node_id=node_id)['ip']

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
    @utils.debug_enabled(logger=logger)
    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str],
                    count: int) -> Optional[Dict[str, Any]]:
        logger.info(node_config)
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create nodes
        ttype = node_config['InstanceType']
        data_center_id = self.provider_config['region']

        with open(self.ssh_key_path, 'r') as f:
            public_key = f.read().strip()
        instance_ids = []
        for _ in range(count):
            spec = get_spec_from_instance(ttype, data_center_id)

            instance_id = cudo_wrapper.launch(
                name=self.cluster_name,
                ssh_key=public_key,
                data_center_id=data_center_id,
                machine_type=spec['machine_type'],
                memory_gib=int(spec['mem_gb']),
                vcpu_count=int(spec['vcpu_count']),
                gpu_count=int(float(spec['gpu_count'])),
                gpu_model=spec['gpu_model'],
                tags=config_tags)
            if instance_id is None:
                raise CudoError('Failed to launch instance.')

            instance_ids.append(instance_id)
            cudo_wrapper.set_tags(instance_id, config_tags)

        retries = 12  # times 10 second
        period = 10  # seconds
        results = {}
        for id in instance_ids:
            n = 0
            while n in range(retries):
                vm = cudo_wrapper.get_instance(id)
                if vm['vm']['short_state'] == 'runn':
                    results[id] = vm['vm']
                    break
                else:
                    time.sleep(period)
        return results

    @synchronized
    @utils.debug_enabled(logger=logger)
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)
        cudo_wrapper.set_tags(node_id, node['tags'])

    @synchronized
    @utils.debug_enabled(logger=logger)
    def terminate_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Terminates the specified node."""
        cudo_wrapper.terminate(node_id)
        return {}

    @synchronized
    def _get_filtered_nodes(self, tag_filters: Dict[str,
                                                    str]) -> Dict[str, Any]:
        """
        SkyPilot Method
        Caches the nodes with the given tag_filters.

        Return Example:
        {
            instance_id_1: {
                status: ...,
                tags: ...,
                name: ...,
                ip: ....
            },
            instance_id_2: {...},
            ...
        }

        Each instance needs to have a dictionary with the following keys:
            - status: str
            - tags: Dict[str, str]
            - name: str
            - ip: str
        """
        instances = cudo_wrapper.list_instances()

        new_cache = {}
        for instance_id, instance in instances.items():
            if instance['status'] != 'runn':
                continue

            if tag_filters == {}:
                new_cache[instance_id] = instance
            elif any(tag in instance['tags'] for tag in tag_filters):
                new_cache[instance_id] = instance

        self.cached_nodes = new_cache
        return self.cached_nodes

    def _get_node(self, node_id: str):
        """
        SkyPilot Method
        Returns the node with the given node_id, if it exists.
        """
        self._get_filtered_nodes({})  # Side effect: updates cache
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id):
        """
        SkyPilot Method
        Returns the node with the given node_id, if it is cached.
        """
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
