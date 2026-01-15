"""AWS moto-based fixtures for integration testing.

This module provides pytest fixtures using moto to mock AWS services,
enabling fast integration tests without requiring real AWS credentials
or incurring AWS costs.

Moto-supported services used by SkyPilot:
- EC2: Instance lifecycle, VPCs, subnets, security groups
- IAM: Roles, instance profiles, policies
- S3: Bucket operations, object storage
- STS: Credential verification

Usage:
    from tests.unit_tests.aws.aws_fixtures import (
        mock_aws_credentials,
        mock_ec2,
        mock_iam,
        mock_s3,
        aws_test_environment,
    )

    def test_ec2_operations(aws_test_environment):
        ec2 = boto3.client('ec2', region_name='us-east-1')
        # Test EC2 operations with moto mock
"""

import os
from typing import Any, Dict, Generator, List, Optional, Tuple
from unittest import mock

import boto3
import pytest

# Import moto - the AWS mocking library
try:
    from moto import mock_aws
    MOTO_AVAILABLE = True
except ImportError:
    MOTO_AVAILABLE = False
    mock_aws = None

# Default test configuration
DEFAULT_REGION = 'us-east-1'
DEFAULT_AZ = 'us-east-1a'
DEFAULT_AMI_ID = 'ami-12345678'
DEFAULT_INSTANCE_TYPE = 'm5.xlarge'
DEFAULT_VPC_CIDR = '10.0.0.0/16'
DEFAULT_SUBNET_CIDR = '10.0.1.0/24'

# GPU instance types for testing
GPU_INSTANCE_TYPES = {
    'p3.2xlarge': {
        'gpu_count': 1,
        'gpu_type': 'V100'
    },
    'p3.8xlarge': {
        'gpu_count': 4,
        'gpu_type': 'V100'
    },
    'p4d.24xlarge': {
        'gpu_count': 8,
        'gpu_type': 'A100'
    },
    'g5.xlarge': {
        'gpu_count': 1,
        'gpu_type': 'A10G'
    },
    'g5.48xlarge': {
        'gpu_count': 8,
        'gpu_type': 'A10G'
    },
}


def requires_moto(func):
    """Decorator to skip tests if moto is not available."""
    return pytest.mark.skipif(not MOTO_AVAILABLE,
                              reason='moto library not installed')(func)


# =============================================================================
# Credential Fixtures
# =============================================================================


@pytest.fixture
def mock_aws_credentials():
    """Fixture to set mock AWS credentials for moto.

    Moto requires AWS credentials to be set (even though they're not used).
    This fixture sets dummy credentials and cleans them up after the test.
    """
    original_env = {}
    env_vars = {
        'AWS_ACCESS_KEY_ID': 'testing',
        'AWS_SECRET_ACCESS_KEY': 'testing',
        'AWS_SECURITY_TOKEN': 'testing',
        'AWS_SESSION_TOKEN': 'testing',
        'AWS_DEFAULT_REGION': DEFAULT_REGION,
    }

    # Save original values and set mock values
    for key, value in env_vars.items():
        original_env[key] = os.environ.get(key)
        os.environ[key] = value

    yield

    # Restore original values
    for key, original_value in original_env.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value


# =============================================================================
# EC2 Fixtures
# =============================================================================


@pytest.fixture
def mock_ec2(mock_aws_credentials):
    """Fixture providing a mocked EC2 environment.

    Creates a basic VPC, subnet, and security group for testing.

    Yields:
        dict with 'client', 'resource', 'vpc_id', 'subnet_id', 'sg_id'
    """
    if not MOTO_AVAILABLE:
        pytest.skip('moto library not installed')

    with mock_aws():
        ec2_client = boto3.client('ec2', region_name=DEFAULT_REGION)
        ec2_resource = boto3.resource('ec2', region_name=DEFAULT_REGION)

        # Create VPC
        vpc_response = ec2_client.create_vpc(CidrBlock=DEFAULT_VPC_CIDR)
        vpc_id = vpc_response['Vpc']['VpcId']

        # Enable DNS hostnames for the VPC
        ec2_client.modify_vpc_attribute(VpcId=vpc_id,
                                        EnableDnsHostnames={'Value': True})

        # Create Internet Gateway and attach to VPC
        igw_response = ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        ec2_client.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

        # Create subnet
        subnet_response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=DEFAULT_SUBNET_CIDR,
            AvailabilityZone=DEFAULT_AZ,
        )
        subnet_id = subnet_response['Subnet']['SubnetId']

        # Enable auto-assign public IP
        ec2_client.modify_subnet_attribute(SubnetId=subnet_id,
                                           MapPublicIpOnLaunch={'Value': True})

        # Create route table with internet gateway route
        rt_response = ec2_client.create_route_table(VpcId=vpc_id)
        rt_id = rt_response['RouteTable']['RouteTableId']
        ec2_client.create_route(RouteTableId=rt_id,
                                DestinationCidrBlock='0.0.0.0/0',
                                GatewayId=igw_id)
        ec2_client.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)

        # Create security group
        sg_response = ec2_client.create_security_group(
            GroupName='test-sg',
            Description='Test security group',
            VpcId=vpc_id,
        )
        sg_id = sg_response['GroupId']

        # Allow SSH ingress
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 22,
                'ToPort': 22,
                'IpRanges': [{
                    'CidrIp': '0.0.0.0/0'
                }]
            }])

        yield {
            'client': ec2_client,
            'resource': ec2_resource,
            'vpc_id': vpc_id,
            'subnet_id': subnet_id,
            'sg_id': sg_id,
            'igw_id': igw_id,
            'region': DEFAULT_REGION,
            'az': DEFAULT_AZ,
        }


@pytest.fixture
def mock_ec2_with_instances(mock_ec2):
    """Fixture with pre-created EC2 instances.

    Creates a head node and worker nodes for Ray cluster testing.

    Yields:
        dict with EC2 resources plus 'head_instance_id', 'worker_instance_ids'
    """
    ec2_client = mock_ec2['client']
    subnet_id = mock_ec2['subnet_id']
    sg_id = mock_ec2['sg_id']

    # Create head instance
    head_response = ec2_client.run_instances(ImageId=DEFAULT_AMI_ID,
                                             InstanceType=DEFAULT_INSTANCE_TYPE,
                                             MinCount=1,
                                             MaxCount=1,
                                             SubnetId=subnet_id,
                                             SecurityGroupIds=[sg_id],
                                             TagSpecifications=[{
                                                 'ResourceType':
                                                     'instance',
                                                 'Tags': [
                                                     {
                                                         'Key': 'Name',
                                                         'Value': 'test-cluster-head'
                                                     },
                                                     {
                                                         'Key': 'ray-cluster-name',
                                                         'Value': 'test-cluster'
                                                     },
                                                     {
                                                         'Key': 'ray-node-type',
                                                         'Value': 'head'
                                                     },
                                                 ]
                                             }])
    head_instance_id = head_response['Instances'][0]['InstanceId']

    # Create worker instances
    worker_response = ec2_client.run_instances(
        ImageId=DEFAULT_AMI_ID,
        InstanceType=DEFAULT_INSTANCE_TYPE,
        MinCount=2,
        MaxCount=2,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        TagSpecifications=[{
            'ResourceType':
                'instance',
            'Tags': [
                {
                    'Key': 'Name',
                    'Value': 'test-cluster-worker'
                },
                {
                    'Key': 'ray-cluster-name',
                    'Value': 'test-cluster'
                },
                {
                    'Key': 'ray-node-type',
                    'Value': 'worker'
                },
            ]
        }])
    worker_instance_ids = [i['InstanceId'] for i in worker_response['Instances']]

    yield {
        **mock_ec2,
        'head_instance_id': head_instance_id,
        'worker_instance_ids': worker_instance_ids,
        'cluster_name': 'test-cluster',
    }


@pytest.fixture
def mock_ec2_multi_region(mock_aws_credentials):
    """Fixture with EC2 resources in multiple regions.

    Useful for testing multi-region failover scenarios.

    Yields:
        dict mapping region names to EC2 client/resource/vpc info
    """
    if not MOTO_AVAILABLE:
        pytest.skip('moto library not installed')

    regions = ['us-east-1', 'us-west-2', 'eu-west-1']
    region_resources = {}

    with mock_aws():
        for region in regions:
            ec2_client = boto3.client('ec2', region_name=region)
            ec2_resource = boto3.resource('ec2', region_name=region)

            # Create VPC
            vpc_response = ec2_client.create_vpc(CidrBlock=DEFAULT_VPC_CIDR)
            vpc_id = vpc_response['Vpc']['VpcId']

            # Create subnet
            az = f'{region}a'
            subnet_response = ec2_client.create_subnet(
                VpcId=vpc_id,
                CidrBlock=DEFAULT_SUBNET_CIDR,
                AvailabilityZone=az,
            )
            subnet_id = subnet_response['Subnet']['SubnetId']

            # Create security group
            sg_response = ec2_client.create_security_group(
                GroupName='test-sg',
                Description='Test security group',
                VpcId=vpc_id,
            )
            sg_id = sg_response['GroupId']

            region_resources[region] = {
                'client': ec2_client,
                'resource': ec2_resource,
                'vpc_id': vpc_id,
                'subnet_id': subnet_id,
                'sg_id': sg_id,
                'az': az,
            }

        yield region_resources


# =============================================================================
# IAM Fixtures
# =============================================================================


@pytest.fixture
def mock_iam(mock_aws_credentials):
    """Fixture providing a mocked IAM environment.

    Creates IAM role and instance profile for EC2 instances.

    Yields:
        dict with 'client', 'role_name', 'role_arn', 'instance_profile_name'
    """
    if not MOTO_AVAILABLE:
        pytest.skip('moto library not installed')

    with mock_aws():
        iam_client = boto3.client('iam', region_name=DEFAULT_REGION)

        # Create IAM role with EC2 trust policy
        assume_role_policy = {
            'Version':
                '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {
                    'Service': 'ec2.amazonaws.com'
                },
                'Action': 'sts:AssumeRole'
            }]
        }

        role_name = 'skypilot-test-role'
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=str(assume_role_policy).replace("'", '"'),
            Description='Test role for SkyPilot',
        )
        role_arn = role_response['Role']['Arn']

        # Create and attach inline policies (moto doesn't have AWS managed policies)
        ec2_policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Action': 'ec2:*',
                'Resource': '*'
            }]
        }
        s3_policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Action': 's3:*',
                'Resource': '*'
            }]
        }

        import json
        iam_client.put_role_policy(RoleName=role_name,
                                   PolicyName='EC2FullAccess',
                                   PolicyDocument=json.dumps(ec2_policy))
        iam_client.put_role_policy(RoleName=role_name,
                                   PolicyName='S3FullAccess',
                                   PolicyDocument=json.dumps(s3_policy))

        # Create instance profile
        instance_profile_name = 'skypilot-test-profile'
        iam_client.create_instance_profile(InstanceProfileName=instance_profile_name)

        # Add role to instance profile
        iam_client.add_role_to_instance_profile(
            InstanceProfileName=instance_profile_name, RoleName=role_name)

        yield {
            'client': iam_client,
            'role_name': role_name,
            'role_arn': role_arn,
            'instance_profile_name': instance_profile_name,
        }


# =============================================================================
# S3 Fixtures
# =============================================================================


@pytest.fixture
def mock_s3(mock_aws_credentials):
    """Fixture providing a mocked S3 environment.

    Creates test buckets for storage testing.

    Yields:
        dict with 'client', 'resource', 'bucket_name', 'bucket_arn'
    """
    if not MOTO_AVAILABLE:
        pytest.skip('moto library not installed')

    with mock_aws():
        s3_client = boto3.client('s3', region_name=DEFAULT_REGION)
        s3_resource = boto3.resource('s3', region_name=DEFAULT_REGION)

        # Create test bucket
        bucket_name = 'skypilot-test-bucket'
        s3_client.create_bucket(Bucket=bucket_name)

        # Upload some test objects
        s3_client.put_object(Bucket=bucket_name,
                             Key='test-data/file1.txt',
                             Body=b'test content 1')
        s3_client.put_object(Bucket=bucket_name,
                             Key='test-data/file2.txt',
                             Body=b'test content 2')
        s3_client.put_object(Bucket=bucket_name,
                             Key='models/model.bin',
                             Body=b'model binary data')

        yield {
            'client': s3_client,
            'resource': s3_resource,
            'bucket_name': bucket_name,
            'bucket_arn': f'arn:aws:s3:::{bucket_name}',
            'region': DEFAULT_REGION,
        }


@pytest.fixture
def mock_s3_with_data(mock_s3):
    """Fixture with more complex S3 data structure.

    Creates a realistic data structure for storage mount testing.

    Yields:
        dict with S3 resources plus 'data_prefix', 'file_list'
    """
    s3_client = mock_s3['client']
    bucket_name = mock_s3['bucket_name']

    # Create directory-like structure
    data_prefix = 'workdir/'
    files = [
        ('workdir/train.py', b'# Training script\nprint("training")'),
        ('workdir/config.yaml', b'epochs: 100\nbatch_size: 32'),
        ('workdir/data/dataset.csv', b'col1,col2\n1,2\n3,4'),
        ('workdir/models/checkpoint.pt', b'pytorch checkpoint data'),
        ('workdir/logs/train.log', b'epoch 1: loss=0.5'),
    ]

    file_list = []
    for key, content in files:
        s3_client.put_object(Bucket=bucket_name, Key=key, Body=content)
        file_list.append(key)

    yield {
        **mock_s3,
        'data_prefix': data_prefix,
        'file_list': file_list,
    }


# =============================================================================
# Combined AWS Environment Fixture
# =============================================================================


@pytest.fixture
def aws_test_environment(mock_aws_credentials):
    """Complete AWS test environment with EC2, IAM, and S3.

    This is the main fixture for comprehensive AWS testing.

    Yields:
        dict with all AWS resources (EC2, IAM, S3)
    """
    if not MOTO_AVAILABLE:
        pytest.skip('moto library not installed')

    with mock_aws():
        # EC2 setup
        ec2_client = boto3.client('ec2', region_name=DEFAULT_REGION)
        ec2_resource = boto3.resource('ec2', region_name=DEFAULT_REGION)

        # Create VPC
        vpc_response = ec2_client.create_vpc(CidrBlock=DEFAULT_VPC_CIDR)
        vpc_id = vpc_response['Vpc']['VpcId']

        # Create Internet Gateway
        igw_response = ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        ec2_client.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

        # Create subnet
        subnet_response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=DEFAULT_SUBNET_CIDR,
            AvailabilityZone=DEFAULT_AZ,
        )
        subnet_id = subnet_response['Subnet']['SubnetId']

        # Create security group
        sg_response = ec2_client.create_security_group(
            GroupName='skypilot-test-sg',
            Description='SkyPilot test security group',
            VpcId=vpc_id,
        )
        sg_id = sg_response['GroupId']

        # Allow SSH
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 22,
                'ToPort': 22,
                'IpRanges': [{
                    'CidrIp': '0.0.0.0/0'
                }]
            }])

        # IAM setup
        iam_client = boto3.client('iam', region_name=DEFAULT_REGION)

        assume_role_policy = {
            'Version':
                '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {
                    'Service': 'ec2.amazonaws.com'
                },
                'Action': 'sts:AssumeRole'
            }]
        }

        role_name = 'skypilot-v1'
        iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=str(assume_role_policy).replace("'", '"'),
        )

        instance_profile_name = 'skypilot-v1'
        iam_client.create_instance_profile(InstanceProfileName=instance_profile_name)
        iam_client.add_role_to_instance_profile(
            InstanceProfileName=instance_profile_name, RoleName=role_name)

        # S3 setup
        s3_client = boto3.client('s3', region_name=DEFAULT_REGION)
        s3_resource = boto3.resource('s3', region_name=DEFAULT_REGION)

        bucket_name = 'skypilot-test-bucket'
        s3_client.create_bucket(Bucket=bucket_name)

        yield {
            'region': DEFAULT_REGION,
            'az': DEFAULT_AZ,
            # EC2
            'ec2_client': ec2_client,
            'ec2_resource': ec2_resource,
            'vpc_id': vpc_id,
            'subnet_id': subnet_id,
            'sg_id': sg_id,
            'igw_id': igw_id,
            # IAM
            'iam_client': iam_client,
            'role_name': role_name,
            'instance_profile_name': instance_profile_name,
            # S3
            's3_client': s3_client,
            's3_resource': s3_resource,
            'bucket_name': bucket_name,
        }


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_instance(
    ec2_client,
    subnet_id: str,
    sg_id: str,
    instance_type: str = DEFAULT_INSTANCE_TYPE,
    tags: Optional[Dict[str, str]] = None,
    ami_id: str = DEFAULT_AMI_ID,
) -> str:
    """Helper to create a test EC2 instance.

    Args:
        ec2_client: boto3 EC2 client
        subnet_id: Subnet ID to launch in
        sg_id: Security group ID
        instance_type: EC2 instance type
        tags: Optional tags to apply
        ami_id: AMI ID to use

    Returns:
        Instance ID
    """
    tag_specs = []
    if tags:
        tag_specs = [{
            'ResourceType': 'instance',
            'Tags': [{
                'Key': k,
                'Value': v
            } for k, v in tags.items()]
        }]

    response = ec2_client.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        TagSpecifications=tag_specs if tag_specs else [],
    )
    return response['Instances'][0]['InstanceId']


def create_ray_cluster(
    ec2_client,
    subnet_id: str,
    sg_id: str,
    cluster_name: str,
    num_workers: int = 2,
    instance_type: str = DEFAULT_INSTANCE_TYPE,
) -> Tuple[str, List[str]]:
    """Helper to create a mock Ray cluster.

    Args:
        ec2_client: boto3 EC2 client
        subnet_id: Subnet ID
        sg_id: Security group ID
        cluster_name: Name for the cluster
        num_workers: Number of worker nodes
        instance_type: Instance type for all nodes

    Returns:
        Tuple of (head_instance_id, [worker_instance_ids])
    """
    # Create head node
    head_id = create_test_instance(ec2_client,
                                   subnet_id,
                                   sg_id,
                                   instance_type,
                                   tags={
                                       'Name': f'{cluster_name}-head',
                                       'ray-cluster-name': cluster_name,
                                       'ray-node-type': 'head',
                                       'skypilot-cluster-name': cluster_name,
                                   })

    # Create worker nodes
    worker_ids = []
    for i in range(num_workers):
        worker_id = create_test_instance(ec2_client,
                                         subnet_id,
                                         sg_id,
                                         instance_type,
                                         tags={
                                             'Name': f'{cluster_name}-worker-{i}',
                                             'ray-cluster-name': cluster_name,
                                             'ray-node-type': 'worker',
                                             'skypilot-cluster-name': cluster_name,
                                         })
        worker_ids.append(worker_id)

    return head_id, worker_ids


def wait_for_instance_state(
    ec2_client,
    instance_id: str,
    target_state: str,
    timeout: int = 60,
) -> bool:
    """Wait for an instance to reach a target state.

    Args:
        ec2_client: boto3 EC2 client
        instance_id: Instance ID to monitor
        target_state: Target state (running, stopped, terminated)
        timeout: Maximum wait time in seconds

    Returns:
        True if target state reached, False if timeout
    """
    import time
    start_time = time.time()

    while time.time() - start_time < timeout:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        if state == target_state:
            return True
        time.sleep(1)

    return False


def get_instances_by_cluster(
    ec2_client,
    cluster_name: str,
) -> List[Dict[str, Any]]:
    """Get all instances belonging to a cluster.

    Args:
        ec2_client: boto3 EC2 client
        cluster_name: Cluster name to filter by

    Returns:
        List of instance dicts
    """
    response = ec2_client.describe_instances(Filters=[{
        'Name': 'tag:ray-cluster-name',
        'Values': [cluster_name]
    }])

    instances = []
    for reservation in response.get('Reservations', []):
        instances.extend(reservation.get('Instances', []))
    return instances
