# Smoke tests for SkyPilot required before merging
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_required_before_merge.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_required_before_merge.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_required_before_merge.py::test_yaml_launch_and_mount
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_required_before_merge.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_required_before_merge.py --generic-cloud aws

from smoke_tests.util import get_cluster_name
from smoke_tests.util import (
    get_cmd_wait_until_job_status_contains_matching_job_id)
from smoke_tests.util import run_one_test
from smoke_tests.util import Test

import sky


def test_yaml_launch_and_mount(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'test_yaml_launch_and_mount',
        [
            f'sky launch -y -c {name} tests/test_yamls/minimal_test_required_before_merge.yaml',
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id=1,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=2 * 60),
        ],
        f'sky down -y {name}',
        timeout=5 * 60,
    )
    run_one_test(test)
