"""AWS configuration bootstrapping.

Note (dev): If API changes are made to adaptors/aws.py and the new API is used
in this or config module, please make sure to reload it as in
_default_ec2_resource() to avoid version mismatch issues.
"""
# The codes are adapted from
# https://github.com/ray-project/ray/tree/ray-2.0.1/python/ray/autoscaler/_private/aws/config.py
# Git commit of the release 2.0.1: 03b6bc7b5a305877501110ec04710a9c57011479
import copy
import functools
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import colorama

from sky import exceptions
from sky import sky_logging
from sky.adaptors import aws
from sky.provision import common
from sky.provision.aws import utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

RAY = 'ray-autoscaler'
SECURITY_GROUP_TEMPLATE = RAY + '-{}'

SKYPILOT = 'skypilot'
DEFAULT_SKYPILOT_INSTANCE_PROFILE = SKYPILOT + '-v1'
DEFAULT_SKYPILOT_IAM_ROLE = SKYPILOT + '-v1'

# Suppress excessive connection dropped logs from boto
logging.getLogger('botocore').setLevel(logging.WARNING)


def _skypilot_log_error_and_exit_for_failover(error: str) -> None:
    """Logs an message then raises a specific RuntimeError to trigger failover.

    Mainly used for handling VPC/subnet errors before nodes are launched.
    """
    # NOTE: keep. The backend looks for this to know no nodes are launched.
    full_error = f'SKYPILOT_ERROR_NO_NODES_LAUNCHED: {error}'
    logger.error(full_error)
    raise RuntimeError(full_error)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
    # create a copy of the input config to modify
    config = copy.deepcopy(config)

    node_cfg = config.node_config
    aws_credentials = config.provider_config.get('aws_credentials', {})

    subnet_ids = node_cfg.get('SubnetIds')  # type: ignore
    security_group_ids = node_cfg.get('SecurityGroupIds')  # type: ignore
    assert 'NetworkInterfaces' not in node_cfg, (
        'SkyPilot: NetworkInterfaces is not supported in '
        'node config')
    assert subnet_ids is None, 'SkyPilot: SubnetIds is not supported.'

    # The head node needs to have an IAM role that allows it to create further
    # EC2 instances.
    if 'IamInstanceProfile' not in node_cfg:
        iam = aws.resource('iam', region_name=region, **aws_credentials)
        node_cfg['IamInstanceProfile'] = _configure_iam_role(iam)

    ec2 = aws.resource('ec2', region_name=region, **aws_credentials)

    # Pick a reasonable subnet if not specified by the user.
    subnets, vpc_id = _get_subnet_and_vpc_id(
        ec2,
        security_group_ids,
        config.provider_config['region'],
        availability_zone=config.provider_config.get('availability_zone'),
        use_internal_ips=config.provider_config.get('use_internal_ips', False),
        vpc_name=config.provider_config.get('vpc_name'))

    # Cluster workers should be in a security group that permits traffic within
    # the group, and also SSH access from outside.
    if security_group_ids is None:
        start_time = time.time()
        logger.debug('\nCreating or updating security groups...')

        # Generate the name of the security group we're looking for...
        security_group_config = config.provider_config.get('security_group', {})
        expected_sg_name = security_group_config.get(
            'GroupName', SECURITY_GROUP_TEMPLATE.format(cluster_name))
        # The extended IP rules specified by the user
        extended_ip_rules = security_group_config.get('IpPermissions', [])
        if extended_ip_rules is None:
            extended_ip_rules = []
        security_group_ids = _configure_security_group(ec2, vpc_id,
                                                       expected_sg_name,
                                                       extended_ip_rules)
        end_time = time.time()
        elapsed = end_time - start_time
        logger.info(
            f'Security groups created or updated in {elapsed:.5f} seconds.')

    # store updated subnet and security group configs in node config
    # NOTE: 'SubnetIds' is not a real config key for AWS instance.
    node_cfg['SubnetIds'] = [s.subnet_id for s in subnets]
    node_cfg['SecurityGroupIds'] = security_group_ids

    # Provide helpful message for missing ImageId for node configuration.
    # NOTE(skypilot): skypilot uses the default AMIs in sky/clouds/aws.py.
    node_ami = config.node_config.get('ImageId')
    if not node_ami:
        _skypilot_log_error_and_exit_for_failover(
            'No ImageId found in the node_config. '
            'ImageId will need to be set manually in your cluster config.')
    return config


def _configure_iam_role(iam) -> Dict[str, Any]:

    def _get_instance_profile(profile_name: str):
        profile = iam.InstanceProfile(profile_name)
        try:
            profile.load()
            return profile
        except aws.botocore_exceptions().ClientError as exc:
            if exc.response.get('Error', {}).get('Code') == 'NoSuchEntity':
                return None
            else:
                utils.handle_boto_error(
                    exc, 'Failed to fetch IAM instance profile data for '
                    f'{colorama.Style.BRIGHT}{profile_name}'
                    f'{colorama.Style.RESET_ALL} from AWS.')
                raise exc

    def _get_role(role_name: str):
        role = iam.Role(role_name)
        try:
            role.load()
            return role
        except aws.botocore_exceptions().ClientError as exc:
            if exc.response.get('Error', {}).get('Code') == 'NoSuchEntity':
                return None
            else:
                utils.handle_boto_error(
                    exc,
                    f'Failed to fetch IAM role data for {colorama.Style.BRIGHT}'
                    f'{role_name}{colorama.Style.RESET_ALL} from AWS.')
                raise exc

    instance_profile_name = DEFAULT_SKYPILOT_INSTANCE_PROFILE
    profile = _get_instance_profile(instance_profile_name)

    if profile is None:
        logger.info(
            f'Creating new IAM instance profile {colorama.Style.BRIGHT}'
            f'{instance_profile_name}{colorama.Style.RESET_ALL} for use as the '
            'default.')
        iam.meta.client.create_instance_profile(
            InstanceProfileName=instance_profile_name)
        profile = _get_instance_profile(instance_profile_name)
        time.sleep(15)  # wait for propagation
    assert profile is not None, 'Failed to create instance profile'

    if not profile.roles:
        role_name = DEFAULT_SKYPILOT_IAM_ROLE
        role = _get_role(role_name)
        if role is None:
            logger.info(
                f'Creating new IAM role {colorama.Style.BRIGHT}{role_name}'
                f'{colorama.Style.RESET_ALL} for use as the default instance '
                'role.')
            policy_doc = {
                'Statement': [{
                    'Effect': 'Allow',
                    'Principal': {
                        'Service': 'ec2.amazonaws.com'
                    },
                    'Action': 'sts:AssumeRole',
                }]
            }
            attach_policy_arns = [
                'arn:aws:iam::aws:policy/AmazonEC2FullAccess',
                'arn:aws:iam::aws:policy/AmazonS3FullAccess',
            ]

            iam.create_role(RoleName=role_name,
                            AssumeRolePolicyDocument=json.dumps(policy_doc))
            role = _get_role(role_name)
            assert role is not None, 'Failed to create role'

            for policy_arn in attach_policy_arns:
                role.attach_policy(PolicyArn=policy_arn)

            # SkyPilot: 'PassRole' is required by the controllers (jobs and
            # services) created with `aws.remote_identity: SERVICE_ACCOUNT` to
            # create instances with the IAM role.
            skypilot_pass_role_policy_doc = {
                'Statement': [
                    {
                        'Effect': 'Allow',
                        'Action': [
                            'iam:GetRole',
                            'iam:PassRole',
                        ],
                        'Resource': role.arn,
                    },
                    {
                        'Effect': 'Allow',
                        'Action': 'iam:GetInstanceProfile',
                        'Resource': profile.arn,
                    },
                ]
            }
            role.Policy('SkyPilotPassRolePolicy').put(
                PolicyDocument=json.dumps(skypilot_pass_role_policy_doc))

        profile.add_role(RoleName=role.name)
        time.sleep(15)  # wait for propagation
    return {'Arn': profile.arn}


@functools.lru_cache(maxsize=128)  # Keep bounded.
def _get_route_tables(ec2, vpc_id: Optional[str], region: str,
                      main: bool) -> List[Any]:
    """Get route tables associated with a VPC and region

    Args:
        ec2: ec2 resource object
        vpc_id: vpc_id is optional, if not provided, all route tables in the
                region will be returned
        region: region is mandatory to allow the lru cache
                   to return the corect results
        main: if True, only main route tables will be returned otherwise
                only non-main route tables will be returned

    Returns:
        A list of route tables associated with the options VPC and region
    """
    filters = [{'Name': 'association.main', 'Values': [str(main).lower()]}]
    if vpc_id is not None:
        filters.append({'Name': 'vpc-id', 'Values': [vpc_id]})
    logger.debug(
        f'Getting route tables with filters: {filters} in region: {region}')
    return ec2.meta.client.describe_route_tables(Filters=filters).get(
        'RouteTables', [])


def _is_subnet_public(ec2, subnet_id, vpc_id: Optional[str]) -> bool:
    """Checks if a subnet is public by existence of a route to an IGW.

    Conventionally, public subnets connect to a IGW, and private subnets to a
    NAT. See ref:
    https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html
    """
    # Get the route tables associated with the subnet
    region = ec2.meta.client.meta.region_name
    all_route_tables = _get_route_tables(ec2, vpc_id, region, main=False)
    route_tables = [
        rt for rt in all_route_tables
        # An RT can be associated with multiple subnets, i.e.,
        # rt['Associations'] is a list of associations.
        # There may be no subnet associated in an association.
        if any(
            assoc.get('SubnetId', '') == subnet_id
            for assoc in rt['Associations'])
    ]

    # Check each route table for an internet gateway route
    def _has_igw_route(route_tables):
        for route_table in route_tables:
            for route in route_table.get('Routes', []):
                if route.get('GatewayId', '').startswith('igw-'):
                    return True
        return False

    logger.debug(f'subnet {subnet_id} route tables: {route_tables}')
    if _has_igw_route(route_tables):
        return True
    if len(route_tables) > 0:
        return False

    # Handle the case that a "main" route table is implicitly associated with
    # subnets. Since the associations are implicit, the filter above won't find
    # any. Check there exists a main route table with routes pointing to an IGW.
    logger.debug('Checking main route table')
    region = ec2.meta.client.meta.region_name
    main_route_tables = _get_route_tables(ec2, vpc_id, region, main=True)
    return _has_igw_route(main_route_tables)


def _usable_subnets(
    ec2,
    user_specified_subnets: Optional[List[Any]],
    all_subnets: List[Any],
    azs: Optional[str],
    vpc_id_of_sg: Optional[str],
    use_internal_ips: bool,
) -> Tuple[List, str]:
    """Prunes subnets down to those that meet the following criteria.

    Subnets must be:
    * 'Available' according to AWS.
    * Public, unless `use_internal_ips` is specified.
    * In one of the AZs, if AZs are provided.
    * In the given VPC, if a VPC is specified for Security Groups.

    Returns:
        List[str]: Subnets that are usable.
        str: VPC ID of the first subnet.
    """

    # For existing cluster, it is ok to return a VPC and subnet not used by
    # the cluster, as AWS will ignore them.
    # There is a corner case where the multi-node cluster was partially
    # launched, launching the cluster again can cause the nodes located on
    # different VPCs, if VPCs in the project have changed. It should be fine to
    # not handle this special case as we don't want to sacrifice the performance
    # for every launch just for this rare case.

    def _are_user_subnets_pruned(current_subnets: List[Any]) -> bool:
        return user_specified_subnets is not None and len(
            current_subnets) != len(user_specified_subnets)

    def _get_pruned_subnets(current_subnets: List[Any]) -> Set[str]:
        current_subnet_ids = {s.subnet_id for s in current_subnets}
        user_specified_subnet_ids = {
            s.subnet_id for s in user_specified_subnets  # type: ignore
        }
        return user_specified_subnet_ids - current_subnet_ids

    try:
        candidate_subnets = (user_specified_subnets if user_specified_subnets
                             is not None else all_subnets)
        if vpc_id_of_sg:
            candidate_subnets = [
                s for s in candidate_subnets if s.vpc_id == vpc_id_of_sg
            ]

        available_subnets = [
            s for s in candidate_subnets if s.state == 'available'
        ]

        if use_internal_ips:
            # Get private subnets.
            #
            # We get private subnets by (1) not _is_subnet_public(), which
            # checks if there is a route to IGW; (2) the subnets must not assign
            # public IPs by default (not map_public_ip_on_launch).
            subnets = [
                s for s in available_subnets
                if not _is_subnet_public(ec2, s.subnet_id, vpc_id_of_sg) and
                not s.map_public_ip_on_launch
            ]
        else:
            # Get public subnets.
            #
            # Note that we do not test for 's.map_public_ip_on_launch' being
            # True. For example, the VPC creation helper from AWS will create a
            # 'public' and a 'private' subnet per AZ. However, the created
            # 'public' subnet by default has map_public_ip_on_launch set to
            # False as well. We can still allow users to launch a public
            # IP-enabled VM in such a public subnet (the underlying
            # ec2.create_instances() call will set AssociatePublicIpAddress to
            # True appropriately).
            subnets = [
                s for s in available_subnets
                if _is_subnet_public(ec2, s.subnet_id, vpc_id_of_sg)
            ]

        subnets = sorted(
            subnets,
            reverse=True,  # sort from Z-A
            key=lambda subnet: subnet.availability_zone,
        )
        logger.debug(f'use_internal_ips: {use_internal_ips}')
        logger.debug(f'subnets: {subnets}')
    except aws.botocore_exceptions().ClientError as exc:
        utils.handle_boto_error(exc,
                                'Failed to fetch available subnets from AWS.')
        raise exc

    if not subnets:
        _skypilot_log_error_and_exit_for_failover(
            'No usable subnets found, try '
            'manually creating an instance in your specified region to '
            'populate the list of subnets and trying this again. '
            'Note that the subnet must map public IPs '
            'on instance launch unless you set `use_internal_ips: true` in '
            'the `provider` config.')
    elif _are_user_subnets_pruned(subnets):
        _skypilot_log_error_and_exit_for_failover(
            f'The specified subnets are not '
            f'usable: {_get_pruned_subnets(subnets)}')

    if azs is not None:
        azs = [az.strip() for az in azs.split(',')]  # type: ignore
        subnets = [
            s for az in azs  # Iterate over AZs first to maintain the ordering
            for s in subnets if s.availability_zone == az
        ]
        if not subnets:
            _skypilot_log_error_and_exit_for_failover(
                f'No usable subnets matching availability zone {azs} found. '
                'Choose a different availability zone or try manually '
                'creating an instance in your specified region to populate '
                'the list of subnets and trying this again. If you have set '
                '`use_internal_ips`, check that this zone has a subnet that '
                '(1) has the substring \'private\' in its name tag and '
                '(2) does not assign public IPs (`map_public_ip_on_launch` '
                'is False).')
        elif _are_user_subnets_pruned(subnets):
            _skypilot_log_error_and_exit_for_failover(
                f'MISMATCH between specified subnets and Availability Zones! '
                'The following Availability Zones were specified in the '
                f'`provider section`: {azs}. The following subnets '
                f'have no matching availability zone: '
                f'{list(_get_pruned_subnets(subnets))}.')

    # Use subnets in only one VPC, so that _configure_security_groups only
    # needs to create a security group in this one VPC. Otherwise, we'd need
    # to set up security groups in all of the user's VPCs and set up networking
    # rules to allow traffic between these groups.
    # See https://github.com/ray-project/ray/pull/14868.
    first_subnet_vpc_id = subnets[0].vpc_id
    subnets = [s for s in subnets if s.vpc_id == first_subnet_vpc_id]
    if _are_user_subnets_pruned(subnets):
        subnet_vpcs = {
            s.subnet_id: s.vpc_id
            for s in user_specified_subnets  # type: ignore
        }
        _skypilot_log_error_and_exit_for_failover(
            'Subnets specified in more than one VPC! '
            'Please ensure that all subnets share the same VPC and retry your '
            f'request. Subnet VPCs: {subnet_vpcs}')
    return subnets, first_subnet_vpc_id


def _vpc_id_from_security_group_ids(ec2, sg_ids: List[str]) -> Any:
    # sort security group IDs to support deterministic unit test stubbing
    sg_ids = sorted(set(sg_ids))
    filters = [{'Name': 'group-id', 'Values': sg_ids}]
    security_groups = ec2.security_groups.filter(Filters=filters)
    vpc_ids = [sg.vpc_id for sg in security_groups]
    vpc_ids = list(set(vpc_ids))

    multiple_vpc_msg = ('All security groups specified in the cluster config '
                        'should belong to the same VPC.\n'
                        f'Security group IDs: {sg_ids}\n'
                        f'Their VPC IDs (expected 1 element): {vpc_ids}\n')
    assert len(vpc_ids) <= 1, multiple_vpc_msg

    no_sg_msg = ('Failed to detect a security group with id equal to any of '
                 'the configured SecurityGroupIds.')
    assert len(vpc_ids) > 0, no_sg_msg

    return vpc_ids[0]


def _get_vpc_id_by_name(ec2, vpc_name: str, region: str) -> str:
    """Returns the VPC ID of the unique VPC with a given name.

    Exits with code 1 if:
      - No VPC with the given name is found in the current region.
      - More than 1 VPC with the given name are found in the current region.
    """
    # Look in the 'Name' tag (shown as Name column in console).
    filters = [{'Name': 'tag:Name', 'Values': [vpc_name]}]
    vpcs = list(ec2.vpcs.filter(Filters=filters))
    if not vpcs:
        _skypilot_log_error_and_exit_for_failover(
            f'No VPC with name {vpc_name!r} is found in '
            f'{region}. To fix: specify a correct VPC name.')
    elif len(vpcs) > 1:
        _skypilot_log_error_and_exit_for_failover(
            f'Multiple VPCs with name {vpc_name!r} '
            f'found in {region}: {vpcs}. '
            'It is ambiguous as to which VPC to use. To fix: specify a '
            'VPC name that is uniquely identifying.')
    assert len(vpcs) == 1, vpcs
    return vpcs[0].id


def _get_subnet_and_vpc_id(ec2, security_group_ids: Optional[List[str]],
                           region: str, availability_zone: Optional[str],
                           use_internal_ips: bool,
                           vpc_name: Optional[str]) -> Tuple[Any, str]:
    if vpc_name is not None:
        vpc_id_of_sg = _get_vpc_id_by_name(ec2, vpc_name, region)
    elif security_group_ids:
        vpc_id_of_sg = _vpc_id_from_security_group_ids(ec2, security_group_ids)
    else:
        vpc_id_of_sg = None

    all_subnets = list(ec2.subnets.all())
    subnets, vpc_id = _usable_subnets(
        ec2,
        user_specified_subnets=None,
        all_subnets=all_subnets,
        azs=availability_zone,
        vpc_id_of_sg=vpc_id_of_sg,
        use_internal_ips=use_internal_ips,
    )
    return subnets, vpc_id


def _configure_security_group(ec2, vpc_id: str, expected_sg_name: str,
                              extended_ip_rules: List) -> List[str]:
    security_group = _get_or_create_vpc_security_group(ec2, vpc_id,
                                                       expected_sg_name)
    sg_ids = [security_group.id]

    inbound_rules = [
        # intra-cluster rules
        {
            'FromPort': -1,
            'ToPort': -1,
            'IpProtocol': '-1',
            'UserIdGroupPairs': [{
                'GroupId': i
            } for i in sg_ids],
        },
        # SSH rules
        {
            'FromPort': 22,
            'ToPort': 22,
            'IpProtocol': 'tcp',
            'IpRanges': [{
                'CidrIp': '0.0.0.0/0'
            }],
        },
        *extended_ip_rules,
    ]
    # upsert the default security group
    if not security_group.ip_permissions:
        # If users specify security groups, we should not change the rules
        # of these security groups. Here we change it because it is the default
        # security group for SkyPilot.
        security_group.authorize_ingress(IpPermissions=inbound_rules)

    return sg_ids


def _get_or_create_vpc_security_group(ec2, vpc_id: str,
                                      expected_sg_name: str) -> Any:
    # Figure out which security groups with this name exist for each VPC...
    vpc_to_existing_sg = {
        sg.vpc_id: sg for sg in _get_security_groups_from_vpc_ids(
            ec2,
            [vpc_id],
            [expected_sg_name],
        )
    }

    if vpc_id in vpc_to_existing_sg:
        return vpc_to_existing_sg[vpc_id]

    try:
        # create a new security group
        ec2.meta.client.create_security_group(
            Description='Auto-created security group for Ray workers',
            GroupName=expected_sg_name,
            VpcId=vpc_id,
        )
    except ec2.meta.client.exceptions.ClientError as e:
        message = ('Failed to create security group. Error: '
                   f'{common_utils.format_exception(e)}')
        logger.warning(message)
        raise exceptions.NoClusterLaunchedError(message) from e

    security_group = _get_security_groups_from_vpc_ids(ec2, [vpc_id],
                                                       [expected_sg_name])

    assert security_group, 'Failed to create security group'
    security_group = security_group[0]

    logger.info(f'Created new security group {colorama.Style.BRIGHT}'
                f'{security_group.group_name}{colorama.Style.RESET_ALL} '
                f'[id={security_group.id}]')
    return security_group


def _get_security_groups_from_vpc_ids(ec2, vpc_ids: List[str],
                                      group_names: List[str]) -> List[Any]:
    unique_vpc_ids = list(set(vpc_ids))
    unique_group_names = set(group_names)

    existing_groups = list(
        ec2.security_groups.filter(Filters=[{
            'Name': 'vpc-id',
            'Values': unique_vpc_ids
        }]))
    filtered_groups = [
        sg for sg in existing_groups if sg.group_name in unique_group_names
    ]
    return filtered_groups
