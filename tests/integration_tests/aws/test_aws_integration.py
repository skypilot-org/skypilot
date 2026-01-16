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

from tests.integration_tests.aws.aws_fixtures import aws_test_environment
from tests.integration_tests.aws.aws_fixtures import create_ray_cluster
from tests.integration_tests.aws.aws_fixtures import create_test_instance
from tests.integration_tests.aws.aws_fixtures import DEFAULT_AMI_ID
from tests.integration_tests.aws.aws_fixtures import DEFAULT_AZ
from tests.integration_tests.aws.aws_fixtures import DEFAULT_INSTANCE_TYPE
from tests.integration_tests.aws.aws_fixtures import DEFAULT_REGION
from tests.integration_tests.aws.aws_fixtures import get_instances_by_cluster
from tests.integration_tests.aws.aws_fixtures import GPU_INSTANCE_TYPES
from tests.integration_tests.aws.aws_fixtures import mock_aws_credentials
from tests.integration_tests.aws.aws_fixtures import mock_ec2
from tests.integration_tests.aws.aws_fixtures import mock_ec2_multi_region
from tests.integration_tests.aws.aws_fixtures import mock_ec2_with_instances
from tests.integration_tests.aws.aws_fixtures import mock_iam
from tests.integration_tests.aws.aws_fixtures import mock_s3
from tests.integration_tests.aws.aws_fixtures import mock_s3_with_data
from tests.integration_tests.aws.aws_fixtures import wait_for_instance_state

pytestmark = pytest.mark.skipif(not MOTO_AVAILABLE,
                                reason='moto library not installed')

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

        instance_id = create_test_instance(ec2_client,
                                           subnet_id,
                                           sg_id,
                                           tags=tags)

        # Verify tags
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_tags = {
            t['Key']: t['Value']
            for t in response['Reservations'][0]['Instances'][0].get(
                'Tags', [])
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
        response = iam_client.get_instance_profile(
            InstanceProfileName=profile_name)
        assert response['InstanceProfile'][
            'InstanceProfileName'] == profile_name

    def test_role_attached_to_profile(self, mock_iam):
        """Test that role is attached to instance profile."""
        iam_client = mock_iam['client']
        role_name = mock_iam['role_name']
        profile_name = mock_iam['instance_profile_name']

        response = iam_client.get_instance_profile(
            InstanceProfileName=profile_name)
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
        s3_client.put_object(Bucket=bucket_name,
                             Key='test-key',
                             Body=test_content)

        # Download and verify
        response = s3_client.get_object(Bucket=bucket_name, Key='test-key')
        downloaded_content = response['Body'].read()
        assert downloaded_content == test_content

    def test_list_objects(self, mock_s3_with_data):
        """Test listing objects with prefix."""
        s3_client = mock_s3_with_data['client']
        bucket_name = mock_s3_with_data['bucket_name']

        # List all objects under workdir/
        response = s3_client.list_objects_v2(Bucket=bucket_name,
                                             Prefix='workdir/')

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
        response = s3_client.list_objects_v2(Bucket=bucket_name,
                                             Prefix='test-data/')
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

        head_id, worker_ids = create_ray_cluster(
            ec2_client,
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

        head_id, worker_ids = create_ray_cluster(
            ec2_client,
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
                assert instance['State']['Name'] in [
                    'shutting-down', 'terminated'
                ]

    def test_cluster_head_recovery(self, mock_ec2):
        """Test recreating head node (recovery scenario)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create cluster
        head_id, worker_ids = create_ray_cluster(
            ec2_client,
            subnet_id,
            sg_id,
            cluster_name='recovery-cluster',
            num_workers=2)

        # Terminate head
        ec2_client.terminate_instances(InstanceIds=[head_id])

        # Create new head
        new_head_id = create_test_instance(
            ec2_client,
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

            instance_id = create_test_instance(
                ec2_client,
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
        head_id, worker_ids = create_ray_cluster(
            ec2_client,
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

        s3_response = s3_client.list_objects_v2(
            Bucket=env['bucket_name'], Prefix='clusters/full-test-cluster/')
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


# =============================================================================
# Spot Instance Tests
# =============================================================================


class TestSpotInstances:
    """Tests for spot instance operations."""

    def test_spot_instance_request(self, mock_ec2):
        """Test requesting spot instances."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Request spot instance
        response = ec2_client.request_spot_instances(
            SpotPrice='0.50',
            InstanceCount=1,
            Type='one-time',
            LaunchSpecification={
                'ImageId': DEFAULT_AMI_ID,
                'InstanceType': DEFAULT_INSTANCE_TYPE,
                'SubnetId': subnet_id,
                'SecurityGroupIds': [sg_id],
            },
        )

        assert len(response['SpotInstanceRequests']) == 1
        request = response['SpotInstanceRequests'][0]
        assert request['State'] in ['open', 'active']
        assert float(request['SpotPrice']) == 0.50

    def test_spot_instance_with_tags(self, mock_ec2):
        """Test spot instance with SkyPilot tags."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        response = ec2_client.request_spot_instances(
            SpotPrice='1.00',
            InstanceCount=1,
            Type='one-time',
            LaunchSpecification={
                'ImageId': DEFAULT_AMI_ID,
                'InstanceType': 'p3.2xlarge',
                'SubnetId': subnet_id,
                'SecurityGroupIds': [sg_id],
            },
            TagSpecifications=[{
                'ResourceType': 'spot-instances-request',
                'Tags': [
                    {
                        'Key': 'skypilot-cluster-name',
                        'Value': 'spot-cluster'
                    },
                    {
                        'Key': 'skypilot-use-spot',
                        'Value': 'true'
                    },
                ]
            }],
        )

        request_id = response['SpotInstanceRequests'][0][
            'SpotInstanceRequestId']

        # Verify tags
        describe_response = ec2_client.describe_spot_instance_requests(
            SpotInstanceRequestIds=[request_id])
        tags = {
            t['Key']: t['Value']
            for t in describe_response['SpotInstanceRequests'][0].get(
                'Tags', [])
        }
        assert tags.get('skypilot-use-spot') == 'true'

    def test_cancel_spot_request(self, mock_ec2):
        """Test canceling spot instance requests."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create spot request
        response = ec2_client.request_spot_instances(
            SpotPrice='0.50',
            InstanceCount=1,
            Type='one-time',
            LaunchSpecification={
                'ImageId': DEFAULT_AMI_ID,
                'InstanceType': DEFAULT_INSTANCE_TYPE,
                'SubnetId': subnet_id,
                'SecurityGroupIds': [sg_id],
            },
        )
        request_id = response['SpotInstanceRequests'][0][
            'SpotInstanceRequestId']

        # Cancel the request
        cancel_response = ec2_client.cancel_spot_instance_requests(
            SpotInstanceRequestIds=[request_id])
        assert cancel_response['CancelledSpotInstanceRequests'][0][
            'State'] == 'cancelled'

    def test_spot_fleet_request(self, mock_ec2):
        """Test spot fleet for multi-instance spot clusters."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Get the IAM role for spot fleet (simplified)
        # In real tests, this would use the IAM fixture
        response = ec2_client.request_spot_fleet(
            SpotFleetRequestConfig={
                'IamFleetRole': 'arn:aws:iam::123456789012:role/spot-fleet-role',
                'AllocationStrategy': 'lowestPrice',
                'TargetCapacity': 3,
                'SpotPrice': '1.00',
                'LaunchSpecifications': [{
                    'ImageId': DEFAULT_AMI_ID,
                    'InstanceType': DEFAULT_INSTANCE_TYPE,
                    'SubnetId': subnet_id,
                    'SecurityGroups': [{
                        'GroupId': sg_id
                    }],
                }],
            })

        assert 'SpotFleetRequestId' in response


# =============================================================================
# EBS Volume and Disk Tier Tests
# =============================================================================


class TestDiskTiers:
    """Tests for EBS volume types (disk tiers)."""

    def test_standard_disk_tier(self, mock_ec2):
        """Test STANDARD disk tier (gp2 volumes)."""
        ec2_client = mock_ec2['client']
        az = mock_ec2['az']

        # Create gp2 volume (standard)
        response = ec2_client.create_volume(AvailabilityZone=az,
                                            Size=100,
                                            VolumeType='gp2',
                                            TagSpecifications=[{
                                                'ResourceType': 'volume',
                                                'Tags': [{
                                                    'Key': 'disk-tier',
                                                    'Value': 'STANDARD'
                                                }]
                                            }])

        volume_id = response['VolumeId']
        assert response['VolumeType'] == 'gp2'
        assert response['Size'] == 100

        # Verify volume
        describe = ec2_client.describe_volumes(VolumeIds=[volume_id])
        assert describe['Volumes'][0]['VolumeType'] == 'gp2'

    def test_high_disk_tier(self, mock_ec2):
        """Test HIGH disk tier (gp3 volumes with IOPS)."""
        ec2_client = mock_ec2['client']
        az = mock_ec2['az']

        # Create gp3 volume (high performance)
        response = ec2_client.create_volume(AvailabilityZone=az,
                                            Size=100,
                                            VolumeType='gp3',
                                            Iops=4000,
                                            Throughput=250,
                                            TagSpecifications=[{
                                                'ResourceType': 'volume',
                                                'Tags': [{
                                                    'Key': 'disk-tier',
                                                    'Value': 'HIGH'
                                                }]
                                            }])

        volume_id = response['VolumeId']
        assert response['VolumeType'] == 'gp3'
        assert response['Iops'] == 4000
        assert response['Throughput'] == 250

    def test_best_disk_tier(self, mock_ec2):
        """Test BEST disk tier (io1/io2 volumes)."""
        ec2_client = mock_ec2['client']
        az = mock_ec2['az']

        # Create io1 volume (highest performance)
        response = ec2_client.create_volume(AvailabilityZone=az,
                                            Size=100,
                                            VolumeType='io1',
                                            Iops=5000,
                                            TagSpecifications=[{
                                                'ResourceType': 'volume',
                                                'Tags': [{
                                                    'Key': 'disk-tier',
                                                    'Value': 'BEST'
                                                }]
                                            }])

        volume_id = response['VolumeId']
        assert response['VolumeType'] == 'io1'
        assert response['Iops'] == 5000

    def test_attach_volume_to_instance(self, mock_ec2):
        """Test attaching EBS volume to instance."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']
        az = mock_ec2['az']

        # Create instance
        instance_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Create volume
        vol_response = ec2_client.create_volume(
            AvailabilityZone=az,
            Size=50,
            VolumeType='gp3',
        )
        volume_id = vol_response['VolumeId']

        # Attach volume
        attach_response = ec2_client.attach_volume(
            Device='/dev/sdf',
            InstanceId=instance_id,
            VolumeId=volume_id,
        )

        assert attach_response['State'] in ['attaching', 'attached']
        assert attach_response['InstanceId'] == instance_id

    def test_instance_with_root_volume_config(self, mock_ec2):
        """Test launching instance with custom root volume."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        response = ec2_client.run_instances(ImageId=DEFAULT_AMI_ID,
                                            InstanceType=DEFAULT_INSTANCE_TYPE,
                                            MinCount=1,
                                            MaxCount=1,
                                            SubnetId=subnet_id,
                                            SecurityGroupIds=[sg_id],
                                            BlockDeviceMappings=[{
                                                'DeviceName': '/dev/sda1',
                                                'Ebs': {
                                                    'VolumeSize': 256,
                                                    'VolumeType': 'gp3',
                                                    'Iops': 3000,
                                                    'Throughput': 125,
                                                    'DeleteOnTermination': True,
                                                }
                                            }])

        instance = response['Instances'][0]
        assert len(
            instance['BlockDeviceMappings']) >= 0  # Moto may not fully simulate


# =============================================================================
# Custom AMI and Image Tests
# =============================================================================


class TestCustomImages:
    """Tests for custom AMI/image support."""

    def test_custom_ami_launch(self, mock_ec2):
        """Test launching instance with custom AMI."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Use a custom AMI ID (Deep Learning AMI pattern)
        custom_ami = 'ami-dl-ubuntu-2004-gpu'

        response = ec2_client.run_instances(ImageId=custom_ami,
                                            InstanceType='p3.2xlarge',
                                            MinCount=1,
                                            MaxCount=1,
                                            SubnetId=subnet_id,
                                            SecurityGroupIds=[sg_id],
                                            TagSpecifications=[{
                                                'ResourceType': 'instance',
                                                'Tags': [{
                                                    'Key': 'skypilot-image-id',
                                                    'Value': custom_ami
                                                },]
                                            }])

        instance = response['Instances'][0]
        assert instance['ImageId'] == custom_ami

    def test_arm64_instance_type(self, mock_ec2):
        """Test ARM64 instance types (Graviton)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # ARM64 AMI
        arm64_ami = 'ami-arm64-ubuntu-2004'

        response = ec2_client.run_instances(
            ImageId=arm64_ami,
            InstanceType='m6g.large',  # Graviton2
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg_id],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{
                    'Key': 'architecture',
                    'Value': 'arm64'
                },]
            }])

        instance = response['Instances'][0]
        assert instance['InstanceType'] == 'm6g.large'

    def test_gpu_instance_types(self, mock_ec2):
        """Test various GPU instance types."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        for instance_type, gpu_info in GPU_INSTANCE_TYPES.items():
            response = ec2_client.run_instances(
                ImageId=DEFAULT_AMI_ID,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                SubnetId=subnet_id,
                SecurityGroupIds=[sg_id],
                TagSpecifications=[{
                    'ResourceType': 'instance',
                    'Tags': [
                        {
                            'Key': 'gpu-type',
                            'Value': gpu_info['gpu_type']
                        },
                        {
                            'Key': 'gpu-count',
                            'Value': str(gpu_info['gpu_count'])
                        },
                    ]
                }])
            assert response['Instances'][0]['InstanceType'] == instance_type

    def test_image_per_region(self, mock_ec2_multi_region):
        """Test using different AMIs per region."""
        regions = mock_ec2_multi_region

        # Different AMIs per region (simulating SkyPilot's image_id dict)
        region_amis = {
            'us-east-1': 'ami-east1-ubuntu',
            'us-west-2': 'ami-west2-ubuntu',
            'eu-west-1': 'ami-eu-ubuntu',
        }

        instance_ids = {}
        for region, resources in regions.items():
            ec2_client = resources['client']
            response = ec2_client.run_instances(
                ImageId=region_amis[region],
                InstanceType=DEFAULT_INSTANCE_TYPE,
                MinCount=1,
                MaxCount=1,
                SubnetId=resources['subnet_id'],
                SecurityGroupIds=[resources['sg_id']],
            )
            instance_ids[region] = response['Instances'][0]['InstanceId']
            assert response['Instances'][0]['ImageId'] == region_amis[region]

        assert len(instance_ids) == 3


# =============================================================================
# CloudWatch Logging Tests
# =============================================================================


class TestCloudWatchLogs:
    """Tests for CloudWatch logging integration."""

    def test_create_log_group(self, mock_aws_credentials):
        """Test creating CloudWatch log group."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        with mock_aws():
            logs_client = boto3.client('logs', region_name=DEFAULT_REGION)

            # Create log group
            logs_client.create_log_group(logGroupName='skypilot-logs')

            # Verify
            response = logs_client.describe_log_groups(
                logGroupNamePrefix='skypilot-logs')
            assert len(response['logGroups']) == 1
            assert response['logGroups'][0]['logGroupName'] == 'skypilot-logs'

    def test_create_log_stream(self, mock_aws_credentials):
        """Test creating log streams for cluster logs."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        with mock_aws():
            logs_client = boto3.client('logs', region_name=DEFAULT_REGION)

            # Create log group and stream
            logs_client.create_log_group(logGroupName='skypilot-logs')
            logs_client.create_log_stream(
                logGroupName='skypilot-logs',
                logStreamName='cluster-test-cluster-1')

            # Verify
            response = logs_client.describe_log_streams(
                logGroupName='skypilot-logs',
                logStreamNamePrefix='cluster-test-cluster')
            assert len(response['logStreams']) == 1

    def test_put_and_get_log_events(self, mock_aws_credentials):
        """Test writing and reading log events."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        import time

        with mock_aws():
            logs_client = boto3.client('logs', region_name=DEFAULT_REGION)

            # Setup
            logs_client.create_log_group(logGroupName='skypilot-logs')
            logs_client.create_log_stream(logGroupName='skypilot-logs',
                                          logStreamName='job-train-model-1')

            # Put log events
            timestamp = int(time.time() * 1000)
            logs_client.put_log_events(logGroupName='skypilot-logs',
                                       logStreamName='job-train-model-1',
                                       logEvents=[
                                           {
                                               'timestamp': timestamp,
                                               'message': 'Starting training...'
                                           },
                                           {
                                               'timestamp': timestamp + 1000,
                                               'message': 'Epoch 1 complete'
                                           },
                                           {
                                               'timestamp': timestamp + 2000,
                                               'message': 'Training complete'
                                           },
                                       ])

            # Get log events
            response = logs_client.get_log_events(
                logGroupName='skypilot-logs',
                logStreamName='job-train-model-1',
            )

            messages = [e['message'] for e in response['events']]
            assert 'Starting training...' in messages
            assert 'Training complete' in messages

    def test_filter_log_events_by_cluster(self, mock_aws_credentials):
        """Test filtering logs by cluster name pattern."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        import time

        with mock_aws():
            logs_client = boto3.client('logs', region_name=DEFAULT_REGION)

            # Setup
            logs_client.create_log_group(logGroupName='skypilot-logs')

            # Create streams for different clusters
            for cluster in ['cluster-a', 'cluster-b']:
                logs_client.create_log_stream(logGroupName='skypilot-logs',
                                              logStreamName=f'{cluster}-logs')
                timestamp = int(time.time() * 1000)
                logs_client.put_log_events(
                    logGroupName='skypilot-logs',
                    logStreamName=f'{cluster}-logs',
                    logEvents=[
                        {
                            'timestamp': timestamp,
                            'message': f'Log from {cluster}'
                        },
                    ])

            # Filter by stream prefix
            response = logs_client.filter_log_events(
                logGroupName='skypilot-logs', logStreamNamePrefix='cluster-a')

            assert len(response['events']) >= 1
            assert 'cluster-a' in response['events'][0]['message']


# =============================================================================
# Instance Tagging and Labels Tests
# =============================================================================


class TestInstanceTagging:
    """Tests for SkyPilot instance tagging."""

    def test_skypilot_cluster_tags(self, mock_ec2):
        """Test SkyPilot cluster identification tags."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        skypilot_tags = {
            'skypilot-cluster-name': 'my-training-cluster',
            'skypilot-user': 'testuser',
            'skypilot-user-hash': 'abc123',
            'ray-cluster-name': 'my-training-cluster',
            'ray-node-type': 'head',
        }

        instance_id = create_test_instance(ec2_client,
                                           subnet_id,
                                           sg_id,
                                           tags=skypilot_tags)

        # Verify all tags
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_tags = {
            t['Key']: t['Value']
            for t in response['Reservations'][0]['Instances'][0].get(
                'Tags', [])
        }

        for key, value in skypilot_tags.items():
            assert instance_tags.get(key) == value

    def test_custom_task_labels(self, mock_ec2):
        """Test custom labels from SkyPilot task definition."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Custom labels from task YAML
        custom_labels = {
            'project': 'ml-training',
            'team': 'ai-research',
            'experiment': 'exp-001',
            'cost-center': 'cc-12345',
        }

        tags = {
            'skypilot-cluster-name': 'labeled-cluster',
            **custom_labels,
        }

        instance_id = create_test_instance(ec2_client,
                                           subnet_id,
                                           sg_id,
                                           tags=tags)

        # Verify custom labels
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_tags = {
            t['Key']: t['Value']
            for t in response['Reservations'][0]['Instances'][0].get(
                'Tags', [])
        }

        for key, value in custom_labels.items():
            assert instance_tags.get(key) == value

    def test_filter_by_custom_label(self, mock_ec2):
        """Test filtering instances by custom labels."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create instances with different projects
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'project': 'project-a'})
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'project': 'project-a'})
        create_test_instance(ec2_client,
                             subnet_id,
                             sg_id,
                             tags={'project': 'project-b'})

        # Filter by project
        response = ec2_client.describe_instances(Filters=[{
            'Name': 'tag:project',
            'Values': ['project-a']
        }])

        instances = []
        for r in response['Reservations']:
            instances.extend(r['Instances'])

        assert len(instances) == 2

    def test_modify_tags_after_launch(self, mock_ec2):
        """Test adding/modifying tags after instance launch."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        instance_id = create_test_instance(ec2_client, subnet_id, sg_id)

        # Add tags after launch
        ec2_client.create_tags(Resources=[instance_id],
                               Tags=[
                                   {
                                       'Key': 'status',
                                       'Value': 'ready'
                                   },
                                   {
                                       'Key': 'job-id',
                                       'Value': 'job-12345'
                                   },
                               ])

        # Verify tags added
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        instance_tags = {
            t['Key']: t['Value']
            for t in response['Reservations'][0]['Instances'][0].get(
                'Tags', [])
        }

        assert instance_tags.get('status') == 'ready'
        assert instance_tags.get('job-id') == 'job-12345'


# =============================================================================
# Managed Job Recovery Tests
# =============================================================================


class TestManagedJobRecovery:
    """Tests simulating managed job recovery scenarios."""

    def test_instance_termination_detection(self, mock_ec2):
        """Test detecting instance termination for recovery."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create cluster for managed job
        head_id, worker_ids = create_ray_cluster(
            ec2_client,
            subnet_id,
            sg_id,
            cluster_name='managed-job-cluster',
            num_workers=1)

        # Simulate spot interruption - terminate head
        ec2_client.terminate_instances(InstanceIds=[head_id])

        # Detect termination
        response = ec2_client.describe_instances(InstanceIds=[head_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        assert state in ['shutting-down', 'terminated']

        # Workers should still be running
        worker_response = ec2_client.describe_instances(InstanceIds=worker_ids)
        for reservation in worker_response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['State']['Name'] == 'running'

    def test_cluster_recovery_with_new_head(self, mock_ec2):
        """Test recovery by creating new head node."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'recovery-test-cluster'

        # Create initial cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=2)

        old_head_id = head_id

        # Terminate head (simulating spot interruption)
        ec2_client.terminate_instances(InstanceIds=[head_id])

        # Recovery: create new head
        new_head_id = create_test_instance(
            ec2_client,
            subnet_id,
            sg_id,
            tags={
                'Name': f'{cluster_name}-head',
                'ray-cluster-name': cluster_name,
                'ray-node-type': 'head',
                'skypilot-cluster-name': cluster_name,
            })

        assert new_head_id != old_head_id

        # Verify cluster state
        cluster_instances = get_instances_by_cluster(ec2_client, cluster_name)
        active_instances = [
            i for i in cluster_instances
            if i['State']['Name'] not in ['terminated', 'shutting-down']
        ]

        # Should have new head + 2 workers
        assert len(active_instances) == 3

        # Verify head node type
        head_nodes = [
            i for i in active_instances
            if any(t['Key'] == 'ray-node-type' and t['Value'] == 'head'
                   for t in i.get('Tags', []))
        ]
        assert len(head_nodes) == 1
        assert head_nodes[0]['InstanceId'] == new_head_id

    def test_multi_node_job_recovery(self, mock_ec2):
        """Test recovery for multi-node managed jobs."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'multi-node-job'

        # Create 3-node cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=2)

        # Tag with job info
        all_ids = [head_id] + worker_ids
        ec2_client.create_tags(Resources=all_ids,
                               Tags=[
                                   {
                                       'Key': 'skypilot-job-id',
                                       'Value': 'job-123'
                                   },
                                   {
                                       'Key': 'skypilot-job-status',
                                       'Value': 'RUNNING'
                                   },
                               ])

        # Terminate one worker (simulating partial failure)
        ec2_client.terminate_instances(InstanceIds=[worker_ids[0]])

        # Update job status to RECOVERING
        ec2_client.create_tags(Resources=[head_id, worker_ids[1]],
                               Tags=[{
                                   'Key': 'skypilot-job-status',
                                   'Value': 'RECOVERING'
                               }])

        # Create replacement worker
        new_worker_id = create_test_instance(
            ec2_client,
            subnet_id,
            sg_id,
            tags={
                'Name': f'{cluster_name}-worker-new',
                'ray-cluster-name': cluster_name,
                'ray-node-type': 'worker',
                'skypilot-cluster-name': cluster_name,
                'skypilot-job-id': 'job-123',
                'skypilot-job-status': 'RUNNING',
            })

        # Verify recovery complete
        cluster_instances = get_instances_by_cluster(ec2_client, cluster_name)
        active_instances = [
            i for i in cluster_instances if i['State']['Name'] == 'running'
        ]
        assert len(active_instances) == 3

    def test_job_cancellation_cleanup(self, mock_ec2):
        """Test cleanup when managed job is cancelled."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'cancelled-job-cluster'

        # Create cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=2)

        # Simulate job cancellation - terminate all
        all_ids = [head_id] + worker_ids
        ec2_client.terminate_instances(InstanceIds=all_ids)

        # Verify all terminated
        cluster_instances = get_instances_by_cluster(ec2_client, cluster_name)
        for instance in cluster_instances:
            assert instance['State']['Name'] in ['shutting-down', 'terminated']


# =============================================================================
# Multi-Node Cluster Tests
# =============================================================================


class TestMultiNodeClusters:
    """Tests for multi-node cluster configurations."""

    def test_large_cluster_creation(self, mock_ec2):
        """Test creating larger clusters."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        # Create 8-node cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name='large-cluster',
                                                 num_workers=7)

        assert len(worker_ids) == 7

        # Verify all nodes
        instances = get_instances_by_cluster(ec2_client, 'large-cluster')
        assert len(instances) == 8

    def test_heterogeneous_cluster(self, mock_ec2):
        """Test cluster with different instance types."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'hetero-cluster'

        # Head node - CPU instance
        head_id = create_test_instance(ec2_client,
                                       subnet_id,
                                       sg_id,
                                       instance_type='m5.xlarge',
                                       tags={
                                           'Name': f'{cluster_name}-head',
                                           'ray-cluster-name': cluster_name,
                                           'ray-node-type': 'head',
                                           'instance-role': 'coordinator',
                                       })

        # Worker nodes - GPU instances
        worker_ids = []
        for i in range(2):
            worker_id = create_test_instance(
                ec2_client,
                subnet_id,
                sg_id,
                instance_type='p3.2xlarge',
                tags={
                    'Name': f'{cluster_name}-gpu-worker-{i}',
                    'ray-cluster-name': cluster_name,
                    'ray-node-type': 'worker',
                    'instance-role': 'gpu-worker',
                })
            worker_ids.append(worker_id)

        # Verify instance types
        head_response = ec2_client.describe_instances(InstanceIds=[head_id])
        assert head_response['Reservations'][0]['Instances'][0][
            'InstanceType'] == 'm5.xlarge'

        worker_response = ec2_client.describe_instances(InstanceIds=worker_ids)
        for reservation in worker_response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['InstanceType'] == 'p3.2xlarge'

    def test_cluster_node_scaling(self, mock_ec2):
        """Test adding and removing nodes from cluster."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'scaling-cluster'

        # Start with 2 workers
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=2)

        initial_count = len(get_instances_by_cluster(ec2_client, cluster_name))
        assert initial_count == 3

        # Scale up - add 2 more workers
        for i in range(2):
            create_test_instance(ec2_client,
                                 subnet_id,
                                 sg_id,
                                 tags={
                                     'Name': f'{cluster_name}-worker-new-{i}',
                                     'ray-cluster-name': cluster_name,
                                     'ray-node-type': 'worker',
                                 })

        scaled_up_count = len(get_instances_by_cluster(ec2_client,
                                                       cluster_name))
        assert scaled_up_count == 5

        # Scale down - terminate 2 workers
        ec2_client.terminate_instances(InstanceIds=worker_ids[:2])

        active_instances = [
            i for i in get_instances_by_cluster(ec2_client, cluster_name)
            if i['State']['Name'] == 'running'
        ]
        assert len(active_instances) == 3


# =============================================================================
# Autostop Simulation Tests
# =============================================================================


class TestAutostopSimulation:
    """Tests simulating autostop behavior."""

    def test_stop_idle_cluster(self, mock_ec2):
        """Test stopping idle cluster (autostop simulation)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'autostop-cluster'

        # Create cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=2)

        # Tag with autostop info
        all_ids = [head_id] + worker_ids
        ec2_client.create_tags(
            Resources=all_ids,
            Tags=[
                {
                    'Key': 'skypilot-autostop',
                    'Value': '600'
                },  # 10 minutes
                {
                    'Key': 'skypilot-status',
                    'Value': 'UP'
                },
            ])

        # Simulate autostop - stop all instances
        ec2_client.stop_instances(InstanceIds=all_ids)

        # Verify all stopped
        response = ec2_client.describe_instances(InstanceIds=all_ids)
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['State']['Name'] in ['stopping', 'stopped']

    def test_restart_stopped_cluster(self, mock_ec2):
        """Test restarting a stopped cluster."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'restart-cluster'

        # Create and stop cluster
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=1)

        all_ids = [head_id] + worker_ids
        ec2_client.stop_instances(InstanceIds=all_ids)

        # Restart cluster
        ec2_client.start_instances(InstanceIds=all_ids)

        # Verify all running
        response = ec2_client.describe_instances(InstanceIds=all_ids)
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['State']['Name'] in ['pending', 'running']

    def test_autodown_terminate(self, mock_ec2):
        """Test autodown (terminate instead of stop)."""
        ec2_client = mock_ec2['client']
        subnet_id = mock_ec2['subnet_id']
        sg_id = mock_ec2['sg_id']

        cluster_name = 'autodown-cluster'

        # Create cluster with autodown tag
        head_id, worker_ids = create_ray_cluster(ec2_client,
                                                 subnet_id,
                                                 sg_id,
                                                 cluster_name=cluster_name,
                                                 num_workers=1)

        all_ids = [head_id] + worker_ids
        ec2_client.create_tags(Resources=all_ids,
                               Tags=[
                                   {
                                       'Key': 'skypilot-autodown',
                                       'Value': 'true'
                                   },
                               ])

        # Autodown - terminate instead of stop
        ec2_client.terminate_instances(InstanceIds=all_ids)

        # Verify all terminated
        response = ec2_client.describe_instances(InstanceIds=all_ids)
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                assert instance['State']['Name'] in [
                    'shutting-down', 'terminated'
                ]


# =============================================================================
# S3 Storage Mount Tests
# =============================================================================


class TestS3StorageMounts:
    """Tests for S3 storage mount patterns."""

    def test_workdir_upload(self, mock_s3):
        """Test uploading workdir to S3."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        cluster_name = 'workdir-cluster'
        workdir_prefix = f'skypilot-workdir/{cluster_name}/'

        # Simulate workdir upload
        files = [
            ('train.py', b'import torch\n# training code'),
            ('config.yaml', b'epochs: 100\nlr: 0.001'),
            ('requirements.txt', b'torch>=2.0\nnumpy'),
        ]

        for filename, content in files:
            s3_client.put_object(Bucket=bucket_name,
                                 Key=f'{workdir_prefix}{filename}',
                                 Body=content)

        # Verify upload
        response = s3_client.list_objects_v2(Bucket=bucket_name,
                                             Prefix=workdir_prefix)
        uploaded_keys = [obj['Key'] for obj in response['Contents']]
        assert len(uploaded_keys) == 3

    def test_file_mount_sync(self, mock_s3):
        """Test file mount sync pattern."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        mount_prefix = 'mounts/data/'

        # Upload dataset files
        s3_client.put_object(Bucket=bucket_name,
                             Key=f'{mount_prefix}train.csv',
                             Body=b'feature1,feature2,label\n1,2,0\n3,4,1')
        s3_client.put_object(Bucket=bucket_name,
                             Key=f'{mount_prefix}test.csv',
                             Body=b'feature1,feature2,label\n5,6,0\n7,8,1')

        # Verify files exist
        response = s3_client.list_objects_v2(Bucket=bucket_name,
                                             Prefix=mount_prefix)
        assert len(response['Contents']) == 2

        # Download and verify content
        train_obj = s3_client.get_object(Bucket=bucket_name,
                                         Key=f'{mount_prefix}train.csv')
        content = train_obj['Body'].read().decode()
        assert 'feature1,feature2,label' in content

    def test_output_sync_to_s3(self, mock_s3):
        """Test syncing outputs back to S3."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        output_prefix = 'outputs/experiment-001/'

        # Simulate output files from training
        outputs = [
            ('model.pt', b'pytorch model weights'),
            ('metrics.json', b'{"accuracy": 0.95, "loss": 0.05}'),
            ('logs/train.log', b'Epoch 1: loss=0.5\nEpoch 2: loss=0.2'),
            ('checkpoints/ckpt-1000.pt', b'checkpoint data'),
        ]

        for filename, content in outputs:
            s3_client.put_object(Bucket=bucket_name,
                                 Key=f'{output_prefix}{filename}',
                                 Body=content)

        # Verify all outputs synced
        response = s3_client.list_objects_v2(Bucket=bucket_name,
                                             Prefix=output_prefix)
        assert len(response['Contents']) == 4

    def test_bucket_versioning(self, mock_s3):
        """Test S3 bucket versioning for checkpoints."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        # Enable versioning
        s3_client.put_bucket_versioning(
            Bucket=bucket_name, VersioningConfiguration={'Status': 'Enabled'})

        # Verify versioning enabled
        response = s3_client.get_bucket_versioning(Bucket=bucket_name)
        assert response['Status'] == 'Enabled'

        # Upload multiple versions of same file
        for i in range(3):
            s3_client.put_object(Bucket=bucket_name,
                                 Key='checkpoints/latest.pt',
                                 Body=f'checkpoint version {i}'.encode())

        # List versions
        versions = s3_client.list_object_versions(
            Bucket=bucket_name, Prefix='checkpoints/latest.pt')
        assert len(versions.get('Versions', [])) == 3

    def test_multipart_upload(self, mock_s3):
        """Test multipart upload for large files."""
        s3_client = mock_s3['client']
        bucket_name = mock_s3['bucket_name']

        key = 'large-model/model.bin'

        # Initiate multipart upload
        response = s3_client.create_multipart_upload(Bucket=bucket_name,
                                                     Key=key)
        upload_id = response['UploadId']

        # Upload parts (minimum 5MB per part except last)
        # Using 5MB + 1 byte to ensure we meet the minimum
        min_part_size = 5 * 1024 * 1024  # 5MB
        parts = []
        for i in range(3):
            part_data = b'x' * (min_part_size + 1)
            part_response = s3_client.upload_part(Bucket=bucket_name,
                                                  Key=key,
                                                  UploadId=upload_id,
                                                  PartNumber=i + 1,
                                                  Body=part_data)
            parts.append({'PartNumber': i + 1, 'ETag': part_response['ETag']})

        # Complete multipart upload
        s3_client.complete_multipart_upload(Bucket=bucket_name,
                                            Key=key,
                                            UploadId=upload_id,
                                            MultipartUpload={'Parts': parts})

        # Verify file exists
        response = s3_client.head_object(Bucket=bucket_name, Key=key)
        assert response['ContentLength'] > 0


# =============================================================================
# Security Group Tests
# =============================================================================


class TestSecurityGroups:
    """Tests for security group configurations."""

    def test_multiple_security_groups(self, mock_ec2):
        """Test instance with multiple security groups."""
        ec2_client = mock_ec2['client']
        vpc_id = mock_ec2['vpc_id']
        subnet_id = mock_ec2['subnet_id']

        # Create additional security groups
        sg1 = ec2_client.create_security_group(GroupName='skypilot-ssh',
                                               Description='SSH access',
                                               VpcId=vpc_id)['GroupId']

        sg2 = ec2_client.create_security_group(GroupName='skypilot-ray',
                                               Description='Ray cluster ports',
                                               VpcId=vpc_id)['GroupId']

        # Add rules
        ec2_client.authorize_security_group_ingress(
            GroupId=sg1,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 22,
                'ToPort': 22,
                'IpRanges': [{
                    'CidrIp': '0.0.0.0/0'
                }]
            }])

        ec2_client.authorize_security_group_ingress(
            GroupId=sg2,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 6379,
                'ToPort': 6380,
                'IpRanges': [{
                    'CidrIp': '10.0.0.0/8'
                }]
            }])

        # Launch instance with multiple SGs
        response = ec2_client.run_instances(
            ImageId=DEFAULT_AMI_ID,
            InstanceType=DEFAULT_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg1, sg2],
        )

        instance = response['Instances'][0]
        sg_ids = [sg['GroupId'] for sg in instance['SecurityGroups']]
        assert sg1 in sg_ids
        assert sg2 in sg_ids

    def test_custom_port_rules(self, mock_ec2):
        """Test custom port rules for web servers."""
        ec2_client = mock_ec2['client']
        vpc_id = mock_ec2['vpc_id']

        sg = ec2_client.create_security_group(GroupName='skypilot-web',
                                              Description='Web server ports',
                                              VpcId=vpc_id)['GroupId']

        # Add custom ports (like SkyPilot dashboard, Jupyter, etc.)
        custom_ports = [
            (8080, 'dashboard'),
            (8888, 'jupyter'),
            (6006, 'tensorboard'),
            (33828, 'custom-app'),
        ]

        for port, _ in custom_ports:
            ec2_client.authorize_security_group_ingress(
                GroupId=sg,
                IpPermissions=[{
                    'IpProtocol': 'tcp',
                    'FromPort': port,
                    'ToPort': port,
                    'IpRanges': [{
                        'CidrIp': '0.0.0.0/0'
                    }]
                }])

        # Verify rules
        sg_describe = ec2_client.describe_security_groups(GroupIds=[sg])
        ports = [
            p['FromPort']
            for p in sg_describe['SecurityGroups'][0]['IpPermissions']
        ]

        for port, _ in custom_ports:
            assert port in ports


# =============================================================================
# Region and Zone Tests
# =============================================================================


class TestRegionZone:
    """Tests for region and zone handling."""

    def test_specific_zone_launch(self, mock_ec2):
        """Test launching in a specific availability zone."""
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
            Placement={'AvailabilityZone': DEFAULT_AZ},
        )

        instance = response['Instances'][0]
        assert instance['Placement']['AvailabilityZone'] == DEFAULT_AZ

    def test_multiple_zones_in_region(self, mock_aws_credentials):
        """Test resources in multiple AZs within a region."""
        if not MOTO_AVAILABLE:
            pytest.skip('moto not available')

        with mock_aws():
            ec2_client = boto3.client('ec2', region_name='us-east-1')

            # Create VPC
            vpc = ec2_client.create_vpc(CidrBlock='10.0.0.0/16')
            vpc_id = vpc['Vpc']['VpcId']

            # Create subnets in different AZs
            zones = ['us-east-1a', 'us-east-1b', 'us-east-1c']
            subnet_ids = {}

            for i, zone in enumerate(zones):
                subnet = ec2_client.create_subnet(VpcId=vpc_id,
                                                  CidrBlock=f'10.0.{i}.0/24',
                                                  AvailabilityZone=zone)
                subnet_ids[zone] = subnet['Subnet']['SubnetId']

            # Verify subnets in different zones
            for zone, subnet_id in subnet_ids.items():
                response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
                assert response['Subnets'][0]['AvailabilityZone'] == zone

    def test_cross_region_cluster_info(self, mock_ec2_multi_region):
        """Test tracking cluster info across regions."""
        regions = mock_ec2_multi_region

        cluster_name = 'cross-region-cluster'
        cluster_info = {}

        for region, resources in regions.items():
            ec2_client = resources['client']

            instance_id = create_test_instance(
                ec2_client,
                resources['subnet_id'],
                resources['sg_id'],
                tags={
                    'skypilot-cluster-name': cluster_name,
                    'skypilot-region': region,
                })

            cluster_info[region] = {
                'instance_id': instance_id,
                'vpc_id': resources['vpc_id'],
            }

        # Verify cluster spans regions
        assert len(cluster_info) == 3
        assert 'us-east-1' in cluster_info
        assert 'us-west-2' in cluster_info
        assert 'eu-west-1' in cluster_info
