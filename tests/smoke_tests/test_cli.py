# Smoke tests for SkyPilot for CLI output
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_cli.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_cli.py --terminate-on-failure

import tempfile
import textwrap
from unittest import mock

import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

from sky import skypilot_config
from sky.client import sdk
from sky.server import common as server_common
from sky.skylet import constants

_CHECK_AWS_BUCKET_DOESNT_EXIST = (
    'aws s3api head-bucket --bucket {bucket_name} 2>/dev/null && exit 1 || exit 0'
)


@pytest.mark.no_remote_server
def test_endpoint_output_basic(generic_cloud: str):
    """Test that sky api info endpoint output is correct."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test('endpoint_output_basic', [
        f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set to default local API server."',
    ],
                                  timeout=smoke_tests_utils.get_timeout(
                                      generic_cloud),
                                  teardown=f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
def test_endpoint_output_config(generic_cloud: str):
    """Test that sky api info endpoint output is correct when config is set."""

    endpoint = server_common.DEFAULT_SERVER_URL

    config = textwrap.dedent(f"""
    api_server:
        endpoint: {endpoint}
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test('endpoint_output_config', [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via {f.name}"',
        ],
                                      timeout=smoke_tests_utils.get_timeout(
                                          generic_cloud),
                                      teardown=f'sky down -y {name}',
                                      env={
                                          skypilot_config.ENV_VAR_GLOBAL_CONFIG:
                                              f.name
                                      })

        smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.no_remote_server
def test_endpoint_output_env(generic_cloud: str):
    """Test that sky api info output is correct when env endpoint is set."""
    name = smoke_tests_utils.get_cluster_name()
    expected_string = f"Endpoint set via the environment variable {constants.SKY_API_SERVER_URL_ENV_VAR}"
    test = smoke_tests_utils.Test('endpoint_output_env', [
        f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "Expecting to see: {expected_string}\n" && echo "$s" | grep "{expected_string}"',
    ],
                                  timeout=smoke_tests_utils.get_timeout(
                                      generic_cloud),
                                  teardown=f'sky down -y {name}',
                                  env={
                                      constants.SKY_API_SERVER_URL_ENV_VAR:
                                          server_common.DEFAULT_SERVER_URL
                                  })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
def test_sky_logout_wih_env_endpoint(generic_cloud: str):
    """Test that sky api logout with env endpoint fails."""
    test = smoke_tests_utils.Test(
        'sky_logout_wih_env_endpoint', [
            f's=$(SKYPILOT_DEBUG=0 sky api logout 2>&1 | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Cannot logout of API server when the endpoint is set via the environment variable. Run unset"',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        env={
            constants.SKY_API_SERVER_URL_ENV_VAR: "https://SUPERFAKE_ENDPOINT.unreachable"
        })
    smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.no_remote_server
def test_sky_login_wih_env_endpoint(generic_cloud: str):
    """Test that sky api login with env endpoint fails."""
    test = smoke_tests_utils.Test(
        'sky_login_wih_env_endpoint', [
            f's=$(SKYPILOT_DEBUG=0 sky api login -e https://SUPERFAKE_ENDPOINT.unreachable 2>&1 | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Cannot login to API server when the endpoint is set via the environment variable. Run unset"',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        env={
            constants.SKY_API_SERVER_URL_ENV_VAR: "https://SUPERFAKE_ENDPOINT.unreachable"
        })
    smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.aws
def test_storage_delete(generic_cloud: str):
    """Test that storage delete works."""
    name = smoke_tests_utils.get_cluster_name()
    bucket_name = f'{name}-bucket'
    bucket_job_yaml = textwrap.dedent(f"""
    name: {name}-job
    resources:
        cpus: 2
        infra: aws
    file_mounts:
        /output:
            name: {bucket_name}
            mode: MOUNT
            store: s3
    run: |
        echo "Data" > /output/data.txt 
    """)
    with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
        job_yaml.write(bucket_job_yaml.encode('utf-8'))
        job_yaml.flush()

        test = smoke_tests_utils.Test('storage_delete', [
            f'echo "bucket name: {bucket_name}"',
            smoke_tests_utils.launch_cluster_for_cloud_cmd(
                'aws', name, skip_remote_server_check=True),
            f's=$(SKYPILOT_DEBUG=0 sky jobs launch -y {job_yaml.name}) && echo "$s" | grep "Job finished (status: SUCCEEDED)."',
            f's=$(SKYPILOT_DEBUG=0 sky storage delete -y {bucket_name}) && echo "$s" && echo "$s" | grep "Deleted S3 bucket {bucket_name}"',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=_CHECK_AWS_BUCKET_DOESNT_EXIST.format(
                    bucket_name=bucket_name)),
        ],
                                      teardown=smoke_tests_utils.
                                      down_cluster_for_cloud_cmd(
                                          name, skip_remote_server_check=True),
                                      timeout=smoke_tests_utils.get_timeout(
                                          generic_cloud))
        smoke_tests_utils.run_one_test(test, check_sky_status=False)
