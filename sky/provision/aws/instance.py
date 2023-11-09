"""AWS instance provisioning.

Note (dev): If API changes are made to adaptors/aws.py and the new API is used
in this or config module, please make sure to reload it as in
_default_ec2_resource() to avoid version mismatch issues.
"""
import copy
import re
import time
from typing import Any, Dict, List, Optional, Set

from sky import sky_logging
from sky import status_lib
from sky.adaptors import aws
from sky.clouds import aws as aws_cloud
from sky.provision import common
from sky.provision.aws import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'  # legacy tag for backward compatibility
TAG_SKYPILOT_HEAD_NODE = 'skypilot-head-node'
# Max retries for general AWS API calls.
BOTO_MAX_RETRIES = 12
# Max retries for creating an instance.
BOTO_CREATE_MAX_RETRIES = 5
# Max retries for deleting security groups etc.
BOTO_DELETE_MAX_ATTEMPTS = 6

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
    if not hasattr(aws, 'version'):
        # For backward compatibility, reload the module if the aws module was
        # imported before and stale. Used for, e.g., a live spot controller
        # running an older version and a new version gets installed by
        # `sky spot launch`.
        #
        # Detailed explanation follows. Assume we're in this situation: an old
        # spot controller running a spot job and then the code gets updated on
        # the controller due to a new `sky spot launch` or `sky start`.
        #
        # First, controller consists of an outer process (sky.spot.controller's
        # main) and an inner process running the controller logic (started as a
        # multiprocessing.Process in sky.spot.controller). `sky.provision.aws`
        # is only imported in the inner process due to its load-on-use
        # semantics.
        #
        # At this point in the normal execution, inner process has loaded
        # {old sky.provision.aws, old sky.adaptors.aws}, and outer process has
        # loaded {old sky.adaptors.aws}.
        #
        # In controller.py's start(), the inner process may exit due to spot job
        # exits or `sky spot cancel`, entering outer process'
        # `finally: ... _cleanup()` path. Inside _cleanup(), we eventually call
        # into `sky.provision.aws` which loads this module for the first time
        # for the outer process. At this point, outer process has loaded
        # {old sky.adaptors.aws, new sky.provision.aws}.
        #
        # This version mismatch becomes a "backward compatibility" problem if
        # `new sky.provision.aws` depends on `new sky.adaptors.aws` (assuming
        # API changes in sky.adaptors.aws). Therefore, here we use a hack to
        # reload sky.adaptors.aws to go from old to new.
        #
        # For version < 1 (variable does not exist), we do not have
        # `max_attempts` in the `aws.resource` call, so we need to reload the
        # module to get the latest `aws.resource` function.
        import importlib  # pylint: disable=import-outside-toplevel
        importlib.reload(aws)
    return aws.resource('ec2',
                        region_name=region,
                        max_attempts=BOTO_MAX_RETRIES)


def _cluster_name_filter(cluster_name_on_cloud: str) -> List[Dict[str, Any]]:
    return [{
        'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name_on_cloud],
    }]


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
    tags = {
        'Name': cluster_name,
        TAG_RAY_CLUSTER_NAME: cluster_name,
        TAG_SKYPILOT_CLUSTER_NAME: cluster_name,
        **tags
    }
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
    num_subnets = len(subnet_ids)
    max_tries = max(num_subnets * (BOTO_CREATE_MAX_RETRIES // num_subnets),
                    len(subnet_ids))
    per_subnet_tries = max_tries // num_subnets
    for i in range(max_tries):
        try:
            if 'NetworkInterfaces' in conf:
                logger.debug(
                    'Attempting to create instances with NetworkInterfaces.'
                    'Ignore SecurityGroupIds.')
                # remove security group IDs previously copied from network
                # interfaces (create_instances call fails otherwise)
                conf.pop('SecurityGroupIds', None)
            else:
                # Try each subnet for per_subnet_tries times.
                subnet_id = subnet_ids[i // per_subnet_tries]
                conf['SubnetId'] = subnet_id

            # NOTE: We set retry=0 for fast failing when the resource is not
            # available. Here we have to handle 'RequestLimitExceeded'
            # error, so the provision would not fail due to request limit
            # issues.
            # Here the backoff config (5, 12) is picked at random and does not
            # have any special meaning.
            backoff = common_utils.Backoff(5, 12)
            instances = None
            for _ in range(utils.BOTO_MAX_RETRIES):
                try:
                    instances = ec2_fail_fast.create_instances(**conf)
                    break
                except aws.botocore_exceptions().ClientError as e:
                    if e.response['Error']['Code'] == 'RequestLimitExceeded':
                        time.sleep(backoff.current_backoff())
                        logger.warning(
                            'create_instances: RequestLimitExceeded, retrying.')
                        continue
                    raise
            if instances is None:
                raise RuntimeError(
                    'Failed to launch instances due to RequestLimitExceeded. '
                    'Max attempts exceeded.')
            return instances
        except aws.botocore_exceptions().ClientError as exc:
            echo = logger.debug
            if (i + 1) % per_subnet_tries == 0:
                # Print the warning only once per subnet
                echo = logger.warning
            echo(f'create_instances: Attempt failed with {exc}')
            if (i + 1) >= max_tries:
                raise RuntimeError(
                    'Failed to launch instances. Max attempts exceeded.'
                ) from exc
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
                    logger.warning(
                        'There are multiple head nodes in the cluster '
                        f'(current head instance id: {head_instance_id}, '
                        f'newly discovered id: {inst.id}). It is likely '
                        f'that something goes wrong.')
                head_instance_id = inst.id
                break
    return head_instance_id


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    ec2 = _default_ec2_resource(region)

    region = ec2.meta.client.meta.region_name
    zone = None
    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []

    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(config.tags).items()))
    filters = [{
        'Name': 'instance-state-name',
        'Values': ['pending', 'running', 'stopping', 'stopped'],
    }, {
        'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name_on_cloud],
    }]
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
                'Value': f'sky-{cluster_name_on_cloud}-head'
            }]
        else:
            node_tag = [{
                'Key': TAG_SKYPILOT_HEAD_NODE,
                'Value': '0'
            }, {
                'Key': TAG_RAY_NODE_KIND,
                'Value': 'worker'
            }, {
                'Key': 'Name',
                'Value': f'sky-{cluster_name_on_cloud}-worker'
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
        raise RuntimeError(
            'The number of running/stopped/stopping '
            f'instances combined ({len(exist_instances)}) in '
            f'cluster "{cluster_name_on_cloud}" is greater than the '
            f'number requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    to_start_count = (config.count - len(running_instances) -
                      len(pending_instances))

    if running_instances:
        zone = running_instances[0].placement['AvailabilityZone']

    if to_start_count < 0:
        raise RuntimeError(
            'The number of running+pending instances '
            f'({config.count - to_start_count}) in cluster '
            f'"{cluster_name_on_cloud}" is greater than the number '
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
            logger.warning(f'Resumed instances are in zone {placement_zone}, '
                           f'while previous instances are in zone {zone}.')
        to_start_count -= len(resumed_instances)

        if head_instance_id is None:
            head_instance_id = _create_node_tag(resumed_instances[0])

    if to_start_count > 0:
        # TODO(suquark): If there are existing instances (already running or
        #  resumed), then we cannot guarantee that they will be in the same
        #  availability zone (when there are multiple zones specified).
        #  This is a known issue before.
        ec2_fail_fast = aws.resource('ec2', region_name=region, max_attempts=0)

        created_instances = _create_instances(ec2_fail_fast,
                                              cluster_name_on_cloud,
                                              config.node_config, tags,
                                              to_start_count)
        created_instances.sort(key=lambda x: x.id)

        created_instance_ids = [n.id for n in created_instances]
        placement_zone = created_instances[0].placement['AvailabilityZone']
        if zone is None:
            zone = placement_zone
        elif zone != placement_zone:
            logger.warning('Newly created instances are in zone '
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
    return common.ProvisionRecord(provider_name='aws',
                                  region=region,
                                  zone=zone,
                                  cluster_name=cluster_name_on_cloud,
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
@common_utils.retry
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
    instances = _filter_instances(ec2,
                                  filters,
                                  included_instances=None,
                                  excluded_instances=None)
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
    instances = _filter_instances(ec2,
                                  filters,
                                  included_instances=None,
                                  excluded_instances=None)
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
    sg_name = provider_config['security_group']['GroupName']
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
    instances = _filter_instances(ec2,
                                  filters,
                                  included_instances=None,
                                  excluded_instances=None)
    instances.terminate()
    if sg_name == aws_cloud.DEFAULT_SECURITY_GROUP_NAME:
        # Using default AWS SG. We don't need to wait for the
        # termination of the instances.
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
) -> Any:
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


def _maybe_move_to_new_sg(
    instance: Any,
    expected_sg: Any,
) -> None:
    """Move the instance to the new security group if needed.

    If the instance is already in the expected security group, do nothing.
    Otherwise, move it to the expected security group.
    Our config.py will automatically create a new security group for every
    GroupName specified in the provider config. But it won't change the
    security group of an existing cluster, so we need to move it to the
    expected security group.
    """
    sg_names = [sg['GroupName'] for sg in instance.security_groups]
    if len(sg_names) != 1:
        logger.warning(
            f'Expected 1 security group for instance {instance.id}, '
            f'but found {len(sg_names)}. Skip creating security group.')
        return
    sg_name = sg_names[0]
    if sg_name == expected_sg.group_name:
        return
    instance.modify_attribute(Groups=[expected_sg.id])


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    sg_name = provider_config['security_group']['GroupName']
    filters = [
        {
            'Name': 'instance-state-name',
            # exclude 'shutting-down' or 'terminated' states
            'Values': ['pending', 'running', 'stopping', 'stopped'],
        },
        *_cluster_name_filter(cluster_name_on_cloud),
    ]
    instances = _filter_instances(ec2,
                                  filters,
                                  included_instances=None,
                                  excluded_instances=None)
    instance_list = list(instances)
    if not instance_list:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Instance with cluster name '
                             f'{cluster_name_on_cloud} not found.')
    sg = _get_sg_from_name(ec2, sg_name)
    if sg is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cannot find new security group '
                             f'{sg_name}. Please check the log '
                             'above and try again.')
    # For multinode cases, we need to change the SG for all instances.
    for instance in instance_list:
        _maybe_move_to_new_sg(instance, sg)

    existing_ports: Set[int] = set()
    for existing_rule in sg.ip_permissions:
        # Skip any non-tcp rules.
        if existing_rule['IpProtocol'] != 'tcp':
            continue
        # Skip any rules that don't have a FromPort or ToPort.
        if 'FromPort' not in existing_rule or 'ToPort' not in existing_rule:
            continue
        existing_ports.update(
            range(existing_rule['FromPort'], existing_rule['ToPort'] + 1))
    ports_to_open = resources_utils.port_set_to_ranges(
        resources_utils.port_ranges_to_set(ports) - existing_ports)

    ip_permissions = []
    for port in ports_to_open:
        if port.isdigit():
            from_port = to_port = port
        else:
            from_port, to_port = port.split('-')
        ip_permissions.append({
            'FromPort': int(from_port),
            'ToPort': int(to_port),
            'IpProtocol': 'tcp',
            'IpRanges': [{
                'CidrIp': '0.0.0.0/0'
            }],
        })

    # For the case when every new ports is already opened.
    if ip_permissions:
        sg.authorize_ingress(IpPermissions=ip_permissions)


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    sg_name = provider_config['security_group']['GroupName']
    if sg_name == aws_cloud.DEFAULT_SECURITY_GROUP_NAME:
        # Using default AWS SG. We only want to delete the SG that is dedicated
        # to this cluster (i.e., this cluster have opened some ports).
        return
    sg = _get_sg_from_name(ec2, sg_name)
    if sg is None:
        logger.warning(
            'Find security group failed. Skip cleanup security group.')
        return
    backoff = common_utils.Backoff()
    for _ in range(BOTO_DELETE_MAX_ATTEMPTS):
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
    logger.warning(
        f'Cannot delete security group {sg_name} after '
        f'{BOTO_DELETE_MAX_ATTEMPTS} attempts. Please delete it manually.')


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    # TODO(suquark): unify state for different clouds
    # possible exceptions: https://github.com/boto/boto3/issues/176
    ec2 = _default_ec2_resource(region)
    client = ec2.meta.client

    filters = [
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name_on_cloud],
        },
    ]

    if state == status_lib.ClusterStatus.UP:
        # NOTE: there could be a terminated/terminating AWS cluster with
        # the same cluster name.
        # Wait the cluster result in errors (cannot wait for 'terminated').
        # So here we exclude terminated/terminating instances.
        filters.append({
            'Name': 'instance-state-name',
            'Values': ['pending', 'running'],
        })
    elif state == status_lib.ClusterStatus.STOPPED:
        filters.append({
            'Name': 'instance-state-name',
            'Values': ['stopping', 'stopped'],
        })

    # boto3 waiter would wait for an empty list forever
    instances = list(ec2.instances.filter(Filters=filters))
    logger.debug(instances)
    if not instances:
        raise RuntimeError(
            f'No instances found for cluster {cluster_name_on_cloud}.')

    if state == status_lib.ClusterStatus.UP:
        waiter = client.get_waiter('instance_running')
    elif state == status_lib.ClusterStatus.STOPPED:
        waiter = client.get_waiter('instance_stopped')
    elif state is None:
        waiter = client.get_waiter('instance_terminated')
    else:
        raise ValueError(f'Unsupported state to wait: {state}')
    # See https://github.com/boto/botocore/blob/develop/botocore/waiter.py
    waiter.wait(WaiterConfig={'Delay': 5, 'MaxAttempts': 120}, Filters=filters)


def get_cluster_info(region: str,
                     cluster_name_on_cloud: str) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    ec2 = _default_ec2_resource(region)
    filters = [
        {
            'Name': 'instance-state-name',
            'Values': ['running'],
        },
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name_on_cloud],
        },
    ]
    running_instances = list(ec2.instances.filter(Filters=filters))
    head_instance_id = _get_head_instance_id(running_instances)

    instances = {}
    for inst in running_instances:
        tags = [(t['Key'], t['Value']) for t in inst.tags]
        # sort tags by key to support deterministic unit test stubbing
        tags.sort(key=lambda x: x[0])
        instances[inst.id] = common.InstanceInfo(
            instance_id=inst.id,
            internal_ip=inst.private_ip_address,
            external_ip=inst.public_ip_address,
            tags=dict(tags),
        )
    instances = dict(sorted(instances.items(), key=lambda x: x[0]))
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
    )
