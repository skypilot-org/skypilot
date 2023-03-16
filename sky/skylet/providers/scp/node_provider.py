import logging
import os
import time
from threading import RLock
from typing import Any, Dict, List, Optional
import copy

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
from sky.skylet.providers.scp import scp_utils
from sky.skylet.providers.scp.config import ZoneConfig
from sky.skylet.providers.scp.scp_utils import SCPClientError
from sky.utils import common_utils

TAG_PATH_PREFIX = '~/.sky/generated/scp/metadata'
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




class SCPError(Exception):
    pass


class SCPNodeProvider(NodeProvider):
    """Node Provider for Lambda Cloud.

    This provider assumes Lambda Cloud credentials are set.
    """
    def __init__(self,
                 provider_config: Dict[str, Any],
                 cluster_name: str) -> None:
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self.scp_client = scp_utils.SCPClient()

        self.cached_nodes = {}
        self.metadata = scp_utils.Metadata(TAG_PATH_PREFIX, cluster_name)
        vms = self._list_instances_in_cluster()

        # The tag file for autodowned clusters is not autoremoved. Hence, if
        # a previous cluster was autodowned and has the same name as the
        # current cluster, then self.metadata might load the old tag file.
        # We prevent this by removing any old vms in the tag file.
        self.metadata.refresh([node['virtualServerId'] for node in vms])

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
                self.metadata[node['virtualServerId']] = {'tags':
                    {
                        TAG_RAY_CLUSTER_NAME: cluster_name,
                        TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
                        TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                        TAG_RAY_USER_NODE_TYPE: 'ray_head_default',
                        TAG_RAY_NODE_NAME: f'ray-{cluster_name}-head',
                        TAG_RAY_LAUNCH_CONFIG: launch_hash,
                    }}

    def _list_instances_in_cluster(self) -> List[Dict[str, Any]]:
        """List running instances in cluster."""
        vms = self.scp_client.list_instances()
        node_list = []
        for node in vms:
            if node['virtualServerName'] == self.cluster_name:
                node['external_ip'] = self.scp_client.get_external_ip(virtual_server_id=node['virtualServerId'],
                                                                      ip=node['ip'])
                node_list.append(node)

        return node_list

    @synchronized
    def _get_filtered_nodes(self,
                            tag_filters: Dict[str, str]) -> Dict[str, Any]:

        def match_tags(vm):
            vm_info = self.metadata[vm['virtualServerId']]
            tags = {} if vm_info is None else vm_info['tags']
            for k, v in tag_filters.items():
                if tags.get(k) != v:
                    return False
            return True

        vms = self._list_instances_in_cluster()
        nodes = [self._extract_metadata(vm) for vm in filter(match_tags, vms)]
        self.cached_nodes = {node['virtualServerId']: node for node in nodes}
        return self.cached_nodes

    def _extract_metadata(self, vm: Dict[str, Any]) -> Dict[str, Any]:
        metadata = { 'virtualServerId': vm['virtualServerId'], 'virtualServerName': vm['virtualServerName'],
                    'status': vm['virtualServerState'], 'tags': {}}
        instance_info = self.metadata[vm['virtualServerId']]
        if instance_info is not None:
            metadata['tags'] = instance_info['tags']
        # TODO(ewzeng): The internal ip is hard to get, so set it to the
        # external ip as a hack. This should be changed in the future.
        #   https://docs.lambdalabs.com/cloud/learn-private-ip-address/
        metadata['internal_ip'] = vm['ip']
        metadata['external_ip'] = vm['external_ip']
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

    def _config_security_group(self, zone_id, product_group,vpc, sg_name):
        response = self.scp_client.create_security_group(zone_id, product_group, vpc, sg_name)
        sg_id = response['resourceId']

        while True:
            sg_contents = self.scp_client.list_security_groups( vpc_id=vpc, sg_name=sg_name)
            sg = [sg["securityGroupState"] for sg in sg_contents if sg["securityGroupId"] == sg_id]
            if len(sg) !=0 and sg[0] == "ACTIVE" : break
            time.sleep(5)

        self.scp_client.add_security_group_rule(sg_id)
        return sg_id

    def _del_security_group(self, sg_id):
        self.scp_client.del_security_group(sg_id)
        while True:
            time.sleep(5)
            sg_contents = self.scp_client.list_security_groups()
            sg = [sg["securityGroupState"] for sg in sg_contents if sg["securityGroupId"] == sg_id]
            if len(sg) ==0: break



    def _del_vm(self, vm_id):
        self.scp_client.terminate_instance(vm_id)
        while True:
            time.sleep(10)
            vm_contents = self.scp_client.list_instances()
            vms = [vm["virtualServerId"] for vm in vm_contents if vm["virtualServerId"] == vm_id]
            if len(vms) == 0: break

    def _del_firwall_inbound(self, firewall_id, rule_id):
        self.scp_client.del_firwall_rule(firewall_id, rule_id)



    def _add_firewall_inbound(self, vpc_id, internal_ip):

        firewall_contents = self.scp_client.list_firwalls()
        firewall_id = [firewall['firewallId'] for firewall in firewall_contents
                       if firewall['vpcId']==vpc_id and firewall['firewallState']=='ACTIVE'][0]
        rule_info = self.scp_client.add_firewall_inbound_rule(firewall_id, internal_ip)
        rule_id = rule_info['resourceId']
        while True:
            time.sleep(5)
            rule_info = self.scp_client.get_firewal_rule_info(firewall_id, rule_id)
            if rule_info['ruleState'] == "ACTIVE" : break
        return firewall_id, rule_id



    def _create_instance_sequence(self, vpc, instance_config):
        undo_func_stack = []
        try:
            response = self.scp_client.create_instance(instance_config)
            vm_id = response.get('resourceId', None)
            while True:
                time.sleep(10)
                vm_info = self.scp_client.get_vm_info(vm_id)
                if vm_info["virtualServerState"] == "RUNNING": break

            vm_internal_ip = vm_info['ip']
            undo_func_stack.append(lambda: self._del_vm(vm_id))
            firewall_id, rule_id = self._add_firewall_inbound(vpc, vm_internal_ip)
            undo_func_stack.append(lambda: self._del_firwall_inbound(firewall_id, rule_id))

            return vm_id, vm_internal_ip, firewall_id, rule_id

        except Exception as e:
            print(e)
            self._undo_funcs(undo_func_stack)
            return None, None, None, None


    def _undo_funcs(self, undo_func_list):
        while len(undo_func_list) >0:
            func = undo_func_list.pop()
            func()

    def create_node(self,
                    node_config: Dict[str, Any],
                    tags: Dict[str, str],
                    count: int) -> None:
        """Creates a number of nodes within the namespace."""
        assert count == 1, count   # Only support 1-node clusters for now
        # raise SCPError("!!!!!!!", node_config)
        """
        0. need VPC where IGW attached, and its public subnets
        1. select a VPC
        2. create a security-group belongs to VPC 
        3. add an inbound rule into the security-group: 0.0.0.0/0 22port
        4. select a subnet
        5. create a VM
        6. get the VM info including IP
        7. add an inbound rule to a Firewall of the VPC: 0.0.0.0/0 22port -> VM IP 
        """
        config_tags = node_config.get('tags', {}).copy()
        config_tags.update(tags)
        config_tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name


        zone_config = ZoneConfig(self.scp_client, node_config['region'])
        vpc_subnets = zone_config.get_vcp_subnets()
        if (len(vpc_subnets) ==0) : raise SCPError("This region/zone does not have available VPCS.")

        instance_config = zone_config.bootstrap_instance_config(node_config)
        instance_config['virtualServerName'] = self.cluster_name

        SUCCESS = False
        for vpc, subnets in vpc_subnets.items():
            sg_id = self._config_security_group(zone_config.zone_id,
                                        zone_config.get_product_group("NETWORKING:Security Group"),
                                        vpc, self.cluster_name+"_sg") #sg_name
            instance_config['securityGroupIds'] =[sg_id]
            for subnet in subnets:
                instance_config['nic']['subnetId'] = subnet
                vm_id, vm_internal_ip, firewall_id, firewall_rule_id = self._create_instance_sequence(vpc, instance_config)
                if vm_id:
                    SUCCESS = True
                    break
            if SUCCESS: break
            else: self._del_security_group(sg_id)

        if not SUCCESS:
            raise SCPError("Cannot create VM")

        try:
            vm_external_ip = self.scp_client.get_external_ip(virtual_server_id=vm_id, ip=vm_internal_ip)
            # self.scp_client.set_ssh_key(external_ip=vm_external_ip)
            # self.scp_client.set_default_config(external_ip=vm_external_ip)
        except: raise SCPError("SSH Init Error")


        config_tags['virtualServerId'] = vm_id
        config_tags['vmInternalIp'] =vm_internal_ip
        config_tags['firewallId'] = firewall_id
        config_tags['firewallRuleId'] = firewall_rule_id
        config_tags['securityGroupId'] = sg_id
        config_tags['vmExternalIp'] = vm_external_ip


        self.metadata[vm_id] = {'tags': config_tags}


    @synchronized
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)
        self.metadata[node_id] = {'tags': node['tags']}

    def terminate_node(self, node_id: str) -> None:
        """Terminates the specified node."""
        try:
            tags = self.metadata[node_id]['tags']
            self._del_firwall_inbound(tags['firewallId'], tags['firewallRuleId'])
            self._del_vm(tags['virtualServerId'])
            self._del_security_group(tags['securityGroupId'])
        except: raise SCPError("Errors during terminating a node")
        finally: self.metadata[node_id] = None

    def _get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        self._get_filtered_nodes({})  # Side effect: updates cache
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)

    @staticmethod
    def bootstrap_config(cluster_config):

        node_config = cluster_config['available_node_types']['ray_head_default']['node_config']
        provider_config = cluster_config['provider']
        node_config['region'] = provider_config['region']
        return cluster_config
