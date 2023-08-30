"""AWS instance provisioning."""
import copy
import re
import time
from typing import Any, Dict, List, Optional

# We still have to depend on Ray logger, because currently
# our logger does not support the same coloring schema as Ray.
# For example, Ray can format the entire message yellow.
from ray.autoscaler._private.cli_logger import cli_logger

from sky import sky_logging
from sky import status_lib
from sky.adaptors import aws
from sky.provision import common
from sky.provision.aws import utils
from sky.utils import common_utils

BOTO_CREATE_MAX_RETRIES = 5

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'  # legacy tag for backward compatibility
TAG_SKYPILOT_HEAD_NODE = 'skypilot-head-node'
BOTO_MAX_RETRIES = 12

logger = sky_logging.init_logger(__name__)

BOTO_MAX_RETRIES = 12

MAX_ATTEMPTS = 6

_DEPENDENCY_VIOLATION_PATTERN = re.compile(
    r'An error occurred \(DependencyViolation\) when calling the '
    r'DeleteSecurityGroup operation(.*): (.*)')

# ======================== About AWS subnet/VPC ========================
# https://stackoverflow.com/questions/37407492/are-there-differences-in-networking-performance-if-ec2-instances-are-in-differen
# https://docs.aws.amazon.com/vpc/latest/userguide/how-it-works.html
# https://docs.aws.amazon.com/vpc/latest/userguide/configure-subnets.html

# ======================== Instance state and lifecycle ========================
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html

# ======================== About AWS availability zone ========================
# Data transfer within the same region but different availability zone
#  costs $0.01/GB:
# https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer_within_the_same_AWS_Region


def _default_ec2_resource(region: str) -> Any:
    return aws.resource('ec2',
                        region_name=region,
                        config=aws.botocore_config().Config(
                            retries={'max_attempts': BOTO_MAX_RETRIES}))


def _cluster_name_filter(cluster_name_on_cloud: str) -> List[Dict[str, Any]]:
    return [{
        'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name_on_cloud],
    }]


def describe_instances(region: str) -> Dict:
    # overhead: 658 ms ± 65.3 ms
    return utils.create_resource('ec2', region).meta.client.describe_instances()


def _format_tags(tags: Dict[str, str]) -> List:
    return [{'Key': k, 'Value': v} for k, v in tags.items()]


def _merge_tag_specs(tag_specs: List[Dict[str, Any]],
                     user_tag_specs: List[Dict[str, Any]]) -> None:
    """Merges user-provided node config tag specifications into a base
    list of node provider tag specifications. The base list of
    node provider tag specs is modified in-place.

    This allows users to add tags and override values of existing
    tags with their own, and only applies to the resource type
    'instance'. All other resource types are appended to the list of
    tag specs.

    Args:
        tag_specs (List[Dict[str, Any]]): base node provider tag specs
        user_tag_specs (List[Dict[str, Any]]): user's node config tag specs
    """

    for user_tag_spec in user_tag_specs:
        if user_tag_spec['ResourceType'] == 'instance':
            for user_tag in user_tag_spec['Tags']:
                exists = False
                for tag in tag_specs[0]['Tags']:
                    if user_tag['Key'] == tag['Key']:
                        exists = True
                        tag['Value'] = user_tag['Value']
                        break
                if not exists:
                    tag_specs[0]['Tags'] += [user_tag]
        else:
            tag_specs += [user_tag_spec]


def _create_instances(ec2_fail_fast, cluster_name: str, node_config: Dict[str,
                                                                          Any],
                      tags: Dict[str, str], count: int) -> List:
    tags = {'Name': cluster_name, TAG_RAY_CLUSTER_NAME: cluster_name, **tags}
    conf = node_config.copy()

    tag_specs = [{
        'ResourceType': 'instance',
        'Tags': _format_tags(tags),
    }]
    user_tag_specs = conf.get('TagSpecifications', [])
    _merge_tag_specs(tag_specs, user_tag_specs)

    # SubnetIds is not a real config key: we must resolve to a
    # single SubnetId before invoking the AWS API.
    subnet_ids = conf.pop('SubnetIds')

    # update config with min/max node counts and tag specs
    conf.update({
        'MinCount': count,
        'MaxCount': count,
        'TagSpecifications': tag_specs
    })

    # NOTE: This ensures that we try ALL availability zones before
    # throwing an error.
    max_tries = max(BOTO_CREATE_MAX_RETRIES, len(subnet_ids))
    for i in range(max_tries):
        try:
            if 'NetworkInterfaces' in conf:
                # remove security group IDs previously copied from network
                # interfaces (create_instances call fails otherwise)
                conf.pop('SecurityGroupIds', None)
            else:
                # Launch failure may be due to instance type availability in
                # the given AZ. Try to always launch in the first listed subnet.
                subnet_id = subnet_ids[i % len(subnet_ids)]
                conf['SubnetId'] = subnet_id

            # NOTE: We set retry=0 for fast failing when the resource is not
            # available. Here we have to handle 'RequestLimitExceeded'
            # error, so the provision would not fail due to request limit
            # issues.
            backoff = common_utils.Backoff(5, 12)
            instances = None
            for _ in range(utils.BOTO_MAX_RETRIES):
                try:
                    instances = ec2_fail_fast.create_instances(**conf)
                    break
                except aws.botocore_exceptions().ClientError as e:
                    if e.response['Error']['Code'] == 'RequestLimitExceeded':
                        time.sleep(backoff.current_backoff())
                        cli_logger.warning(
                            'create_instances: RequestLimitExceeded, retrying.')
                        continue
                    raise
            if instances is None:
                raise RuntimeError(
                    'Failed to launch instances due to RequestLimitExceeded. '
                    'Max attempts exceeded.')
            return instances
        except aws.botocore_exceptions().ClientError as exc:
            if (i + 1) >= max_tries:
                raise RuntimeError(
                    'Failed to launch instances. Max attempts exceeded.'
                ) from exc
            else:
                cli_logger.warning(
                    f'create_instances: Attempt failed with {exc}, retrying.')
    assert False, 'This code should not be reachable'


def _get_head_instance_id(instances: List) -> Optional[str]:
    head_instance_id = None
    head_node_markers = (
        (TAG_SKYPILOT_HEAD_NODE, '1'),
        (TAG_RAY_NODE_KIND, 'head'),  # backward compat with Ray
    )
    for inst in instances:
        for t in inst.tags:
            if (t['Key'], t['Value']) in head_node_markers:
                if head_instance_id is not None:
                    cli_logger.warning(
                        'There are multiple head nodes in the cluster '
                        f'(current head instance id: {head_instance_id}, '
                        f'newly discovered id: {inst.id}). It is likely '
                        f'that something goes wrong.')
                head_instance_id = inst.id
                break
    return head_instance_id


def start_instances(region: str, cluster_name: str,
                    config: common.InstanceConfig) -> common.ProvisionMetadata:
    """See sky/provision/__init__.py"""
    ec2 = utils.create_resource('ec2', region=region)

    region = ec2.meta.client.meta.region_name
    zone = None
    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []

    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(config.tags).items()))
    filters = [
        {
            'Name': 'instance-state-name',
            'Values': ['pending', 'running', 'stopping', 'stopped'],
        },
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name],
        },
    ]
    exist_instances = list(ec2.instances.filter(Filters=filters))
    exist_instances.sort(key=lambda x: x.id)
    head_instance_id = _get_head_instance_id(exist_instances)

    pending_instances = []
    running_instances = []
    stopping_instances = []
    stopped_instances = []

    for inst in exist_instances:
        state = inst.state['Name']
        if state == 'pending':
            pending_instances.append(inst)
        elif state == 'running':
            running_instances.append(inst)
        elif state == 'stopping':
            stopping_instances.append(inst)
        elif state == 'stopped':
            stopped_instances.append(inst)
        else:
            raise RuntimeError(f'Impossible state "{state}".')

    def _create_node_tag(target_instance, is_head: bool = True) -> str:
        if is_head:
            node_tag = [{
                'Key': TAG_SKYPILOT_HEAD_NODE,
                'Value': '1'
            }, {
                'Key': TAG_RAY_NODE_KIND,
                'Value': 'head'
            }, {
                'Key': 'Name',
                'Value': f'{cluster_name}-head'
            }]
        else:
            node_tag = [{
                'Key': TAG_RAY_NODE_KIND,
                'Value': 'worker'
            }, {
                'Key': 'Name',
                'Value': f'{cluster_name}-worker'
            }]
        ec2.meta.client.create_tags(
            Resources=[target_instance.id],
            Tags=target_instance.tags + node_tag,
        )
        return target_instance.id

    if head_instance_id is None:
        if running_instances:
            head_instance_id = _create_node_tag(running_instances[0])
        elif pending_instances:
            head_instance_id = _create_node_tag(pending_instances[0])

    # TODO(suquark): Maybe in the future, users could adjust the number
    #  of instances dynamically. Then this case would not be an error.
    if config.resume_stopped_nodes and len(exist_instances) > config.count:
        raise RuntimeError('The number of running/stopped/stopping '
                           f'instances combined ({len(exist_instances)}) in '
                           f'cluster "{cluster_name}" is greater than the '
                           f'number requested by the user ({config.count}). '
                           'This is likely a resource leak. '
                           'Use "sky down" to terminate the cluster.')

    to_start_count = (config.count - len(running_instances) -
                      len(pending_instances))

    if running_instances:
        zone = running_instances[0].placement['AvailabilityZone']

    if to_start_count < 0:
        raise RuntimeError('The number of running+pending instances '
                           f'({config.count - to_start_count}) in cluster '
                           f'"{cluster_name}" is greater than the number '
                           f'requested by the user ({config.count}). '
                           'This is likely a resource leak. '
                           'Use "sky down" to terminate the cluster.')

    # Try to reuse previously stopped nodes with compatible configs
    if config.resume_stopped_nodes and to_start_count > 0 and (
            stopping_instances or stopped_instances):
        for inst in stopping_instances:
            if to_start_count <= len(stopped_instances):
                break
            inst.wait_until_stopped()
            stopped_instances.append(inst)

        resumed_instances = stopped_instances[:to_start_count]
        resumed_instances.sort(key=lambda x: x.id)
        resumed_instance_ids = [t.id for t in resumed_instances]
        ec2.meta.client.start_instances(InstanceIds=resumed_instance_ids)
        if tags:
            # empty tags will result in error in the API call
            ec2.meta.client.create_tags(
                Resources=resumed_instance_ids,
                Tags=_format_tags(tags),
            )
            for inst in resumed_instances:
                inst.tags = _format_tags(tags)  # sync the tags info
        placement_zone = resumed_instances[0].placement['AvailabilityZone']
        if zone is None:
            zone = placement_zone
        elif zone != placement_zone:
            cli_logger.warning(
                f'Resumed instances are in zone {placement_zone}, '
                f'while previous instances are in zone {zone}.')
        to_start_count -= len(resumed_instances)

        if head_instance_id is None:
            head_instance_id = _create_node_tag(resumed_instances[0])

    if to_start_count > 0:
        # TODO(suquark): If there are existing instances (already running or
        #  resumed), then we cannot guarantee that they will be in the same
        #  availability zone (when there are multiple zones specified).
        #  This is a known issue before.
        ec2_fail_fast = utils.create_resource('ec2',
                                              region=region,
                                              max_attempts=0)
        created_instances = _create_instances(ec2_fail_fast, cluster_name,
                                              config.node_config, tags,
                                              to_start_count)
        created_instances.sort(key=lambda x: x.id)

        created_instance_ids = [n.id for n in created_instances]
        placement_zone = created_instances[0].placement['AvailabilityZone']
        if zone is None:
            zone = placement_zone
        elif zone != placement_zone:
            cli_logger.warning('Newly created instances are in zone '
                               f'{placement_zone}, '
                               f'while previous instances are in zone {zone}.')

        # NOTE: we only create worker tags for newly started nodes, because
        # the worker tag is a legacy feature, so we would not care about
        # more corner cases.
        if head_instance_id is None:
            head_instance_id = _create_node_tag(created_instances[0])
            for inst in created_instances[1:]:
                _create_node_tag(inst, is_head=False)
        else:
            for inst in created_instances:
                _create_node_tag(inst, is_head=False)

    assert head_instance_id is not None
    return common.ProvisionMetadata(provider_name='aws',
                                    region=region,
                                    zone=zone,
                                    cluster_name=cluster_name,
                                    head_instance_id=head_instance_id,
                                    resumed_instance_ids=resumed_instance_ids,
                                    created_instance_ids=created_instance_ids)


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


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    if 'ports' not in provider_config:
        return
    region = provider_config['region']
    ec2 = aws.resource('ec2',
                       region_name=region,
                       config=aws.botocore_config().Config(
                           retries={'max_attempts': BOTO_MAX_RETRIES}))
    sg_name = provider_config['security_group']['GroupName']
    # GroupNames will only filter SGs in the default VPC, so we need to use
    # Filters here. Ref:
    # https://boto3.amazonaws.com/v1/documentation/api/1.26.112/reference/services/ec2/service-resource/security_groups.html  # pylint: disable=line-too-long
    sgs = ec2.security_groups.filter(Filters=[{
        'Name': 'group-name',
        'Values': [sg_name]
    }])
    num_sg = len(list(sgs))
    if num_sg == 0:
        logger.warning(f'Expected security group {sg_name} not found. '
                       'Skip cleanup.')
        return
    if num_sg > 1:
        # TODO(tian): Better handle this case. Maybe we can check when creating
        # the SG and throw an error if there is already an existing SG with the
        # same name.
        logger.warning(f'Found {num_sg} security groups with name {sg_name}. '
                       'Skip cleanup. Please delete them manually.')
        return
    backoff = common_utils.Backoff()
    for _ in range(MAX_ATTEMPTS):
        try:
            list(sgs)[0].delete()
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


def wait_instances(region: str, cluster_name: str, state: str) -> None:
    """See sky/provision/__init__.py"""
    # possible exceptions: https://github.com/boto/boto3/issues/176
    ec2 = utils.create_resource('ec2', region=region)
    client = ec2.meta.client

    filters = [
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name],
        },
    ]

    if state == 'running':
        # NOTE: there could be a terminated/terminating AWS cluster with
        # the same cluster name.
        # Wait the cluster result in errors (cannot wait for 'terminated').
        # So here we exclude terminated/terminating instances.
        filters.append({
            'Name': 'instance-state-name',
            'Values': ['pending', 'running'],
        })
    elif state == 'stopped':
        filters.append({
            'Name': 'instance-state-name',
            'Values': ['stopping', 'stopped'],
        })

    # boto3 waiter would wait for an empty list forever
    instances = list(ec2.instances.filter(Filters=filters))
    if not instances:
        return

    if state == 'running':
        waiter = client.get_waiter('instance_running')
    elif state == 'stopped':
        waiter = client.get_waiter('instance_stopped')
    elif state == 'terminated':
        waiter = client.get_waiter('instance_terminated')
    else:
        raise ValueError(f'Unsupported state to wait: {state}')
    # See https://github.com/boto/botocore/blob/develop/botocore/waiter.py
    waiter.wait(WaiterConfig={'Delay': 5, 'MaxAttempts': 120}, Filters=filters)


def get_cluster_metadata(region: str,
                         cluster_name: str) -> common.ClusterMetadata:
    """See sky/provision/__init__.py"""
    ec2 = utils.create_resource('ec2', region=region)
    filters = [
        {
            'Name': 'instance-state-name',
            'Values': ['running'],
        },
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name],
        },
    ]
    running_instances = list(ec2.instances.filter(Filters=filters))
    head_instance_id = _get_head_instance_id(running_instances)

    instances = {}
    for inst in running_instances:
        tags = [(t['Key'], t['Value']) for t in inst.tags]
        # sort tags by key to support deterministic unit test stubbing
        tags.sort(key=lambda x: x[0])
        instances[inst.id] = common.InstanceMetadata(
            instance_id=inst.id,
            private_ip=inst.private_ip_address,
            public_ip=inst.public_ip_address,
            tags=dict(tags),
        )
    instances = dict(sorted(instances.items(), key=lambda x: x[0]))
    return common.ClusterMetadata(
        instances=instances,
        head_instance_id=head_instance_id,
    )
