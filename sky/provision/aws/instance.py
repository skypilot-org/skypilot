"""AWS instance provisioning.

Note (dev): If API changes are made to adaptors/aws.py and the new API is used
in this or config module, please make sure to reload it as in
_default_ec2_resource() to avoid version mismatch issues.
"""
import copy
import logging
from multiprocessing import pool
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar

from sky import sky_logging
from sky.adaptors import aws
from sky.clouds import aws as aws_cloud
from sky.clouds.utils import aws_utils
from sky.provision import common
from sky.provision import constants
from sky.provision.aws import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_T = TypeVar('_T')

# Max retries for general AWS API calls.
BOTO_MAX_RETRIES = 12
# Max retries for creating an instance.
BOTO_CREATE_MAX_RETRIES = 5
# Max retries for deleting security groups etc.
BOTO_DELETE_MAX_ATTEMPTS = 6

_DEPENDENCY_VIOLATION_PATTERN = re.compile(
    r'An error occurred \(DependencyViolation\) when calling the '
    r'DeleteSecurityGroup operation(.*): (.*)')

_RESUME_INSTANCE_TIMEOUT = 480  # 8 minutes
_RESUME_PER_INSTANCE_TIMEOUT = 120  # 2 minutes

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


def _default_ec2_resource(region: str, check_credentials: bool = True) -> Any:
    if not hasattr(aws, 'version'):
        # For backward compatibility, reload the module if the aws module was
        # imported before and stale. Used for, e.g., a live jobs controller
        # running an older version and a new version gets installed by
        # `sky jobs launch`.
        #
        # Detailed explanation follows. Assume we're in this situation: an old
        # jobs controller running a managed job and then the code gets updated
        # on the controller due to a new `sky jobs launch or `sky start`.
        #
        # First, controller consists of an outer process (sky.jobs.controller's
        # main) and an inner process running the controller logic (started as a
        # multiprocessing.Process in sky.jobs.controller). `sky.provision.aws`
        # is only imported in the inner process due to its load-on-use
        # semantics.
        #
        # At this point in the normal execution, inner process has loaded
        # {old sky.provision.aws, old sky.adaptors.aws}, and outer process has
        # loaded {old sky.adaptors.aws}.
        #
        # In controller.py's start(), the inner process may exit due to managed
        # job exits or `sky jobs cancel`, entering outer process'
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
                        max_attempts=BOTO_MAX_RETRIES,
                        check_credentials=check_credentials)


def _cluster_name_filter(cluster_name_on_cloud: str) -> List[Dict[str, Any]]:
    return [{
        'Name': f'tag:{constants.TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name_on_cloud],
    }]


def _ec2_call_with_retry_on_server_error(ec2_fail_fast_fn: Callable[..., _T],
                                         log_level=logging.DEBUG,
                                         **kwargs) -> _T:
    # Here we have to handle 'RequestLimitExceeded' error, so the provision
    # would not fail due to request limit issues.
    # Here the backoff config (5, 12) is picked at random and does not
    # have any special meaning.
    backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=12)
    ret = None
    for _ in range(utils.BOTO_MAX_RETRIES):
        try:
            ret = ec2_fail_fast_fn(**kwargs)
            break
        except aws.botocore_exceptions().ClientError as e:
            # Retry server side errors, as they are likely to be transient.
            # https://docs.aws.amazon.com/AWSEC2/latest/APIReference/errors-overview.html#api-error-codes-table-server # pylint: disable=line-too-long
            error_code = e.response['Error']['Code']
            if error_code in [
                    'RequestLimitExceeded', 'ServerInternal',
                    'ServiceUnavailable', 'InternalError', 'Unavailable'
            ]:
                time.sleep(backoff.current_backoff())
                logger.debug(f'create_instances: {error_code}, retrying.')
                continue
            logger.log(log_level, f'create_instances: Attempt failed with {e}')
            raise
    if ret is None:
        raise RuntimeError(
            f'Failed to call ec2 function {ec2_fail_fast_fn} due to '
            'RequestLimitExceeded. Max attempts exceeded.')
    return ret


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


def _create_instances(ec2_fail_fast, cluster_name: str,
                      node_config: Dict[str, Any], tags: Dict[str, str],
                      count: int, associate_public_ip_address: bool) -> List:
    tags = {
        'Name': cluster_name,
        constants.TAG_RAY_CLUSTER_NAME: cluster_name,
        constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name,
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

    # We are adding 'NetworkInterfaces' in the inner loop and having both keys
    # is considered invalid by the create_instances API.
    security_group_ids = conf.pop('SecurityGroupIds', None)
    # Guaranteed by config.py (the bootstrapping phase):
    assert 'NetworkInterfaces' not in conf, conf
    assert security_group_ids is not None, conf

    logger.debug(f'Creating {count} instances with config: \n{conf}')

    # NOTE: This ensures that we try ALL availability zones before
    # throwing an error.
    num_subnets = len(subnet_ids)
    max_tries = max(num_subnets * (BOTO_CREATE_MAX_RETRIES // num_subnets),
                    len(subnet_ids))
    per_subnet_tries = max_tries // num_subnets
    for i in range(max_tries):
        try:
            # Try each subnet for per_subnet_tries times.
            subnet_id = subnet_ids[i // per_subnet_tries]

            network_interfaces = [{
                'SubnetId': subnet_id,
                'DeviceIndex': 0,
                # Whether the VM(s) should have a public IP.
                'AssociatePublicIpAddress': associate_public_ip_address,
                'Groups': security_group_ids,
            }]
            conf['NetworkInterfaces'] = network_interfaces

            instances = _ec2_call_with_retry_on_server_error(
                ec2_fail_fast.create_instances, **conf)
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
    head_node_markers = tuple(constants.HEAD_NODE_TAGS.items())

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
    # NOTE: We set max_attempts=0 for fast failing when the resource is not
    # available (although the doc says it will only retry for network
    # issues, practically, it retries for capacity errors, etc as well).
    ec2_fail_fast = aws.resource('ec2', region_name=region, max_attempts=0)

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
        'Name': f'tag:{constants.TAG_RAY_CLUSTER_NAME}',
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
        node_type_tags = (constants.HEAD_NODE_TAGS
                          if is_head else constants.WORKER_NODE_TAGS)
        node_tag = [{'Key': k, 'Value': v} for k, v in node_type_tags.items()]
        if is_head:
            node_tag.append({
                'Key': 'Name',
                'Value': f'sky-{cluster_name_on_cloud}-head'
            })
        else:
            node_tag.append({
                'Key': 'Name',
                'Value': f'sky-{cluster_name_on_cloud}-worker'
            })
        # Remove AWS internal tags, as they are not allowed to be set by users.
        target_instance_tags = [
            tag for tag in target_instance.tags
            if not tag['Key'].startswith('aws:')
        ]
        ec2.meta.client.create_tags(
            Resources=[target_instance.id],
            Tags=target_instance_tags + node_tag,
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
        time_start = time.time()
        if stopping_instances:
            plural = 's' if len(stopping_instances) > 1 else ''
            verb = 'are' if len(stopping_instances) > 1 else 'is'
            logger.warning(
                f'Instance{plural} {stopping_instances} {verb} still in '
                'STOPPING state on AWS. It can only be resumed after it is '
                'fully STOPPED. Waiting ...')
        while (stopping_instances and
               to_start_count > len(stopped_instances) and
               time.time() - time_start < _RESUME_INSTANCE_TIMEOUT):
            inst = stopping_instances.pop(0)
            with pool.ThreadPool(processes=1) as pool_:
                # wait_until_stopped() is a blocking call, and sometimes it can
                # take significant time to return due to AWS keeping the
                # instance in STOPPING state. We add a timeout for it to make
                # SkyPilot more responsive.
                fut = pool_.apply_async(inst.wait_until_stopped)
                per_instance_time_start = time.time()
                while (time.time() - per_instance_time_start <
                       _RESUME_PER_INSTANCE_TIMEOUT):
                    if fut.ready():
                        fut.get()
                        break
                    time.sleep(1)
                else:
                    logger.warning(
                        f'Instance {inst.id} is still in stopping state '
                        f'(Timeout: {_RESUME_PER_INSTANCE_TIMEOUT}). '
                        'Retrying ...')
                    stopping_instances.append(inst)
                    time.sleep(5)
                    continue
            stopped_instances.append(inst)
        if stopping_instances and to_start_count > len(stopped_instances):
            msg = ('Timeout for waiting for existing instances '
                   f'{stopping_instances} in STOPPING state to '
                   'be STOPPED before restarting them. Please try again later.')
            logger.error(msg)
            raise RuntimeError(msg)

        resumed_instances = stopped_instances[:to_start_count]
        resumed_instances.sort(key=lambda x: x.id)
        resumed_instance_ids = [t.id for t in resumed_instances]
        logger.debug(f'Resuming stopped instances {resumed_instance_ids}.')
        _ec2_call_with_retry_on_server_error(
            ec2_fail_fast.meta.client.start_instances,
            InstanceIds=resumed_instance_ids,
            log_level=logging.WARNING)
        if tags:
            # empty tags will result in error in the API call
            ec2.meta.client.create_tags(Resources=resumed_instance_ids,
                                        Tags=_format_tags(tags))
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
        target_reservation_names = (config.node_config.get(
            'CapacityReservationSpecification',
            {}).get('CapacityReservationTarget',
                    {}).get('CapacityReservationId', []))
        created_instances = []
        if target_reservation_names:
            node_config = copy.deepcopy(config.node_config)
            # Clear the capacity reservation specification settings in the
            # original node config, as we will create instances with
            # reservations with specific settings for each reservation.
            node_config['CapacityReservationSpecification'] = {
                'CapacityReservationTarget': {}
            }

            reservations = aws_utils.list_reservations_for_instance_type(
                node_config['InstanceType'], region=region)
            # Filter the reservations by the user-specified ones, because
            # reservations contain 'open' reservations as well, which do not
            # need to explicitly specify in the config for creating instances.
            target_reservations = []
            for r in reservations:
                if (r.targeted and r.name in target_reservation_names):
                    target_reservations.append(r)
            logger.debug(f'Reservations: {reservations}')
            logger.debug(f'Target reservations: {target_reservations}')

            target_reservations_list = sorted(
                target_reservations,
                key=lambda x: x.available_resources,
                reverse=True)
            for r in target_reservations_list:
                if r.available_resources <= 0:
                    # We have sorted the reservations by the available
                    # resources, so if the reservation is not available, the
                    # following reservations are not available either.
                    break
                reservation_count = min(r.available_resources, to_start_count)
                logger.debug(f'Creating {reservation_count} instances '
                             f'with reservation {r.name}')
                node_config['CapacityReservationSpecification'][
                    'CapacityReservationTarget'] = {
                        'CapacityReservationId': r.name
                    }
                if r.type == aws_utils.ReservationType.BLOCK:
                    # Capacity block reservations needs to specify the market
                    # type during instance creation.
                    node_config['InstanceMarketOptions'] = {
                        'MarketType': aws_utils.ReservationType.BLOCK.value
                    }
                created_reserved_instances = _create_instances(
                    ec2_fail_fast,
                    cluster_name_on_cloud,
                    node_config,
                    tags,
                    reservation_count,
                    associate_public_ip_address=(
                        not config.provider_config['use_internal_ips']))
                created_instances.extend(created_reserved_instances)
                to_start_count -= reservation_count
                if to_start_count <= 0:
                    break

        # TODO(suquark): If there are existing instances (already running or
        #  resumed), then we cannot guarantee that they will be in the same
        #  availability zone (when there are multiple zones specified).
        #  This is a known issue before.

        if to_start_count > 0:
            # Remove the capacity reservation specification from the node config
            # as we have already created the instances with the reservations.
            config.node_config.get('CapacityReservationSpecification',
                                   {}).pop('CapacityReservationTarget', None)
            created_remaining_instances = _create_instances(
                ec2_fail_fast,
                cluster_name_on_cloud,
                config.node_config,
                tags,
                to_start_count,
                associate_public_ip_address=(
                    not config.provider_config['use_internal_ips']))
            created_instances.extend(created_remaining_instances)
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
            'Name': f'tag:{constants.TAG_RAY_NODE_KIND}',
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
    managed_by_skypilot = provider_config['security_group'].get(
        'ManagedBySkyPilot', True)
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
            'Name': f'tag:{constants.TAG_RAY_NODE_KIND}',
            'Values': ['worker'],
        })
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Instance
    instances = _filter_instances(ec2,
                                  filters,
                                  included_instances=None,
                                  excluded_instances=None)
    instances.terminate()
    if (sg_name == aws_cloud.DEFAULT_SECURITY_GROUP_NAME or
            not managed_by_skypilot):
        # Using default AWS SG or user specified security group. We don't need
        # to wait for the termination of the instances, as we do not need to
        # delete the SG.
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
        # Skip any non-tcp rules or if all traffic (-1) is specified.
        if existing_rule['IpProtocol'] not in ['tcp', '-1']:
            continue
        # Skip any rules that don't have a FromPort or ToPort.
        if 'FromPort' in existing_rule and 'ToPort' in existing_rule:
            existing_ports.update(
                range(existing_rule['FromPort'], existing_rule['ToPort'] + 1))
        elif existing_rule['IpProtocol'] == '-1':
            # For AWS, IpProtocol = -1 means all traffic
            for group_pairs in existing_rule['UserIdGroupPairs']:
                if group_pairs['GroupId'] != sg.id:
                    # We skip the port opening when the rule allows access from
                    # other security groups, as that is likely added by a user
                    # manually and satisfy their requirement.
                    # The security group created by SkyPilot allows all traffic
                    # from the same security group, which should not be skipped.
                    existing_ports.add(-1)
                    break
            break

    ports_to_open = []
    # Do not need to open any ports when all traffic is already allowed.
    if -1 not in existing_ports:
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
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del ports  # Unused.
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    sg_name = provider_config['security_group']['GroupName']
    managed_by_skypilot = provider_config['security_group'].get(
        'ManagedBySkyPilot', True)
    if (sg_name == aws_cloud.DEFAULT_SECURITY_GROUP_NAME or
            not managed_by_skypilot):
        # 1) Using default AWS SG or 2) the SG is specified by the user.
        # We only want to delete the SG that is dedicated to this cluster (i.e.,
        # this cluster have opened some ports).
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
            'Name': f'tag:{constants.TAG_RAY_CLUSTER_NAME}',
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


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    ec2 = _default_ec2_resource(region)
    filters = [
        {
            'Name': 'instance-state-name',
            'Values': ['running'],
        },
        {
            'Name': f'tag:{constants.TAG_RAY_CLUSTER_NAME}',
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
        instances[inst.id] = [
            common.InstanceInfo(
                instance_id=inst.id,
                internal_ip=inst.private_ip_address,
                external_ip=inst.public_ip_address,
                tags=dict(tags),
            )
        ]
    instances = dict(sorted(instances.items(), key=lambda x: x[0]))
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='aws',
        provider_config=provider_config,
    )
