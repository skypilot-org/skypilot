import re
import unittest
import uuid

import boto3
import botocore.exceptions
import moto
import pytest

import sky
from sky import global_user_state
from sky.backends import cloud_vm_ray_backend
from sky.provision.aws import instance as aws_instance
from sky.utils import db_utils


@pytest.fixture
def _mock_db_conn(tmp_path, monkeypatch):
    db_path = tmp_path / 'state_testing.db'
    db_conn = db_utils.SQLiteConn(str(db_path), global_user_state.create_table)
    monkeypatch.setattr(global_user_state, '_DB', db_conn)


@pytest.mark.parametrize('enable_all_clouds', [[sky.AWS()]], indirect=True)
def test_aws_region_failover(enable_all_clouds, _mock_db_conn, mock_aws_backend,
                             monkeypatch, capfd):
    """Test SkyPilot's ability to failover between AWS regions."""
    region_attempt_count = {'count': 0}

    def mock_create_instances(ec2_fail_fast, cluster_name, node_config, tags,
                              count, associate_public_ip_address):
        region = ec2_fail_fast.meta.client.meta.region_name
        region_attempt_count['count'] += 1
        if region == 'us-east-1' and region_attempt_count['count'] == 1:
            raise botocore.exceptions.ClientError(
                {
                    'Error': {
                        'Code': 'InsufficientInstanceCapacity',
                        'Message': 'Insufficient capacity in us-east-1'
                    }
                }, 'RunInstances')

        ec2 = boto3.resource('ec2', region_name=region)
        vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
        subnet = ec2.create_subnet(VpcId=vpc.id,
                                   CidrBlock='10.0.0.0/24',
                                   AvailabilityZone=f"{region}a")
        ec2.meta.client.modify_subnet_attribute(
            SubnetId=subnet.id, MapPublicIpOnLaunch={'Value': True})

        mock_instances = []
        for i in range(count):
            instance_id = f'i-{uuid.uuid4().hex[:8]}'
            instance = unittest.mock.MagicMock()
            instance.id = instance_id
            instance.tags = [{'Key': 'Name', 'Value': cluster_name}]
            instance.state = {'Name': 'running'}
            instance.placement = {'AvailabilityZone': f'{region}a'}
            instance.instance_type = 't2.micro'
            instance.public_ip_address = f'192.168.1.{i}'
            instance.private_ip_address = f'10.0.0.{i}'
            instance.security_groups = [{
                'GroupName': 'test-sg',
                'GroupId': 'sg-12345678'
            }]
            instance.key_name = 'test-key'
            instance.vpc_id = vpc.id
            mock_instances.append(instance)
        return mock_instances

    with moto.mock_aws():
        monkeypatch.setattr(aws_instance, '_create_instances',
                            mock_create_instances)
        task = sky.Task(run='echo hi')
        task.set_resources(sky.Resources(sky.AWS(), instance_type='t2.micro'))

        with unittest.mock.patch.object(
                cloud_vm_ray_backend.FailoverCloudErrorHandlerV2,
                '_aws_handler',
                wraps=cloud_vm_ray_backend.FailoverCloudErrorHandlerV2.
                _aws_handler) as mock_handler:
            try:
                sky.stream_and_get(
                    sky.launch(task, cluster_name='test-failover',
                               dryrun=False))
                assert mock_handler.called, "Failover handler was not called"
                assert region_attempt_count[
                    'count'] > 1, "Did not try multiple regions"
                out, err = capfd.readouterr()
                all_output = out + err
                print("\n=== CAPTURED STDOUT ===")
                print(out)
                print("\n=== CAPTURED STDERR ===")
                print(err)
                assert "Insufficient capacity in us-east-1" in all_output
                assert "Launching on AWS us-east-2" in all_output
                assert re.search(
                    r"Provisioning 'test-failover' took \d+\.\d+ seconds",
                    all_output)
            finally:
                sky.down('test-failover')
