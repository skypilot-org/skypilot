# Smoke tests for SkyPilot required before merging
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_pre_merge.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_pre_merge.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_pre_merge.py::test_yaml_launch_and_mount
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_pre_merge.py --aws

import pytest
from smoke_tests import smoke_tests_utils

import sky


@pytest.mark.aws
@pytest.mark.azure
@pytest.mark.gcp
def test_yaml_launch_and_mount():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_yaml_launch_and_mount',
        [
            f'sky launch -y -c {name} tests/test_yamls/minimal_test_pre_merge.yaml',
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
