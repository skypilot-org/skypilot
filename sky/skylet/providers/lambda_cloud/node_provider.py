import logging
import os
import time
from threading import RLock
from typing import Any, Dict, List, Optional

from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import (
    TAG_RAY_CLUSTER_NAME,
    TAG_RAY_USER_NODE_TYPE,
    TAG_RAY_NODE_NAME,
    TAG_RAY_LAUNCH_CONFIG,
    TAG_RAY_NODE_STATUS,
    STATUS_UP_TO_DATE,
    TAG_RAY_NODE_KIND,
    NODE_KIND_WORKER,
    NODE_KIND_HEAD,
)
from ray.autoscaler._private.util import hash_launch_conf
from sky.skylet.providers.lambda_cloud import lambda_utils
from sky.utils import common_utils

TAG_PATH_PREFIX = '~/.sky/generated/lambda_cloud/metadata'
REMOTE_RAY_YAML = '~/ray_bootstrap_config.yaml'

logger = logging.getLogger(__name__)


def synchronized(f):
    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return f(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class LambdaNodeProvider(NodeProvider):
    """Node Provider for Lambda Cloud.

    This provider assumes Lambda Cloud credentials are set.
    """

    def __init__(self,
                 provider_config: Dict[str, Any],
                 cluster_name: str) -> None:
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self.lambda_client = lambda_utils.LambdaCloudClient()
        self.cached_nodes = {}
        self.metadata = lambda_utils.Metadata(TAG_PATH_PREFIX, cluster_name)
        vms = self._list_instances_in_cluster()

        # The tag file for autodowned clusters is not autoremoved. Hence, if
        # a previous cluster was autodowned and has the same name as the
        # current cluster, then self.metadata might load the old tag file.
        # We prevent this by removing any old vms in the tag file.
        self.metadata.refresh([node['id'] for node in vms])

        # If tag file does not exist on head, create it and add basic tags.
        # This is a hack to make sure that ray on head can access some
        # important tags.
        # TODO(ewzeng): change when Lambda Cloud adds tag support.
        ray_yaml_path = os.path.expanduser(REMOTE_RAY_YAML)
        if os.path.exists(ray_yaml_path) and not os.path.exists(
                self.metadata.path):
            config = common_utils.read_yaml(ray_yaml_path)
            # Ensure correct cluster so sky launch on head node works correctly
            if config['cluster_name'] != cluster_name:
                return
            # Compute launch hash
            head_node_config = config.get('head_node', {})
            head_node_type = config.get('head_node_type')
            if head_node_type:
                head_config = config['available_node_types'][head_node_type]
                head_node_config.update(head_config["node_config"])
            launch_hash = hash_launch_conf(head_node_config, config['auth'])
            # Populate tags
            for node in vms:
                self.metadata[node['id']] = {'tags':
                    {
                        TAG_RAY_CLUSTER_NAME: cluster_name,
                        TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                        TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                        TAG_RAY_USER_NODE_TYPE: 'ray_head_default',
                        TAG_RAY_NODE_NAME: f'ray-{cluster_name}-head',
                        TAG_RAY_LAUNCH_CONFIG: launch_hash,
                    }}

    def _list_instances_in_cluster(self) -> Dict[str, Any]:
        """List running instances in cluster."""
        vms = self.lambda_client.list_instances()
        return [
            node for node in vms
            if node['name'] == self.cluster_name
        ]

    @synchronized
    def _get_filtered_nodes(self,
                            tag_filters: Dict[str, str]) -> Dict[str, Any]:

        def match_tags(vm):
            vm_info = self.metadata[vm['id']]
            tags = {} if vm_info is None else vm_info['tags']
            for k, v in tag_filters.items():
                if tags.get(k) != v:
                    return False
            return True

        vms = self._list_instances_in_cluster()
        nodes = [self._extract_metadata(vm) for vm in filter(match_tags, vms)]
        self.cached_nodes = {node['id']: node for node in nodes}
        return self.cached_nodes

    def _extract_metadata(self, vm: Dict[str, Any]) -> Dict[str, Any]:
        metadata = {'id': vm['id'], 'status': vm['status'], 'tags': {}}
        instance_info = self.metadata[vm['id']]
        if instance_info is not None:
            metadata['tags'] = instance_info['tags']
        ip = vm['ip']
        metadata['external_ip'] = ip
        # TODO(ewzeng): The internal ip is hard to get, so set it to the
        # external ip as a hack. This should be changed in the future.
        #   https://docs.lambdalabs.com/cloud/learn-private-ip-address/
        metadata['internal_ip'] = ip
        return metadata

    def non_terminated_nodes(self, tag_filters: Dict[str, str]) -> List[str]:
        """Return a list of node ids filtered by the specified tags dict.

        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes() must
        be called again to refresh results.

        Examples:
            >>> provider.non_terminated_nodes({TAG_RAY_NODE_KIND: "worker"})
            ["node-1", "node-2"]
        """
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)
        return [k for k, _ in nodes.items()]

    def is_running(self, node_id: str) -> bool:
        """Return whether the specified node is running."""
        return self._get_cached_node(node_id=node_id) is not None

    def is_terminated(self, node_id: str) -> bool:
        """Return whether the specified node is terminated."""
        return self._get_cached_node(node_id=node_id) is None

    def node_tags(self, node_id: str) -> Dict[str, str]:
        """Returns the tags of the given node (string dict)."""
        return self._get_cached_node(node_id=node_id)['tags']

    def external_ip(self, node_id: str) -> str:
        """Returns the external ip of the given node."""
        return self._get_cached_node(node_id=node_id)['external_ip']

    def internal_ip(self, node_id: str) -> str:
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_cached_node(node_id=node_id)['internal_ip']

    def create_node(self,
                    node_config: Dict[str, Any],
                    tags: Dict[str, str],
                    count: int) -> None:
        """Creates a number of nodes within the namespace."""
        assert count == 1, count   # Only support 1-node clusters for now

        # get the tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # create the node
        ttype = node_config['InstanceType']
        region = self.provider_config['region']
        vm_list = self.lambda_client.create_instances(instance_type=ttype,
                                                      region=region,
                                                      quantity=1,
                                                      name=self.cluster_name)
        assert len(vm_list) == 1, len(vm_list)
        vm_id = vm_list[0]
        self.metadata[vm_id] = {'tags': config_tags}

        # Wait for booting to finish
        # TODO(ewzeng): For multi-node, launch all vms first and then wait.
        while True:
            vms = self.lambda_client.list_instances()
            for vm in vms:
                if vm['id'] == vm_id and vm['status'] == 'active':
                    return
            time.sleep(10)

    @synchronized
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)
        self.metadata[node_id] = {'tags': node['tags']}

    def terminate_node(self, node_id: str) -> None:
        """Terminates the specified node."""
        self.lambda_client.remove_instances(node_id)
        self.metadata[node_id] = None

    def _get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        self._get_filtered_nodes({})  # Side effect: updates cache
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
