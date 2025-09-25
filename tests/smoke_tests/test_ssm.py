# Smoke tests for SkyPilot for SSM functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_ssm.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_ssm.py --terminate-on-failure

import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants


@pytest.mark.aws
def test_ssm_public():
    """Test that ssm works with public IP addresses."""
    name = smoke_tests_utils.get_cluster_name()
    vpc = "DO_NOT_DELETE_lloyd-airgapped-plus-gateway"
    vpc_config = (f'--config aws.vpc_name={vpc} '
                  f'--config aws.use_ssm=true '
                  f'--config aws.security_group_name=lloyd-airgap-gw-sg')

    test = smoke_tests_utils.Test(
        'ssm_public',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} {vpc_config} tests/test_yamls/minimal.yaml | tee /dev/stderr) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        ],
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
    vpc_config = (f'--config aws.vpc_name={vpc} '
                  f'--config aws.use_ssm=true '
                  f'--config aws.security_group_name=lloyd-airgap-gw-sg '
                  f'--config aws.use_internal_ips=true')

    test = smoke_tests_utils.Test(
        'ssm_private',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} {vpc_config} tests/test_yamls/minimal.yaml | tee /dev/stderr) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('aws'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_ssm_private_custom_ami():
    """Test that ssm works with private IP addresses and a custom AMI.
    """

    ami_id = "ami-04b95a57b104f3c92"

    config = textwrap.dedent(f"""
    aws:
        ssh_user: my-own-user-name
        use_internal_ips: true
        vpc_name: DO_NOT_DELETE_lloyd-airgapped-plus-gateway
        use_ssm: true
        post_provision_runcmd:
            - systemctl enable amazon-ssm-agent
            - systemctl start amazon-ssm-agent
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'ssm_private_custom_ami',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id {ami_id} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            ],
            teardown=f'sky down -y {name}',
            timeout=smoke_tests_utils.get_timeout('aws'),
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f.name,
                constants.SKY_API_SERVER_URL_ENV_VAR:
                    sky.server.common.get_server_url()
            },
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_ssm_private_proxy_command_and_use_ssm():
    """Test that ssm errors if ssh_proxy_command and use_ssm are set.
    """
    name = smoke_tests_utils.get_cluster_name()

    warning_message = 'use_ssm is set to true, ' \
        'but ssh_proxy_command is already set to'

    VALIDATE_SSM_OUTPUT = ('echo "$s" && echo "==Validating launching==" && '
                           f'echo "$s" | grep "{warning_message}"')

    get_instance_id_command = 'aws ec2 describe-instances ' + \
        '--region us-west-1 --filters Name=private-ip-address,Values=%h ' + \
                    '--query \"Reservations[].Instances[].InstanceId\" ' + \
                    '--output text'
    ssm_proxy_command = 'aws ssm start-session --target ' + \
        '\"$(' + get_instance_id_command + ')\" ' + \
        '--region us-west-1 ' + \
        '--document-name AWS-StartSSHSession ' + \
        '--parameters portNumber=%p'

    vpc = "DO_NOT_DELETE_lloyd-airgapped-plus-gateway"

    config = textwrap.dedent(f"""
    aws:
        use_internal_ips: true
        vpc_name: {vpc}
        use_ssm: true
        ssh_proxy_command: {ssm_proxy_command!r}
        security_group_name: lloyd-airgap-gw-sg
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        test = smoke_tests_utils.Test(
            'ssm_private_proxy_command_and_use_ssm',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml | tee /dev/stderr) && {VALIDATE_SSM_OUTPUT}',
            ],
            teardown=f'sky down -y {name}',
            timeout=smoke_tests_utils.get_timeout('aws'),
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f.name,
                constants.SKY_API_SERVER_URL_ENV_VAR:
                    sky.server.common.get_server_url()
            },
        )
        smoke_tests_utils.run_one_test(test)
