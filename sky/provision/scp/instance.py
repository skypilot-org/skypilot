"""SCP instance provisioning."""

from sky.clouds.utils import scp_utils
from typing import Any, Dict, List, Optional


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    scp_client = scp_utils.SCPClient()
    vm_list = scp_client.list_instances()

    for vm in vm_list:
        vm_info = scp_client.get_virtual_server_info(vm['virtualServerId'])
        sg_id = vm_info['securityGroupIds'][0]['securityGroupId']
        scp_client.add_new_security_group_in_rule(sg_id, ports[0])
        scp_client.add_new_security_group_out_rule(sg_id, ports[0])
        vpc_id = vm_info['vpcId']
        firewall_list = scp_client.list_firewalls()
        internal_ip = vm_info['ip']
        for firewall in firewall_list:
            if (firewall['vpcId'] == vpc_id):
                firewall_id = firewall['firewallId']
                rule_info = scp_client.add_new_firewall_inbound_rule(
                    firewall_id, internal_ip, ports[0])
                if rule_info is not None:
                    rule_id = rule_info['resourceId']
                    scp_client.wait_firewall_inbound_rule_complete(
                        firewall_id, rule_id)
                rule_info = scp_client.add_new_firewall_outbound_rule(
                    firewall_id, internal_ip, ports[0])
                if rule_info is not None:
                    rule_id = rule_info['resourceId']
                    scp_client.wait_firewall_outbound_rule_complete(
                        firewall_id, rule_id)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    """cleanup_ports is implemented in skypilot/sky/skylet/providers/scp/node_provider.py$terminate_node
       cleanup_ports cannot be not reached for SCP because the program terminates after terminate_node
    """
