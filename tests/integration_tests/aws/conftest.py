"""Pytest configuration for AWS integration tests.

This module imports and exposes the AWS moto-based fixtures for use in tests.
"""

import pytest

# Import all fixtures from aws_fixtures module to make them available
# to tests in this directory and subdirectories.
from tests.integration_tests.aws.aws_fixtures import aws_test_environment
from tests.integration_tests.aws.aws_fixtures import create_ray_cluster
from tests.integration_tests.aws.aws_fixtures import create_test_instance
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

# Re-export fixtures so pytest can discover them
__all__ = [
    'mock_aws_credentials',
    'mock_ec2',
    'mock_ec2_with_instances',
    'mock_ec2_multi_region',
    'mock_iam',
    'mock_s3',
    'mock_s3_with_data',
    'aws_test_environment',
]
