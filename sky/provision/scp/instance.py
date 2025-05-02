"""SCP instance provisioning."""

import logging
import time
from typing import Any, Dict, List, Optional

from sky import status_lib
from sky.provision.scp import utils
from sky.provision import common

logger = logging.getLogger(__name__)
client = utils.SCPClient()


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:

    zone_id = config.node_config['zone_id']
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)

    to_start_count = config.count - len(running_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(running_instances)} nodes, but {config.count} are required.')

    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(running_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='scp',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    stopped_instances = _filter_instances(cluster_name_on_cloud, ['STOPPED'])
    if to_start_count == len(stopped_instances):
        head_instance_id = _get_head_instance_id(stopped_instances)
        client.start_instance(head_instance_id)
        while True:
            instance_info = client.get_virtual_server_info(head_instance_id)
            if instance_info["virtualServerState"] == 'RUNNING':
                break
            time.sleep(2)
        resumed_instance_ids = [head_instance_id]
        return common.ProvisionRecord(provider_name='scp',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=resumed_instance_ids,
                                      created_instance_ids=[])

    # SCP does not support multi-node
    instance_config = config.docker_config
    instance_config['virtualServerName'] = cluster_name_on_cloud

    instance_id = None
    vpc_subnets = _get_or_create_vpc_subnets(zone_id)
    for vpc, subnets in vpc_subnets.items():
        sg_id = _config_security_group(zone_id, vpc, cluster_name_on_cloud)
        if sg_id is None:
            continue
        try:
            instance_config['securityGroupIds'] = [sg_id]
            for subnet in subnets:
                instance_config['nic']['subnetId'] = subnet
                instance_id = _create_instance_sequence(vpc, instance_config)
                if instance_id is not None:
                    break
        except Exception as e:
            _delete_security_group(sg_id)
            logger.error(f'run_instances error: {e}')
            continue

    if instance_id is None:
        raise RuntimeError('instance creation error')

    if head_instance_id is None:
        head_instance_id = instance_id

    created_instance_ids = [instance_id]

    return common.ProvisionRecord(provider_name='scp',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def _get_or_create_vpc_subnets(zone_id):
    while len(_get_vcp_subnets(zone_id)) == 0:
        try:
            response = client.create_vpc(zone_id)
            time.sleep(5)
            vpc_id = response['resourceId']
            while True:
                vpc_info = client.get_vpc_info(vpc_id)
                if vpc_info['vpcState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            response = client.create_subnet(vpc_id, zone_id)
            time.sleep(5)
            subnet_id = response['resourceId']
            while True:
                subnet_info = client.get_subnet_info(subnet_id)
                if subnet_info['subnetState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            response = client.create_internet_gateway(vpc_id)
            time.sleep(5)
            internet_gateway_id = response['resourceId']
            while True:
                internet_gateway_info = client.get_internet_gateway_info(
                    internet_gateway_id)
                if internet_gateway_info['internetGatewayState'] == 'ATTACHED':
                    break
                else:
                    time.sleep(5)

            while True:
                vpc_info = client.get_vpc_info(vpc_id)
                if vpc_info['vpcState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            break
        except Exception as e:
            time.sleep(10)
            logger.error(f'vpc creation error: {e}')
            continue

    vpc_subnets = _get_vcp_subnets(zone_id)
    return vpc_subnets


def _filter_instances(cluster_name_on_cloud,
                      status_filter: Optional[List[str]]):
    instances = client.get_instances()
    exist_instances = []
    if status_filter is not None:
        for instance in instances:
            if instance[
                    'virtualServerName'] == cluster_name_on_cloud and instance[
                        'virtualServerState'] in status_filter:
                exist_instances.append(instance)
        return exist_instances
    else:
        return instances


def _get_head_instance_id(instances):
    head_instance_id = None
    if len(instances) > 0:
        head_instance_id = instances[0]['virtualServerId']
    else:
        head_instance_id = None
    return head_instance_id


def _get_vcp_subnets(zone_id):
    vpc_contents = client.get_vpcs(zone_id)
    vpc_list = [
        item['vpcId'] for item in vpc_contents if item['vpcState'] == 'ACTIVE'
    ]

    igw_contents = client.get_internet_gateway()
    vps_with_igw = [
        item['vpcId']
        for item in igw_contents
        if item['internetGatewayState'] == 'ATTACHED'
    ]

    vpc_list = [vpc for vpc in vpc_list if vpc in vps_with_igw]

    subnet_contents = client.get_subnets()

    vpc_subnets = {}
    for vpc in vpc_list:
        subnet_list = [
            item['subnetId']
            for item in subnet_contents
            if item['subnetState'] == 'ACTIVE' and item['vpcId'] == vpc
        ]
        if subnet_list:
            vpc_subnets[vpc] = subnet_list

    return vpc_subnets


def _config_security_group(zone_id, vpc, cluster_name):
    sg_name = cluster_name.replace("-", "") + "sg"
    if len(sg_name) > 20:
        sg_name = sg_name[:9] + '0' + sg_name[-10:]  # should be less than 21

    undo_func_stack = []
    try:
        response = client.create_security_group(zone_id, vpc, sg_name)
        sg_id = response['resourceId']
        undo_func_stack.append(lambda: _delete_security_group(sg_id))
        while True:
            sg_contents = client.get_security_groups(vpc, sg_name)
            sg = [
                sg["securityGroupState"]
                for sg in sg_contents
                if sg["securityGroupId"] == sg_id
            ]
            if sg and sg[0] == 'ACTIVE':
                break
            time.sleep(5)

        client.add_security_group_in_rule(sg_id)
        client.add_security_group_out_rule(sg_id)

        return sg_id
    except Exception as e:
        _undo_funcs(undo_func_stack)
        logger.error(f'security group creation error: {e}')
        return None


def _delete_security_group(sg_id):
    client.delete_security_group(sg_id)
    while True:
        time.sleep(5)
        sg_contents = client.get_security_groups()
        sg = [
            sg['securityGroupState']
            for sg in sg_contents
            if sg['securityGroupId'] == sg_id
        ]
        if not sg:
            break


def _undo_funcs(undo_func_list):
    while undo_func_list:
        func = undo_func_list.pop()
        func()


def _create_instance_sequence(vpc, instance_config):
    undo_func_stack = []
    try:
        instance_id, internal_ip = _create_instance(instance_config)
        undo_func_stack.append(lambda: _delete_instance(instance_id))
        firewall_id = _get_firewall_id(vpc)
        in_rule_id = _add_firewall_inbound(firewall_id, internal_ip)
        undo_func_stack.append(
            lambda: _delete_firewall_rules(firewall_id, in_rule_id))
        out_rule_id = _add_firewall_outbound(firewall_id, internal_ip)
        undo_func_stack.append(
            lambda: _delete_firewall_rules(firewall_id, out_rule_id))
        return instance_id

    except Exception as e:
        logger.error(f'instance creation error: {e}')
        _undo_funcs(undo_func_stack)
        return None


def _delete_instance(instance_id):
    client.terminate_instance(instance_id)
    while True:
        time.sleep(10)
        instances = client.get_instances()
        inst = [
            instance['virtualServerId']
            for instance in instances
            if instance['virtualServerId'] == instance_id
        ]
        if not inst:
            break


def _delete_firewall_rules(firewall_id, rule_ids):
    if not isinstance(rule_ids, list):
        rule_ids = [rule_ids]

    attempts = 0
    max_attempts = 300
    while attempts < max_attempts:
        try:
            client.delete_firewall_rules(firewall_id, rule_ids)
            if _exist_firewall_rule(firewall_id, rule_ids) is False:
                break
        except Exception as e:
            attempts += 1
            time.sleep(5)
            logger.error(f'delete firewall rule error: {e}')
            continue
    return


def _exist_firewall_rule(firewall_id, rule_ids):
    firewall_rules = client.get_firewall_rules(firewall_id)
    for rule_id in rule_ids:
        if rule_id in firewall_rules:
            return True
    return False


def _get_firewall_id(vpc_id):
    firewall_contents = client.get_firewalls()
    firewall_id = [
        firewall['firewallId']
        for firewall in firewall_contents
        if firewall['vpcId'] == vpc_id and
        (firewall['firewallState'] in ['ACTIVE', 'DEPLOYING'])
    ][0]

    return firewall_id


def _add_firewall_inbound(firewall_id, internal_ip):
    attempts = 0
    max_attempts = 300

    while attempts < max_attempts:
        try:
            rule_info = client.add_firewall_inbound_rule(
                firewall_id, internal_ip)
            rule_id = rule_info['resourceId']
            while True:
                time.sleep(5)
                rule_info = client.get_firewall_rule_info(firewall_id, rule_id)
                if rule_info['ruleState'] == 'ACTIVE':
                    break
            return rule_id
        except Exception as e:
            attempts += 1
            time.sleep(10)
            logger.error(f'add firewall inbound rule error: {e}')
            continue
    raise RuntimeError('firewall rule error')


def _add_firewall_outbound(firewall_id, internal_ip):
    attempts = 0
    max_attempts = 300

    while attempts < max_attempts:
        try:
            rule_info = client.add_firewall_outbound_rule(
                firewall_id, internal_ip)
            rule_id = rule_info['resourceId']
            while True:
                time.sleep(5)
                rule_info = client.get_firewall_rule_info(firewall_id, rule_id)
                if rule_info['ruleState'] == 'ACTIVE':
                    break
            return rule_id
        except Exception as e:
            attempts += 1
            time.sleep(10)
            logger.error(f'add firewall outbound rule error: {e}')
            continue
    raise RuntimeError('firewall rule error')


def _create_instance(instance_config):
    response = client.create_instance(instance_config)
    instance_id = response.get('resourceId', None)
    while True:
        time.sleep(10)
        instance_info = client.get_virtual_server_info(instance_id)
        if instance_info['virtualServerState'] == 'RUNNING':
            break
    return instance_id, instance_info['ip']


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config, worker_only
    instances = client.get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_id = instance['virtualServerId']
            client.stop_instance(instance_id)
            while True:
                instance_info = client.get_virtual_server_info(instance_id)
                time.sleep(2)
                if instance_info['virtualServerState'] == 'STOPPED':
                    break


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config, worker_only
    instances = client.get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            try:
                instance_id = instance['virtualServerId']
                instance_info = client.get_virtual_server_info(instance_id)
                vpc_id = instance_info['vpcId']
                sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
                firewall_id = _get_firewall_id(vpc_id)
                rule_ids = _get_firewall_rule_ids(instance_info, firewall_id,
                                                  None)
                _delete_firewall_rules(firewall_id, rule_ids)
                _delete_instance(instance_id)
                _delete_security_group(sg_id)
            except Exception as e:
                logger.error(f'terminate_instances error: {e}')


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:

    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'CREATING': status_lib.ClusterStatus.INIT,
        'EDITING': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
        'STARTING': status_lib.ClusterStatus.INIT,
        'RESTARTING': status_lib.ClusterStatus.INIT,
        'STOPPING': status_lib.ClusterStatus.STOPPED,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'TERMINATING': None,
        'TERMINATED': None,
    }

    statuses = {}
    for instance in instances:
        status = status_map[instance['virtualServerState']]
        if non_terminated_only and status is None:
            continue
        statuses[instance['virtualServerId']] = status
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str, state: str) -> None:
    del region, cluster_name_on_cloud, state


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:

    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)

    instances = {}
    for instance in running_instances:
        instances[instance['virtualServerId']] = [
            common.InstanceInfo(instance_id=instance['virtualServerId'],
                                internal_ip=instance['ip'],
                                external_ip=client.get_external_ip(
                                    instance['virtualServerId'],
                                    instance['ip']),
                                tags={})
        ]

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='scp',
        provider_config=provider_config,
    )


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:

    del provider_config
    instances = client.get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_info = client.get_virtual_server_info(
                instance['virtualServerId'])
            sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
            client.add_new_security_group_rule(sg_id, 'IN', ports)
            vpc_id = instance_info['vpcId']
            internal_ip = instance_info['ip']
            _add_firewall_rule(vpc_id, internal_ip, ports)


def _add_firewall_rule(vpc_id: str, internal_ip: str, ports: List[str]) -> None:

    firewall_list = client.get_firewalls()

    for firewall in firewall_list:
        if firewall['vpcId'] == vpc_id:
            firewall_id = firewall['firewallId']

            attempts = 0
            max_attempts = 300
            while attempts < max_attempts:
                try:
                    rule_info = client.add_new_firewall_rule(
                        firewall_id, internal_ip, 'IN', ports)
                    if rule_info is not None:
                        rule_id = rule_info['resourceId']
                        client.wait_firewall_rule_complete(firewall_id, rule_id)
                    break
                except Exception as e:  # pylint: disable=broad-except
                    attempts += 1
                    time.sleep(10)
                    logger.error(f'add firewall rule error: {e}')
                    continue


def cleanup_ports(  # pylint: disable=pointless-string-statement
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:

    del provider_config
    instances = client.get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_info = client.get_virtual_server_info(
                instance['virtualServerId'])
            vpc_id = instance_info['vpcId']
            firewall_id = _get_firewall_id(vpc_id)
            rule_ids = _get_firewall_rule_ids(instance_info, firewall_id, ports)
            _delete_firewall_rules(firewall_id, rule_ids)


def _get_firewall_rule_ids(instance_info, firewall_id,
                           ports: Optional[List[str]]):
    rule_ids = []
    if ports is not None:
        destination_ip = instance_info['ip']
        rules = client.get_firewall_rules(firewall_id)
        for rule in rules:
            port_list = ','.join(rule['tcpServices'])
            port = ','.join(ports)
            if destination_ip == rule['destinationIpAddresses'][
                    0] and '0.0.0.0/0' == rule['sourceIpAddresses'][
                        0] and port == port_list:
                rule_ids.append(rule['ruleId'])
    else:
        ip = instance_info['ip']
        rules = client.get_firewall_rules(firewall_id)
        for rule in rules:
            if ip == rule['destinationIpAddresses'][0] and '0.0.0.0/0' == rule[
                    'sourceIpAddresses'][0]:
                rule_ids.append(rule['ruleId'])
            if ip == rule['sourceIpAddresses'][0] and '0.0.0.0/0' == rule[
                    'destinationIpAddresses'][0]:
                rule_ids.append(rule['ruleId'])
    return rule_ids
