import logging
import os
import time
from threading import RLock
from typing import Any, Dict, List, Optional
import copy

from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler._private.cli_logger import cli_logger
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


def _validation_check(node_config):
    err_msg = None
    if 'diskSize' not in node_config:
        err_msg = "Disk size value is mandatory."
    elif node_config['diskSize'] < 100 or  node_config['diskSize'] > 300:
        err_msg =  f'The disk size must be between 100 and 300. ' \
                   f'Input: {node_config["diskSize"]}'
    if err_msg:
        raise SCPError(err_msg)


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
        self.cache_stopped_nodes = provider_config.get("cache_stopped_nodes", True)
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

        if self.cache_stopped_nodes:
            print("cache_stopped_nodes value is True")
            return [k for k, v in nodes.items() if not v["status"].startswith("STOPPED")]
        else:
            print("cache_stopped_nodes value is False")
            return [k for k, v in nodes.items()]

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

    def _config_security_group(self, zone_id,vpc, cluster_name):
        sg_name = cluster_name.replace("-", "") + "sg"
        if len(sg_name)>20: sg_name = sg_name[:9]+'0'+sg_name[-10:] # should be less than 21

        undo_func_stack = []
        try :
            response = self.scp_client.create_security_group(zone_id, vpc, sg_name)
            sg_id = response['resourceId']
            undo_func_stack.append(lambda: self._del_security_group(sg_id))
            while True:
                sg_contents = self.scp_client.list_security_groups( vpc_id=vpc, sg_name=sg_name)
                sg = [sg["securityGroupState"] for sg in sg_contents if sg["securityGroupId"] == sg_id]
                if len(sg) !=0 and sg[0] == "ACTIVE" : break
                time.sleep(5)

            self.scp_client.add_security_group_in_rule(sg_id)
            self.scp_client.add_security_group_out_rule(sg_id)  # out all

            return sg_id
        except Exception as e:
            print(e)
            self._undo_funcs(undo_func_stack)
            return None

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

    def _del_firwall_rules(self, firewall_id, rule_ids):
        if not isinstance(rule_ids, list): rule_ids = [rule_ids]
        self.scp_client.del_firwall_rules(firewall_id, rule_ids)



    def _add_firewall_inbound(self, firewall_id, internal_ip):

        rule_info = self.scp_client.add_firewall_inbound_rule(firewall_id, internal_ip)
        rule_id = rule_info['resourceId']
        while True:
            time.sleep(5)
            rule_info = self.scp_client.get_firewal_rule_info(firewall_id, rule_id)
            if rule_info['ruleState'] == "ACTIVE" : break
        return rule_id

    def _add_firewall_outbound(self, firewall_id, internal_ip):

        rule_info = self.scp_client.add_firewall_outbound_rule(firewall_id, internal_ip)
        rule_id = rule_info['resourceId']
        while True:
            time.sleep(5)
            rule_info = self.scp_client.get_firewal_rule_info(firewall_id, rule_id)
            if rule_info['ruleState'] == "ACTIVE": break
        return  rule_id


    def _get_firewall_id(self, vpc_id):

        firewall_contents = self.scp_client.list_firwalls()
        print(firewall_contents)

        firewall_id = [firewall['firewallId'] for firewall in firewall_contents
                       if firewall['vpcId'] == vpc_id and (firewall['firewallState'] in ['ACTIVE', 'DEPLOYING'])][0]

        return firewall_id

    def _create_instance_sequence(self, vpc, instance_config):
        undo_func_stack = []
        try:
            response = self.scp_client.create_instance(instance_config)
            vm_id = response.get('resourceId', None)
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", 1111)
            while True:
                time.sleep(10)
                vm_info = self.scp_client.get_vm_info(vm_id)
                if vm_info["virtualServerState"] == "RUNNING": break
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", 222222)
            vm_internal_ip = vm_info['ip']
            undo_func_stack.append(lambda: self._del_vm(vm_id))
            firewall_id = self._get_firewall_id(vpc)
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!! 33333  out of index", 3333333)

            in_rule_id = self._add_firewall_inbound(firewall_id, vm_internal_ip)
            undo_func_stack.append(lambda: self._del_firwall_rules(firewall_id, in_rule_id))
            out_rule_id = self._add_firewall_outbound(firewall_id, vm_internal_ip)
            undo_func_stack.append(lambda: self._del_firwall_rules(firewall_id, in_rule_id))
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", 4444444444444)
            firewall_rules = [in_rule_id, out_rule_id]
            return vm_id, vm_internal_ip, firewall_id, firewall_rules

        except Exception as e:
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", e)
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

        if self.cache_stopped_nodes:
            VALIDITY_TAGS = [
                TAG_RAY_CLUSTER_NAME,
                TAG_RAY_NODE_KIND,
                TAG_RAY_LAUNCH_CONFIG,
                TAG_RAY_USER_NODE_TYPE,
            ]
            filters = {tag: config_tags[tag] for tag in VALIDITY_TAGS if tag in config_tags}
            reuse_nodes = self._stopped_nodes(filters)[:count]
            logger.info(
                f"Reusing nodes {list(reuse_nodes)}. "
                "To disable reuse, set `cache_stopped_nodes: False` "
                "under `provider` in the cluster configuration.",
            )

            for vm_id in reuse_nodes:
                self._start_vm(vm_id=vm_id)
                self.set_node_tags(vm_id, config_tags)

                while True:
                    time.sleep(5)
                    vm_info = self.scp_client.get_vm_info(vm_id)
                    if vm_info["virtualServerState"] == "RUNNING": break

            count -= len(reuse_nodes)

        if count:
            zone_config = ZoneConfig(self.scp_client, node_config)
            vpc_subnets = zone_config.get_vcp_subnets()
            if (len(vpc_subnets) == 0): raise SCPError("This region/zone does not have available VPCS.")

            instance_config = zone_config.bootstrap_instance_config(node_config)
            instance_config['virtualServerName'] = self.cluster_name

            for vpc, subnets in vpc_subnets.items():
                sg_id = self._config_security_group(zone_config.zone_id, vpc, self.cluster_name)  # sg_name
                if sg_id is None: continue

                instance_config['securityGroupIds'] = [sg_id]
                for subnet in subnets:
                    instance_config['nic']['subnetId'] = subnet
                    SUCCESS = self._try_vm_creation(vpc, sg_id, config_tags, instance_config)
                    if SUCCESS: return

                self._del_security_group(sg_id)


            raise SCPError("Cannot create VM")


    def _try_vm_creation(self, vpc, sg_id, config_tags, instance_config):
        vm_id, vm_internal_ip, firewall_id, firwall_rules = self._create_instance_sequence(vpc, instance_config)
        if vm_id is None: return False  # if creation success

        vm_external_ip = self.scp_client.get_external_ip(virtual_server_id=vm_id, ip=vm_internal_ip)
        creation_tags = {}
        creation_tags['virtualServerId'] = vm_id
        creation_tags['vmInternalIp'] = vm_internal_ip
        creation_tags['firewallId'] = firewall_id
        creation_tags['firewallRuleIds'] = firwall_rules
        creation_tags['securityGroupId'] = sg_id
        creation_tags['vmExternalIp'] = vm_external_ip
        self.metadata[vm_id] = {'tags': config_tags, 'creation': creation_tags}
        return True





    def _stopped_nodes(self, tag_filters):
        """Return a list of stopped node ids filtered by the specified tags dict."""
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)
        return [k for k, v in nodes.items() if v["status"].startswith("STOPPED")]

    @synchronized
    def set_node_tags(self, node_id: str, tags: Dict[str, str]) -> None:
        """Sets the tag values (string dict) for the specified node."""
        node = self._get_node(node_id)
        node['tags'].update(tags)

        # self.metadata[node_id] = {'tags': node['tags']}
        metadata = self.metadata[node_id]
        metadata['tags'] =  node['tags']
        self.metadata[node_id] = metadata



    def terminate_node(self, node_id: str) -> None:
        """Terminates the specified node."""
        if self.cache_stopped_nodes:
            try:
                cli_logger.print(
                    f"Stopping instance {node_id}"
                    "(to fully terminate instead, "
                    "set `cache_stopped_nodes: False` "
                    "under `provider` in the cluster configuration)"
                )
                self._stop_vm(node_id)
            except:
                raise SCPError("Errors during stopping a node")
        else:
            try:
                creation_tags = self.metadata[node_id]['creation']
                self._del_firwall_rules(creation_tags['firewallId'], creation_tags['firewallRuleIds'])
                self._del_vm(creation_tags['virtualServerId'])
                self._del_security_group(creation_tags['securityGroupId'])
                self.metadata[node_id] = None
            except:
                raise SCPError("Errors during terminating a node")


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
        node_config['auth'] = cluster_config['auth']

        #Add file mount: metadata path
        metadata_path = f'{TAG_PATH_PREFIX}-{cluster_config["cluster_name"]}'
        cluster_config['file_mounts'][metadata_path] = metadata_path

        _validation_check(node_config)

        return cluster_config

    def _start_vm(self, vm_id):
        self.scp_client.start_instance(vm_id)
        while True:
            time.sleep(2)
            vm_info = self.scp_client.get_vm_info(vm_id)
            if vm_info["virtualServerState"] == "RUNNING": break

    def _stop_vm(self, vm_id):
        self.scp_client.stop_instance(vm_id)
        while True:
            time.sleep(2)
            vm_info = self.scp_client.get_vm_info(vm_id)
            if vm_info["virtualServerState"] == "STOPPED": break

