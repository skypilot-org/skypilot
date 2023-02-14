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
from sky.skylet.providers.lambda_cloud import lambda_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils

TAG_PATH_PREFIX = '~/.sky/generated/lambda_cloud/metadata'
REMOTE_RAY_YAML = '~/ray_bootstrap_config.yaml'
REMOTE_RAY_SSH_KEY = '~/ray_bootstrap_key.pem'
GET_INTERNAL_IP_CMD = 'ip -4 -br addr show | grep -Eo "10\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"'

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

    def __init__(self, provider_config: Dict[str, Any],
                 cluster_name: str) -> None:
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self.lambda_client = lambda_utils.LambdaCloudClient()
        self.cached_nodes = {}
        self.metadata = lambda_utils.Metadata(TAG_PATH_PREFIX, cluster_name)

        # Check if running on head node of cluster
        self.on_head = False
        ray_yaml_path = os.path.expanduser(REMOTE_RAY_YAML)
        if os.path.exists(ray_yaml_path):
            config = common_utils.read_yaml(ray_yaml_path)
            if config['cluster_name'] == cluster_name:
                self.on_head = True

    def _guess_and_add_missing_tags(self, vms: Dict[str, Any]) -> None:
        """Adds missing vms to local tag file and guesses their tags."""
        for node in vms:
            if self.metadata.exists(node['id']):
                pass
            elif node['name'] == f'{self.cluster_name}-head':
                self.metadata[node['id']] = {
                    'tags': {
                        TAG_RAY_CLUSTER_NAME: self.cluster_name,
                        TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                        TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                        TAG_RAY_USER_NODE_TYPE: 'ray_head_default',
                        TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-head',
                    }
                }
            elif node['name'] == f'{self.cluster_name}-worker':
                self.metadata[node['id']] = {
                    'tags': {
                        TAG_RAY_CLUSTER_NAME: self.cluster_name,
                        TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                        TAG_RAY_NODE_KIND: NODE_KIND_WORKER,
                        TAG_RAY_USER_NODE_TYPE: 'ray_worker_default',
                        TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-worker',
                    }
                }

    def _list_instances_in_cluster(self) -> Dict[str, Any]:
        """List running instances in cluster."""
        vms = self.lambda_client.list_instances()
        possible_names = [
            f'{self.cluster_name}-head', f'{self.cluster_name}-worker'
        ]
        return [node for node in vms if node.get('name') in possible_names]

    @synchronized
    def _get_filtered_nodes(self, tag_filters: Dict[str,
                                                    str]) -> Dict[str, Any]:

        def _extract_metadata(vm: Dict[str, Any]) -> Dict[str, Any]:
            metadata = {'id': vm['id'], 'status': vm['status'], 'tags': {}}
            instance_info = self.metadata[vm['id']]
            if instance_info is not None:
                metadata['tags'] = instance_info['tags']
            ip = vm['ip']
            metadata['external_ip'] = ip
            # Optimization: it is dificult to get the internal ip, so set
            # internal ip to external ip. On the head node, where internal ip
            # actually matters, we run _get_internal_ip()
            metadata['internal_ip'] = ip
            return metadata

        def _match_tags(vm: Dict[str, Any]):
            vm_info = self.metadata[vm['id']]
            tags = {} if vm_info is None else vm_info['tags']
            for k, v in tag_filters.items():
                if tags.get(k) != v:
                    return False
            return True

        def _get_internal_ip(node: Dict[str, Any]):
            runner = command_runner.SSHCommandRunner(
                node['external_ip'], 'ubuntu',
                os.path.expanduser(REMOTE_RAY_SSH_KEY))
            out = runner.run(GET_INTERNAL_IP_CMD, require_outputs=True)
            assert out[0] == 0
            node['internal_ip'] = out[1].strip()

        vms = self._list_instances_in_cluster()
        self._guess_and_add_missing_tags(vms)
        nodes = [_extract_metadata(vm) for vm in filter(_match_tags, vms)]
        if self.on_head:
            subprocess_utils.run_in_parallel(_get_internal_ip, nodes)
        self.cached_nodes = {node['id']: node for node in nodes}
        return self.cached_nodes

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

    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str],
                    count: int) -> None:
        """Creates a number of nodes within the namespace."""
        # Get tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create nodes
        ttype = node_config['InstanceType']
        region = self.provider_config['region']
        if config_tags[TAG_RAY_NODE_KIND] == NODE_KIND_HEAD:
            name = f'{self.cluster_name}-head'
        else:
            name = f'{self.cluster_name}-worker'
        # Lambda launch api only supports launching one node at a time,
        # so we do a loop. Remove loop when launch api allows quantity > 1
        booting_list = []
        for _ in range(count):
            vm_id = self.lambda_client.create_instances(instance_type=ttype,
                                                        region=region,
                                                        quantity=1,
                                                        name=name)[0]
            self.metadata[vm_id] = {'tags': config_tags}
            booting_list.append(vm_id)
            time.sleep(10)  # Avoid api rate limits

        # Wait for nodes to finish booting
        while True:
            vms = self._list_instances_in_cluster()
            for vm_id in booting_list.copy():
                for vm in vms:
                    if vm['id'] == vm_id and vm['status'] == 'active':
                        booting_list.remove(vm_id)
            if len(booting_list) == 0:
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
