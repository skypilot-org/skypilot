import typing
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.clouds.aws import AWS
from sky.provision.aws import config


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
