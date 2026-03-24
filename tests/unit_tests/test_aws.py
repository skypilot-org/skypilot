import pathlib
import typing
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import logs
from sky import resources
from sky import skypilot_config
from sky.backends import backend_utils
from sky.clouds import Region
from sky.clouds import Zone
from sky.clouds.aws import AWS
from sky.provision import constants as provision_constants
from sky.provision.aws import config
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
