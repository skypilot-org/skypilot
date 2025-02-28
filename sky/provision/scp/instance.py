"""SCP instance provisioning."""

import time
from typing import Any, Dict, List, Optional

from sky.clouds.utils import scp_utils


def _add_firewall_rule(scp_client: scp_utils.SCPClient, vpc_id: str,
                       internal_ip: str, ports: List[str]) -> None:

    firewall_list = scp_client.list_firewalls()

    for firewall in firewall_list:
        if firewall['vpcId'] == vpc_id:
            firewall_id = firewall['firewallId']

            attempts = 0
            max_attempts = 300
            while attempts < max_attempts:
                try:
                    rule_info = scp_client.add_new_firewall_rule(
                        firewall_id, internal_ip, 'IN', ports)
                    if rule_info is not None:
                        rule_id = rule_info['resourceId']
                        scp_client.wait_firewall_rule_complete(
                            firewall_id, rule_id)
                    break
                except Exception:  # pylint: disable=broad-except
                    attempts += 1
                    time.sleep(10)
                    continue

            attempts = 0
            max_attempts = 300
            while attempts < max_attempts:
                try:
                    rule_info = scp_client.add_new_firewall_rule(
                        firewall_id, internal_ip, 'OUT', ports)
                    if rule_info is not None:
                        rule_id = rule_info['resourceId']
                        scp_client.wait_firewall_rule_complete(
                            firewall_id, rule_id)
                    break
                except Exception:  # pylint: disable=broad-except
                    attempts += 1
                    time.sleep(10)
                    continue


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""

    del cluster_name_on_cloud
    del provider_config

    scp_client = scp_utils.SCPClient()
    vm_list = scp_client.list_instances()

    for vm in vm_list:
        vm_info = scp_client.get_virtual_server_info(vm['virtualServerId'])
        sg_id = vm_info['securityGroupIds'][0]['securityGroupId']
        scp_client.add_new_security_group_rule(sg_id, 'IN', ports)
        scp_client.add_new_security_group_rule(sg_id, 'OUT', ports)

        vpc_id = vm_info['vpcId']
        internal_ip = vm_info['ip']
        _add_firewall_rule(scp_client, vpc_id, internal_ip, ports)


def cleanup_ports(  # pylint: disable=pointless-string-statement
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    """cleanup_ports is implemented
       in sky/skylet/providers/scp/node_provider.py$terminate_node
       because it cannot be reached for SCP after terminate_node
    """

    del cluster_name_on_cloud
    del ports
    del provider_config
