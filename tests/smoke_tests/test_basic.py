# Smoke tests for SkyPilot for basic functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_basic.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_basic.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_basic.py::test_minimal
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_basic.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_basic.py --generic-cloud aws

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
from sky.utils import yaml_utils


# ---------- Dry run: 2 Tasks in a chain. ----------
@pytest.mark.no_vast  #requires GCP and AWS set up
@pytest.mark.no_fluidstack  #requires GCP and AWS set up
@pytest.mark.no_hyperbolic  #requires GCP and AWS set up
@pytest.mark.no_seeweb  #requires GCP and AWS set up
def test_example_app():
    test = smoke_tests_utils.Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    smoke_tests_utils.run_one_test(test)


# ---------- A minimal task ----------
def test_minimal(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'minimal',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Test launch output again on existing cluster
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check the logs downloading
            f'log_path=$(sky logs {name} 1 --sync-down | grep "Job 1 logs:" | sed -E "s/^.*Job 1 logs: (.*)\\x1b\\[0m/\\1/g") && echo "$log_path" '
            # We need to explicitly expand the log path as it starts with ~, and linux does not automatically
            # expand it when having it in a variable.
            '  && expanded_log_path=$(eval echo "$log_path") && echo "$expanded_log_path" '
            '  && test -f $expanded_log_path/run.log',
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
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_minimal_arm64(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'minimal_arm',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} --instance-type m6g.large tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Test launch output again on existing cluster
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} --instance-type m6g.large tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check the logs downloading
            f'log_path=$(sky logs {name} 1 --sync-down | grep "Job 1 logs:" | sed -E "s/^.*Job 1 logs: (.*)\\x1b\\[0m/\\1/g") && echo "$log_path" '
            # We need to explicitly expand the log path as it starts with ~, and linux does not automatically
            # expand it when having it in a variable.
            '  && expanded_log_path=$(eval echo "$log_path") && echo "$expanded_log_path" '
            '  && test -f $expanded_log_path/run.log',
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
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- A minimal task with git repository workdir ----------
def test_minimal_with_git_workdir(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'minimal_with_git_workdir',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --git-url https://github.com/skypilot-org/skypilot.git --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
            # Checkout to releases/0.10.0
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref releases/0.10.0 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "releases/0\.10\.0" || exit 1\'',
            # Checkout to default branch
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --git-url https://github.com/skypilot-org/skypilot.git --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
            # Checkout to releases/0.10.0
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref releases/0.10.0 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "releases/0\.10\.0" || exit 1\'',
            # Checkout to tag v0.10.0
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref v0.10.0 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "v0\.10\.0" || exit 1\'',
            # Checkout to commit 41c25f40
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref 41c25f40 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "41c25f40" || exit 1\'',
            # Checkout to default branch
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


def test_minimal_with_git_workdir_docker(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'minimal_with_git_workdir',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --image-id docker:ubuntu:20.04 --git-url https://github.com/skypilot-org/skypilot.git --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
            # Checkout to releases/0.10.0
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --image-id docker:ubuntu:20.04 --git-url https://github.com/skypilot-org/skypilot.git --git-ref releases/0.10.0 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "releases/0\.10\.0" || exit 1\'',
            # Checkout to default branch
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --image-id docker:ubuntu:20.04 --git-url https://github.com/skypilot-org/skypilot.git --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
            # Checkout to releases/0.10.0
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref releases/0.10.0 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "releases/0\.10\.0" || exit 1\'',
            # Checkout to tag v0.10.0
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref v0.10.0 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "v0\.10\.0" || exit 1\'',
            # Checkout to commit 41c25f40
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git --git-ref 41c25f40 tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep "41c25f40" || exit 1\'',
            # Checkout to default branch
            f'sky exec {name} --git-url https://github.com/skypilot-org/skypilot.git tests/test_yamls/minimal.yaml',
            # Check the current branch
            f'sky exec {name} \'git status | grep master || exit 1\'',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test fast launch ----------
def test_launch_fast(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'test_launch_fast',
        [
            # First launch to create the cluster
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} --fast {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',

            # Second launch to test fast launch - should not reprovision
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --fast tests/test_yamls/minimal.yaml) && '
            ' echo "$s" && '
            # Validate that cluster was not re-launched.
            '! echo "$s" | grep -A 1 "Launching on" | grep "is up." && '
            # Validate that setup was re-run.
            'echo "$s" | grep -A 1 "Job started. Streaming logs..." | grep "(setup" | grep "running setup" && '
            # Validate that the task ran and finished.
            'echo "$s" | grep -A 1 "task run finish" | grep "Job finished (status: SUCCEEDED)"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# See cloud exclusion explanations in test_autostop
@pytest.mark.no_fluidstack
@pytest.mark.no_lambda_cloud
@pytest.mark.no_ibm
@pytest.mark.no_kubernetes
@pytest.mark.no_hyperbolic
@pytest.mark.no_seeweb
def test_launch_fast_with_autostop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    test = smoke_tests_utils.Test(
        'test_launch_fast_with_autostop',
        [
            # First launch to create the cluster with a short autostop
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} --fast -i 1 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',
            f'sky status -r {name} | grep UP',

            # Ensure cluster is stopped
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
            # Even the cluster is stopped, cloud platform may take a while to
            # delete the VM.
            # FIXME(aylei): this can be flaky, sleep longer for now.
            f'sleep 60',
            # Launch again. Do full output validation - we expect the cluster to re-launch
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --fast -i 1 tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + autostop_timeout,
    )
    smoke_tests_utils.run_one_test(test)


# We override the AWS config to force the cluster to relaunch, so only run the
# test on AWS.
@pytest.mark.aws
def test_launch_fast_with_cluster_changes(generic_cloud: str, tmp_path):
    name = smoke_tests_utils.get_cluster_name()
    tmp_config_path = tmp_path / 'sky.yaml'
    test = smoke_tests_utils.Test(
        'test_launch_fast_with_cluster_changes',
        [
            # Initial launch
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra {generic_cloud} --fast {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',

            # Launch again - setup and provisioning should be skipped
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --fast tests/test_yamls/minimal.yaml) && '
            ' echo "$s" && '
            # Validate that cluster was not re-launched.
            '! echo "$s" | grep -A 1 "Launching on" | grep "is up." && '
            # Validate that setup was re-run.
            'echo "$s" | grep -A 1 "Job started. Streaming logs..." | grep "(setup" | grep "running setup" && '
            f'sky logs {name} 2 --status',

            # Copy current config as a base.
            f'cp ${{{skypilot_config.ENV_VAR_GLOBAL_CONFIG}:-~/.sky/config.yaml}} {tmp_config_path} && '
            # Set config override. This should change the cluster yaml, forcing reprovision/setup
            f'echo "aws: {{ remote_identity: test }}" >> {tmp_config_path}',
            # Launch and do full output validation. Setup/provisioning should be run.
            f's=$(SKYPILOT_DEBUG=0 {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={tmp_config_path} sky launch -y -c {name} --fast tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 3 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ------------ Test stale job ------------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
@pytest.mark.no_vast  # This requires port opening
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
def test_stale_job(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'stale_job',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            f'sky stop -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=100),
            f'sky start -y {name}',
            f'sky logs {name} 1 --status',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED_DRIVER',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast
@pytest.mark.aws
def test_aws_stale_job_manual_restart():
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.AWS.max_cluster_name_length())
    region = 'us-east-2'
    test = smoke_tests_utils.Test(
        'aws_stale_job_manual_restart',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'id={smoke_tests_utils.AWS_GET_INSTANCE_ID.format(region=region, name_on_cloud=name_on_cloud)} && '
                 f'aws ec2 stop-instances --region {region} '
                 f'--instance-ids $id')),
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=40),
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[sky.JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS),
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast
@pytest.mark.aws
def test_aws_manual_restart_recovery():
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.AWS.max_cluster_name_length())
    region = 'us-east-2'
    test = smoke_tests_utils.Test(
        'test_aws_manual_restart_recovery',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd(
                'aws', name, skip_remote_server_check=True),
            f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky autostop {name} -y -i 1',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=180),
            # Restart the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=(
                    f'id=`aws ec2 describe-instances --region {region} --filters '
                    f'Name=tag:ray-cluster-name,Values={name_on_cloud} '
                    f'--query Reservations[].Instances[].InstanceId '
                    f'--output text` && '
                    # Wait for the instance to be stopped before restarting.
                    f'aws ec2 wait instance-stopped --region {region} '
                    f'--instance-ids $id && '
                    # Start the instance.
                    f'aws ec2 start-instances --region {region} '
                    f'--instance-ids $id && '
                    # Wait for the instance to be running.
                    f'aws ec2 wait instance-running --region {region} '
                    f'--instance-ids $id'),
                skip_remote_server_check=True),
            # Status refresh should time out, as the restarted
            # instance would get a new IP address.
            # We should see a warning message on how to recover
            # from this state.
            f'sky status -r {name} | grep -i "Failed getting cluster status" | grep -i "sky start" | grep -i "to recover from INIT status."',
            # Recover the cluster.
            f'sky start -y {name}',
            # Wait for the cluster to be up.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.UP],
                timeout=300),
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name, skip_remote_server_check=True)}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast
@pytest.mark.gcp
def test_gcp_stale_job_manual_restart():
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.GCP.max_cluster_name_length())
    zone = 'us-central1-a'
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name={name_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    stop_cmd = (f'gcloud compute instances stop --zone={zone}'
                f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'gcp_stale_job_manual_restart',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky launch -y -c {name} --infra gcp/*/us-central1-a {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=stop_cmd),
            'sleep 40',
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[sky.JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS)
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
@pytest.mark.no_fluidstack  # Requires amazon S3
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support num_nodes > 1 yet.
def test_env_check(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'env_check',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/env_check.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            # Test with only setup.
            f'sky launch -y -c {name} tests/test_yamls/test_only_setup.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 2 | grep "hello world"',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- CLI logs ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_logs instead.
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet.
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support num_nodes > 1 yet.
def test_cli_logs(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    num_nodes = 2
    if generic_cloud == 'kubernetes':
        # Kubernetes does not support multi-node
        num_nodes = 1
    timestamp = time.time()
    test = smoke_tests_utils.Test('cli_logs', [
        f'sky launch -y -c {name} --infra {generic_cloud} --num-nodes {num_nodes} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo {timestamp} 1"',
        f'sky exec {name} "echo {timestamp} 2"',
        f'sky exec {name} "echo {timestamp} 3"',
        f'sky exec {name} "echo {timestamp} 4"',
        f'sky logs {name} 2 --status',
        f'sky logs {name} 3 4 --sync-down',
        f'sky logs {name} * --sync-down',
        f'sky logs {name} 1 | grep "{timestamp} 1"',
        f'sky logs {name} | grep "{timestamp} 4"',
    ], f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_logs():
    name = smoke_tests_utils.get_cluster_name()
    timestamp = time.time()
    test = smoke_tests_utils.Test(
        'SCP_cli_logs',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} "echo {timestamp} 1"',
            f'sky exec {name} "echo {timestamp} 2"',
            f'sky exec {name} "echo {timestamp} 3"',
            f'sky exec {name} "echo {timestamp} 4"',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 4 --sync-down',
            f'sky logs {name} * --sync-down',
            f'sky logs {name} 1 | grep "{timestamp} 1"',
            f'sky logs {name} | grep "{timestamp} 4"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ------- Testing the core API --------
# Most of the core APIs have been tested in the CLI tests.
# These tests are for testing the return value of the APIs not fully used in CLI.
def test_core_api_sky_launch_exec(generic_cloud: str):
    with smoke_tests_utils.override_sky_config():
        # We need to override the sky api endpoint env if --remote-server is
        # specified, so we can run the test on the remote server.
        name = smoke_tests_utils.get_cluster_name()
        task = sky.Task(run="whoami")
        task.set_resources(
            sky.Resources(infra=generic_cloud,
                          **smoke_tests_utils.LOW_RESOURCE_PARAM))
        try:
            job_id, handle = sky.get(sky.launch(task, cluster_name=name))
            assert job_id == 1
            assert handle is not None
            assert handle.cluster_name == name
            assert str(handle.launched_resources.cloud).lower(
            ) == generic_cloud.lower()
            job_id_exec, handle_exec = sky.get(sky.exec(task,
                                                        cluster_name=name))
            assert job_id_exec == 2
            assert handle_exec is not None
            assert handle_exec.cluster_name == name
            assert str(handle_exec.launched_resources.cloud).lower(
            ) == generic_cloud.lower()
            # For dummy task (i.e. task.run is None), the job won't be submitted.
            dummy_task = sky.Task()
            job_id_dummy, _ = sky.get(sky.exec(dummy_task, cluster_name=name))
            assert job_id_dummy is None
            # Check the cluster status from the dashboard
            cluster_exist = False
            status_request_id = (
                smoke_tests_utils.get_dashboard_cluster_status_request_id())
            status_response = (smoke_tests_utils.get_response_from_request_id(
                status_request_id))
            for cluster in status_response:
                if cluster['name'] == name:
                    cluster_exist = True
                    break
            assert cluster_exist, status_response
        finally:
            sky.get(sky.down(name))


# The sky launch CLI has some additional checks to make sure the cluster is up/
# restarted. However, the core API doesn't have these; make sure it still works
@pytest.mark.no_kubernetes
def test_core_api_sky_launch_fast(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    try:
        task = sky.Task(run="whoami").set_resources(
            sky.Resources(infra=generic_cloud,
                          **smoke_tests_utils.LOW_RESOURCE_PARAM))
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
        # Sleep to let the cluster autostop
        smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
            cluster_name=name,
            cluster_status=[sky.ClusterStatus.STOPPED],
            timeout=120)
        # Run it again - should work with fast=True
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
    finally:
        sky.down(name)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support autostop and host controllers
@pytest.mark.no_seeweb  # Seeweb does not support host controllers
def test_jobs_launch_and_logs(generic_cloud: str):
    # The first `with` is to override the sky api endpoint env if --remote-server
    # is specified, so the test knows it's running on the remote server, thats
    # part of our test suite. The sky api endpoint has cache and is_api_server_local()
    # could be already cached before the first line of this test, so we need to
    # override and invalidate the cache in the first `with`.
    # The second `with` is to override the skypilot config to use the low
    # controller resource in the memory, thats part of the SDK support.
    with smoke_tests_utils.override_sky_config():
        # Use the context manager
        with skypilot_config.override_skypilot_config(
                smoke_tests_utils.LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG):
            name = smoke_tests_utils.get_cluster_name()
            task = sky.Task(run="echo start job; sleep 30; echo end job")
            task.set_resources(
                sky.Resources(infra=generic_cloud,
                              **smoke_tests_utils.LOW_RESOURCE_PARAM))
            job_id, handle = sky.stream_and_get(sky.jobs.launch(task,
                                                                name=name))
            assert handle is not None
            # Check the job status from the dashboard
            queue_request_id = (
                smoke_tests_utils.get_dashboard_jobs_queue_request_id())
            queue_response = (smoke_tests_utils.get_response_from_request_id(
                queue_request_id))
            job_exist = False
            for job in queue_response:
                if job['job_id'] == job_id:
                    job_exist = True
                    break
            assert job_exist
            try:
                with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as f:
                    sky.jobs.tail_logs(job_id=job_id, output_stream=f)
                    f.seek(0)
                    content = f.read()
                    assert content.count('start job') == 1
                    assert content.count('end job') == 1
            finally:
                sky.jobs.cancel(job_ids=[job_id])


# ---------- Testing YAML Specs ----------
# Our sky storage requires credentials to check the bucket existence when
# loading a task from the yaml file, so we cannot make it a unit test.
class TestYamlSpecs:
    # TODO(zhwu): Add test for `to_yaml_config` for the Storage object.
    #  We should not use `examples/storage_demo.yaml` here, since it requires
    #  users to ensure bucket names to not exist and/or be unique.
    _TEST_YAML_PATHS = [
        'examples/minimal.yaml', 'examples/managed_job.yaml',
        'examples/using_file_mounts.yaml', 'examples/resnet_app.yaml',
        'examples/multi_hostname.yaml'
    ]

    def _is_dict_subset(self, d1, d2):
        """Check if d1 is the subset of d2."""
        for k, v in d1.items():
            if k not in d2:
                if isinstance(v, list) or isinstance(v, dict):
                    assert len(v) == 0, (k, v)
                else:
                    assert False, (k, v)
            elif isinstance(v, dict):
                assert isinstance(d2[k], dict), (k, v, d2)
                self._is_dict_subset(v, d2[k])
            elif isinstance(v, str):
                if k == 'accelerators':
                    resources = sky.Resources()
                    resources._set_accelerators(v, None)
                    assert resources.accelerators == d2[k], (k, v, d2)
                else:
                    assert v.lower() == d2[k].lower(), (k, v, d2[k])
            else:
                assert v == d2[k], (k, v, d2[k])

    def _check_equivalent(self, yaml_path):
        """Check if the yaml is equivalent after load and dump again."""
        origin_task_config = yaml_utils.read_yaml(yaml_path)

        task = sky.Task.from_yaml(yaml_path)
        new_task_config = task.to_yaml_config()
        # d1 <= d2
        print(origin_task_config, new_task_config)
        self._is_dict_subset(origin_task_config, new_task_config)

    def test_load_dump_yaml_config_equivalent(self):
        """Test if the yaml config is equivalent after load and dump again."""
        pathlib.Path('~/datasets').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/tmpfile').expanduser().touch()
        pathlib.Path('~/.ssh').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/.ssh/id_rsa.pub').expanduser().touch()
        pathlib.Path('~/tmp-workdir').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/Downloads/tpu').expanduser().mkdir(parents=True,
                                                           exist_ok=True)
        for yaml_path in self._TEST_YAML_PATHS:
            self._check_equivalent(yaml_path)


# ---------- Testing Multiple Accelerators ----------
@pytest.mark.no_vast  # Vast has low availability for K80 GPUs
@pytest.mark.no_fluidstack  # Fluidstack does not support K80 gpus for now
@pytest.mark.no_paperspace  # Paperspace does not support K80 gpus
@pytest.mark.no_nebius  # Nebius does not support K80s
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support K80s
def test_multiple_accelerators_ordered():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support T4
def test_multiple_accelerators_ordered_with_default():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered_with_default.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep spot',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support T4
def test_multiple_accelerators_unordered():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-unordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support T4
def test_multiple_accelerators_unordered_with_default():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-unordered-with-default',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered_with_default.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep spot',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Requires other clouds to be enabled
@pytest.mark.no_fluidstack  # Requires other clouds to be enabled
@pytest.mark.no_hyperbolic  # Requires other clouds to be enabled
@pytest.mark.no_seeweb  # Requires other clouds to be enabled
def test_multiple_resources():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-resources',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_resources.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.fixture(scope='session')
def unreachable_context():
    """Setup the kubernetes context for the test.

    This fixture will copy the kubeconfig file and inject an unreachable context
    to it. So this must be session scoped that the kubeconfig is modified before
    the local API server starts.
    """
    if smoke_tests_utils.is_non_docker_remote_api_server():
        yield
        return
    # Get kubeconfig path from environment variable or use default
    kubeconfig_path = os.environ.get('KUBECONFIG',
                                     os.path.expanduser('~/.kube/config'))
    if not os.path.exists(kubeconfig_path):
        yield
        return
    import shutil

    # Create a temp kubeconfig
    temp_kubeconfig = tempfile.NamedTemporaryFile(delete=False, suffix='.yaml')
    shutil.copy(kubeconfig_path, temp_kubeconfig.name)
    original_kubeconfig = os.environ.get('KUBECONFIG')
    os.environ['KUBECONFIG'] = temp_kubeconfig.name

    free_port = common_utils.find_free_port(30000)
    unreachable_name = '_unreachable_context_'
    subprocess.run(
        'kubectl config set-cluster unreachable-cluster '
        f'--server=https://127.0.0.1:{free_port} && '
        'kubectl config set-credentials unreachable-user '
        '--token="aQo=" && '
        'kubectl config set-context ' + unreachable_name + ' '
        '--cluster=unreachable-cluster --user=unreachable-user && '
        # Restart the API server to pick up kubeconfig change
        # TODO(aylei): There is a implicit API server restart before starting
        # smoke tests in CI pipeline. We should move that to fixture to make
        # the test coherent.
        'sky api stop || true && sky api start',
        shell=True,
        check=True)

    yield unreachable_name

    # Clean up
    if original_kubeconfig:
        os.environ['KUBECONFIG'] = original_kubeconfig
    else:
        os.environ.pop('KUBECONFIG', None)
    os.unlink(temp_kubeconfig.name)


@pytest.mark.kubernetes
def test_kubernetes_context_failover(unreachable_context):
    """Test if the kubernetes context failover works.

    This test requires two kubernetes clusters:
    - kind-skypilot: the local cluster with mock labels for 8 H100 GPUs.
    - another accessible cluster: with enough CPUs
    To start the first cluster, run:
      sky local up
      # Add mock label for accelerator
      kubectl label node --overwrite skypilot-control-plane skypilot.co/accelerator=h100 --context kind-skypilot
      # Patch accelerator capacity
      kubectl patch node skypilot-control-plane --subresource=status -p '{"status": {"capacity": {"nvidia.com/gpu": "8"}}}' --context kind-skypilot
      # Add a new namespace to test the handling of namespaces
      kubectl create namespace test-namespace --context kind-skypilot
      # Set the namespace to test-namespace
      kubectl config set-context kind-skypilot --namespace=test-namespace --context kind-skypilot
    """
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip('Skipping test because the Kubernetes configs and '
                    'credentials are located on the remote API server '
                    'and not the machine where the test is running')

    # Get context that is not kind-skypilot
    contexts = subprocess.check_output('kubectl config get-contexts -o name',
                                       shell=True).decode('utf-8').split('\n')
    assert unreachable_context in contexts, (
        'unreachable_context should be initialized in the fixture')
    context = [
        context for context in contexts
        if (context != 'kind-skypilot' and context != unreachable_context)
    ][0]
    # Test unreachable context and non-existing context do not break failover
    config = textwrap.dedent(f"""\
    kubernetes:
      allowed_contexts:
        - {context}
        - {unreachable_context}
        - _nonexist_
        - kind-skypilot
    """)
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()
        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'kubernetes-context-failover',
            [
                # Check if kind-skypilot is provisioned with H100 annotations already
                'NODE_INFO=$(kubectl get nodes -o yaml --context kind-skypilot) && '
                'echo "$NODE_INFO" | grep nvidia.com/gpu | grep 8 && '
                'echo "$NODE_INFO" | grep skypilot.co/accelerator | grep h100 || '
                '{ echo "kind-skypilot does not exist '
                'or does not have mock labels for GPUs. Check the instructions in '
                'tests/test_smoke.py::test_kubernetes_context_failover." && exit 1; }',
                # Check namespace for kind-skypilot is test-namespace
                'kubectl get namespaces --context kind-skypilot | grep test-namespace || '
                '{ echo "Should set the namespace to test-namespace for kind-skypilot. Check the instructions in '
                'tests/test_smoke.py::test_kubernetes_context_failover." && exit 1; }',
                'sky show-gpus --infra kubernetes/kind-skypilot | grep H100 | grep "1, 2, 4, 8"',
                # Get contexts and set current context to the other cluster that is not kind-skypilot
                f'kubectl config use-context {context}',
                # H100 should not be in the current context
                f'! sky show-gpus --infra kubernetes/{context} | grep H100',
                # H100 should be displayed as long as it is available in one of the contexts
                'sky show-gpus --infra kubernetes | grep H100',
                f'sky launch -y -c {name}-1 --cpus 1 echo hi',
                f'sky logs {name}-1 --status',
                # It should be launched not on kind-skypilot
                f'sky status -v {name}-1 | grep "{context}"',
                # Test failure for launching H100 on other cluster
                f'sky launch -y -c {name}-2 --gpus H100 --cpus 1 --infra kubernetes/{context} echo hi && exit 1 || true',
                # Test failover
                f'sky launch -y -c {name}-3 --gpus H100 --cpus 1 --infra kubernetes echo hi',
                f'sky logs {name}-3 --status',
                # Test pods
                f'kubectl get pods --context kind-skypilot | grep "{name}-3"',
                # It should be launched on kind-skypilot
                f'sky status -v {name}-3 | grep "kind-skypilot"',
                # Should be 7 free GPUs
                f'sky show-gpus --infra kubernetes/kind-skypilot | grep H100 | grep "  7"',
                # Remove the line with "kind-skypilot"
                f'sed -i "/kind-skypilot/d" {f.name}',
                f'export KUBECONFIG={f.name}',
                # Test failure for launching on unreachable context
                f'kubectl config use-context {unreachable_context}',
                f'sky launch -y -c {name}-4 --gpus H100 --cpus 1 --infra kubernetes/{unreachable_context} echo hi && exit 1 || true',
                # Test failover from unreachable context
                f'sky launch -y -c {name}-5 --cpus 1 echo hi',
                # switch back to kind-skypilot where GPU cluster is launched
                f'kubectl config use-context kind-skypilot',
                # test if sky status --kubernetes shows H100
                f'sky status --kubernetes | grep H100 || '
                '{ echo "sky status --kubernetes does not show H100." && exit 1; }',
            ],
            f'sky down -y {name}-1 {name}-3 {name}-5',
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f.name,
                constants.SKY_API_SERVER_URL_ENV_VAR:
                    sky.server.common.get_server_url()
            },
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_seeweb  # Seeweb fails to provision resources
def test_launch_and_exec_async(generic_cloud: str):
    """Test if the launch and exec commands work correctly with --async."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'launch_and_exec_async',
        [
            f'sky launch -c {name} -y --async',
            # Async exec.
            f'sky exec {name} echo --async',
            # Async exec and cancel immediately.
            (f's=$(sky exec {name} echo --async) && '
             'echo "$s" && '
             'cancel_cmd=$(echo "$s" | grep "To cancel the request" | '
             'sed -E "s/.*run: (sky api cancel .*).*/\\1/") && '
             'echo "Extracted cancel command: $cancel_cmd" && '
             '$cancel_cmd'),
            # Sync exec must succeed after command end.
            (
                f's=$(sky exec {name} echo) && echo "$s" && '
                'echo "===check exec output===" && '
                'job_id=$(echo "$s" | grep "Job submitted, ID:" | '
                'sed -E "s/.*Job submitted, ID: ([0-9]+).*/\\1/") && '
                f'sky logs {name} $job_id --status | grep "SUCCEEDED" && '
                # If job_id is 1, async_job_id will be 2, and vice versa.
                'async_job_id=$((3-job_id)) && '
                f'echo "===check async job===" && echo "Job ID: $async_job_id" && '
                # Wait async job to succeed.
                f'{smoke_tests_utils.get_cmd_wait_until_job_status_succeeded(name, "$async_job_id")}'
            ),
            # Cluster must be UP since the sync exec has been completed.
            f'sky status {name} | grep "UP"',
            # The cancelled job should not be scheduled, the job ID 3 is just
            # not exist.
            f'! sky logs {name} 3 --status | grep "SUCCEEDED"',
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud))
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic fails to provision resources
@pytest.mark.no_kubernetes  # Kubernetes runs to UP state too fast
def test_cancel_launch_and_exec_async(generic_cloud: str):
    """Test if async launch and exec commands work correctly when cluster is shutdown"""
    name = smoke_tests_utils.get_cluster_name()

    wait_cmd = smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
        name, [sky.ClusterStatus.INIT], 30)
    # This test need cluster to be in INIT state, so that job will be cancelled,
    # so we need to reduce the waiting time for the cluster to avoid it goes to UP state
    wait_cmd = wait_cmd.replace('sleep 10', 'sleep 1')
    test = smoke_tests_utils.Test(
        'cancel_launch_and_exec_async', [
            f'sky launch -c {name} -y --infra {generic_cloud} --async',
            (f's=$(sky exec {name} echo --async) && '
             'echo "$s" && '
             'logs_cmd=$(echo "$s" | grep "Check logs with" | '
             'sed -E "s/.*with: (sky api logs .*).*/\\1/") && '
             'echo "Extracted logs command: $logs_cmd" && '
             f'{wait_cmd} && '
             f'sky down -y {name} && '
             'log_output=$(eval $logs_cmd || true) && '
             'echo "===logs===" && echo "$log_output" && '
             'echo "$log_output" | grep "cancelled"'),
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud))
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Exit Codes for CLI commands ----------
def test_cli_exit_codes(generic_cloud: str):
    """Test that CLI commands properly return exit codes based on job success/failure."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cli_exit_codes',
        [
            # Test successful job exit code (0)
            f'sky launch -y -c {name} --infra {generic_cloud} "echo success" && echo "Exit code: $?"',
            f'sky logs {name} 1 --status | grep SUCCEEDED',

            # Test that sky logs with successful job returns 0
            f'sky logs {name} 1 && echo "Exit code: $?"',

            # Test failed job exit code (100)
            f'sky exec {name} "exit 1" || echo "Command failed with code: $?" | grep "Command failed with code: 100"',
            f'sky logs {name} 2 --status | grep FAILED',
            f'sky logs {name} 2 || echo "Job logs exit code: $?" | grep "Job logs exit code: 100"',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_cloud_open_ports():
    """Test Lambda Cloud open ports functionality.

    It tests the functionality by opening both a single port and a port range,
    verifying that both types of rules are created successfully. It also tests
    that consecutive individual ports are properly merged into a single range rule.
    """
    # Test ports and port ranges
    single_port = '12345'
    single_port_int = int(single_port)
    port_range = '5000-5010'
    port_range_start = 5000
    port_range_end = 5010

    # Consecutive ports that should be merged
    consecutive_ports = ['6000', '6001', '6002']
    consecutive_start = 6000
    consecutive_end = 6002

    # Store initial rules to avoid modifying rules that existed before the test
    initial_rules = []
    lambda_client = None

    from sky.provision.lambda_cloud import instance
    from sky.provision.lambda_cloud import lambda_utils

    try:
        # Initialize Lambda Cloud client
        lambda_client = lambda_utils.LambdaCloudClient()

        # Check if our test method exists - if not, test will be skipped
        if not hasattr(lambda_client, 'list_firewall_rules') or not hasattr(
                lambda_client, 'create_firewall_rule'):
            pytest.skip(
                'LambdaCloudClient doesn\'t have required firewall rule methods'
            )

        # Skip test for us-south-1 region where firewall rules are not supported
        if any('us-south-1' in str(rule)
               for rule in lambda_client.list_catalog().values()):
            # Check if our current region is us-south-1
            instances = lambda_client.list_instances()
            for inst in instances:
                if inst.get('region', {}).get('name') == 'us-south-1':
                    pytest.skip(
                        'Firewall rules not supported in us-south-1 region')

        # Get initial rules for debugging and tracking purposes
        initial_rules = lambda_client.list_firewall_rules()
        print(f'Initial firewall rules count: {len(initial_rules)}')

        # Print example rule structure for debugging
        if initial_rules:
            print(f'Example rule structure: {initial_rules[0]}')

        # 1. Test opening a single port
        print(f'Opening single port {single_port}')
        instance.open_ports('smoke-test-cluster', [single_port])
        print(f'Successfully called open_ports for single port {single_port}')

        # 2. Test opening a port range
        print(f'Opening port range {port_range}')
        instance.open_ports('smoke-test-cluster', [port_range])
        print(f'Successfully called open_ports for port range {port_range}')

        # 3. Test opening consecutive ports to verify merging
        print(f'Opening consecutive ports {", ".join(consecutive_ports)}')
        instance.open_ports('smoke-test-cluster', consecutive_ports)
        print('Successfully called open_ports for consecutive ports '
              f'{", ".join(consecutive_ports)}')

        # Verify rules were created by getting current rules
        current_rules = lambda_client.list_firewall_rules()
        print(f'Rules after adding our test ports: {len(current_rules)}')

        # Basic verification that rules were added
        # (should have at least as many rules as before)
        assert len(current_rules) >= len(
            initial_rules), 'No new rules were added'

        # 4. Verify consecutive ports were merged into a range
        merged_range_found = False
        for rule in current_rules:
            if (rule.get('protocol') == 'tcp' and rule.get('port_range') and
                    len(rule.get('port_range')) == 2 and
                    rule.get('port_range')[0] == consecutive_start and
                    rule.get('port_range')[1] == consecutive_end):

                # Check that it's our auto-generated rule
                description = rule.get('description', '')
                if 'SkyPilot auto-generated' in description:
                    merged_range_found = True
                    print('Found merged port range rule: TCP '
                          f'{consecutive_start}-{consecutive_end}')
                    break

        # Make sure port merging worked - now with a hard assertion
        assert merged_range_found, (
            f'Ports {consecutive_ports} were not merged into a single range '
            f'rule {consecutive_start}-{consecutive_end}. '
            'Port rule merging is not working as expected.')

    except Exception as e:
        import traceback
        print(f'Error in test: {e}')
        print(traceback.format_exc())
        pytest.fail(f'Error testing Lambda Cloud open_ports: {str(e)}')

    finally:
        # Clean up the test ports we created, being careful to preserve
        # pre-existing rules
        if lambda_client is None:
            print('Lambda client not initialized, skipping cleanup')
        elif not initial_rules:
            print('No initial rules were recorded, skipping cleanup for safety')
        else:
            try:
                # We need to clean up manually since instance.cleanup_ports
                # intentionally skips cleanup for Lambda Cloud (as firewall
                # rules are global to the account)

                # Get all current rules
                current_rules = lambda_client.list_firewall_rules()

                # Create a set of "fingerprints" for initial rules for faster
                # comparison.
                # Use a tuple of (protocol, source_network, port_range) as a
                # fingerprint.
                initial_rule_fingerprints = set()
                for rule in initial_rules:
                    # Convert port_range to a tuple so it can be hashed
                    port_range_tuple = tuple(rule.get(
                        'port_range', [])) if rule.get('port_range') else None
                    fingerprint = (rule.get('protocol'),
                                   rule.get('source_network'), port_range_tuple)
                    initial_rule_fingerprints.add(fingerprint)

                # Identify rules that match our test ports and weren't in the
                # initial set.
                rules_to_remove = []
                for rule in current_rules:
                    # Create fingerprint for this rule
                    port_range_tuple = tuple(rule.get(
                        'port_range', [])) if rule.get('port_range') else None
                    fingerprint = (rule.get('protocol'),
                                   rule.get('source_network'), port_range_tuple)

                    # Skip rules that existed before our test
                    if fingerprint in initial_rule_fingerprints:
                        continue

                    # Description check (all our rules should have SkyPilot in
                    # description).
                    description = rule.get('description', '')
                    if 'SkyPilot auto-generated' not in description:
                        continue

                    # Check if this rule matches our single test port
                    if (rule.get('protocol') == 'tcp' and
                            rule.get('port_range') and
                            len(rule.get('port_range')) == 2 and
                            rule.get('port_range')[0] == single_port_int and
                            rule.get('port_range')[1] == single_port_int):
                        rules_to_remove.append(rule)
                        print(f'Found test single port rule to clean up: '
                              f'TCP {single_port_int}')

                    # Check if this rule matches our port range
                    elif (rule.get('protocol') == 'tcp' and
                          rule.get('port_range') and
                          len(rule.get('port_range')) == 2 and
                          rule.get('port_range')[0] == port_range_start and
                          rule.get('port_range')[1] == port_range_end):
                        rules_to_remove.append(rule)
                        print(f'Found test port range rule to clean up: '
                              f'TCP {port_range_start}-{port_range_end}')

                    # Check if this rule matches our consecutive ports
                    # (either merged or individual)
                    elif (rule.get('protocol') == 'tcp' and
                          rule.get('port_range') and
                          len(rule.get('port_range')) == 2):
                        port_start = rule.get('port_range')[0]
                        port_end = rule.get('port_range')[1]

                        # Check if it's the merged range
                        if (port_start == consecutive_start and
                                port_end == consecutive_end):
                            rules_to_remove.append(rule)
                            print(
                                f'Found merged consecutive ports rule to clean up: '
                                f'TCP {consecutive_start}-{consecutive_end}')

                        # Check if it's an individual port from our consecutive
                        # range.
                        elif (port_start == port_end and consecutive_start <=
                              port_start <= consecutive_end):
                            rules_to_remove.append(rule)
                            print(f'Found individual consecutive port rule to '
                                  f'clean up: TCP {port_start}')

                if rules_to_remove:
                    print(f'Cleaning up {len(rules_to_remove)} test firewall '
                          'rule(s)')

                    # Build rule list without our test rules
                    api_rules = []
                    for rule in current_rules:
                        # Check if this rule should be removed
                        should_remove = False
                        for rule_to_remove in rules_to_remove:
                            if (rule.get('protocol')
                                    == rule_to_remove.get('protocol') and
                                    rule.get('source_network')
                                    == rule_to_remove.get('source_network') and
                                    rule.get('port_range')
                                    == rule_to_remove.get('port_range')):
                                should_remove = True
                                break

                        # Skip if this rule should be removed
                        if should_remove:
                            continue

                        if rule.get('protocol') and rule.get('source_network'):
                            api_rule = {
                                'protocol': rule.get('protocol'),
                                'source_network': rule.get('source_network'),
                                'description': rule.get('description', '')
                            }

                            # Add port_range for non-icmp protocols
                            if rule.get('protocol') != 'icmp' and rule.get(
                                    'port_range'):
                                api_rule['port_range'] = rule.get('port_range')

                            api_rules.append(api_rule)

                    # Update the rules without our test rule(s)
                    data = json.dumps({'data': api_rules})
                    lambda_utils._try_request_with_backoff(
                        'put',
                        f'{lambda_utils.API_ENDPOINT}/firewall-rules',
                        data=data,
                        headers=lambda_client.headers,
                    )
                    print('Cleanup completed successfully')
                else:
                    print('No matching new rules found to clean up')
            except Exception as e:
                print(f'Warning: Failed to clean up test firewall rule: {e}')
                # Don't fail the test if cleanup fails


def test_cli_output(generic_cloud: str):
    """Test that CLI commands properly stream output."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cli_output',
        [
            ('s=$(sky check) && echo "$s" && echo "===Validating check output===" && '
             'echo "$s" | grep "Enabled infra"'),
            # Get the launch plan output before the prompting
            (
                f's=$(yes no | sky launch -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} || true) && '
                'echo "$s" && echo "===Validating launch plan===" && '
                'echo "$s" | grep "CHOSEN" && '
                'border=$(echo "$s" | grep -A 1 "Considered resources" | tail -n +2) && '
                'echo $border && echo "===Table should have 3 borders===" && '
                # Strawman idea: validate the table has 3 borders to ensure it is completed.
                'echo "$s" | grep -- "$border" | wc -l | grep 3'),
        ])
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_sky_down_with_multiple_sgs():
    """Test that sky down works with multiple security groups.

    The goal is to ensure that when we run sky down we get the typical
    terminating output with no extra output. If the output changes please
    update the test.
    """
    name_one = smoke_tests_utils.get_cluster_name()
    vpc_one = "DO_NOT_DELETE"
    name_two = smoke_tests_utils.get_cluster_name() + '-2'
    vpc_two = "DO_NOT_DELETE_lloyd-airgapped-plus-gateway"

    validate_terminating_output = (
        f'printf "%s" "$s" && echo "\n===Validating terminating output===" && '
        # Ensure each terminating line is present.
        f'printf "%s" "$s" | grep "Terminating cluster {name_one}...done" && '
        f'printf "%s" "$s" | grep "Terminating cluster {name_two}...done" && '
        # Ensure the last line is present.
        f'printf "%s" "$s" | grep "Terminating 2 clusters" && '
        # # Ensure there are only 3 lines.
        f'echo "$s" | sed "/^$/d" | wc -l | grep 3')

    test = smoke_tests_utils.Test(
        'sky_down_with_multiple_sgs',
        [
            # Launch cluster one.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name_one} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} --config aws.vpc_name={vpc_one} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Launch cluster two.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name_two} --infra aws/us-west-1 {smoke_tests_utils.LOW_RESOURCE_ARG} --config aws.vpc_name={vpc_two} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Run sky down and validate the output.
            f's=$(SKYPILOT_DEBUG=0 sky down -y {name_one} {name_two} 2>&1) && {validate_terminating_output}',
        ],
        teardown=f'sky down -y {name_one} {name_two}',
        timeout=smoke_tests_utils.get_timeout('aws'),
    )
    smoke_tests_utils.run_one_test(test)


def test_launch_with_failing_setup(generic_cloud: str):
    """Test that failing setup outputs the right error message."""
    name = smoke_tests_utils.get_cluster_name()

    cluster_yaml = textwrap.dedent("""
    num_nodes: 3

    setup: |
        echo "Running setup."
        if [ "$SKYPILOT_SETUP_NODE_RANK" -eq 0 ]; then
            echo "I'm a bad worker, failing..."
            exit 1
        fi
        if [ "$SKYPILOT_SETUP_NODE_RANK" -eq 1 ]; then
            echo "I'm a good worker, passing..."
        fi
        if [ "$SKYPILOT_SETUP_NODE_RANK" -eq 2 ]; then
            echo "I'm a bad worker, failing..."
            exit 1
        fi

    run: |
        echo "Finished"
    """)

    validate_output = (
        f'printf "%s" "$s" && echo "\n===Validating terminating output===" && '
        f'printf "%s" "$s" | grep "See error logs above for more details." && '
        f'printf "%s" "$s" | grep "setup failed. Failed workers: (pid="')

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(cluster_yaml.encode('utf-8'))
        f.flush()

        test = smoke_tests_utils.Test(
            'launch_with_failing_setup',
            [
                f's=$(SKYPILOT_DEBUG=1 sky launch -c {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {f.name} | tee /dev/stderr) && {validate_output}'
            ],
            teardown=f'sky down -y {name}',
            timeout=smoke_tests_utils.get_timeout(generic_cloud),
        )
        smoke_tests_utils.run_one_test(test)
