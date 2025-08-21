# Smoke tests for SkyPilot for SSM functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_ssm.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_ssm.py --terminate-on-failure

from ast import Name
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants
from sky.skylet import events
from sky.utils import common_utils


@pytest.mark.aws
def test_ssm_public():
    """Test that ssm works with public IP addresses."""
    name = smoke_tests_utils.get_cluster_name()
    vpc = "DO_NOT_DELETE_lloyd-airgapped-plus-gateway"
    vpc_config = f'--config aws.vpc_name={vpc} ' \
        f'--config aws.use_ssm=true ' \
        f'--config aws.security_group_name=lloyd-airgap-gw-sg'

    test = smoke_tests_utils.Test(
        'ssm_public',
        [
            f'export s=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} {vpc_config} tests/test_yamls/minimal.yaml)',
        ] + list(smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT_EXPORTED),
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('aws'),
    )
    smoke_tests_utils.run_one_test(test)

@pytest.mark.aws
def test_ssm_private():
    """Test that ssm works with private IP addresses.

    Because we turn on use_internal_ips SkyPilot will automatically
    use the private subnet to create the cluster.
    """
    name = smoke_tests_utils.get_cluster_name()
    vpc = "DO_NOT_DELETE_lloyd-airgapped-plus-gateway"
    vpc_config = f'--config aws.vpc_name={vpc} ' \
        f'--config aws.use_ssm=true ' \
        f'--config aws.security_group_name=lloyd-airgap-gw-sg ' \
        f'--config aws.use_internal_ips=true'

    test = smoke_tests_utils.Test(
        'ssm_private',
        [
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} {vpc_config} tests/test_yamls/minimal.yaml',
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('aws'),
    )
    outputs = smoke_tests_utils.run_one_test(test)
    for output in outputs:
        print(output)
    smoke_tests_utils.run_one_test(test)