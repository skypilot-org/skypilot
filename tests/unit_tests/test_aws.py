import pathlib
from types import SimpleNamespace
import typing
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky import logs
from sky import resources
from sky import skypilot_config
from sky.backends import backend_utils
from sky.clouds import Region
from sky.clouds import Zone
from sky.clouds.aws import AWS
from sky.provision import common as provision_common
from sky.provision import constants as provision_constants
from sky.provision.aws import config
from sky.provision.aws import instance as aws_instance
from sky.utils import common_utils
from sky.utils import config_utils


def test_aws_label():
    aws = AWS()
    # Invalid - AWS prefix
    assert not aws.is_label_valid('aws:whatever', 'value')[0]
    # Valid - valid prefix
    assert aws.is_label_valid('any:whatever', 'value')[0]
    # Valid - valid prefix
    assert aws.is_label_valid('Owner', 'username-1')[0]
    # Invalid - Too long
    assert not (aws.is_label_valid(
        'sprinto:thisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thing',
        'value',
    )[0])
    # Invalid - Too long
    assert not (aws.is_label_valid(
        'sprinto:short',
        'thisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thingthisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thing',
    )[0])


def test_create_instances_adds_volume_tag_spec():
    ec2_fail_fast = MagicMock()
    ec2_fail_fast.create_instances.return_value = ['instance']

    node_config = {
        'SubnetIds': ['subnet-123'],
        'SecurityGroupIds': ['sg-123'],
        'InstanceType': 'g5.xlarge',
    }
    tags = {
        'Owner': 'alice',
    }

    result = aws_instance._create_instances(
        ec2_fail_fast=ec2_fail_fast,
        cluster_name='cluster',
        node_config=node_config,
        tags=tags,
        count=1,
        associate_public_ip_address=True,
        max_efa_interfaces=0,
    )

    assert result == ['instance']

    call_kwargs = ec2_fail_fast.create_instances.call_args.kwargs
    assert call_kwargs['MinCount'] == 1
    assert call_kwargs['MaxCount'] == 1

    tag_specs = {
        tag_spec['ResourceType']: tag_spec['Tags']
        for tag_spec in call_kwargs['TagSpecifications']
    }
    expected_tags = [{
        'Key': 'Name',
        'Value': 'cluster'
    }, {
        'Key': provision_constants.TAG_RAY_CLUSTER_NAME,
        'Value': 'cluster'
    }, {
        'Key': provision_constants.TAG_SKYPILOT_CLUSTER_NAME,
        'Value': 'cluster'
    }, {
        'Key': 'Owner',
        'Value': 'alice'
    }]
    assert tag_specs['instance'] == expected_tags
    assert tag_specs['volume'] == expected_tags


def test_merge_tag_specs_merges_volume_tags():
    base_tag_specs = [{
        'ResourceType': 'instance',
        'Tags': [{
            'Key': 'Name',
            'Value': 'cluster'
        }],
    }, {
        'ResourceType': 'volume',
        'Tags': [{
            'Key': 'Name',
            'Value': 'cluster'
        }, {
            'Key': 'Owner',
            'Value': 'alice'
        }],
    }]
    user_tag_specs = [{
        'ResourceType': 'volume',
        'Tags': [{
            'Key': 'Owner',
            'Value': 'bob'
        }, {
            'Key': 'Team',
            'Value': 'ml'
        }],
    }]

    aws_instance._merge_tag_specs(base_tag_specs, user_tag_specs)

    volume_tags = next(tag_spec['Tags']
                       for tag_spec in base_tag_specs
                       if tag_spec['ResourceType'] == 'volume')
    assert volume_tags == [{
        'Key': 'Name',
        'Value': 'cluster'
    }, {
        'Key': 'Owner',
        'Value': 'bob'
    }, {
        'Key': 'Team',
        'Value': 'ml'
    }]


def test_run_instances_tags_resumed_instance_volumes():
    stopped_instance = SimpleNamespace(
        id='i-stopped',
        state={'Name': 'stopped'},
        placement={'AvailabilityZone': 'us-east-1a'},
        tags=[{
            'Key': key,
            'Value': value
        } for key, value in provision_constants.HEAD_NODE_TAGS.items()],
        block_device_mappings=[{
            'Ebs': {
                'VolumeId': 'vol-1'
            }
        }, {
            'Ebs': {
                'VolumeId': 'vol-2'
            }
        }],
    )

    mock_ec2 = MagicMock()
    mock_ec2.meta.client.meta.region_name = 'us-east-1'
    mock_ec2.instances.filter.return_value = [stopped_instance]

    mock_ec2_fail_fast = MagicMock()
    mock_ec2_fail_fast.meta.client.start_instances.return_value = {}

    provision_config = provision_common.ProvisionConfig(
        provider_config={'use_internal_ips': False},
        authentication_config={},
        docker_config={},
        node_config={},
        count=1,
        tags={'Owner': 'alice'},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )

    with patch.object(aws_instance,
                      '_default_ec2_resource',
                      return_value=mock_ec2), patch.object(
                          aws_instance.aws,
                          'resource',
                          return_value=mock_ec2_fail_fast):
        record = aws_instance.run_instances(region='us-east-1',
                                            cluster_name='cluster',
                                            cluster_name_on_cloud='cluster',
                                            config=provision_config)

    assert record.resumed_instance_ids == ['i-stopped']
    assert record.created_instance_ids == []

    create_tags_calls = mock_ec2.meta.client.create_tags.call_args_list
    assert len(create_tags_calls) == 2
    assert create_tags_calls[0].kwargs == {
        'Resources': ['i-stopped'],
        'Tags': [{
            'Key': 'Owner',
            'Value': 'alice'
        }],
    }
    assert create_tags_calls[1].kwargs == {
        'Resources': ['vol-1', 'vol-2'],
        'Tags': [{
            'Key': 'Owner',
            'Value': 'alice'
        }],
    }


# Verbatim from AWS: credentials allowed to tag instances but not volumes.
_VOLUME_TAG_DENIED_MSG = (
    'You are not authorized to perform this operation. User: '
    'arn:aws:iam::123456789012:user/restricted is not authorized to perform: '
    'ec2:CreateTags on resource: arn:aws:ec2:us-east-1:123456789012:volume/* '
    'because no identity-based policy allows the ec2:CreateTags action.')
# Verbatim from AWS: credentials that may not launch instances at all. Same
# error code, different action -- the fallback must not confuse the two.
_RUN_INSTANCES_DENIED_MSG = (
    'You are not authorized to perform this operation. User: '
    'arn:aws:iam::123456789012:user/restricted is not authorized to perform: '
    'ec2:RunInstances on resource: '
    'arn:aws:ec2:us-east-1:123456789012:instance/* because no identity-based '
    'policy allows the ec2:RunInstances action.')
# Verbatim from AWS: credentials that may tag volumes but not instances. Same
# code AND same action as the volume refusal -- only the resource differs, and
# dropping the volume tags cannot help here.
_INSTANCE_TAG_DENIED_MSG = (
    'You are not authorized to perform this operation. User: '
    'arn:aws:iam::123456789012:user/restricted is not authorized to perform: '
    'ec2:CreateTags on resource: '
    'arn:aws:ec2:us-east-1:123456789012:instance/* because no identity-based '
    'policy allows the ec2:CreateTags action.')


def _unauthorized(message: str, operation: str = 'RunInstances'):
    return aws_instance.aws.botocore_exceptions().ClientError(
        error_response={
            'Error': {
                'Code': 'UnauthorizedOperation',
                'Message': message,
            }
        },
        operation_name=operation,
    )


def _fail_fast_mock(region='us-east-1'):
    ec2_fail_fast = MagicMock()
    ec2_fail_fast.meta.client.meta.region_name = region
    return ec2_fail_fast


def _launch(ec2_fail_fast, cluster_name='cluster', enforce_volume_tags=False):
    return aws_instance._create_instances(
        ec2_fail_fast=ec2_fail_fast,
        cluster_name=cluster_name,
        node_config={
            'SubnetIds': ['subnet-123'],
            'SecurityGroupIds': ['sg-123'],
            'InstanceType': 'm5.large',
        },
        tags={'Owner': 'alice'},
        count=1,
        associate_public_ip_address=True,
        max_efa_interfaces=0,
        enforce_volume_tags=enforce_volume_tags,
    )


def test_create_instances_retries_without_volume_tags_when_denied():
    """A launch must survive credentials that cannot tag volumes.

    AWS refuses the whole RunInstances call in that case, so the launch cannot
    just continue -- it has to be reissued without the volume tags.
    """
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = [
        _unauthorized(_VOLUME_TAG_DENIED_MSG),
        ['instance'],
    ]

    assert _launch(ec2_fail_fast) == ['instance']
    assert ec2_fail_fast.create_instances.call_count == 2

    first, second = ec2_fail_fast.create_instances.call_args_list
    assert {spec['ResourceType'] for spec in first.kwargs['TagSpecifications']
           } == {'instance', 'volume'}
    # Only the volume tags are given up; the instance stays tagged, so the
    # cluster is still discoverable.
    assert [
        spec['ResourceType'] for spec in second.kwargs['TagSpecifications']
    ] == ['instance']
    keys = {
        tag['Key']
        for spec in second.kwargs['TagSpecifications']
        for tag in spec['Tags']
    }
    assert provision_constants.TAG_RAY_CLUSTER_NAME in keys


def test_create_instances_costs_nothing_when_tagging_is_permitted():
    """Credentials that may tag volumes must see no extra API call.

    The fallback lives in an exception handler, so the permitted path has to
    stay a single RunInstances -- exactly as before this behaviour existed.
    """
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = [['instance']]

    assert _launch(ec2_fail_fast) == ['instance']
    assert ec2_fail_fast.create_instances.call_count == 1
    assert {
        spec['ResourceType'] for spec in
        ec2_fail_fast.create_instances.call_args.kwargs['TagSpecifications']
    } == {'instance', 'volume'}


def test_create_instances_does_not_swallow_launch_permission_error():
    """UnauthorizedOperation for RunInstances itself must surface as-is.

    It shares the error code with the volume-tag denial, so keying off the
    code alone would silently retry a launch that can never succeed.
    """
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = _unauthorized(
        _RUN_INSTANCES_DENIED_MSG)

    with pytest.raises(RuntimeError):
        _launch(ec2_fail_fast)

    # Retried across subnets, but never with the volume tags removed.
    for call in ec2_fail_fast.create_instances.call_args_list:
        assert {
            spec['ResourceType'] for spec in call.kwargs['TagSpecifications']
        } == {'instance', 'volume'}


@pytest.mark.parametrize(
    ('enforce_tags', 'should_fail'),
    [
        # Nothing asked for: volumes are tagged when allowed, given up when
        # not.
        (None, False),
        ([], False),
        # Instance tagging is already required -- a refusal to tag instances
        # is never swallowed -- so naming it says nothing about volumes.
        (['instance'], False),
        # Volume tagging demanded, in either order.
        (['volume'], True),
        (['instance', 'volume'], True),
        (['volume', 'instance'], True),
    ])
def test_enforce_tags_config_shapes(enforce_tags, should_fail):
    """Every accepted `aws.enforce_tags` value resolves to the right behaviour.

    Goes through run_instances so the config is read the way provisioning
    reads it, rather than re-implementing the lookup in the test.
    """
    provider_config = {'use_internal_ips': False}
    if enforce_tags is not None:
        provider_config['enforce_tags'] = enforce_tags

    mock_ec2 = MagicMock()
    mock_ec2.meta.client.meta.region_name = 'us-east-1'
    mock_ec2.instances.filter.return_value = []

    created = SimpleNamespace(id='i-new',
                              placement={'AvailabilityZone': 'us-east-1a'},
                              tags=[])
    mock_ec2_fail_fast = _fail_fast_mock()
    mock_ec2_fail_fast.create_instances.side_effect = [
        _unauthorized(_VOLUME_TAG_DENIED_MSG),
        [created],
    ]

    provision_config = provision_common.ProvisionConfig(
        provider_config=provider_config,
        authentication_config={},
        docker_config={},
        node_config={
            'SubnetIds': ['subnet-123'],
            'SecurityGroupIds': ['sg-123'],
            'InstanceType': 'm5.large',
        },
        count=1,
        tags={'Owner': 'alice'},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )

    with patch.object(aws_instance,
                      '_default_ec2_resource',
                      return_value=mock_ec2), patch.object(
                          aws_instance.aws,
                          'resource',
                          return_value=mock_ec2_fail_fast):
        if should_fail:
            with pytest.raises(exceptions.InvalidCloudCredentials):
                aws_instance.run_instances(region='us-east-1',
                                           cluster_name='cluster',
                                           cluster_name_on_cloud='cluster',
                                           config=provision_config)
            # Enforcement must never retry with the volume tags stripped.
            for call in mock_ec2_fail_fast.create_instances.call_args_list:
                assert {
                    spec['ResourceType']
                    for spec in call.kwargs['TagSpecifications']
                } == {'instance', 'volume'}
        else:
            record = aws_instance.run_instances(region='us-east-1',
                                                cluster_name='cluster',
                                                cluster_name_on_cloud='cluster',
                                                config=provision_config)
            assert record.created_instance_ids == ['i-new']
            # Degraded: reissued without the volume tags.
            assert [
                spec['ResourceType'] for spec in mock_ec2_fail_fast.
                create_instances.call_args.kwargs['TagSpecifications']
            ] == ['instance']


def test_create_instances_enforced_volume_tags_fail_the_launch():
    """`aws.enforce_tags: [volume]` turns the refusal into a failure.

    Compliance deployments need a guarantee, not a warning buried in a log,
    so the launch must stop rather than quietly produce untagged volumes.
    """
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = _unauthorized(
        _VOLUME_TAG_DENIED_MSG)

    with pytest.raises(exceptions.InvalidCloudCredentials) as excinfo:
        _launch(ec2_fail_fast, enforce_volume_tags=True)

    message = str(excinfo.value)
    assert 'aws.enforce_tags' in message
    assert 'ec2:CreateTags' in message
    # Never retried without the volume tags: that is the thing being enforced.
    for call in ec2_fail_fast.create_instances.call_args_list:
        assert {
            spec['ResourceType'] for spec in call.kwargs['TagSpecifications']
        } == {'instance', 'volume'}


def test_enforced_volume_tags_do_not_affect_a_permitted_launch():
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = [['instance']]

    assert _launch(ec2_fail_fast, enforce_volume_tags=True) == ['instance']
    assert ec2_fail_fast.create_instances.call_count == 1


def test_create_instances_does_not_swallow_instance_tag_denial():
    """A refusal to tag *instances* must not be read as a volume problem.

    It carries the same code and the same `ec2:CreateTags` action as the
    volume refusal; only the resource differs. Dropping the volume tags
    cannot help, so mistaking it would report the wrong cause and then fail
    again anyway.
    """
    ec2_fail_fast = _fail_fast_mock()
    ec2_fail_fast.create_instances.side_effect = _unauthorized(
        _INSTANCE_TAG_DENIED_MSG)

    with pytest.raises(RuntimeError):
        _launch(ec2_fail_fast)

    # Retried across subnets, but never with the volume tags stripped.
    for call in ec2_fail_fast.create_instances.call_args_list:
        assert {
            spec['ResourceType'] for spec in call.kwargs['TagSpecifications']
        } == {'instance', 'volume'}


def test_volume_tagging_is_retried_on_every_launch():
    """The refusal must not be cached across launches.

    Caching it would leave volumes untagged after someone grants the missing
    permission, until the API server happened to be restarted.
    """
    first = _fail_fast_mock()
    first.create_instances.side_effect = [
        _unauthorized(_VOLUME_TAG_DENIED_MSG),
        ['instance'],
    ]
    _launch(first)

    # A later launch in the same process asks for volume tags again, so a
    # permission granted in the meantime takes effect immediately.
    second = _fail_fast_mock()
    second.create_instances.side_effect = [['instance']]
    _launch(second, cluster_name='cluster2')
    assert second.create_instances.call_count == 1
    assert {
        spec['ResourceType'] for spec in
        second.create_instances.call_args.kwargs['TagSpecifications']
    } == {'instance', 'volume'}


@patch.object(aws_instance, 'logger')
def test_run_instances_volume_tag_failure_does_not_abort_resume(mock_logger):
    """Resuming must not fail because the volumes cannot be tagged."""
    stopped_instance = SimpleNamespace(
        id='i-stopped',
        state={'Name': 'stopped'},
        placement={'AvailabilityZone': 'us-east-1a'},
        tags=[{
            'Key': key,
            'Value': value
        } for key, value in provision_constants.HEAD_NODE_TAGS.items()],
        block_device_mappings=[{
            'Ebs': {
                'VolumeId': 'vol-1'
            }
        }],
    )

    mock_ec2 = MagicMock()
    mock_ec2.meta.client.meta.region_name = 'us-east-1'
    mock_ec2.instances.filter.return_value = [stopped_instance]

    def create_tags_side_effect(**kwargs):
        if kwargs['Resources'] == ['vol-1']:
            raise _unauthorized(_VOLUME_TAG_DENIED_MSG, 'CreateTags')
        return {}

    mock_ec2.meta.client.create_tags.side_effect = create_tags_side_effect

    mock_ec2_fail_fast = MagicMock()
    mock_ec2_fail_fast.meta.client.start_instances.return_value = {}

    provision_config = provision_common.ProvisionConfig(
        provider_config={'use_internal_ips': False},
        authentication_config={},
        docker_config={},
        node_config={},
        count=1,
        tags={'Owner': 'alice'},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )

    with patch.object(aws_instance,
                      '_default_ec2_resource',
                      return_value=mock_ec2), patch.object(
                          aws_instance.aws,
                          'resource',
                          return_value=mock_ec2_fail_fast):
        record = aws_instance.run_instances(region='us-east-1',
                                            cluster_name='cluster',
                                            cluster_name_on_cloud='cluster',
                                            config=provision_config)

    # The resume succeeded and the refusal was reported, not raised.
    assert record.resumed_instance_ids == ['i-stopped']
    assert any('Volumes will not be tagged' in str(call)
               for call in mock_logger.warning.call_args_list)


def test_usable_subnets(monkeypatch):
    """Test the output of the usable_subnets function."""

    vpc_name = "test_vpc"
    vpc_id = "test-vpc-id"
    region = "test-region"

    subnets = MagicMock()
    monkeypatch.setattr(subnets, 'all', lambda: [])

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets
    # monkeypatch.setattr(mock_ec2, 'subnets', subnets
    # Case 1: default VPC has no subnets.
    monkeypatch.setattr(config, 'get_vpc_id_by_name',
                        lambda *args, **kwargs: vpc_id)
    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name=None,
                                      subnet_names=None)

    error_message = str(e.value)
    assert f"{provision_constants.ERROR_NO_NODES_LAUNCHED}: The default VPC in {region} either does not exist or has no subnets." == error_message

    # Case 2: Specified VPC has no subnets.
    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name=vpc_name,
                                      subnet_names=None)

    error_message = str(e.value)
    assert f"{provision_constants.ERROR_NO_NODES_LAUNCHED}: No candidate subnets found in specified VPC {vpc_id}." == error_message

    # Case 3: All the subnets are public and use_internal_ips is True.
    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)
    subnet = MagicMock()
    subnet.vpc = MagicMock()
    subnet.vpc.is_default = True
    subnet.vpc_id = vpc_id
    subnet.state = 'available'
    monkeypatch.setattr(subnets, 'all', lambda: [subnet])
    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=True,
                                      vpc_name=vpc_name,
                                      subnet_names=None)

    error_message = str(e.value)
    assert f"{provision_constants.ERROR_NO_NODES_LAUNCHED}: The use_internal_ips option is set to True, but all candidate subnets are public." == error_message

    # Case 4: All the subnets are private and use_internal_ips is False
    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: False)
    subnet = MagicMock()
    subnet.vpc = MagicMock()
    subnet.vpc.is_default = True
    subnet.vpc_id = vpc_id
    subnet.state = 'available'
    monkeypatch.setattr(subnets, 'all', lambda: [subnet])
    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name=vpc_name,
                                      subnet_names=None)

    error_message = str(e.value)
    assert f"{provision_constants.ERROR_NO_NODES_LAUNCHED}: All candidate subnets are private, did you mean to set use_internal_ips to True?" == error_message


def test_subnet_names_resolves_by_tag(monkeypatch):
    """Test that subnet_names resolves subnets by tag:Name filter."""
    vpc_id = 'test-vpc-id'
    region = 'us-east-1'

    # Create mock subnets returned by the filter call
    mock_subnet_1 = MagicMock()
    mock_subnet_1.vpc_id = vpc_id
    mock_subnet_1.subnet_id = 'subnet-aaa'
    mock_subnet_1.state = 'available'
    mock_subnet_1.availability_zone = 'us-east-1a'
    mock_subnet_1.map_public_ip_on_launch = False

    mock_subnet_2 = MagicMock()
    mock_subnet_2.vpc_id = vpc_id
    mock_subnet_2.subnet_id = 'subnet-bbb'
    mock_subnet_2.state = 'available'
    mock_subnet_2.availability_zone = 'us-east-1b'
    mock_subnet_2.map_public_ip_on_launch = False

    filtered_subnets = [mock_subnet_1, mock_subnet_2]

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = filtered_subnets
    subnets_mock.filter.return_value = filtered_subnets

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    # Subnets are public
    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)

    result_subnets, result_vpc_id = config._get_subnet_and_vpc_id(
        ec2=mock_ec2,
        security_group_ids=None,
        region=region,
        availability_zone=None,
        use_internal_ips=False,
        vpc_name=None,
        subnet_names=['my-subnet-1', 'my-subnet-2'])

    # Verify filter was called with correct tag:Name filter
    subnets_mock.filter.assert_called_once_with(Filters=[{
        'Name': 'tag:Name',
        'Values': ['my-subnet-1', 'my-subnet-2'],
    }])
    assert result_vpc_id == vpc_id
    assert len(result_subnets) == 2


def test_subnet_names_single_string(monkeypatch):
    """Test that a single string subnet_name is converted to a list."""
    vpc_id = 'test-vpc-id'
    region = 'us-east-1'

    mock_subnet = MagicMock()
    mock_subnet.vpc_id = vpc_id
    mock_subnet.subnet_id = 'subnet-aaa'
    mock_subnet.state = 'available'
    mock_subnet.availability_zone = 'us-east-1a'
    mock_subnet.map_public_ip_on_launch = False

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = [mock_subnet]
    subnets_mock.filter.return_value = [mock_subnet]

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)

    result_subnets, result_vpc_id = config._get_subnet_and_vpc_id(
        ec2=mock_ec2,
        security_group_ids=None,
        region=region,
        availability_zone=None,
        use_internal_ips=False,
        vpc_name=None,
        subnet_names='my-single-subnet')

    # Should convert string to list and pass to filter
    subnets_mock.filter.assert_called_once_with(Filters=[{
        'Name': 'tag:Name',
        'Values': ['my-single-subnet'],
    }])
    assert result_vpc_id == vpc_id
    assert len(result_subnets) == 1


def test_subnet_names_not_found(monkeypatch):
    """Test error when specified subnet names don't match any subnets."""
    region = 'us-east-1'

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = []
    # No subnets match the filter
    subnets_mock.filter.return_value = []

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name=None,
                                      subnet_names=['nonexistent-subnet'])

    error_message = str(e.value)
    assert 'No subnets with name(s)' in error_message
    assert 'nonexistent-subnet' in error_message


def test_subnet_names_infers_vpc(monkeypatch):
    """Test that VPC ID is inferred from specified subnets when no vpc_name."""
    vpc_id = 'vpc-inferred'
    region = 'us-east-1'

    mock_subnet = MagicMock()
    mock_subnet.vpc_id = vpc_id
    mock_subnet.subnet_id = 'subnet-aaa'
    mock_subnet.state = 'available'
    mock_subnet.availability_zone = 'us-east-1a'
    mock_subnet.map_public_ip_on_launch = False

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = [mock_subnet]
    subnets_mock.filter.return_value = [mock_subnet]

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)

    _, result_vpc_id = config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                                     security_group_ids=None,
                                                     region=region,
                                                     availability_zone=None,
                                                     use_internal_ips=False,
                                                     vpc_name=None,
                                                     subnet_names=['my-subnet'])

    # VPC should be inferred from the first matching subnet
    assert result_vpc_id == vpc_id


def test_subnet_names_with_vpc_name(monkeypatch):
    """Test that subnet_names works together with vpc_name."""
    vpc_id = 'vpc-explicit'
    region = 'us-east-1'

    mock_subnet = MagicMock()
    mock_subnet.vpc_id = vpc_id
    mock_subnet.subnet_id = 'subnet-aaa'
    mock_subnet.state = 'available'
    mock_subnet.availability_zone = 'us-east-1a'
    mock_subnet.map_public_ip_on_launch = False

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = [mock_subnet]
    subnets_mock.filter.return_value = [mock_subnet]

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    monkeypatch.setattr(config, 'get_vpc_id_by_name',
                        lambda *args, **kwargs: vpc_id)
    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)

    result_subnets, result_vpc_id = config._get_subnet_and_vpc_id(
        ec2=mock_ec2,
        security_group_ids=None,
        region=region,
        availability_zone=None,
        use_internal_ips=False,
        vpc_name='my-vpc',
        subnet_names=['my-subnet'])

    assert result_vpc_id == vpc_id
    assert len(result_subnets) == 1


def test_subnet_names_wrong_vpc(monkeypatch):
    """Test error when subnets don't belong to the specified VPC."""
    region = 'us-east-1'

    # Subnet belongs to a different VPC than the one specified
    mock_subnet = MagicMock()
    mock_subnet.vpc_id = 'vpc-other'
    mock_subnet.subnet_id = 'subnet-aaa'
    mock_subnet.state = 'available'
    mock_subnet.availability_zone = 'us-east-1a'

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = [mock_subnet]
    subnets_mock.filter.return_value = [mock_subnet]

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    monkeypatch.setattr(config, 'get_vpc_id_by_name',
                        lambda *args, **kwargs: 'vpc-specified')

    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name='my-vpc',
                                      subnet_names=['my-subnet'])

    error_message = str(e.value)
    assert 'No candidate subnets found in specified VPC' in error_message


def test_security_group_tagged_on_create():
    """Test that create_security_group is called with skypilot tag."""
    mock_ec2 = MagicMock()

    # No existing security group found
    mock_ec2.SecurityGroup.return_value = None
    mock_ec2.security_groups = MagicMock()
    mock_ec2.security_groups.filter.return_value = []

    # After creation, return a mock security group
    created_sg = MagicMock(id='sg-new', group_name='test-sg')
    with patch.object(config,
                      'get_security_group_from_vpc_id',
                      side_effect=[None, created_sg]):
        config._get_or_create_vpc_security_group(ec2=mock_ec2,
                                                 vpc_id='vpc-123',
                                                 expected_sg_name='test-sg')

    mock_ec2.meta.client.create_security_group.assert_called_once()
    call_kwargs = mock_ec2.meta.client.create_security_group.call_args[1]
    assert 'TagSpecifications' in call_kwargs
    tag_specs = call_kwargs['TagSpecifications']
    assert tag_specs == [{
        'ResourceType': 'security-group',
        'Tags': [{
            'Key': 'skypilot',
            'Value': 'true',
        }],
    }]


def test_ssm_default(monkeypatch):
    """Test that SSM is explicitly set to true if use_internal_ips is true
    and ssh_proxy_command is not set.
    """
    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    tmp_yaml_path = '/tmp/fake-yaml-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: tmp_yaml_path)
    # Patch make_deploy_variables.
    monkeypatch.setattr(resources.Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {'region': 'us-east-1'})
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    config_dict = {
        'aws': {
            'use_internal_ips': True
        },
    }
    config_dict = config_utils.Config.from_dict(config_dict)

    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)

    use_internal_ips = skypilot_config.get_effective_region_config(
        cloud=str(AWS()).lower(),
        region='us-east-1',
        keys=('use_internal_ips',),
        default_value=False)
    loaded_config = skypilot_config._get_loaded_config()
    print(f'_get_loaded_config: {loaded_config}')
    assert use_internal_ips is True

    def fill_template_side_effect(*args, **kwargs):
        config_dict = args[1]
        print(config_dict)
        assert 'ssh_proxy_command' in config_dict
        assert "ssm" in config_dict['ssh_proxy_command']
        assert 'use_internal_ips' in config_dict
        assert config_dict['use_internal_ips'] is True
        raise RuntimeError('fake-error')

    monkeypatch.setattr(common_utils, 'fill_template',
                        fill_template_side_effect)
    with pytest.raises(RuntimeError) as e:
        backend_utils.write_cluster_config(
            to_provision=resources.Resources(cloud=AWS(),
                                             instance_type='c2.xlarge'),
            num_nodes=1,
            cluster_config_template='aws-ray.yml.j2',
            cluster_name='fake-cluster',
            local_wheel_path=pathlib.Path('fake-wheel-path'),
            wheel_hash='fake-wheel-hash',
            region=Region(name='fake-region'),
            zones=[Zone(name='fake-zone')])


def test_subnet_names_in_cluster_config(monkeypatch):
    """Test that subnet_names from config is passed through to the template."""
    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    tmp_yaml_path = '/tmp/fake-yaml-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: tmp_yaml_path)
    monkeypatch.setattr(resources.Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {'region': 'us-east-1'})
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    config_dict = {
        'aws': {
            'subnet_names': ['my-subnet-1', 'my-subnet-2'],
        },
    }
    config_dict = config_utils.Config.from_dict(config_dict)

    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)

    subnet_names = skypilot_config.get_effective_region_config(
        cloud=str(AWS()).lower(),
        region='us-east-1',
        keys=('subnet_names',),
        default_value=None)
    assert subnet_names == ['my-subnet-1', 'my-subnet-2']

    def fill_template_side_effect(*args, **kwargs):
        template_vars = args[1]
        assert 'subnet_names' in template_vars
        assert template_vars['subnet_names'] == ['my-subnet-1', 'my-subnet-2']
        raise RuntimeError('fake-error')

    monkeypatch.setattr(common_utils, 'fill_template',
                        fill_template_side_effect)
    with pytest.raises(RuntimeError):
        backend_utils.write_cluster_config(
            to_provision=resources.Resources(cloud=AWS(),
                                             instance_type='c2.xlarge'),
            num_nodes=1,
            cluster_config_template='aws-ray.yml.j2',
            cluster_name='fake-cluster',
            local_wheel_path=pathlib.Path('fake-wheel-path'),
            wheel_hash='fake-wheel-hash',
            region=Region(name='fake-region'),
            zones=[Zone(name='fake-zone')])


def test_subnet_names_default_none_in_cluster_config(monkeypatch):
    """Test that subnet_names defaults to None when not configured."""
    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    tmp_yaml_path = '/tmp/fake-yaml-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: tmp_yaml_path)
    monkeypatch.setattr(resources.Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {'region': 'us-east-1'})
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    config_dict = {
        'aws': {},
    }
    config_dict = config_utils.Config.from_dict(config_dict)

    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)

    def fill_template_side_effect(*args, **kwargs):
        template_vars = args[1]
        assert 'subnet_names' in template_vars
        assert template_vars['subnet_names'] is None
        raise RuntimeError('fake-error')

    monkeypatch.setattr(common_utils, 'fill_template',
                        fill_template_side_effect)
    with pytest.raises(RuntimeError):
        backend_utils.write_cluster_config(
            to_provision=resources.Resources(cloud=AWS(),
                                             instance_type='c2.xlarge'),
            num_nodes=1,
            cluster_config_template='aws-ray.yml.j2',
            cluster_name='fake-cluster',
            local_wheel_path=pathlib.Path('fake-wheel-path'),
            wheel_hash='fake-wheel-hash',
            region=Region(name='fake-region'),
            zones=[Zone(name='fake-zone')])


def test_ssm_explicit_default(monkeypatch):
    """Test that SSM is false if explicitly set to false even if
    use_internal_ips is true and ssh_proxy_command is not set.
    """
    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    tmp_yaml_path = '/tmp/fake-yaml-path'
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: tmp_yaml_path)
    # Patch make_deploy_variables.
    monkeypatch.setattr(resources.Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {'region': 'us-east-1'})
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    config_dict = {
        'aws': {
            'use_ssm': False,
            'use_internal_ips': True
        },
    }
    config_dict = config_utils.Config.from_dict(config_dict)

    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)

    use_internal_ips = skypilot_config.get_effective_region_config(
        cloud=str(AWS()).lower(),
        region='us-east-1',
        keys=('use_internal_ips',),
        default_value=False)
    loaded_config = skypilot_config._get_loaded_config()
    print(f'_get_loaded_config: {loaded_config}')
    assert use_internal_ips is True

    def fill_template_side_effect(*args, **kwargs):
        config_dict = args[1]
        print(config_dict)
        assert 'ssh_proxy_command' in config_dict
        assert config_dict['ssh_proxy_command'] is None
        assert 'use_internal_ips' in config_dict
        assert config_dict['use_internal_ips'] is True
        raise RuntimeError('fake-error')

    monkeypatch.setattr(common_utils, 'fill_template',
                        fill_template_side_effect)
    with pytest.raises(RuntimeError) as e:
        backend_utils.write_cluster_config(
            to_provision=resources.Resources(cloud=AWS(),
                                             instance_type='c2.xlarge'),
            num_nodes=1,
            cluster_config_template='aws-ray.yml.j2',
            cluster_name='fake-cluster',
            local_wheel_path=pathlib.Path('fake-wheel-path'),
            wheel_hash='fake-wheel-hash',
            region=Region(name='fake-region'),
            zones=[Zone(name='fake-zone')])


def test_subnet_names_multi_az_no_error(monkeypatch):
    """Test that subnet_names spanning multiple AZs does not raise MISMATCH.

    When user specifies subnets in us-east-1a and us-east-1b, and SkyPilot
    picks AZ us-east-1a, the us-east-1b subnet is filtered out. This is
    expected behavior, not a mismatch error.
    """
    vpc_id = 'test-vpc-id'
    region = 'us-east-1'

    mock_subnet_1a = MagicMock()
    mock_subnet_1a.vpc_id = vpc_id
    mock_subnet_1a.subnet_id = 'subnet-1a'
    mock_subnet_1a.state = 'available'
    mock_subnet_1a.availability_zone = 'us-east-1a'
    mock_subnet_1a.map_public_ip_on_launch = False

    mock_subnet_1b = MagicMock()
    mock_subnet_1b.vpc_id = vpc_id
    mock_subnet_1b.subnet_id = 'subnet-1b'
    mock_subnet_1b.state = 'available'
    mock_subnet_1b.availability_zone = 'us-east-1b'
    mock_subnet_1b.map_public_ip_on_launch = False

    filtered_subnets = [mock_subnet_1a, mock_subnet_1b]

    subnets_mock = MagicMock()
    subnets_mock.all.return_value = filtered_subnets
    subnets_mock.filter.return_value = filtered_subnets

    mock_ec2 = MagicMock()
    mock_ec2.subnets = subnets_mock

    monkeypatch.setattr('sky.provision.aws.config._is_subnet_public',
                        lambda *args, **kwargs: True)

    # Launch with AZ us-east-1a — should succeed with only subnet-1a
    result_subnets, result_vpc_id = config._get_subnet_and_vpc_id(
        ec2=mock_ec2,
        security_group_ids=None,
        region=region,
        availability_zone='us-east-1a',
        use_internal_ips=False,
        vpc_name=None,
        subnet_names=['subnet-1a-name', 'subnet-1b-name'])

    assert result_vpc_id == vpc_id
    # Only the subnet in the chosen AZ should remain
    assert len(result_subnets) == 1
    assert result_subnets[0].availability_zone == 'us-east-1a'


# --- bootstrap_instances: default security group pre-creation ---------------


def _run_bootstrap_sg(security_group_config):
    """Run bootstrap_instances with mocked AWS calls.

    Returns the mock for _configure_security_group to let tests assert
    which security groups were configured.
    """
    provision_config = provision_common.ProvisionConfig(
        provider_config={
            'region': 'us-east-2',
            'security_group': security_group_config,
        },
        authentication_config={},
        docker_config={},
        node_config={
            'ImageId': 'ami-12345',
            # Skip IAM role configuration.
            'IamInstanceProfile': {
                'Name': 'dummy-profile'
            },
        },
        count=1,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )
    mock_subnet = MagicMock()
    mock_subnet.subnet_id = 'subnet-12345'
    with patch.object(config.aws, 'resource'), \
            patch.object(config, '_get_subnet_and_vpc_id',
                         return_value=([mock_subnet], 'vpc-12345')), \
            patch.object(config, '_configure_security_group',
                         return_value=['sg-12345']) as mock_configure_sg:
        config.bootstrap_instances('us-east-2', 'test-cluster',
                                   provision_config)
    return mock_configure_sg


def test_bootstrap_skips_default_sg_for_user_specified_sg():
    """No default-SG pre-creation when the SG is specified by the user.

    A user-specified security group (aws.security_group_name) is never
    deleted by SkyPilot at teardown, so the default security group would
    never be used; attempting to create it is a pointless CreateSecurityGroup
    call that surfaces a scary (but harmless) warning for users whose IAM
    policy denies it.
    """
    mock_sg = _run_bootstrap_sg({
        'GroupName': 'user-sg',
        'ManagedBySkyPilot': False,
    })
    assert mock_sg.call_count == 1
    assert mock_sg.call_args[0][2] == 'user-sg'


def test_bootstrap_precreates_default_sg_for_managed_sg():
    """Default SG is pre-created for SkyPilot-managed per-cluster SGs.

    These SGs (created when ports are opened) are deleted at teardown;
    the default SG lets terminate_instances re-parent instances so the
    per-cluster SG can be deleted without blocking on termination.
    """
    mock_sg = _run_bootstrap_sg({
        'GroupName': 'sky-sg-test-cluster',
        'ManagedBySkyPilot': True,
    })
    assert mock_sg.call_count == 2
    assert mock_sg.call_args_list[0][0][2] == 'sky-sg-test-cluster'
    assert (mock_sg.call_args_list[1][0][2] ==
            config.aws_cloud.DEFAULT_SECURITY_GROUP_NAME)


def test_bootstrap_precreates_default_sg_when_flag_absent():
    """Backward compatibility: older cluster YAMLs lack ManagedBySkyPilot."""
    mock_sg = _run_bootstrap_sg({
        'GroupName': 'sky-sg-test-cluster',
    })
    assert mock_sg.call_count == 2


def test_bootstrap_no_precreate_when_using_default_sg():
    """No second call when the cluster already uses the default SG."""
    mock_sg = _run_bootstrap_sg({
        'GroupName': config.aws_cloud.DEFAULT_SECURITY_GROUP_NAME,
    })
    assert mock_sg.call_count == 1
