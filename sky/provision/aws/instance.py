"""AWS instance provisioning."""
import re
import time
from typing import Any, Dict, List, Optional, Union

from botocore import config

from sky import sky_logging
from sky import status_lib
from sky.adaptors import aws
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

BOTO_MAX_RETRIES = 12
# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'

MAX_ATTEMPTS = 6

_DEPENDENCY_VIOLATION_PATTERN = re.compile(
    r'An error occurred \(DependencyViolation\) when calling the '
    r'DeleteSecurityGroup operation(.*): (.*)')


def _default_ec2_resource(region: str) -> Any:
    return aws.resource(
        'ec2',
        region_name=region,
        config=config.Config(retries={'max_attempts': BOTO_MAX_RETRIES}))


def _cluster_name_filter(cluster_name_on_cloud: str) -> List[Dict[str, Any]]:
    return [{
        'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name_on_cloud],
    }]


def _filter_instances(ec2, filters: List[Dict[str, Any]],
                      included_instances: Optional[List[str]],
                      excluded_instances: Optional[List[str]]):
    instances = ec2.instances.filter(Filters=filters)
    if included_instances is not None and excluded_instances is not None:
        raise ValueError('"included_instances" and "exclude_instances"'
                         'cannot be specified at the same time.')
    if included_instances is not None:
        instances = instances.filter(InstanceIds=included_instances)
    elif excluded_instances is not None:
        included_instances = []
        for inst in list(instances):
            if inst.id not in excluded_instances:
                included_instances.append(inst.id)
        instances = instances.filter(InstanceIds=included_instances)
    return instances


# TODO(suquark): Does it make sense to not expose this and always assume
# non_terminated_only=True?
# Will there be callers who would want this to be False?
# stop() and terminate() for example already implicitly assume non-terminated.
def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters = _cluster_name_filter(cluster_name_on_cloud)
    instances = ec2.instances.filter(Filters=filters)
    status_map = {
        'pending': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        # TODO(zhwu): stopping and shutting-down could occasionally fail
        # due to internal errors of AWS. We should cover that case.
        'stopping': status_lib.ClusterStatus.STOPPED,
        'stopped': status_lib.ClusterStatus.STOPPED,
        'shutting-down': None,
        'terminated': None,
    }
    statuses = {}
    for inst in instances:
        status = status_map[inst.state['Name']]
        if non_terminated_only and status is None:
            continue
        statuses[inst.id] = status
    return statuses


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters: List[Dict[str, Any]] = [
        {
            'Name': 'instance-state-name',
            'Values': ['pending', 'running'],
        },
        *_cluster_name_filter(cluster_name_on_cloud),
    ]
    if worker_only:
        filters.append({
            'Name': f'tag:{TAG_RAY_NODE_KIND}',
            'Values': ['worker'],
        })
    instances = _filter_instances(ec2, filters, None, None)
    instances.stop()
    # TODO(suquark): Currently, the implementation of GCP and Azure will
    #  wait util the cluster is fully terminated, while other clouds just
    #  trigger the termination process (via http call) and then return.
    #  It's not clear that which behavior should be expected. We will not
    #  wait for the termination for now, since this is the default behavior
    #  of most cloud implementations (including AWS).


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters = [
        {
            'Name': 'instance-state-name',
            # exclude 'shutting-down' or 'terminated' states
            'Values': ['pending', 'running', 'stopping', 'stopped'],
        },
        *_cluster_name_filter(cluster_name_on_cloud),
    ]
    if worker_only:
        filters.append({
            'Name': f'tag:{TAG_RAY_NODE_KIND}',
            'Values': ['worker'],
        })
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Instance
    instances = _filter_instances(ec2, filters, None, None)
    instances.terminate()
    if 'ports' not in provider_config:
        return
    # If ports are specified, we need to delete the newly created Security
    # Group. Here we wait for all instances to be terminated, since the
    # Security Group dependent on them.
    for instance in instances:
        instance.wait_until_terminated()
    # TODO(suquark): Currently, the implementation of GCP and Azure will
    #  wait util the cluster is fully terminated, while other clouds just
    #  trigger the termination process (via http call) and then return.
    #  It's not clear that which behavior should be expected. We will not
    #  wait for the termination for now, since this is the default behavior
    #  of most cloud implementations (including AWS).


def _get_sg_from_name(
    ec2: Any,
    sg_name: str,
):
    # GroupNames will only filter SGs in the default VPC, so we need to use
    # Filters here. Ref:
    # https://boto3.amazonaws.com/v1/documentation/api/1.26.112/reference/services/ec2/service-resource/security_groups.html  # pylint: disable=line-too-long
    sgs = ec2.security_groups.filter(Filters=[{
        'Name': 'group-name',
        'Values': [sg_name]
    }])
    num_sg = len(list(sgs))
    if num_sg == 0:
        logger.warning(f'Expected security group {sg_name} not found. ')
        return None
    if num_sg > 1:
        # TODO(tian): Better handle this case. Maybe we can check when creating
        # the SG and throw an error if there is already an existing SG with the
        # same name.
        logger.warning(f'Found {num_sg} security groups with name {sg_name}. ')
        return None
    return list(sgs)[0]


def _copy_sg_and_use(
    region: str,
    cluster_name_on_cloud: str,
    previous_sg_name: str,
) -> Optional[str]:
    ec2 = _default_ec2_resource(region)
    previous_sg = _get_sg_from_name(ec2, previous_sg_name)
    if previous_sg is None:
        logger.warning('Find previous security group failed. Skip creating '
                       'new security group.')
        return None
    sg_name = f'sky-sg-{cluster_name_on_cloud}'
    filters = _cluster_name_filter(cluster_name_on_cloud)
    instances = ec2.instances.filter(Filters=filters)
    if len(list(instances)) != 1:
        logger.warning(
            f'Expected 1 instance with cluster name {cluster_name_on_cloud}, '
            f'but found {len(instances)}. Skip creating security group.')
        return None
    instance = list(instances)[0]
    sg = ec2.create_security_group(
        GroupName=sg_name,
        Description='Auto-created security group for Ray workers',
        VpcId=instance.vpc_id)
    sg.authorize_ingress(IpPermissions=previous_sg.ip_permissions)
    instance.modify_attribute(Groups=[sg.id])
    return sg_name


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[Union[int, str]],
    provider_config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)

    ip_permissions = []
    for port in ports:
        if isinstance(port, int):
            from_port = to_port = port
        else:
            from_to_port = port.split('-')
            from_port = int(from_to_port[0])
            to_port = int(from_to_port[1])
        ip_permissions.append({
            'FromPort': from_port,
            'ToPort': to_port,
            'IpProtocol': 'tcp',
            'IpRanges': [{
                'CidrIp': '0.0.0.0/0'
            }],
        })

    # TODO(tian): Check if the SG is the default SG. If so, change to a new
    # SG instead of modifying the default SG.
    sg_name = provider_config['security_group']['GroupName']
    if sg_name == f'sky-sg-{common_utils.user_and_hostname_hash()}':
        new_sg_name = _copy_sg_and_use(region, cluster_name_on_cloud, sg_name)
        if new_sg_name is None:
            logger.warning('Cannot create new security group. Skip open ports.')
            return None
        logger.debug(f'Created new security group {new_sg_name}.')
        sg_name = new_sg_name
    sg = _get_sg_from_name(ec2, sg_name)
    if sg is None:
        logger.warning('Find new security group failed. Skip open ports.')
        return None
    sg.authorize_ingress(IpPermissions=ip_permissions)
    return sg_name


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    if 'ports' not in provider_config:
        return
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    sg_name = provider_config['security_group']['GroupName']
    sg = _get_sg_from_name(ec2, sg_name)
    if sg is None:
        logger.warning(
            'Find security group failed. Skip cleanup security group.')
        return
    backoff = common_utils.Backoff()
    for _ in range(MAX_ATTEMPTS):
        try:
            sg.delete()
        except aws.botocore_exceptions().ClientError as e:
            if _DEPENDENCY_VIOLATION_PATTERN.findall(str(e)):
                logger.debug(
                    f'Security group {sg_name} is still in use. Retry.')
                time.sleep(backoff.current_backoff())
                continue
            raise
        return
    logger.warning(f'Cannot delete security group {sg_name} after '
                   f'{MAX_ATTEMPTS} attempts. Please delete it manually.')
