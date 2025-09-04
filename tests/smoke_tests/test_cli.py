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
