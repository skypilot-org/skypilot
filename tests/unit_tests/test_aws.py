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
                                      vpc_name=None)

    error_message = str(e.value)
    assert f"SKYPILOT_ERROR_NO_NODES_LAUNCHED: The default VPC in {region} either does not exist or has no subnets." == error_message

    # Case 2: Specified VPC has no subnets.
    with pytest.raises(RuntimeError) as e:
        config._get_subnet_and_vpc_id(ec2=mock_ec2,
                                      security_group_ids=None,
                                      region=region,
                                      availability_zone=None,
                                      use_internal_ips=False,
                                      vpc_name=vpc_name)

    error_message = str(e.value)
    assert f"SKYPILOT_ERROR_NO_NODES_LAUNCHED: No candidate subnets found in specified VPC {vpc_id}." == error_message

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
                                      vpc_name=vpc_name)

    error_message = str(e.value)
    assert f"SKYPILOT_ERROR_NO_NODES_LAUNCHED: The use_internal_ips option is set to True, but all candidate subnets are public." == error_message

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
                                      vpc_name=vpc_name)

    error_message = str(e.value)
    assert f"SKYPILOT_ERROR_NO_NODES_LAUNCHED: All candidate subnets are private, did you mean to set use_internal_ips to True?" == error_message


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
