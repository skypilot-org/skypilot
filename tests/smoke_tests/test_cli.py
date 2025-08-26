# Smoke tests for SkyPilot for CLI output
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_cli.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_cli.py --terminate-on-failure

import pytest
import tempfile
import textwrap
from unittest import mock

from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

from sky import skypilot_config
from sky.server import common as server_common
from sky.client import sdk
from sky.skylet import constants

# TODO(lloyd): Add tests for zh case.

def test_endpoint_output_basic(generic_cloud: str):
    """Test that sky api info output is correct."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'endpoint_output_basic',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via the command line."',
        ],
        timeout=smoke_tests_utils.get_timeout('aws'),
        teardown=f'sky down -y {name}'
    )
    smoke_tests_utils.run_one_test(test)

def test_endpoint_output_config(generic_cloud: str):
    """Test that sky api info output is correct."""
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip('We don\'t want to run this test on remote server')

    endpoint = server_common.DEFAULT_SERVER_URL

    config = textwrap.dedent(f"""
    api_server:
        endpoint: {endpoint}
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'endpoint_output_basic',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
                f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via {f.name}."',
            ],
            timeout=smoke_tests_utils.get_timeout('aws'),
            teardown=f'sky down -y {name}',
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f.name
            }
        )

        with pytest.MonkeyPatch().context() as m:
            # Mock the environment config to return a non-existing endpoint.
            # The sky api login command should not read from environment config
            # when an explicit endpoint is provided as an argument.
            smoke_tests_utils.run_one_test(test, check_sky_status=False)

def test_endpoint_output_config(generic_cloud: str):
    """Test that sky api info output is correct."""
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip('We don\'t want to run this test on remote server')

    endpoint = server_common.DEFAULT_SERVER_URL

    config = textwrap.dedent(f"""
    api_server:
        endpoint: {endpoint}
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'endpoint_output_basic',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
                f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via {f.name}."',
            ],
            timeout=smoke_tests_utils.get_timeout('aws'),
            teardown=f'sky down -y {name}',
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f.name
            }
        )

        with pytest.MonkeyPatch().context() as m:
            # Mock the environment config to return a non-existing endpoint.
            # The sky api login command should not read from environment config
            # when an explicit endpoint is provided as an argument.
            smoke_tests_utils.run_one_test(test, check_sky_status=False)

# def test_endpoint_output_config():
#     if not smoke_tests_utils.is_remote_server_test():
#         pytest.skip('This test is only for remote server')

#     endpoint = docker_utils.get_api_server_endpoint_inside_docker()
#     config_path = skypilot_config._GLOBAL_CONFIG_PATH
#     backup_path = f'{config_path}.backup_for_test_remote_server_api_login'

#     test = smoke_tests_utils.Test(
#         'endpoint_output_config',
#         [
#             # Backup existing config file if it exists
#             f'if [ -f {config_path} ]; then cp {config_path} {backup_path}; fi',
#             # Run sky api login
#             f'sky api login -e {endpoint}',
#             # Echo the config file content to see what was written
#             f'echo "Config file content after sky api login:" && cat {config_path}',
#             # Verify the config file is updated with the endpoint
#             f'grep -q "endpoint: {endpoint}" {config_path}',
#             # Verify the api_server section exists
#             f'grep -q "api_server:" {config_path}',
#             # Verify the endpoint output is correct
#             f's=$(sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via {config_path}."',
#         ],
#         # Restore original config file if backup exists
#         f'if [ -f {backup_path} ]; then mv {backup_path} {config_path}; fi',
#     )

#     with pytest.MonkeyPatch().context() as m:
#         # Mock HTTP requests at the right level for subprocess commands
#         import requests
#         original_request = requests.request

#         def mock_http_request(method, url, *args, **kwargs):
#             if '/api/health' in url and method == 'GET':
#                 mock_response = mock.MagicMock()
#                 mock_response.status_code = 200
#                 mock_response.json.return_value = {
#                     'status': 'healthy',
#                     'api_version': '1.0.0',
#                     'version': '1.0.0',
#                     'version_on_disk': '1.0.0',
#                     'commit': '1234567890',
#                     'basic_auth_enabled': False,
#                     'user': None
#                 }
#                 mock_response.raise_for_status = mock.MagicMock()
#                 return mock_response
#             return original_request(method, url, *args, **kwargs)

#         # Patch requests.request to affect subprocess commands
#         m.setattr('requests.request', mock_http_request)
        
#         # m.setattr(docker_utils, 'get_api_server_endpoint_inside_docker',
#         #           lambda: 'http://255.255.255.255:41540')

#         m.setattr(server_common, 'check_server_healthy',
#                   lambda endpoint: (server_common.ApiServerStatus.HEALTHY,
#                                      mock.MagicMock(status=server_common.ApiServerStatus.HEALTHY,
#                                                    api_version='1.0.0',
#                                                    version='1.0.0',
#                                                    version_on_disk='1.0.0',
#                                                    commit='1234567890',
#                                                    basic_auth_enabled=False)))
        
#         # Mock the environment config to return a non-existing endpoint.
#         # The sky api login command should not read from environment config
#         # when an explicit endpoint is provided as an argument.
#         smoke_tests_utils.run_one_test(test, check_sky_status=False)


def test_endpoint_output_env(generic_cloud: str):
    """Test that sky api info output is correct."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'endpoint_output_basic',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via the environment variable {constants.SKY_API_SERVER_URL_ENV_VAR}."',
        ],
        timeout=smoke_tests_utils.get_timeout('aws'),
        teardown=f'sky down -y {name}',
        env={
            constants.SKY_API_SERVER_URL_ENV_VAR: server_common.DEFAULT_SERVER_URL
        }
    )
    smoke_tests_utils.run_one_test(test)


# TODO(lloyd): Add tests where we write some random file to config and then
# run sky api info.

def test_remote_server_api_login_and_logout_no_config():
    if not smoke_tests_utils.is_remote_server_test():
        pytest.skip('This test is only for remote server')

    endpoint = docker_utils.get_api_server_endpoint_inside_docker()
    config = textwrap.dedent(f"""
    api_server:
        endpoint: {endpoint}
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

    test = smoke_tests_utils.Test(
        'remote-server-api-login',
        [
            # Backup existing config file if it exists
            f'if [ -f {config_path} ]; then cp {config_path} {backup_path}; fi',
            # Run sky api login
            f'sky api login -e {endpoint}',
            # Echo the config file content to see what was written
            f'echo "Config file content after sky api login:" && cat {config_path}',
            # Verify the config file is updated with the endpoint
            f'grep -q "endpoint: {endpoint}" {config_path}',
            # Verify the api_server section exists
            f'grep -q "api_server:" {config_path}',
        ],
        # Restore original config file if backup exists
        f'if [ -f {backup_path} ]; then mv {backup_path} {config_path}; fi',
    )

    with pytest.MonkeyPatch().context() as m:
        m.setattr(docker_utils, 'get_api_server_endpoint_inside_docker',
                  lambda: 'http://255.255.255.255:41540')
        # Mock the environment config to return a non-existing endpoint.
        # The sky api login command should not read from environment config
        # when an explicit endpoint is provided as an argument.
        smoke_tests_utils.run_one_test(test, check_sky_status=False)