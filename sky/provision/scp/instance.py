"""SCP instance provisioning."""

from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import logging
import random
import string
import time
from typing import Any, Dict, List, Optional, Tuple

from sky.clouds.utils import scp_utils
from sky.provision import common
from sky.utils import status_lib

logger = logging.getLogger(__name__)


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name  # unused
    zone_id = config.node_config['zone_id']

    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])

    to_start_count = config.count - len(running_instances)

    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(running_instances)} instances, but {config.count} '
            'are required')

    if to_start_count == 0:
        head_instance_id = _get_head_instance_id(running_instances)
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head instance')
        logger.info(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(running_instances)} instances, no need to start more')
        return common.ProvisionRecord(provider_name='scp',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    existing_instances = _filter_instances(cluster_name_on_cloud, None)
    stopped_instances = _filter_instances(cluster_name_on_cloud,
                                          ['STOPPED', 'STOPPING'])

    def _detect_naming_version(existing_instances,
                               cluster_name_on_cloud) -> str:
        v2_head = _head(cluster_name_on_cloud)
        v2_worker_prefix = _worker(cluster_name_on_cloud)
        has_v2 = any(instance['virtualServerName'] == v2_head or
                     instance['virtualServerName'].startswith(v2_worker_prefix)
                     for instance in existing_instances)
        if has_v2:
            return 'v2'
        has_v1 = any(instance['virtualServerName'] == cluster_name_on_cloud
                     for instance in existing_instances)
        if has_v1:
            return 'v1'

        if not existing_instances:
            logger.debug(
                'detect_naming_version: no instances for cluster %s; '
                'defaulting to v2.', cluster_name_on_cloud)
        else:
            logger.error(
                'detect_naming_version: unexpected instance names for cluster '
                '%s: %s; defaulting to v2.', cluster_name_on_cloud, [
                    instance['virtualServerName']
                    for instance in existing_instances
                ])
        return 'v2'

    naming_version = _detect_naming_version(existing_instances,
                                            cluster_name_on_cloud)

    if naming_version == 'v2':
        cluster_instance_names = [_head(cluster_name_on_cloud)] + [
            f'{_worker(cluster_name_on_cloud)}-{i:02d}'
            for i in range(1, config.count)
        ]
    else:
        if config.count > 1:
            raise RuntimeError(
                'This cluster uses the legacy naming scheme and cannot be '
                'scaled to multi-node automatically. '
                'Please `sky down` and relaunch.')
        cluster_instance_names = [cluster_name_on_cloud]

    existing_instance_names = [
        instance['virtualServerName'] for instance in existing_instances
    ]
    resume_instance_names = [
        instance['virtualServerName'] for instance in stopped_instances
    ]
    create_instance_names = [
        instance_name for instance_name in cluster_instance_names
        if instance_name not in existing_instance_names
    ]

    vpc_subnets = _get_or_create_vpc_subnets(zone_id)

    def _resume(instance_name):
        instance_id = _get_instance_id(instance_name, cluster_name_on_cloud)
        while True:
            state = scp_utils.SCPClient().get_instance_info(
                instance_id)['virtualServerState']
            if state == 'RUNNING':
                return instance_id, 'resumed'
            if state == 'STOPPED':
                break
            time.sleep(2)

        scp_utils.SCPClient().start_instance(instance_id)
        while True:
            info = scp_utils.SCPClient().get_instance_info(instance_id)
            if info['virtualServerState'] == 'RUNNING':
                return instance_id, 'resumed'
            time.sleep(2)

    def _create(instance_name):
        instance_config = deepcopy(config.docker_config)
        instance_config['virtualServerName'] = instance_name
        cnt = config.count

        for vpc, subnets in vpc_subnets.items():
            sg_id = _create_security_group(zone_id, vpc, cnt)
            if not sg_id:
                continue

            created_in_this_vpc = False
            try:
                instance_config['securityGroupIds'] = [sg_id]
                for subnet in subnets:
                    instance_config['nic']['subnetId'] = subnet
                    instance_id = _create_instance(vpc, instance_config, cnt)
                    if instance_id:
                        created_in_this_vpc = True
                        return instance_id, 'created'
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'run_instances error ({instance_name}): {e}')
            finally:
                if not created_in_this_vpc:
                    try:
                        _delete_security_group(sg_id)
                    except Exception:  # pylint: disable=broad-except
                        pass

        raise RuntimeError(f'instance creation error: {instance_name}')

    tasks = (
        [(_resume, instance_name) for instance_name in resume_instance_names] +
        [(_create, instance_name) for instance_name in create_instance_names])

    instance_ids_statuses = []
    if tasks:
        with ThreadPoolExecutor(max_workers=min(len(tasks), 32)) as ex:
            execution = [
                ex.submit(function, instance_name)
                for function, instance_name in tasks
            ]
            for e in as_completed(execution):
                try:
                    instance_ids_statuses.append(e.result())
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'run_instances error: {e}')

    wait_time = time.time() + 600
    while time.time() < wait_time:
        running_instances = _filter_instances(cluster_name_on_cloud,
                                              ['RUNNING'])
        if len(running_instances) == config.count:
            break
        pending_instances = _filter_instances(
            cluster_name_on_cloud,
            ['CREATING', 'EDITING', 'STARTING', 'RESTARTING', 'STOPPING'])
        if not pending_instances:
            break
        time.sleep(3)

    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    if len(running_instances) != config.count:
        raise RuntimeError(f'Expected {config.count} running instances, '
                           f'but got {len(running_instances)} instances')

    head_instance_id = _get_head_instance_id(running_instances)
    if head_instance_id is None:
        raise RuntimeError('Head instance is not running')

    resumed_instance_ids = []
    created_instance_ids = []
    for instance_id, status in instance_ids_statuses:
        if status == 'resumed':
            resumed_instance_ids.append(instance_id)
        elif status == 'created':
            created_instance_ids.append(instance_id)

    return common.ProvisionRecord(provider_name='scp',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=resumed_instance_ids,
                                  created_instance_ids=created_instance_ids)


def _head(cluster_name_on_cloud: str):
    return (f'{cluster_name_on_cloud[:8]}-'
            f'{_suffix(cluster_name_on_cloud)}-head')


def _worker(cluster_name_on_cloud: str):
    return (f'{cluster_name_on_cloud[:8]}-'
            f'{_suffix(cluster_name_on_cloud)}-worker')


def _suffix(name: str, n: int = 5):
    return hashlib.sha1(name.encode()).hexdigest()[:n]


def _get_instance_id(instance_name, cluster_name_on_cloud):
    instances = _filter_instances(cluster_name_on_cloud, None)
    for instance in instances:
        if instance_name == instance['virtualServerName']:
            return instance['virtualServerId']
    return None


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
    v2_head_instance_name = _head(cluster_name_on_cloud)
    v2_worker_prefix = _worker(cluster_name_on_cloud)
    v1_head_instance_name = cluster_name_on_cloud

    cluster_instances = [
        instance for instance in instances
        if instance['virtualServerName'] == v2_head_instance_name or
        instance['virtualServerName'].startswith(v2_worker_prefix) or
        instance['virtualServerName'] == v1_head_instance_name
    ]

    if status_filter is None:
        return cluster_instances
    return [
        instance for instance in cluster_instances
        if instance['virtualServerState'] in status_filter
    ]


def _get_head_instance_id(instances):
    if len(instances) > 0:
        for instance in instances:
            if instance['virtualServerName'].endswith('-head'):
                return instance['virtualServerId']
        return instances[0]['virtualServerId']
    return None


def _create_security_group(zone_id, vpc, cnt):
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

        scp_utils.SCPClient().add_security_group_rule(sg_id, 'IN', None, cnt)
        scp_utils.SCPClient().add_security_group_rule(sg_id, 'OUT', None, cnt)

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


def _create_instance(vpc_id, instance_config, cnt):
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
        in_rule_id = _add_firewall_rule(firewall_id, internal_ip, 'IN', None,
                                        cnt)
        undo_func_stack.append(
            lambda: _delete_firewall_rule(firewall_id, in_rule_id))
        out_rule_id = _add_firewall_rule(firewall_id, internal_ip, 'OUT', None,
                                         cnt)
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
                       ports: Optional[List[str]], cnt: Optional[int]):
    attempts = 0
    max_attempts = 300
    while attempts < max_attempts:
        try:
            rule_info = scp_utils.SCPClient().add_firewall_rule(
                firewall_id, internal_ip, direction, ports, cnt)
            if rule_info is not None:
                rule_id = rule_info['resourceId']
                while True:
                    rule_info = scp_utils.SCPClient().get_firewall_rule_info(
                        firewall_id, rule_id)
                    if rule_info['ruleState'] == 'ACTIVE':
                        return rule_id
            else:
                return None
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
            if not _remaining_firewall_rule(firewall_id, rule_ids):
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
    del provider_config
    instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])

    if worker_only:
        head_instance_name = _head(cluster_name_on_cloud)
        instances = [
            instance for instance in instances
            if instance['virtualServerName'] != head_instance_name
        ]

    if not instances:
        return

    def _stop(instance):
        try:
            instance_id = instance['virtualServerId']
            scp_utils.SCPClient().stop_instance(instance_id)
            while True:
                info = scp_utils.SCPClient().get_instance_info(instance_id)
                if info['virtualServerState'] == 'STOPPED':
                    return instance_id
                time.sleep(2)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'stop_instances error: {e}')

    with ThreadPoolExecutor(max_workers=min(len(instances), 32)) as ex:
        execution = [ex.submit(_stop, instance) for instance in instances]
        for e in as_completed(execution):
            e.result()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config
    instances = _filter_instances(cluster_name_on_cloud, ['RUNNING', 'STOPPED'])

    if worker_only:
        head_instance_name = _head(cluster_name_on_cloud)
        instances = [
            instance for instance in instances
            if instance['virtualServerName'] != head_instance_name
        ]

    if not instances:
        return

    def _terminate(instance):
        try:
            instance_id = instance['virtualServerId']
            instance_info = scp_utils.SCPClient().get_instance_info(instance_id)
            vpc_id = instance_info['vpcId']
            sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
            firewall_id = _get_firewall_id(vpc_id)
            rule_ids = _get_firewall_rule_ids(instance_info, firewall_id, None)
            _delete_firewall_rule(firewall_id, rule_ids)
            _delete_instance(instance_id)
            _delete_security_group(sg_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'terminate_instances error: {e}')

    with ThreadPoolExecutor(max_workers=min(len(instances), 32)) as ex:
        execution = [ex.submit(_terminate, instance) for instance in instances]
        for e in as_completed(execution):
            e.result()


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    del cluster_name, retry_if_missing  # unused
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

    # max-worker-port - min-worker-port should be at least 3 * nproc
    # RAY_worker_maximum_startup_concurrency for the performance
    custom_ray_options = {
        'node-manager-port': 11001,
        'min-worker-port': 11002,
        'max-worker-port': 11200,
        'ray-client-server-port': 10001
    }

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        custom_ray_options=custom_ray_options,
        provider_name='scp',
        provider_config=provider_config,
    )


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del provider_config
    instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(instances)
    instance_info = scp_utils.SCPClient().get_instance_info(head_instance_id)
    sg_id = instance_info['securityGroupIds'][0]['securityGroupId']
    scp_utils.SCPClient().add_security_group_rule(sg_id, 'IN', ports, None)
    vpc_id = instance_info['vpcId']
    internal_ip = instance_info['ip']
    firewall_id = _get_firewall_id(vpc_id)
    _add_firewall_rule(firewall_id, internal_ip, 'IN', ports, None)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del provider_config
    instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    head_instance_id = _get_head_instance_id(instances)
    instance_info = scp_utils.SCPClient().get_instance_info(head_instance_id)
    vpc_id = instance_info['vpcId']
    firewall_id = _get_firewall_id(vpc_id)
    rule_ids = _get_firewall_rule_ids(instance_info, firewall_id, ports)
    _delete_firewall_rule(firewall_id, rule_ids)
