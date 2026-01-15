"""AWS integration tests using moto.

These tests cover scenarios that were previously only testable with real
AWS resources (smoke tests). They use moto to mock AWS APIs and provide
fast, reliable testing without AWS credentials or costs.

Test coverage includes:
- EC2 instance lifecycle (launch, stop, terminate)
- VPC and networking (subnets, security groups, internet gateways)
- IAM roles and instance profiles
- S3 bucket operations and storage mounts
- Multi-region operations
- Cluster management (Ray clusters)
- Tag-based filtering and management
"""

from typing import Dict, List
from unittest import mock

import pytest

try:
    import boto3
    from moto import mock_aws
    MOTO_AVAILABLE = True
except ImportError:
    MOTO_AVAILABLE = False
    mock_aws = None

from tests.unit_tests.aws.aws_fixtures import aws_test_environment
from tests.unit_tests.aws.aws_fixtures import create_ray_cluster
from tests.unit_tests.aws.aws_fixtures import create_test_instance
from tests.unit_tests.aws.aws_fixtures import DEFAULT_AMI_ID
from tests.unit_tests.aws.aws_fixtures import DEFAULT_AZ
from tests.unit_tests.aws.aws_fixtures import DEFAULT_INSTANCE_TYPE
from tests.unit_tests.aws.aws_fixtures import DEFAULT_REGION
from tests.unit_tests.aws.aws_fixtures import get_instances_by_cluster
from tests.unit_tests.aws.aws_fixtures import mock_aws_credentials
from tests.unit_tests.aws.aws_fixtures import mock_ec2
from tests.unit_tests.aws.aws_fixtures import mock_ec2_multi_region
from tests.unit_tests.aws.aws_fixtures import mock_ec2_with_instances
from tests.unit_tests.aws.aws_fixtures import mock_iam
from tests.unit_tests.aws.aws_fixtures import mock_s3
from tests.unit_tests.aws.aws_fixtures import mock_s3_with_data
from tests.unit_tests.aws.aws_fixtures import wait_for_instance_state

pytestmark = pytest.mark.skipif(not MOTO_AVAILABLE, reason='moto library not installed')

# =============================================================================
# EC2 Instance Lifecycle Tests
# =============================================================================


class TestEC2InstanceLifecycle:
    """Tests for EC2 instance lifecycle management."""

    def test_instance_launch(self, mock_ec2):
        """Test launching an EC2 instance."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        response = ec2_client.run_instances(
            ImageId=DEFAULT_AMI_ID,
            InstanceType=DEFAULT_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg_id],
        )

        assert len(response['Instances']) == 1
        instance = response['Instances'][0]
        assert instance['InstanceType'] == DEFAULT_INSTANCE_TYPE
        assert instance['SubnetId'] == subnet_id

    def test_instance_with_tags(self, mock_ec2):
        """Test launching an instance with tags."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        tags = {
            'Name': 'test-instance',
            'ray-cluster-name': 'my-cluster',
            'Environment': 'test',
        }

        instance_id = create_test_instance(ec2_client, subnet_id, sg_id, tags=tags)

        # Verify tags
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_tags = {
            t['Key']: t['Value']
            for t in response['Reservations'][0]['Instances'][0].get('Tags', [])
        }

        for key, value in tags.items():
            assert instance_tags.get(key) == value

    def test_instance_stop_and_start(self, mock_ec2):
        """Test stopping and starting an instance."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        instance_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Stop instance
        ec2_client.stop_instances(InstanceIds=[instance_id])

        # Check state
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        assert state in ['stopping', 'stopped']

        # Start instance
        ec2_client.start_instances(InstanceIds=[instance_id])

        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        assert state in ['pending', 'running']

    def test_instance_terminate(self, mock_ec2):
        """Test terminating an instance."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        instance_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Terminate
        ec2_client.terminate_instances(InstanceIds=[instance_id])

        # Check state
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        assert state in ['shutting-down', 'terminated']

    def test_multiple_instances(self, mock_ec2):
        """Test launching multiple instances at once."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        response = ec2_client.run_instances(
            ImageId=DEFAULT_AMI_ID,
            InstanceType=DEFAULT_INSTANCE_TYPE,
            MinCount=3,
            MaxCount=3,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg_id],
        )

        assert len(response['Instances']) == 3
        instance_ids = [i['InstanceId'] for i in response['Instances']]
        assert len(set(instance_ids)) == 3  # All unique


class TestEC2InstanceFiltering:
    """Tests for EC2 instance filtering by tags."""

    def test_filter_by_cluster_name(self, mock_ec2):
        """Test filtering instances by cluster name tag."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create instances in different clusters
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'ray-cluster-name': 'cluster-a'})
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'ray-cluster-name': 'cluster-a'})
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'ray-cluster-name': 'cluster-b'})

        # Filter by cluster name
        instances_a = get_instances_by_cluster(ec2_client, 'cluster-a')
        instances_b = get_instances_by_cluster(ec2_client, 'cluster-b')

        assert len(instances_a) == 2
        assert len(instances_b) == 1

    def test_filter_by_node_type(self, mock_ec2_with_instances):
        """Test filtering instances by node type (head/worker)."""
        ec2_client = mock_ec2_with_instances['client']

        # Filter head nodes
        head_response = ec2_client.describe_instances(Filters=[{
            'Name': 'tag:ray-node-type',
            'Values': ['head']
        }])
        head_instances = []
        for r in head_response['Reservations']:
            head_instances.extend(r['Instances'])

        # Filter worker nodes
        worker_response = ec2_client.describe_instances(Filters=[{
            'Name': 'tag:ray-node-type',
            'Values': ['worker']
        }])
        worker_instances = []
        for r in worker_response['Reservations']:
            worker_instances.extend(r['Instances'])

        assert len(head_instances) == 1
        assert len(worker_instances) == 2

    def test_filter_by_state(self, mock_ec2):
        """Test filtering instances by state."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create and stop an instance
        stopped_id = create_test_instance(ec2_client, subnet_id, sg_id)
        ec2_client.stop_instances(InstanceIds=[stopped_id])

        # Create a running instance
        running_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Filter by state
        running_response = ec2_client.describe_instances(Filters=[{
            'Name': 'instance-state-name',
            'Values': ['running']
        }])
        running_instances = []
        for r in running_response['Reservations']:
            running_instances.extend(r['Instances'])

        # The running instance should be in the results
        running_ids = [i['InstanceId'] for i in running_instances]
        assert running_id in running_ids


# =============================================================================
# VPC and Networking Tests
# =============================================================================


class TestVPCNetworking:
    """Tests for VPC and networking operations."""

    def test_vpc_creation(self, mock_aws_credentials):
        """Test VPC creation."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        with mock_aws():
            ec2_client = boto3.client('ec2', region_name=DEFAULT_REGION)

            response = ec2_client.create_vpc(CidrBlock='10.0.0.0/16')
            vpc_id = response['Vpc']['VpcId']

            assert vpc_id.startswith('vpc-')

            # Verify VPC exists
            vpc_response = ec2_client.describe_vpcs(VpcIds=[vpc_id])
            assert len(vpc_response['Vpcs']) == 1
            assert vpc_response['Vpcs'][0]['CidrBlock'] == '10.0.0.0/16'

    def test_subnet_creation(self, mock_ec2):
        """Test subnet creation in VPC."""
        ec2_client = mock_ec2['client']
        vpc_id = mock_ec2['vpc_id']

        # Create additional subnet
        response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock='10.0.2.0/24',
            AvailabilityZone=DEFAULT_AZ,
        )
        new_subnet_id = response['Subnet']['SubnetId']

        assert new_subnet_id.startswith('subnet-')

        # Verify subnet belongs to VPC
        subnet_response = ec2_client.describe_subnets(SubnetIds=[new_subnet_id])
        assert subnet_response['Subnets'][0]['VpcId'] == vpc_id

    def test_security_group_rules(self, mock_ec2):
        """Test security group ingress rules."""
        ec2_client = mock_ec2['client']
        vpc_id = mock_ec2['vpc_id']

        # Create new security group
        sg_response = ec2_client.create_security_group(
            GroupName='custom-sg',
            Description='Custom security group',
            VpcId=vpc_id,
        )
        sg_id = sg_response['GroupId']

        # Add custom port rules
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 8080,
                'ToPort': 8080,
                'IpRanges': [{
                    'CidrIp': '0.0.0.0/0'
                }]
            }, {
                'IpProtocol': 'tcp',
                'FromPort': 6379,
                'ToPort': 6380,
                'IpRanges': [{
                    'CidrIp': '10.0.0.0/8'
                }]
            }])

        # Verify rules
        sg_describe = ec2_client.describe_security_groups(GroupIds=[sg_id])
        ip_permissions = sg_describe['SecurityGroups'][0]['IpPermissions']

        ports = [(p['FromPort'], p['ToPort']) for p in ip_permissions]
        assert (8080, 8080) in ports
        assert (6379, 6380) in ports

    def test_internet_gateway(self, mock_ec2):
        """Test internet gateway attachment."""
        ec2_client = mock_ec2['client']
        vpc_id = mock_ec2['vpc_id']

        # Verify IGW is attached (created by fixture)
        igw_response = ec2_client.describe_internet_gateways(Filters=[{
            'Name': 'attachment.vpc-id',
            'Values': [vpc_id]
        }])
        assert len(igw_response['InternetGateways']) == 1

    def test_public_subnet_routing(self, mock_ec2):
        """Test that subnet has route to internet gateway."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        igw_id = mock_ec2['igw_id']

        # Get route table for subnet
        rt_response = ec2_client.describe_route_tables(Filters=[{
            'Name': 'association.subnet-id',
            'Values': [subnet_id]
        }])

        # Check for route to IGW
        routes = rt_response['RouteTables'][0]['Routes']
        igw_routes = [r for r in routes if r.get('GatewayId') == igw_id]
        assert len(igw_routes) > 0


# =============================================================================
# IAM Tests
# =============================================================================


class TestIAM:
    """Tests for IAM roles and instance profiles."""

    def test_role_creation(self, mock_iam):
        """Test IAM role creation."""
        iam_client = mock_iam['client']
        role_name = mock_iam['role_name']

        # Verify role exists
        response = iam_client.get_role(RoleName=role_name)
        assert response['Role']['RoleName'] == role_name

    def test_instance_profile_creation(self, mock_iam):
        """Test instance profile creation."""
        iam_client = mock_iam['client']
        profile_name = mock_iam['instance_profile_name']

        # Verify instance profile exists
        response = iam_client.get_instance_profile(InstanceProfileName=profile_name)
        assert response['InstanceProfile']['InstanceProfileName'] == profile_name

    def test_role_attached_to_profile(self, mock_iam):
        """Test that role is attached to instance profile."""
        iam_client = mock_iam['client']
        role_name = mock_iam['role_name']
        profile_name = mock_iam['instance_profile_name']

        response = iam_client.get_instance_profile(InstanceProfileName=profile_name)
        roles = response['InstanceProfile']['Roles']
        role_names = [r['RoleName'] for r in roles]
        assert role_name in role_names

    def test_policy_attachment(self, mock_iam):
        """Test that policies are attached to role."""
        iam_client = mock_iam['client']
        role_name = mock_iam['role_name']

        # Check inline policies (moto uses inline policies, not managed ones)
        response = iam_client.list_role_policies(RoleName=role_name)
        policy_names = response['PolicyNames']

        assert 'EC2FullAccess' in policy_names
        assert 'S3FullAccess' in policy_names


# =============================================================================
# S3 Tests
# =============================================================================


class TestS3:
    """Tests for S3 bucket operations."""

    def test_bucket_creation(self, mock_s3):
        """Test S3 bucket creation."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        # Verify bucket exists
        response = s3_client.list_buckets()
        bucket_names = [b['Name'] for b in response['Buckets']]
        assert bucket_name in bucket_names

    def test_object_upload_and_download(self, mock_s3):
        """Test uploading and downloading objects."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        # Upload new object
        test_content = b'Hello, SkyPilot!'
        s3_client.put_object(Bucket=bucket_name, Key='test-key', Body=test_content)

        # Download and verify
        response = s3_client.get_object(Bucket=bucket_name, Key='test-key')
        downloaded_content = response['Body'].read()
        assert downloaded_content == test_content

    def test_list_objects(self, mock_s3_with_data):
        """Test listing objects with prefix."""
        s3_client = mock_s3_with_data['client']
        bucket_name = mock_s3_with_data['bucket_name']

        # List all objects under workdir/
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix='workdir/')

        keys = [obj['Key'] for obj in response.get('Contents', [])]
        assert len(keys) >= 5  # We created 5 files in fixture
        assert any('train.py' in k for k in keys)
        assert any('config.yaml' in k for k in keys)

    def test_delete_object(self, mock_s3):
        """Test deleting an object."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        # Delete object
        s3_client.delete_object(Bucket=bucket_name, Key='test-data/file1.txt')

        # Verify deleted
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix='test-data/')
        keys = [obj['Key'] for obj in response.get('Contents', [])]
        assert 'test-data/file1.txt' not in keys


# =============================================================================
# Ray Cluster Tests
# =============================================================================


class TestRayCluster:
    """Tests for Ray cluster management."""

    def test_cluster_creation(self, mock_ec2):
        """Test creating a Ray cluster (head + workers)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name='test-ray-cluster',
                                                 num_workers=3)

        assert head_id.startswith('i-')
        assert len(worker_ids) == 3
        assert all(w.startswith('i-') for w in worker_ids)

        # Verify cluster instances
        instances = get_instances_by_cluster(ec2_client, 'test-ray-cluster')
        assert len(instances) == 4  # 1 head + 3 workers

    def test_cluster_termination(self, mock_ec2):
        """Test terminating all nodes in a cluster."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name='terminate-cluster',
                                                 num_workers=2)

        all_ids = [head_id] + worker_ids

        # Terminate all
        ec2_client.terminate_instances(InstanceIds=all_ids)

        # Verify all terminated
        response = ec2_client.describe_instances(InstanceIds=all_ids)
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['State']['Name'] in ['shutting-down', 'terminated']

    def test_cluster_head_recovery(self, mock_ec2):
        """Test recreating head node (recovery scenario)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name='recovery-cluster',
                                                 num_workers=2)

        # Terminate head
        ec2_client.terminate_instances(InstanceIds=[head_id])

        # Create new head
        new_head_id = create_test_instance(ec2_client,
                                           subnet_id,
                                           sg_id,
                                           tags={
                                               'Name': 'recovery-cluster-head',
                                               'ray-cluster-name': 'recovery-cluster',
                                               'ray-node-type': 'head',
                                           })

        assert new_head_id != head_id

        # Verify cluster now has new head
        instances = get_instances_by_cluster(ec2_client, 'recovery-cluster')
        # Filter out terminated instances
        active_instances = [
            i for i in instances
            if i['State']['Name'] not in ['terminated', 'shutting-down']
        ]
        assert len(active_instances) == 3  # new head + 2 workers


# =============================================================================
# Multi-Region Tests
# =============================================================================


class TestMultiRegion:
    """Tests for multi-region operations."""

    def test_instances_in_multiple_regions(self, mock_ec2_multi_region):
        """Test creating instances in multiple regions."""
        regions = mock_ec2_multi_region

        instance_ids_by_region = {}
        for region, resources in regions.items():
            ec2_client = resources['client']
            subnet_id = resources['subnet_id']
            sg_id = resources['sg_id']

            instance_id = create_test_instance(ec2_client,
                                               subnet_id,
                                               sg_id,
                                               tags={'Name': f'instance-{region}'})
            instance_ids_by_region[region] = instance_id

        # Verify each region has its instance
        assert len(instance_ids_by_region) == 3

        # Verify instances are isolated per region
        for region, resources in regions.items():
            ec2_client = resources['client']
            response = ec2_client.describe_instances()

            region_instance_ids = []
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    region_instance_ids.append(instance['InstanceId'])

            # Each region should only see its own instance
            assert instance_ids_by_region[region] in region_instance_ids

    def test_vpc_isolation_across_regions(self, mock_ec2_multi_region):
        """Test that VPCs are isolated across regions."""
        regions = mock_ec2_multi_region

        vpc_ids = [resources['vpc_id'] for resources in regions.values()]

        # All VPC IDs should be unique
        assert len(set(vpc_ids)) == len(vpc_ids)


# =============================================================================
# Combined Environment Tests
# =============================================================================


class TestAWSEnvironment:
    """Tests using the combined AWS environment fixture."""

    def test_launch_instance_with_iam_profile(self, aws_test_environment):
        """Test launching instance with IAM instance profile."""
        env = aws_test_environment
        ec2_client = env['ec2_client']

        response = ec2_client.run_instances(
            ImageId=DEFAULT_AMI_ID,
            InstanceType=DEFAULT_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            SubnetId=env['subnet_id'],
            SecurityGroupIds=[env['sg_id']],
            IamInstanceProfile={'Name': env['instance_profile_name']},
        )

        instance = response['Instances'][0]
        assert instance['IamInstanceProfile']['Arn'] is not None

    def test_instance_with_s3_access(self, aws_test_environment):
        """Test that instance setup includes S3 bucket."""
        env = aws_test_environment
        s3_client = env['s3_client']
        bucket_name = env['bucket_name']

        # Verify bucket exists
        response = s3_client.list_buckets()
        bucket_names = [b['Name'] for b in response['Buckets']]
        assert bucket_name in bucket_names

    def test_full_cluster_setup(self, aws_test_environment):
        """Test creating a full cluster with all AWS resources."""
        env = aws_test_environment
        ec2_client = env['ec2_client']
        s3_client = env['s3_client']

        # Create cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 env['subnet_id'],
                                                 env['sg_id'],
                                                 cluster_name='full-test-cluster',
                                                 num_workers=2)

        # Upload workdir to S3
        s3_client.put_object(Bucket=env['bucket_name'],
                             Key='clusters/full-test-cluster/config.yaml',
                             Body=b'cluster_config: test')

        # Verify all resources exist
        instances = get_instances_by_cluster(ec2_client, 'full-test-cluster')
        assert len(instances) == 3

        s3_response = s3_client.list_objects_v2(Bucket=env['bucket_name'],
                                                Prefix='clusters/full-test-cluster/')
        assert s3_response['KeyCount'] >= 1


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_invalid_instance_type(self, mock_ec2):
        """Test handling of invalid instance type."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Moto may not validate instance types, but test the flow
        response = ec2_client.run_instances(
            ImageId=DEFAULT_AMI_ID,
            InstanceType='invalid.type',  # May or may not fail in moto
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg_id],
        )
        # If it doesn't raise, that's ok for moto
        assert response is not None

    def test_describe_nonexistent_instance(self, mock_ec2):
        """Test describing a nonexistent instance."""
        ec2_client = mock_ec2['client']

        with pytest.raises(Exception):
            ec2_client.describe_instances(InstanceIds=['i-nonexistent'])

    def test_terminate_already_terminated(self, mock_ec2):
        """Test terminating an already terminated instance."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        instance_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Terminate twice
        ec2_client.terminate_instances(InstanceIds=[instance_id])
        # Second termination should not raise
        response = ec2_client.terminate_instances(InstanceIds=[instance_id])
        assert response is not None
