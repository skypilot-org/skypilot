# Smoke tests for SkyPilot
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/test_smoke.py
#
# Terminate failed clusters after test finishes
# > pytest tests/test_smoke.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/test_smoke.py::test_minimal
#
# Only run managed job tests
# > pytest tests/test_smoke.py --managed-jobs
#
# Only run sky serve tests
# > pytest tests/test_smoke.py --sky-serve
#
# Only run test for AWS + generic tests
# > pytest tests/test_smoke.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/test_smoke.py --generic-cloud aws

import pytest
from smoke_tests.util import _get_cluster_name
from smoke_tests.util import _get_timeout
from smoke_tests.util import _VALIDATE_LAUNCH_OUTPUT
from smoke_tests.util import _WAIT_UNTIL_CLUSTER_STATUS_CONTAINS
from smoke_tests.util import run_one_test
from smoke_tests.util import Test

from sky.status_lib import ClusterStatus


# ---------- Dry run: 2 Tasks in a chain. ----------
@pytest.mark.no_fluidstack  #requires GCP and AWS set up
def test_example_app():
    test = Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    run_one_test(test)


# ---------- A minimal task ----------
def test_minimal(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'minimal',
        [
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml) && {_VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Test launch output again on existing cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml) && {_VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check the logs downloading
            f'log_path=$(sky logs {name} 1 --sync-down | grep "Job 1 logs:" | sed -E "s/^.*Job 1 logs: (.*)\\x1b\\[0m/\\1/g") && echo "$log_path" && test -f $log_path/run.log',
            # Ensure the raylet process has the correct file descriptor limit.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # Install jq for the next test.
            f'sky exec {name} \'sudo apt-get update && sudo apt-get install -y jq\'',
            # Check the cluster info
            f'sky exec {name} \'echo "$SKYPILOT_CLUSTER_INFO" | jq .cluster_name | grep {name}\'',
            f'sky logs {name} 5 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'echo "$SKYPILOT_CLUSTER_INFO" | jq .cloud | grep -i {generic_cloud}\'',
            f'sky logs {name} 6 --status',  # Ensure the job succeeded.
            # Test '-c' for exec
            f'sky exec -c {name} echo',
            f'sky logs {name} 7 --status',
            f'sky exec echo -c {name}',
            f'sky logs {name} 8 --status',
            f'sky exec -c {name} echo hi test',
            f'sky logs {name} 9 | grep "hi test"',
            f'sky exec {name} && exit 1 || true',
            f'sky exec -c {name} && exit 1 || true',
        ],
        f'sky down -y {name}',
        _get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Test fast launch ----------
def test_launch_fast(generic_cloud: str):
    name = _get_cluster_name()

    test = Test(
        'test_launch_fast',
        [
            # First launch to create the cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast tests/test_yamls/minimal.yaml) && {_VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',

            # Second launch to test fast launch - should not reprovision
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --fast tests/test_yamls/minimal.yaml) && '
            ' echo "$s" && '
            # Validate that cluster was not re-launched.
            '! echo "$s" | grep -A 1 "Launching on" | grep "is up." && '
            # Validate that setup was not re-run.
            '! echo "$s" | grep -A 1 "Running setup on" | grep "running setup" && '
            # Validate that the task ran and finished.
            'echo "$s" | grep -A 1 "task run finish" | grep "Job finished (status: SUCCEEDED)"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=_get_timeout(generic_cloud),
    )
    run_one_test(test)


# See cloud exclusion explanations in test_autostop
@pytest.mark.no_fluidstack
@pytest.mark.no_lambda_cloud
@pytest.mark.no_ibm
@pytest.mark.no_kubernetes
def test_launch_fast_with_autostop(generic_cloud: str):
    name = _get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    test = Test(
        'test_launch_fast_with_autostop',
        [
            # First launch to create the cluster with a short autostop
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast -i 1 tests/test_yamls/minimal.yaml) && {_VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',
            f'sky status -r {name} | grep UP',

            # Ensure cluster is stopped
            _WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=autostop_timeout),

            # Launch again. Do full output validation - we expect the cluster to re-launch
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --fast -i 1 tests/test_yamls/minimal.yaml) && {_VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=_get_timeout(generic_cloud) + autostop_timeout,
    )
    run_one_test(test)
