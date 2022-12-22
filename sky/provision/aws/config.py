"""AWS configuration bootstrapping."""
# The codes are adapted from
# https://github.com/ray-project/ray/tree/ray-2.0.1/python/ray/autoscaler/_private/aws/config.py
# Git commit of the release 2.0.1: 03b6bc7b5a305877501110ec04710a9c57011479
import copy
import itertools
import json
import logging
import textwrap
import time
from distutils import version
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import botocore
import boto3

from ray.autoscaler._private.cli_logger import cf, cli_logger

from sky import authentication
from sky import sky_logging
from sky.adaptors import aws
from sky.provision.aws import utils
from sky.provision import common

logger = sky_logging.init_logger(__name__)

RAY = 'ray-autoscaler'
SECURITY_GROUP_TEMPLATE = RAY + '-{}'

SKYPILOT = 'skypilot'
DEFAULT_SKYPILOT_INSTANCE_PROFILE = SKYPILOT + '-v1'
DEFAULT_SKYPILOT_IAM_ROLE = SKYPILOT + '-v1'

# todo: cli_logger should handle this assert properly
# this should probably also happens somewhere else
assert version.StrictVersion(boto3.__version__) >= version.StrictVersion(
    '1.4.8'), 'Boto3 version >= 1.4.8 required, try `pip install -U boto3`'

# Suppress excessive connection dropped logs from boto
logging.getLogger('botocore').setLevel(logging.WARNING)


def _skypilot_log_error_and_exit_for_failover(error: str) -> None:
    """Logs an message then raises a specific RuntimeError to trigger failover.

    Mainly used for handling VPC/subnet errors before nodes are launched.
    """
    # NOTE: keep. The backend looks for this to know no nodes are launched.
    prefix = "SKYPILOT_ERROR_NO_NODES_LAUNCHED: "
    raise RuntimeError(prefix + error)


def bootstrap_instances(region: str, cluster_name: str,
              config: common.InstanceConfig) -> common.InstanceConfig:
    """See sky/provision/__init__.py"""
    # create a copy of the input config to modify
    config = copy.deepcopy(config)

    node_cfg = config.node_config
    aws_credentials = config.provider_config.get('aws_credentials', {})

    # If NetworkInterfaces are provided, extract the necessary fields for the
    # config stages below.
    if 'NetworkInterfaces' in node_cfg:
        subnet_ids, security_group_ids = (
            _configure_subnets_and_groups_from_network_interfaces(node_cfg))
    else:
        subnet_ids = node_cfg.get('SubnetIds')  # type: ignore
        security_group_ids = node_cfg.get('SecurityGroupIds')  # type: ignore

    # The head node needs to have an IAM role that allows it to create further
    # EC2 instances.
    if 'IamInstanceProfile' not in node_cfg:
        iam = aws.resource('iam', region_name=region, **aws_credentials)
        node_cfg['IamInstanceProfile'] = _configure_iam_role(iam)

    # Configure SSH access, using an existing key pair if possible.
    node_cfg['UserData'] = _configure_ssh_keypair(
        config.authentication_config['ssh_user'])

    ec2 = aws.resource('ec2', region_name=region, **aws_credentials)

    if subnet_ids is not None:
        subnets, vpc_id = _validate_subnet(ec2, subnet_ids, security_group_ids)
    else:
        # Pick a reasonable subnet if not specified by the user.
        subnets, vpc_id = _create_subnet(
            ec2,
            security_group_ids,
            config.provider_config['region'],
            availability_zone=config.provider_config.get('availability_zone'),
            use_internal_ips=config.provider_config.get('use_internal_ips',
                                                        False),
            vpc_name=config.provider_config.get('vpc_name'))

    # Cluster workers should be in a security group that permits traffic within
    # the group, and also SSH access from outside.
    if security_group_ids is None:
        start_time = time.time()
        logger.info('Creating or updating security groups...')

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
                                                       config.provider_config.get("ports", []),
                                                       extended_ip_rules)
        end_time = time.time()
        elapsed = end_time - start_time
        logger.info(f'Security groups created or updated in {elapsed:.5f} seconds.')

    # store updated subnet and security group configs in node config
    # NOTE: "SubnetIds" is not a real config key for AWS instance.
    node_cfg['SubnetIds'] = [s.subnet_id for s in subnets]
    node_cfg['SecurityGroupIds'] = security_group_ids

    # Provide helpful message for missing ImageId for node configuration.
    # NOTE(skypilot): skypilot uses the default AMIs in aws.py.
    node_ami = config.node_config.get('ImageId')
    if not node_ami:
        _skypilot_log_error_and_exit_for_failover(
            'No ImageId found in the node_config. '
            'ImageId will need to be set manually in your cluster config.')

    return config


def _configure_ssh_keypair(ssh_user: str) -> str:
    """Configure SSH access for AWS, using an existing key pair if possible"""
    _, public_key_path = authentication.get_or_generate_keys()
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    # Use cloud init in UserData to set up the authorized_keys to get
    # around the number of keys limit and permission issues with
    # ec2.describe_key_pairs.
    # https://aws.amazon.com/premiumsupport/knowledge-center/ec2-user-account-cloud-init-user-data/
    return textwrap.dedent(f"""\
        #cloud-config
        users:
        - name: {ssh_user}
          ssh-authorized-keys:
            - {public_key}
        """)


def _configure_iam_role(iam) -> Dict[str, Any]:

    def _get_instance_profile(profile_name: str):
        profile = iam.InstanceProfile(profile_name)
        try:
            profile.load()
            return profile
        except botocore.exceptions.ClientError as exc:
            if exc.response.get('Error', {}).get('Code') == 'NoSuchEntity':
                return None
            else:
                utils.handle_boto_error(
                    exc,
                    'Failed to fetch IAM instance profile data for '
                    '{} from AWS.',
                    cf.bold(profile_name),
                )
                raise exc

    def _get_role(role_name: str):
        role = iam.Role(role_name)
        try:
            role.load()
            return role
        except botocore.exceptions.ClientError as exc:
            if exc.response.get('Error', {}).get('Code') == 'NoSuchEntity':
                return None
            else:
                utils.handle_boto_error(
                    exc,
                    'Failed to fetch IAM role data for {} from AWS.',
                    cf.bold(role_name),
                )
                raise exc

    instance_profile_name = DEFAULT_SKYPILOT_INSTANCE_PROFILE
    profile = _get_instance_profile(instance_profile_name)

    if profile is None:
        cli_logger.verbose(
            'Creating new IAM instance profile {} for use as the default.',
            cf.bold(instance_profile_name),
        )
        iam.meta.client.create_instance_profile(
            InstanceProfileName=instance_profile_name)
        profile = _get_instance_profile(instance_profile_name)
        time.sleep(15)  # wait for propagation

    cli_logger.doassert(profile is not None,
                        'Failed to create instance profile.')  # todo: err msg
    assert profile is not None, 'Failed to create instance profile'

    if not profile.roles:
        role_name = DEFAULT_SKYPILOT_IAM_ROLE
        role = _get_role(role_name)
        if role is None:
            cli_logger.verbose(
                'Creating new IAM role {} for use as the default '
                'instance role.',
                cf.bold(role_name),
            )
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
            cli_logger.doassert(role is not None,
                                'Failed to create role.')  # todo: err msg

            assert role is not None, 'Failed to create role'

            for policy_arn in attach_policy_arns:
                role.attach_policy(PolicyArn=policy_arn)

        profile.add_role(RoleName=role.name)
        time.sleep(15)  # wait for propagation
    return {'Arn': profile.arn}


def _usable_subnet_ids(
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

    def _are_user_subnets_pruned(current_subnets: List[Any]) -> bool:
        return user_specified_subnets is not None and len(
            current_subnets) != len(user_specified_subnets)

    def _get_pruned_subnets(current_subnets: List[Any]) -> Set[str]:
        current_subnet_ids = {s.subnet_id for s in current_subnets}
        user_specified_subnet_ids = {
            s.subnet_id for s in user_specified_subnets  # type: ignore
        }
        return user_specified_subnet_ids - current_subnet_ids

    def _subnet_name_tag_contains(subnet, substr: str) -> bool:
        tags = subnet.meta.data['Tags']
        for tag in tags:
            if tag['Key'] == 'Name':
                name = tag['Value']
                return substr in name
        return False

    try:
        candidate_subnets = (user_specified_subnets if user_specified_subnets
                             is not None else all_subnets)
        if vpc_id_of_sg:
            candidate_subnets = [
                s for s in candidate_subnets if s.vpc_id == vpc_id_of_sg
            ]

        subnets = sorted(
            (
                s for s in candidate_subnets if s.state == 'available' and (
                    # If using internal IPs, the subnets must not assign public
                    # IPs. Additionally, requires that each eligible subnet
                    # contain a name tag which includes the substring
                    # 'private'. This is a HACK; see below.
                    #
                    # Reason: the first two checks alone are not enough. For
                    # example, the VPC creation helper from AWS will create a
                    # "public" and a "private" subnet per AZ. However, the
                    # created "public" subnet by default has
                    # map_public_ip_on_launch set to False as well. This means
                    # we could've launched in that subnet, which will make any
                    # instances not able to send outbound traffic to the
                    # Internet, due to the way route tables/gateways are set up
                    # for that public subnet. The "public" subnets are NOT
                    # intended to host data plane VMs, while the "private"
                    # subnets are.
                    #
                    # An alternative to the subnet name hack is to ensure
                    # there's a route (dest=0.0.0.0/0, target=nat-*) in the
                    # subnet's route table so that outbound connections
                    # work. This seems hard to do, given a ec2.Subnet
                    # object. (Easy to see in console though.) So we opt for
                    # the subnet name requirement for now.
                    (use_internal_ips and not s.map_public_ip_on_launch and
                     _subnet_name_tag_contains(s, 'private')) or
                    # Or if using public IPs, the subnets must assign public
                    # IPs.
                    (not use_internal_ips and s.map_public_ip_on_launch)
                    # NOTE: SkyPilot also changes the semantics of
                    # 'use_internal_ips' through the above two conditions.
                    # Previously, this flag by itself does not enforce only
                    # choosing subnets that do not assign public IPs.  Now we
                    # do so.
                    #
                    # In both before and now, this flag makes Ray communicate
                    # between the client and the head node using the latter's
                    # private ip.
                )),
            reverse=True,  # sort from Z-A
            key=lambda subnet: subnet.availability_zone,
        )
    except botocore.exceptions.ClientError as exc:
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
        _skypilot_log_error_and_exit_for_failover(f'The specified subnets are not '
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
                '(1) has the substring "private" in its name tag and '
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
    cli_logger.doassert(len(vpc_ids) <= 1, multiple_vpc_msg)
    assert len(vpc_ids) <= 1, multiple_vpc_msg

    no_sg_msg = ('Failed to detect a security group with id equal to any of '
                 'the configured SecurityGroupIds.')
    cli_logger.doassert(len(vpc_ids) > 0, no_sg_msg)
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
        _skypilot_log_error_and_exit_for_failover(f'No VPC with name {vpc_name!r} is found in '
                           f'{region}. To fix: specify a correct VPC name.')
    elif len(vpcs) > 1:
        _skypilot_log_error_and_exit_for_failover(
            f'Multiple VPCs with name {vpc_name!r} '
            f'found in {region}: {vpcs}. '
            'It is ambiguous as to which VPC to use. To fix: specify a '
            'VPC name that is uniquely identifying.')
    assert len(vpcs) == 1, vpcs
    return vpcs[0].id


def _validate_subnet(ec2, subnet_ids: List[str],
                     security_group_ids: List[str]) -> Tuple[List, str]:
    if security_group_ids:
        _vpc_id_from_security_group_ids(ec2, security_group_ids)
    subnet_ids = tuple(subnet_ids)
    subnets = list(
        ec2.subnets.filter(Filters=[{
            'Name': 'subnet-id',
            'Values': list(subnet_ids)
        }]))

    # TODO: better error message
    cli_logger.doassert(
        len(subnets) == len(subnet_ids), 'Not all subnet IDs found: {}',
        subnet_ids)
    assert len(subnets) == len(subnet_ids), 'Subnet ID not found: {}'.format(
        subnet_ids)
    return subnets, subnets[0].vpc_id


def _create_subnet(ec2, security_group_ids: List[str], region: str,
                   availability_zone: str, use_internal_ips: bool,
                   vpc_name: Optional[str]) -> Tuple[Any, str]:
    if vpc_name is not None:
        vpc_id_of_sg = _get_vpc_id_by_name(ec2, vpc_name, region)
    elif security_group_ids:
        vpc_id_of_sg = _vpc_id_from_security_group_ids(ec2, security_group_ids)
    else:
        vpc_id_of_sg = None

    all_subnets = list(ec2.subnets.all())
    subnets, vpc_id = _usable_subnet_ids(
        None,
        all_subnets,
        azs=availability_zone,
        vpc_id_of_sg=vpc_id_of_sg,
        use_internal_ips=use_internal_ips,
    )
    return subnets, vpc_id


def _retrieve_user_specified_rules(ports):
    rules = []
    for port in ports:
        if isinstance(port, int):
            from_port = to_port = port
        else:
            from_port, to_port = port.split("-")
            from_port = int(from_port)
            to_port = int(to_port)
        rules.append(
            {
                "FromPort": from_port,
                "ToPort": to_port,
                "IpProtocol": "tcp",
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }
        )
    return rules


def _configure_security_group(ec2, vpc_id: str, expected_sg_name: str, ports: List[Union[int, str]],
                              extended_ip_rules: List) -> List[str]:
    security_group = _get_or_create_vpc_security_group(ec2, vpc_id,
                                                       expected_sg_name)
    sg_ids = [security_group.id]

    inbound_rules = _retrieve_user_specified_rules(ports)
    inbound_rules.extend([
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
    ])
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

    # create a new security group
    ec2.meta.client.create_security_group(
        Description='Auto-created security group for Ray workers',
        GroupName=expected_sg_name,
        VpcId=vpc_id,
    )
    security_group = _get_security_groups_from_vpc_ids(ec2, [vpc_id],
                                                       [expected_sg_name])
    security_group = security_group[0] if security_group else None

    cli_logger.doassert(security_group,
                        'Failed to create security group')  # err msg

    cli_logger.verbose(
        'Created new security group {}',
        cf.bold(security_group.group_name),
        _tags=dict(id=security_group.id),
    )
    cli_logger.doassert(security_group,
                        'Failed to create security group')  # err msg
    assert security_group, 'Failed to create security group'
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


def _configure_subnets_and_groups_from_network_interfaces(
        node_cfg: Dict[str, Any]) -> Tuple[List, List]:
    """
    Copies all network interface subnet and security group IDs into their
    parent node config.

    Args:
        node_cfg (Dict[str, Any]): node config to bootstrap
    Raises:
        ValueError: If [1] subnet and security group IDs exist at both the
        node config and network interface levels, [2] any network interface
        doesn't have a subnet defined, or [3] any network interface doesn't
        have a security group defined.
    """
    net_ifs = node_cfg['NetworkInterfaces']
    # If NetworkInterfaces are defined, SubnetId and SecurityGroupIds
    # can't be specified in the same node type config.
    conflict_keys = ['SubnetId', 'SubnetIds', 'SecurityGroupIds']
    if any(conflict in node_cfg for conflict in conflict_keys):
        raise ValueError(
            'If NetworkInterfaces are defined, subnets and security groups '
            'must ONLY be given in each NetworkInterface.')

    subnets = [ni.get('SubnetId', '') for ni in net_ifs]
    if not all(subnets):
        raise ValueError(
            'NetworkInterfaces are defined but at least one is missing a '
            'subnet. Please ensure all interfaces have a subnet assigned.')
    security_groups = [ni.get('Groups', []) for ni in net_ifs]
    if not all(security_groups):
        raise ValueError(
            'NetworkInterfaces are defined but at least one is missing a '
            'security group. Please ensure all interfaces have a security '
            'group assigned.')

    return subnets, list(itertools.chain(*security_groups))
