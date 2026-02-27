# Smoke tests for SkyPilot required before merging
# If the change includes an interface modification or touches the core API,
# the reviewer could decide it's necessary to trigger a pre-merge test and
# leave a comment /quicktest-core will then trigger this test.
#
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_quick_tests_core.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_quick_tests_core.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_quick_tests_core.py::test_yaml_launch_and_mount
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_quick_tests_core.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_quick_tests_core.py --generic-cloud aws

import os
import subprocess

import pytest
from smoke_tests import smoke_tests_utils

import sky


@pytest.mark.no_hyperbolic  # Hyperbolic does not support AWS
@pytest.mark.no_seeweb  # Seeweb does not support file mounting
def test_yaml_launch_and_mount(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_yaml_launch_and_mount',
        [
            f'sky launch -y -c {name} tests/test_yamls/minimal_test_quick_tests_core.yaml',
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id=1,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=2 * 60),
        ],
        f'sky down -y {name}',
        timeout=5 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def test_admin_policy_client_version(monkeypatch, generic_cloud: str):
    """Test that admin policy correctly receives client version info.

    This test verifies the E2E flow: client sends version headers,
    server passes them to admin policy, and policy can access them.
    Uses pytest monkeypatch to set custom version headers.
    """
    import pathlib

    from sky.client import sdk
    from sky.server import constants
    from sky.server import rest

    # Use absolute paths for reliability
    repo_root = pathlib.Path(__file__).parent.parent.parent
    policy_module_path = str(repo_root / 'tests' / 'smoke_tests')
    policy_config_path = str(repo_root / 'tests' / 'smoke_tests' /
                             'echo_client_version_config.yaml')

    # Use specific test values that we control (not constants.API_VERSION)
    test_api_version = 12345
    test_version = 'test-version-1.2.3'

    # Start API server with the test admin policy
    env = {
        **os.environ,
        'PYTHONPATH': f'{policy_module_path}:{os.environ.get("PYTHONPATH", "")}',
        'SKYPILOT_CONFIG': policy_config_path,
    }
    subprocess.run(['sky', 'api', 'stop'], check=False)
    subprocess.run(['sky', 'api', 'start'], env=env, check=True)

    try:
        # Monkeypatch the HTTP headers with our test values
        monkeypatch.setitem(rest._session.headers, constants.API_VERSION_HEADER,
                            str(test_api_version))
        monkeypatch.setitem(rest._session.headers, constants.VERSION_HEADER,
                            test_version)

        # Create minimal task to trigger server-side admin policy validation
        task = sky.Task(run='echo test')
        task.set_resources(sky.Resources(cpus='2+'))

        # This should fail with policy error containing version info
        with pytest.raises(Exception) as exc_info:
            request_id = sdk.launch(task=task,
                                    cluster_name='test-policy-version')
            sdk.stream_and_get(request_id)

        # Verify error contains the exact version values we sent
        error_msg = str(exc_info.value)
        assert 'ECHO_CLIENT_VERSION' in error_msg, (
            f'Expected ECHO_CLIENT_VERSION in error: {error_msg}')
        assert f'api_version={test_api_version}' in error_msg, (
            f'Expected api_version={test_api_version} in error: {error_msg}')
        assert f'version={test_version}' in error_msg, (
            f'Expected version={test_version} in error: {error_msg}')
    finally:
        # Teardown: restart API without policy
        subprocess.run(['sky', 'api', 'stop'], check=False)
        subprocess.run(['sky', 'api', 'start'], check=True)
