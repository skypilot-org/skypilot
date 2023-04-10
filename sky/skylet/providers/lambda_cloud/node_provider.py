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
    TAG_RAY_NODE_STATUS,
    STATUS_UP_TO_DATE,
    TAG_RAY_NODE_KIND,
    NODE_KIND_WORKER,
    NODE_KIND_HEAD,
)
from sky.skylet.providers.lambda_cloud import lambda_utils
from sky import authentication as auth
from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import ux_utils

_TAG_PATH_PREFIX = '~/.sky/generated/lambda_cloud/metadata'
_REMOTE_RAY_SSH_KEY = '~/ray_bootstrap_key.pem'
_GET_INTERNAL_IP_CMD = 'ip -4 -br addr show | grep -Eo "10\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"'

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
        self.cached_nodes: Dict[str, Dict[str, Any]] = {}
        self.metadata = lambda_utils.Metadata(_TAG_PATH_PREFIX, cluster_name)
        self.ssh_key_path = os.path.expanduser(auth.PRIVATE_SSH_KEY_PATH)
        remote_ssh_key = os.path.expanduser(_REMOTE_RAY_SSH_KEY)
        if os.path.exists(remote_ssh_key):
            self.ssh_key_path = remote_ssh_key

    def _guess_and_add_missing_tags(self, vms: List[Dict[str, Any]]) -> None:
        """Adds missing vms to local tag file and guesses their tags."""
        for node in vms:
            if self.metadata.get(node['id']) is not None:
                pass
            elif node['name'] == f'{self.cluster_name}-head':
                self.metadata.set(
                    node['id'], {
                        'tags': {
                            TAG_RAY_CLUSTER_NAME: self.cluster_name,
                            TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                            TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                            TAG_RAY_USER_NODE_TYPE: 'ray_head_default',
                            TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-head',
                        }
                    })
            elif node['name'] == f'{self.cluster_name}-worker':
                self.metadata.set(
                    node['id'], {
                        'tags': {
                            TAG_RAY_CLUSTER_NAME: self.cluster_name,
                            TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                            TAG_RAY_NODE_KIND: NODE_KIND_WORKER,
                            TAG_RAY_USER_NODE_TYPE: 'ray_worker_default',
                            TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-worker',
                        }
                    })

    def _list_instances_in_cluster(self) -> List[Dict[str, Any]]:
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
            instance_info = self.metadata.get(vm['id'])
            if instance_info is not None:
                metadata['tags'] = instance_info['tags']
            with ux_utils.print_exception_no_traceback():
                if 'ip' not in vm:
                    raise lambda_utils.LambdaCloudError(
                        'A node ip address was not found. Either '
                        '(1) Lambda Cloud has internally errored, or '
                        '(2) the cluster is still booting. '
                        'You can manually terminate the cluster on the '
                        'Lambda Cloud console or (in case 2) wait for '
                        'booting to finish (~2 minutes).')
            metadata['external_ip'] = vm['ip']
            return metadata

        def _match_tags(vm: Dict[str, Any]):
            vm_info = self.metadata.get(vm['id'])
            tags = {} if vm_info is None else vm_info['tags']
            for k, v in tag_filters.items():
                if tags.get(k) != v:
                    return False
            return True

        def _get_internal_ip(node: Dict[str, Any]):
            # TODO(ewzeng): cache internal ips in metadata file to reduce
            # ssh overhead.
            runner = command_runner.SSHCommandRunner(node['external_ip'],
                                                     'ubuntu',
                                                     self.ssh_key_path)
            rc, stdout, stderr = runner.run(_GET_INTERNAL_IP_CMD,
                                            require_outputs=True,
                                            stream_logs=False)
            subprocess_utils.handle_returncode(
                rc,
                _GET_INTERNAL_IP_CMD,
                'Failed get obtain private IP from node',
                stderr=stdout + stderr)
            node['internal_ip'] = stdout.strip()

        vms = self._list_instances_in_cluster()
        self.metadata.refresh([node['id'] for node in vms])
        self._guess_and_add_missing_tags(vms)
        nodes = [_extract_metadata(vm) for vm in filter(_match_tags, vms)]
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
        node = self._get_cached_node(node_id=node_id)
        if node is None:
            return {}
        return node['tags']

    def external_ip(self, node_id: str) -> Optional[str]:
        """Returns the external ip of the given node."""
        node = self._get_cached_node(node_id=node_id)
        if node is None:
            return None
        return node.get('external_ip')

    def internal_ip(self, node_id: str) -> Optional[str]:
        """Returns the internal ip (Ray ip) of the given node."""
        node = self._get_cached_node(node_id=node_id)
        if node is None:
            return None
        return node.get('internal_ip')

    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str],
                    count: int) -> None:
        """Creates a number of nodes within the namespace."""
        # Get tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create nodes
        instance_type = node_config['InstanceType']
        region = self.provider_config['region']
        if config_tags[TAG_RAY_NODE_KIND] == NODE_KIND_HEAD:
            name = f'{self.cluster_name}-head'
        else:
            name = f'{self.cluster_name}-worker'
        # Lambda launch api only supports launching one node at a time,
        # so we do a loop. Remove loop when launch api allows quantity > 1
        booting_list = []
        for _ in range(count):
            vm_id = self.lambda_client.create_instances(
                instance_type=instance_type,
                region=region,
                quantity=1,
                name=name)[0]
            self.metadata.set(vm_id, {'tags': config_tags})
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
        assert node is not None, node_id
        node['tags'].update(tags)
        self.metadata.set(node_id, {'tags': node['tags']})

    def terminate_node(self, node_id: str) -> None:
        """Terminates the specified node."""
        self.lambda_client.remove_instances(node_id)
        self.metadata.set(node_id, None)

    def _get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        self._get_filtered_nodes({})  # Side effect: updates cache
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
