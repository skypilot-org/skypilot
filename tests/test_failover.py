from collections import namedtuple
import types
import unittest
import uuid

import boto3
import botocore.exceptions
from moto import mock_aws
import pytest

import sky
from sky.backends.cloud_vm_ray_backend import FailoverCloudErrorHandlerV2
from sky.provision.aws import config as aws_config
from sky.provision.aws import instance as aws_instance


@pytest.mark.parametrize('enable_all_clouds', [[sky.AWS()]], indirect=True)
def test_aws_region_failover(enable_all_clouds, monkeypatch):
    """Test SkyPilot's ability to failover between AWS regions when a capacity error occurs."""

    # Set AWS credentials as environment variables (as recommended by Moto)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    # Create a counter to track which region is being accessed
    region_attempt_count = {'count': 0}
    region_subnets = {}

    # Create a Subnet class to match what SkyPilot expects
    Subnet = namedtuple(
        'Subnet',
        ['subnet_id', 'vpc_id', 'availability_zone', 'map_public_ip_on_launch'])

    # Mock subnet and VPC discovery
    def mock_get_subnet_and_vpc_id(*args, **kwargs):
        # Get the region from kwargs
        region = kwargs.get('region', 'us-east-1')

        # If we already have subnet info for this region, return it
        if region in region_subnets:
            return region_subnets[region]

        # Otherwise create a subnet in the requested region
        ec2 = boto3.resource('ec2', region_name=region)

        # Create VPC and subnet in the requested region
        vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
        subnet = ec2.create_subnet(VpcId=vpc.id,
                                   CidrBlock='10.0.0.0/24',
                                   AvailabilityZone=f"{region}a")

        # Configure subnet to map public IPs
        ec2.meta.client.modify_subnet_attribute(
            SubnetId=subnet.id, MapPublicIpOnLaunch={'Value': True})

        # Store subnet object
        subnet_obj = Subnet(subnet_id=subnet.id,
                            vpc_id=vpc.id,
                            availability_zone=f"{region}a",
                            map_public_ip_on_launch=True)

        region_subnets[region] = ([subnet_obj], vpc.id)
        return ([subnet_obj], vpc.id)

    # Mock security groups
    def mock_get_or_create_vpc_security_group(*args, **kwargs):
        # Return a mock security group
        return unittest.mock.Mock(id="sg-12345678", group_name="test-sg")

    # Mock IAM role
    def mock_configure_iam_role(*args, **kwargs):
        return {'Name': 'skypilot-test-role'}

    def mock_create_instances(ec2_fail_fast, cluster_name, node_config, tags,
                              count, associate_public_ip_address):
        # This information helps us identify which region is being used
        region = ec2_fail_fast.meta.client.meta.region_name
        region_attempt_count['count'] += 1
        # First region should fail with InsufficientInstanceCapacity
        if region == 'us-east-1' and region_attempt_count['count'] == 1:
            # Simulate capacity error in first region
            raise botocore.exceptions.ClientError(
                {
                    'Error': {
                        'Code': 'InsufficientInstanceCapacity',
                        'Message': 'Insufficient capacity in us-east-1'
                    }
                }, 'RunInstances')

        # Dynamically create a region subnet entry if it doesn't exist
        if region not in region_subnets:
            # Create a mock ec2 resource for this region
            ec2 = boto3.resource('ec2', region_name=region)

            # Create VPC and subnet in the region
            vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
            subnet = ec2.create_subnet(VpcId=vpc.id,
                                       CidrBlock='10.0.0.0/24',
                                       AvailabilityZone=f"{region}a")

            # Configure subnet to map public IPs
            ec2.meta.client.modify_subnet_attribute(
                SubnetId=subnet.id, MapPublicIpOnLaunch={'Value': True})

            # Store subnet object
            subnet_obj = Subnet(subnet_id=subnet.id,
                                vpc_id=vpc.id,
                                availability_zone=f"{region}a",
                                map_public_ip_on_launch=True)

            region_subnets[region] = ([subnet_obj], vpc.id)

        # For all regions (except first attempt at us-east-1), create mock instances
        mock_instances = []
        for i in range(count):
            instance_id = f'i-{uuid.uuid4().hex[:8]}'

            # Create a more complete mock instance with all required attributes
            instance = unittest.mock.MagicMock()
            instance.id = instance_id
            instance.tags = [{'Key': 'Name', 'Value': cluster_name}]
            instance.state = {'Name': 'running'}

            # Add placement attribute that includes AvailabilityZone
            instance.placement = {'AvailabilityZone': f'{region}a'}

            # Add other attributes SkyPilot might need
            instance.instance_type = 't2.micro'
            instance.public_ip_address = f'192.168.1.{i}'
            instance.private_ip_address = f'10.0.0.{i}'
            instance.security_groups = [{
                'GroupName': 'test-sg',
                'GroupId': 'sg-12345678'
            }]
            instance.key_name = 'test-key'
            instance.vpc_id = region_subnets[region][
                1]  # Use the VPC ID we stored earlier

            mock_instances.append(instance)

        return mock_instances

    def mock_wait_instances(region, cluster_name_on_cloud, state):
        # Always return successfully without waiting
        return

    def mock_post_provision_runtime_setup(cloud_name, cluster_name,
                                          cluster_yaml, provision_record,
                                          custom_resource, log_dir):
        # Create a mock ClusterInfo with a head instance
        import copy

        from sky.provision.common import ClusterInfo
        from sky.provision.common import InstanceInfo

        # Get region from the provision record
        region = provision_record.region

        # Create a head instance
        head_instance_id = f'i-{uuid.uuid4().hex[:8]}'

        # Create instance info for the head
        head_instance = InstanceInfo(
            instance_id=head_instance_id,
            internal_ip='10.0.0.1',
            external_ip='192.168.1.1',
            tags={
                'Name': cluster_name.name_on_cloud,
                'ray-cluster-name': cluster_name.name_on_cloud,
                'ray-node-type': 'head'
            })

        # Create ClusterInfo
        instances = {head_instance_id: [head_instance]}
        cluster_info = ClusterInfo(instances=instances,
                                   head_instance_id=head_instance_id,
                                   provider_name='aws',
                                   provider_config={
                                       'region': region,
                                       'use_internal_ips': False
                                   },
                                   ssh_user='ubuntu')

        return cluster_info

    def mock_execute(self, handle, task, detach_run, dryrun=False):
        # Return a fake job ID without attempting to SSH
        return 1234

    # Use Moto's context manager to mock AWS - now all boto3 clients will be mocked
    with mock_aws():
        # Create a simple AMI for our test
        # ec2_client = boto3.client('ec2', region_name='us-east-1')

        # # Create a VPC and subnet in us-east-1
        # vpc = ec2_client.create_vpc(CidrBlock='10.0.0.0/16')
        # subnet = ec2_client.create_subnet(
        #     VpcId=vpc['Vpc']['VpcId'],
        #     CidrBlock='10.0.0.0/24',
        #     AvailabilityZone='us-east-1a'
        # )

        # # Store the subnet information for us-east-1
        # subnet_obj = Subnet(
        #     subnet_id=subnet['Subnet']['SubnetId'],
        #     vpc_id=vpc['Vpc']['VpcId'],
        #     availability_zone='us-east-1a',
        #     map_public_ip_on_launch=True
        # )
        # region_subnets['us-east-1'] = ([subnet_obj], vpc['Vpc']['VpcId'])

        # # Create a test AMI
        # instance = ec2_client.run_instances(
        #     ImageId='ami-12345678',  # This is fine in Moto
        #     MinCount=1,
        #     MaxCount=1,
        #     InstanceType='t2.micro'
        # )['Instances'][0]

        # ami_id = ec2_client.create_image(
        #     InstanceId=instance['InstanceId'],
        #     Name='test-ami'
        # )['ImageId']

        # # Make it available in multiple regions
        # ec2_west = boto3.client('ec2', region_name='us-west-2')
        # west_ami = ec2_west.copy_image(
        #     Name='test-ami-west',
        #     SourceImageId=ami_id,
        #     SourceRegion='us-east-1'
        # )['ImageId']

        # # Create a VPC and subnet in us-west-2 as well
        # vpc_west = ec2_west.create_vpc(CidrBlock='10.0.0.0/16')
        # subnet_west = ec2_west.create_subnet(
        #     VpcId=vpc_west['Vpc']['VpcId'],
        #     CidrBlock='10.0.0.0/24',
        #     AvailabilityZone='us-west-2a'
        # )

        # # Store the subnet information for us-west-2
        # subnet_obj_west = Subnet(
        #     subnet_id=subnet_west['Subnet']['SubnetId'],
        #     vpc_id=vpc_west['Vpc']['VpcId'],
        #     availability_zone='us-west-2a',
        #     map_public_ip_on_launch=True
        # )
        # region_subnets['us-west-2'] = ([subnet_obj_west], vpc_west['Vpc']['VpcId'])

        # Apply our mocks after setting up the environment but before running the test
        monkeypatch.setattr(aws_config, '_get_subnet_and_vpc_id',
                            mock_get_subnet_and_vpc_id)
        monkeypatch.setattr(aws_config, '_get_or_create_vpc_security_group',
                            mock_get_or_create_vpc_security_group)
        monkeypatch.setattr(aws_config, '_configure_iam_role',
                            mock_configure_iam_role)
        monkeypatch.setattr(aws_instance, '_create_instances',
                            mock_create_instances)
        monkeypatch.setattr(sky.provision.aws, 'wait_instances',
                            mock_wait_instances)
        # Add mock for post_provision_runtime_setup
        monkeypatch.setattr(sky.provision.provisioner,
                            'post_provision_runtime_setup',
                            mock_post_provision_runtime_setup)
        # Add mock for _execute
        monkeypatch.setattr(sky.backends.cloud_vm_ray_backend.CloudVmRayBackend,
                            '_execute', mock_execute)
        # Create a task that will try us-east-1 first, then failover to us-west-2
        task = sky.Task(run='echo hi')
        task.set_resources(
            sky.Resources(
                sky.AWS(),
                # Don't specify a region, so it will try multiple regions
                instance_type='t2.micro',
            ))

        # Use a spy on the AWS handler to confirm it gets called
        with unittest.mock.patch.object(
                FailoverCloudErrorHandlerV2,
                '_aws_handler',
                wraps=FailoverCloudErrorHandlerV2._aws_handler) as mock_handler:
            try:
                # Launch with minimal output
                result = sky.stream_and_get(
                    sky.launch(task, cluster_name='test-failover',
                               dryrun=False))

                # Verify the AWS handler was called (meaning a failure occurred)
                assert mock_handler.called, "Failover handler was not called"

                # Verify the task was launched successfully
                assert result.success, "Task launch failed"

                # Verify attempt counter shows we tried multiple regions
                assert region_attempt_count[
                    'count'] > 1, "Did not try multiple regions"
            finally:
                # Clean up
                sky.down('test-failover')
