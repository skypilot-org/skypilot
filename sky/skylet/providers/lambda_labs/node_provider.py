import logging
import time
from threading import RLock

from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME
from sky.skylet.providers.lambda_labs.lambda_utils import LambdaClient, Metadata

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
    """Node Provider for Lambda Labs.

    This provider assumes Lambda credentials have already been set up.

    Nodes may be in one of three states: {pending, running, terminated}. Nodes
    appear immediately once started by ``create_node``, and transition
    immediately to terminated when ``terminate_node`` is called.
    """

    def __init__(self, provider_config, cluster_name):
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()

        # Assumes lambda authentication has been set up.
        self.lambda_client = LambdaClient()
        # Only used for tags
        self.local_metadata = Metadata(cluster_name)
        # Cache node objects
        self.cached_nodes = {}

    @synchronized
    def _get_filtered_nodes(self, tag_filters):

        def match_tags(vm):
            vm_info = self.local_metadata[vm['id']]
            tags = {} if vm_info is None else vm_info['tags']
            for k, v in tag_filters.items():
                if tags.get(k) != v:
                    return False
            return True

        vms = self.lambda_client.ls().get('data', [])
        vms = [
            node for node in vms
            if node['name'] == self.cluster_name
        ]
        nodes = [self._extract_metadata(vm) for vm in filter(match_tags, vms)]
        self.cached_nodes = {node['id']: node for node in nodes}
        return self.cached_nodes

    def _extract_metadata(self, vm):
        metadata = {'id': vm['id'], 'status': vm['status'], 'tags': {}}
        instance_info = self.local_metadata[vm['id']]
        if instance_info is not None:
            metadata['tags'] = instance_info['tags']
        ip = vm['ip']
        metadata['external_ip'] = ip
        metadata['internal_ip'] = ip
        return metadata

    def non_terminated_nodes(self, tag_filters):
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

    def is_running(self, node_id):
        """Return whether the specified node is running."""
        return self._get_node(node_id=node_id) is not None

    def is_terminated(self, node_id):
        """Return whether the specified node is terminated."""
        return self._get_node(node_id=node_id) is None

    def node_tags(self, node_id):
        """Returns the tags of the given node (string dict)."""
        return self._get_cached_node(node_id=node_id)['tags']

    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        return self._get_cached_node(node_id=node_id)['external_ip']

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_cached_node(node_id=node_id)['internal_ip']

    def create_node(self, node_config, tags, count):
        assert count == 1, count   # Only support 1-node clusters for now
        self._create_node(node_config, tags, count)

    def _create_node(self, node_config, tags, count):
        """Creates a number of nodes within the namespace."""
        del count  # unused

        # get the tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # create the node
        ttype = node_config['InstanceType']
        region = self.provider_config['region']
        vm_resp = self.lambda_client.up(instance_type=ttype,
                                        region=region,
                                        quantity=1,
                                        name=self.cluster_name)
        vm_list = vm_resp.get('data', []).get('instance_ids', [])
        assert len(vm_list) == 1, len(vm_list)
        vm_id = vm_list[0]
        self.local_metadata[vm_id] = {'tags': config_tags}

        # Wait for booting to finish
        # TODO(ewzeng): For multi-node, launch all vms first and then wait.
        while True:
            vms = self.lambda_client.ls().get('data', [])
            for vm in vms:
                if vm['id'] == vm_id and vm['status'] == 'active':
                    return
            time.sleep(10)

    @synchronized
    def set_node_tags(self, node_id, tags):
        """Sets the tag values (string dict) for the specified node."""
        node_tags = self._get_cached_node(node_id)['tags']
        node_tags.update(tags)
        self.local_metadata[node_id] = {"tags": node_tags}

    def terminate_node(self, node_id):
        """Terminates the specified node."""
        self.lambda_client.rm(node_id)
        self.local_metadata[node_id] = None

    def _get_node(self, node_id):
        self._get_filtered_nodes({})  # Side effect: updates cache
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id):
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)
