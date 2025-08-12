"""SCP instance provisioning."""

import logging
import random
import string
import time
from typing import Any, Dict, List, Optional, Tuple

from sky.clouds.utils import scp_utils
from sky.provision import common
from sky.utils import status_lib

logger = logging.getLogger(__name__)


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
    if to_start_count <= len(stopped_instances):
        head_instance_id = _get_head_instance_id(stopped_instances)
        scp_utils.SCPClient().start_instance(head_instance_id)
        while True:
            instance_info = scp_utils.SCPClient().get_instance_info(
                head_instance_id)
            if instance_info['virtualServerState'] == 'RUNNING':
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
        sg_id = _create_security_group(zone_id, vpc)
        if sg_id is None:
            continue
        try:
            instance_config['securityGroupIds'] = [sg_id]
            for subnet in subnets:
                instance_config['nic']['subnetId'] = subnet
                instance_id = _create_instance(vpc, instance_config)
                if instance_id is not None:
                    break
        except Exception as e:  # pylint: disable=broad-except
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
            response = scp_utils.SCPClient().create_vpc(zone_id)
            time.sleep(5)
            vpc_id = response['resourceId']
            while True:
                vpc_info = scp_utils.SCPClient().get_vpc_info(vpc_id)
                if vpc_info['vpcState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            response = scp_utils.SCPClient().create_subnet(vpc_id, zone_id)
            time.sleep(5)
            subnet_id = response['resourceId']
            while True:
                subnet_info = scp_utils.SCPClient().get_subnet_info(subnet_id)
                if subnet_info['subnetState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            response = scp_utils.SCPClient().create_internet_gateway(vpc_id)
            time.sleep(5)
            internet_gateway_id = response['resourceId']
            while True:
                internet_gateway_info = scp_utils.SCPClient(
                ).get_internet_gateway_info(internet_gateway_id)
                if internet_gateway_info['internetGatewayState'] == 'ATTACHED':
                    break
                else:
                    time.sleep(5)

            while True:
                vpc_info = scp_utils.SCPClient().get_vpc_info(vpc_id)
                if vpc_info['vpcState'] == 'ACTIVE':
                    break
                else:
                    time.sleep(5)

            break
        except Exception as e:  # pylint: disable=broad-except
            time.sleep(10)
            logger.error(f'vpc creation error: {e}')
            continue

    vpc_subnets = _get_vcp_subnets(zone_id)
    return vpc_subnets


def _get_vcp_subnets(zone_id):
    vpc_contents = scp_utils.SCPClient().get_vpcs(zone_id)
    vpc_list = [
        item['vpcId'] for item in vpc_contents if item['vpcState'] == 'ACTIVE'
    ]

    igw_contents = scp_utils.SCPClient().get_internet_gateway()
    vpc_with_igw = [
        item['vpcId']
        for item in igw_contents
        if item['internetGatewayState'] == 'ATTACHED'
    ]

    vpc_list = [vpc for vpc in vpc_list if vpc in vpc_with_igw]

    subnet_contents = scp_utils.SCPClient().get_subnets()

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


def _filter_instances(cluster_name_on_cloud,
                      status_filter: Optional[List[str]]):
    instances = scp_utils.SCPClient().get_instances()
    filtered_instances = []
    if status_filter is not None:
        for instance in instances:
            if instance[
                    'virtualServerName'] == cluster_name_on_cloud and instance[
                        'virtualServerState'] in status_filter:
                filtered_instances.append(instance)
        return filtered_instances
    else:
        return instances


def _get_head_instance_id(instances):
    head_instance_id = None
    if len(instances) > 0:
        head_instance_id = instances[0]['virtualServerId']
    return head_instance_id


def _create_security_group(zone_id, vpc):
    sg_name = 'sky' + ''.join(random.choices(string.ascii_lowercase, k=8))

    undo_func_stack = []
    try:
        response = scp_utils.SCPClient().create_security_group(
            zone_id, vpc, sg_name)
        sg_id = response['resourceId']
        undo_func_stack.append(lambda: _delete_security_group(sg_id))
        while True:
            sg_contents = scp_utils.SCPClient().get_security_groups(
                vpc, sg_name)
            sg = [
                sg['securityGroupState']
                for sg in sg_contents
                if sg['securityGroupId'] == sg_id
            ]
            if sg and sg[0] == 'ACTIVE':
                break
            time.sleep(5)

        scp_utils.SCPClient().add_security_group_rule(sg_id, 'IN', None)
        scp_utils.SCPClient().add_security_group_rule(sg_id, 'OUT', None)

        return sg_id
    except Exception as e:  # pylint: disable=broad-except
        _undo_functions(undo_func_stack)
        logger.error(f'security group creation error: {e}')
        return None


def _delete_security_group(sg_id):
    scp_utils.SCPClient().delete_security_group(sg_id)
    while True:
        time.sleep(5)
        sg_contents = scp_utils.SCPClient().get_security_groups()
        sg = [
            sg['securityGroupState']
            for sg in sg_contents
            if sg['securityGroupId'] == sg_id
        ]
        if not sg:
            break


def _undo_functions(undo_func_list):
    while undo_func_list:
        func = undo_func_list.pop()
        func()


def _create_instance(vpc_id, instance_config):
    undo_func_stack = []
    try:
        instance = scp_utils.SCPClient().create_instance(instance_config)
        instance_id = instance['resourceId']
        while True:
            time.sleep(10)
            instance_info = scp_utils.SCPClient().get_instance_info(instance_id)
            if instance_info['virtualServerState'] == 'RUNNING':
                break
        undo_func_stack.append(lambda: _delete_instance(instance_id))
        firewall_id = _get_firewall_id(vpc_id)
        internal_ip = instance_info['ip']
        in_rule_id = _add_firewall_rule(firewall_id, internal_ip, 'IN', None)
        undo_func_stack.append(
            lambda: _delete_firewall_rule(firewall_id, in_rule_id))
        out_rule_id = _add_firewall_rule(firewall_id, internal_ip, 'OUT', None)
        undo_func_stack.append(
            lambda: _delete_firewall_rule(firewall_id, out_rule_id))
        return instance_id

    except Exception as e:  # pylint: disable=broad-except
        _undo_functions(undo_func_stack)
        logger.error(f'instance creation error: {e}')
        return None


def _delete_instance(instance_id):
    scp_utils.SCPClient().terminate_instance(instance_id)
    while True:
        time.sleep(10)
        instances = scp_utils.SCPClient().get_instances()
        inst = [
            instance['virtualServerId']
            for instance in instances
            if instance['virtualServerId'] == instance_id
        ]
        if not inst:
            break


def _get_firewall_id(vpc_id):
    firewalls = scp_utils.SCPClient().get_firewalls()
    firewall_id = [
        firewall['firewallId']
        for firewall in firewalls
        if firewall['vpcId'] == vpc_id and
        (firewall['firewallState'] in ['ACTIVE', 'DEPLOYING'])
    ][0]
    return firewall_id


def _add_firewall_rule(firewall_id, internal_ip, direction,
                       ports: Optional[List[str]]):
    attempts = 0
    max_attempts = 300

    while attempts < max_attempts:
        try:
            rule_info = scp_utils.SCPClient().add_firewall_rule(
                firewall_id, internal_ip, direction, ports)
            rule_id = rule_info['resourceId']
            while True:
                rule_info = scp_utils.SCPClient().get_firewall_rule_info(
                    firewall_id, rule_id)
                if rule_info['ruleState'] == 'ACTIVE':
                    return rule_id
        except Exception as e:  # pylint: disable=broad-except
            attempts += 1
            time.sleep(10)
            logger.error(f'add firewall rule error: {e}')
            continue
    raise RuntimeError('add firewall rule error')


def _delete_firewall_rule(firewall_id, rule_ids):
    if not isinstance(rule_ids, list):
        rule_ids = [rule_ids]

    attempts = 0
    max_attempts = 300
    while attempts < max_attempts:
        try:
            scp_utils.SCPClient().delete_firewall_rule(firewall_id, rule_ids)
            if _remaining_firewall_rule(firewall_id, rule_ids) is False:
                return
        except Exception as e:  # pylint: disable=broad-except
            attempts += 1
            time.sleep(5)
            logger.error(f'delete firewall rule error: {e}')
            continue
    raise RuntimeError('delete firewall rule error')


def _remaining_firewall_rule(firewall_id, rule_ids):
    firewall_rules = scp_utils.SCPClient().get_firewall_rules(firewall_id)
    for rule_id in rule_ids:
        if rule_id in firewall_rules:
            return True
    return False


def _get_firewall_rule_ids(instance_info, firewall_id,
                           ports: Optional[List[str]]):
    rule_ids = []
    if ports is not None:
        destination_ip = instance_info['ip']
        rules = scp_utils.SCPClient().get_firewall_rules(firewall_id)
        for rule in rules:
            port_list = ','.join(rule['tcpServices'])
            port = ','.join(ports)
            if destination_ip == rule['destinationIpAddresses'][
                    0] and '0.0.0.0/0' == rule['sourceIpAddresses'][
                        0] and port == port_list:
                rule_ids.append(rule['ruleId'])
    else:
        ip = instance_info['ip']
        rules = scp_utils.SCPClient().get_firewall_rules(firewall_id)
        for rule in rules:
            if ip == rule['destinationIpAddresses'][0] and '0.0.0.0/0' == rule[
                    'sourceIpAddresses'][0]:
                rule_ids.append(rule['ruleId'])
            if ip == rule['sourceIpAddresses'][0] and '0.0.0.0/0' == rule[
                    'destinationIpAddresses'][0]:
                rule_ids.append(rule['ruleId'])
    return rule_ids


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config, worker_only
    instances = scp_utils.SCPClient().get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_id = instance['virtualServerId']
            scp_utils.SCPClient().stop_instance(instance_id)
            while True:
                instance_info = scp_utils.SCPClient().get_instance_info(
                    instance_id)
                time.sleep(2)
                if instance_info['virtualServerState'] == 'STOPPED':
                    break


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config, worker_only
    instances = scp_utils.SCPClient().get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            try:
                instance_id = instance['virtualServerId']
                instance_info = scp_utils.SCPClient().get_instance_info(
                    instance_id)
                vpc_id = instance_info['vpcId']
                sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
                firewall_id = _get_firewall_id(vpc_id)
                rule_ids = _get_firewall_rule_ids(instance_info, firewall_id,
                                                  None)
                _delete_firewall_rule(firewall_id, rule_ids)
                _delete_instance(instance_id)
                _delete_security_group(sg_id)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'terminate_instances error: {e}')


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    del cluster_name  # unused
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

    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for instance in instances:
        status = status_map[instance['virtualServerState']]
        if non_terminated_only and status is None:
            continue
        statuses[instance['virtualServerId']] = (status, None)
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str, state: str) -> None:
    del region, cluster_name_on_cloud, state


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region

    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)

    instances = {}
    for instance in running_instances:
        instances[instance['virtualServerId']] = [
            common.InstanceInfo(
                instance_id=instance['virtualServerId'],
                internal_ip=instance['ip'],
                external_ip=scp_utils.SCPClient().get_external_ip(
                    instance['virtualServerId'], instance['ip']),
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
    instances = scp_utils.SCPClient().get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_info = scp_utils.SCPClient().get_instance_info(
                instance['virtualServerId'])
            sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
            scp_utils.SCPClient().add_security_group_rule(sg_id, 'IN', ports)
            vpc_id = instance_info['vpcId']
            internal_ip = instance_info['ip']
            firewall_id = _get_firewall_id(vpc_id)
            _add_firewall_rule(firewall_id, internal_ip, 'IN', ports)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:

    del provider_config
    instances = scp_utils.SCPClient().get_instances()

    for instance in instances:
        if instance['virtualServerName'] == cluster_name_on_cloud:
            instance_info = scp_utils.SCPClient().get_instance_info(
                instance['virtualServerId'])
            vpc_id = instance_info['vpcId']
            firewall_id = _get_firewall_id(vpc_id)
            rule_ids = _get_firewall_rule_ids(instance_info, firewall_id, ports)
            _delete_firewall_rule(firewall_id, rule_ids)
