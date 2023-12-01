"""RunPod Node Provider. A RunPod pod is a node within the Sky paradigm.

Node provider is called by the Ray Autoscaler to provision new compute resources.

To show debug messages, export SKYPILOT_DEBUG=1

Class definition: https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/node_provider.py
"""

import logging
import os
import subprocess
from threading import RLock
import time
from types import ModuleType
from typing import Any, Dict, List, Optional

from ray.autoscaler._private.command_runner import DockerCommandRunner
from ray.autoscaler._private.command_runner import SSHCommandRunner
from ray.autoscaler.command_runner import CommandRunnerInterface
from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import NODE_KIND_HEAD
from ray.autoscaler.tags import NODE_KIND_WORKER
from ray.autoscaler.tags import STATUS_UP_TO_DATE
from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME
from ray.autoscaler.tags import TAG_RAY_NODE_KIND
from ray.autoscaler.tags import TAG_RAY_NODE_NAME
from ray.autoscaler.tags import TAG_RAY_NODE_STATUS
from ray.autoscaler.tags import TAG_RAY_USER_NODE_TYPE

from sky import authentication as auth
import sky.clouds.utils.runpod_utils as runpod_api
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils

_REMOTE_RAY_YAML = '~/ray_bootstrap_config.yaml'
_REMOTE_RAY_SSH_KEY = '~/ray_bootstrap_key.pem'
_GET_INTERNAL_IP_CMD = r'ip -4 -br addr show | grep UP | grep -Eo "(10\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)|172\.(1[6-9]|2[0-9][0-9]?|3[0-1]))\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"'

logger = logging.getLogger(__name__)


# Monkey patch SSHCommandRunner to allow specifying SSH port
def set_port(self, port):
    self.ssh_options.arg_dict['Port'] = port


SSHCommandRunner.set_port = set_port


class RunPodError(Exception):
    pass


def synchronized(func):
    """Decorator for synchronizing access to a method across threads."""

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return func(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class RunPodNodeProvider(NodeProvider):
    """ Node Provider for RunPod. """

    def __init__(self, provider_config: Dict[str, Any],
                 cluster_name: str) -> None:
        """ Initialize the RunPodNodeProvider.
        cached_nodes | The list of nodes that have been cached for quick access.
        ssh_key_name | The name of the SSH key to use for the pods.
        """
        NodeProvider.__init__(self, provider_config, cluster_name)

        self.lock = RLock()
        self.cached_nodes: Dict[str, Dict[str, Any]] = {}
        ray_yaml_path = os.path.expanduser(_REMOTE_RAY_YAML)
        self.on_head = (os.path.exists(ray_yaml_path) and
                        common_utils.read_yaml(ray_yaml_path)['cluster_name']
                        == cluster_name)
        self.ssh_key_path = os.path.expanduser(auth.PRIVATE_SSH_KEY_PATH)

        if self.on_head:
            self.ssh_key_path = os.path.expanduser(_REMOTE_RAY_SSH_KEY)

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

    def external_port(self, node_id):
        """Returns the external SSH port of the given node."""
        return self._get_node(node_id=node_id)['ssh_port']

    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_node(node_id=node_id)['internal_ip']

    def create_node(self, node_config: Dict[str, Any], tags: Dict[str, str],
                    count: int) -> Optional[Dict[str, Any]]:
        """Creates a number of nodes within the namespace."""
        # Get the tags
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name

        # Create nodes
        ttype = node_config['InstanceType']
        region = self.provider_config['region']

        if config_tags[TAG_RAY_NODE_KIND] == NODE_KIND_HEAD:
            name = f'{self.cluster_name}-head'
            # Occasionally, the head node will continue running for a short
            # period after termination. This can lead to the following bug:
            #   1. Head node autodowns but continues running.
            #   2. The next autodown event is triggered, which executes ray up.
            #   3. Head node stops running.
            # In this case, a new head node is created after the cluster has
            # terminated. We avoid this with the following check:
            if self.on_head:
                raise RuntimeError('Head already exists.')
        else:
            name = f'{self.cluster_name}-worker'

        for _ in range(count):
            instance_id = runpod_api.launch(name=name,
                                            instance_type=ttype,
                                            region=region)

        if instance_id is None:
            raise RunPodError('Failed to launch instance.')

        runpod_api.set_tags(instance_id, config_tags)

        instance_status = runpod_api.list_instances().get(instance_id, {})
        while not (instance_status.get('status') == "RUNNING" and
                   instance_status.get('ssh_port')):
            time.sleep(3)
            instance_status = runpod_api.list_instances().get(instance_id, {})

        print(f"Instance {instance_id} is running and ready to use.")

        return instance_id

    @synchronized
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)
        runpod_api.set_tags(node_id, node['tags'])
        return None

    def terminate_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Terminates the specified node."""
        runpod_api.remove(node_id)
        return None

    @synchronized
    def _get_filtered_nodes(self, tag_filters: Dict[str,
                                                    str]) -> Dict[str, Any]:
        """SkyPilot Method
        Caches the nodes with the given tag_filters.
        """

        def _get_internal_ip(node: Dict[str, Any]):
            # TODO(ewzeng): cache internal ips in metadata file to reduce
            # ssh overhead.
            if node['ip'] is None:
                node['internal_ip'] = None
                return

            retry_cnt = 0
            while True:
                runner = command_runner.SSHCommandRunner(node['ip'],
                                                         'root',
                                                         self.ssh_key_path,
                                                         port=node['ssh_port'])
                rc, stdout, stderr = runner.run(_GET_INTERNAL_IP_CMD,
                                                require_outputs=True,
                                                stream_logs=False)
                if rc != 255:
                    break
                if retry_cnt >= 3:
                    # This is a common error that occurs when:
                    # 1. network glitch happens.
                    # 2. we are on the head node. RunPod's special IP firewall
                    #   rules prevent us from accessing the node itself via
                    #   its external IP.
                    node['internal_ip'] = None
                    if self.on_head:
                        # This is a hack to get the internal IP on the head node
                        # as ray autoscaler requires the internal IP to match
                        # the heartbeat IP.
                        proc = subprocess_utils.run(_GET_INTERNAL_IP_CMD,
                                                    stdout=subprocess.PIPE)
                        stdout = proc.stdout.decode('utf-8').strip()
                        if not proc.returncode and stdout:
                            node['internal_ip'] = stdout
                    return
                retry_cnt += 1
                time.sleep(1)
            subprocess_utils.handle_returncode(
                rc,
                _GET_INTERNAL_IP_CMD,
                'Failed get obtain private IP from node',
                stderr=stdout + stderr)
            node['internal_ip'] = stdout.strip()

        instances = runpod_api.list_instances()
        possible_names = [
            f'{self.cluster_name}-head', f'{self.cluster_name}-worker'
        ]

        filtered_nodes = {}
        for instance_id, instance in instances.items():
            if instance['status'] not in [
                    'CREATED', 'RUNNING', 'RESTARTING', 'PAUSED'
            ]:
                continue
            if instance.get('name') in possible_names:
                filtered_nodes[instance_id] = instance
        self._guess_and_add_missing_tags(filtered_nodes)
        subprocess_utils.run_in_parallel(_get_internal_ip,
                                         list(filtered_nodes.values()))
        return filtered_nodes

    def _guess_and_add_missing_tags(self, vms: Dict[str, Any]) -> None:
        """Adds missing vms to local tag file and guesses their tags."""
        for node in vms.values():
            if node.get('tags', None):
                pass
            elif node['name'] == f'{self.cluster_name}-head':
                node['tags'] = {
                    TAG_RAY_CLUSTER_NAME: self.cluster_name,
                    TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                    TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                    TAG_RAY_USER_NODE_TYPE: 'ray_head_default',
                    TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-head',
                }
            elif node['name'] == f'{self.cluster_name}-worker':
                node['tags'] = {
                    TAG_RAY_CLUSTER_NAME: self.cluster_name,
                    TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                    TAG_RAY_NODE_KIND: NODE_KIND_WORKER,
                    TAG_RAY_USER_NODE_TYPE: 'ray_worker_default',
                    TAG_RAY_NODE_NAME: f'ray-{self.cluster_name}-worker',
                }

    def _get_node(self, node_id: str):
        """ SkyPilot Method
        Returns the node with the given node_id, if it exists.
        """
        instances = self._get_filtered_nodes(tag_filters={})
        return instances.get(node_id, None)

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

        NOTE: RunPod forwards internal ports to a pool of external ports.
        The default internal port is 22, but the external port will be
        different and needs to be read from the node's metadata.

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

        command_runner = SSHCommandRunner(**common_args)
        if use_internal_ip:
            port = 22
            print(f"Using internal port {port} for node {node_id}")
        else:
            port = self.external_port(node_id)
            print(f"Using port {port} for node {node_id}")
        command_runner.set_port(port)

        if docker_config and docker_config["container_name"] != "":
            return DockerCommandRunner(docker_config, **common_args)
        else:
            return command_runner
