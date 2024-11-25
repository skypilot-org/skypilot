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

import pathlib
import subprocess
import tempfile
import textwrap
import time

import pytest
from smoke_tests.util import BUMP_UP_SECONDS
from smoke_tests.util import get_cluster_name
from smoke_tests.util import get_cmd_wait_until_cluster_status_contains
from smoke_tests.util import (
    get_cmd_wait_until_job_status_contains_without_matching_job)
from smoke_tests.util import get_timeout
from smoke_tests.util import run_one_test
from smoke_tests.util import SCP_TYPE
from smoke_tests.util import Test
from smoke_tests.util import VALIDATE_LAUNCH_OUTPUT

import sky
from sky.skylet import events
from sky.skylet.job_lib import JobStatus
from sky.status_lib import ClusterStatus
from sky.utils import common_utils


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
    name = get_cluster_name()
    test = Test(
        'minimal',
        [
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml) && {VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Test launch output again on existing cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml) && {VALIDATE_LAUNCH_OUTPUT}',
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
        get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Test fast launch ----------
def test_launch_fast(generic_cloud: str):
    name = get_cluster_name()

    test = Test(
        'test_launch_fast',
        [
            # First launch to create the cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast tests/test_yamls/minimal.yaml) && {VALIDATE_LAUNCH_OUTPUT}',
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
        timeout=get_timeout(generic_cloud),
    )
    run_one_test(test)


# See cloud exclusion explanations in test_autostop
@pytest.mark.no_fluidstack
@pytest.mark.no_lambda_cloud
@pytest.mark.no_ibm
@pytest.mark.no_kubernetes
def test_launch_fast_with_autostop(generic_cloud: str):
    name = get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    test = Test(
        'test_launch_fast_with_autostop',
        [
            # First launch to create the cluster with a short autostop
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast -i 1 tests/test_yamls/minimal.yaml) && {VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',
            f'sky status -r {name} | grep UP',

            # Ensure cluster is stopped
            get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[ClusterStatus.STOPPED],
                timeout=autostop_timeout),
            # Even the cluster is stopped, cloud platform may take a while to
            # delete the VM.
            f'sleep {BUMP_UP_SECONDS}',
            # Launch again. Do full output validation - we expect the cluster to re-launch
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --fast -i 1 tests/test_yamls/minimal.yaml) && {VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=get_timeout(generic_cloud) + autostop_timeout,
    )
    run_one_test(test)


# ------------ Test stale job ------------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
def test_stale_job(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'stale_job',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            f'sky stop {name} -y',
            get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[ClusterStatus.STOPPED],
                timeout=100),
            f'sky start {name} -y',
            f'sky logs {name} 1 --status',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED_DRIVER',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_stale_job_manual_restart():
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.AWS.max_cluster_name_length())
    region = 'us-east-2'
    test = Test(
        'aws_stale_job_manual_restart',
        [
            f'sky launch -y -c {name} --cloud aws --region {region} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            f'id=`aws ec2 describe-instances --region {region} --filters '
            f'Name=tag:ray-cluster-name,Values={name_on_cloud} '
            f'--query Reservations[].Instances[].InstanceId '
            '--output text`; '
            f'aws ec2 stop-instances --region {region} '
            '--instance-ids $id',
            get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[ClusterStatus.STOPPED],
                timeout=40),
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS),
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_stale_job_manual_restart():
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.GCP.max_cluster_name_length())
    zone = 'us-west2-a'
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name={name_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    stop_cmd = (f'gcloud compute instances stop --zone={zone}'
                f' --quiet $({query_cmd})')
    test = Test(
        'gcp_stale_job_manual_restart',
        [
            f'sky launch -y -c {name} --cloud gcp --zone {zone} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            stop_cmd,
            'sleep 40',
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS)
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
@pytest.mark.no_fluidstack  # Requires amazon S3
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_env_check(generic_cloud: str):
    name = get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = Test(
        'env_check',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --detach-setup examples/env_check.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


# ---------- CLI logs ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_logs instead.
def test_cli_logs(generic_cloud: str):
    name = get_cluster_name()
    num_nodes = 2
    if generic_cloud == 'kubernetes':
        # Kubernetes does not support multi-node
        num_nodes = 1
    timestamp = time.time()
    test = Test('cli_logs', [
        f'sky launch -y -c {name} --cloud {generic_cloud} --num-nodes {num_nodes} "echo {timestamp} 1"',
        f'sky exec {name} "echo {timestamp} 2"',
        f'sky exec {name} "echo {timestamp} 3"',
        f'sky exec {name} "echo {timestamp} 4"',
        f'sky logs {name} 2 --status',
        f'sky logs {name} 3 4 --sync-down',
        f'sky logs {name} * --sync-down',
        f'sky logs {name} 1 | grep "{timestamp} 1"',
        f'sky logs {name} | grep "{timestamp} 4"',
    ], f'sky down -y {name}')
    run_one_test(test)


@pytest.mark.scp
def test_scp_logs():
    name = get_cluster_name()
    timestamp = time.time()
    test = Test(
        'SCP_cli_logs',
        [
            f'sky launch -y -c {name} {SCP_TYPE} "echo {timestamp} 1"',
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
    run_one_test(test)


# ------- Testing the core API --------
# Most of the core APIs have been tested in the CLI tests.
# These tests are for testing the return value of the APIs not fully used in CLI.


@pytest.mark.gcp
def test_core_api_sky_launch_exec():
    name = get_cluster_name()
    task = sky.Task(run="whoami")
    task.set_resources(sky.Resources(cloud=sky.GCP()))
    job_id, handle = sky.launch(task, cluster_name=name)
    assert job_id == 1
    assert handle is not None
    assert handle.cluster_name == name
    assert handle.launched_resources.cloud.is_same_cloud(sky.GCP())
    job_id_exec, handle_exec = sky.exec(task, cluster_name=name)
    assert job_id_exec == 2
    assert handle_exec is not None
    assert handle_exec.cluster_name == name
    assert handle_exec.launched_resources.cloud.is_same_cloud(sky.GCP())
    # For dummy task (i.e. task.run is None), the job won't be submitted.
    dummy_task = sky.Task()
    job_id_dummy, _ = sky.exec(dummy_task, cluster_name=name)
    assert job_id_dummy is None
    sky.down(name)


# The sky launch CLI has some additional checks to make sure the cluster is up/
# restarted. However, the core API doesn't have these; make sure it still works
def test_core_api_sky_launch_fast(generic_cloud: str):
    name = get_cluster_name()
    cloud = sky.clouds.CLOUD_REGISTRY.from_str(generic_cloud)
    try:
        task = sky.Task(run="whoami").set_resources(sky.Resources(cloud=cloud))
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
        # Sleep to let the cluster autostop
        get_cmd_wait_until_cluster_status_contains(
            cluster_name=name,
            cluster_status=[ClusterStatus.STOPPED],
            timeout=120)
        # Run it again - should work with fast=True
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
    finally:
        sky.down(name)


# ---------- Testing YAML Specs ----------
# Our sky storage requires credentials to check the bucket existance when
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
        origin_task_config = common_utils.read_yaml(yaml_path)

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
@pytest.mark.no_fluidstack  # Fluidstack does not support K80 gpus for now
@pytest.mark.no_paperspace  # Paperspace does not support K80 gpus
def test_multiple_accelerators_ordered():
    name = get_cluster_name()
    test = Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
def test_multiple_accelerators_ordered_with_default():
    name = get_cluster_name()
    test = Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered_with_default.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep Spot',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
def test_multiple_accelerators_unordered():
    name = get_cluster_name()
    test = Test(
        'multiple-accelerators-unordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
def test_multiple_accelerators_unordered_with_default():
    name = get_cluster_name()
    test = Test(
        'multiple-accelerators-unordered-with-default',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered_with_default.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep Spot',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Requires other clouds to be enabled
def test_multiple_resources():
    name = get_cluster_name()
    test = Test(
        'multiple-resources',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_resources.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Sky Benchmark ----------
@pytest.mark.no_fluidstack  # Requires other clouds to be enabled
@pytest.mark.no_paperspace  # Requires other clouds to be enabled
@pytest.mark.no_kubernetes
@pytest.mark.aws  # SkyBenchmark requires S3 access
def test_sky_bench(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'sky-bench',
        [
            f'sky bench launch -y -b {name} --cloud {generic_cloud} -i0 tests/test_yamls/minimal.yaml',
            'sleep 120',
            f'sky bench show {name} | grep sky-bench-{name} | grep FINISHED',
        ],
        f'sky bench down {name} -y; sky bench delete {name} -y',
    )
    run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_context_failover():
    """Test if the kubernetes context failover works.

    This test requires two kubernetes clusters:
    - kind-skypilot: the local cluster with mock labels for 8 H100 GPUs.
    - another accessible cluster: with enough CPUs
    To start the first cluster, run:
      sky local up
      # Add mock label for accelerator
      kubectl label node --overwrite skypilot-control-plane skypilot.co/accelerator=h100 --context kind-skypilot
      # Get the token for the cluster in context kind-skypilot
      TOKEN=$(kubectl config view --minify --context kind-skypilot -o jsonpath=\'{.users[0].user.token}\')
      # Get the API URL for the cluster in context kind-skypilot
      API_URL=$(kubectl config view --minify --context kind-skypilot -o jsonpath=\'{.clusters[0].cluster.server}\')
      # Add mock capacity for GPU
      curl --header "Content-Type: application/json-patch+json" --header "Authorization: Bearer $TOKEN" --request PATCH --data \'[{"op": "add", "path": "/status/capacity/nvidia.com~1gpu", "value": "8"}]\' "$API_URL/api/v1/nodes/skypilot-control-plane/status"
      # Add a new namespace to test the handling of namespaces
      kubectl create namespace test-namespace --context kind-skypilot
      # Set the namespace to test-namespace
      kubectl config set-context kind-skypilot --namespace=test-namespace --context kind-skypilot
    """
    # Get context that is not kind-skypilot
    contexts = subprocess.check_output('kubectl config get-contexts -o name',
                                       shell=True).decode('utf-8').split('\n')
    context = [context for context in contexts if context != 'kind-skypilot'][0]
    config = textwrap.dedent(f"""\
    kubernetes:
      allowed_contexts:
        - kind-skypilot
        - {context}
    """)
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()
        name = get_cluster_name()
        test = Test(
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
                'sky show-gpus --cloud kubernetes --region kind-skypilot | grep H100 | grep "1, 2, 3, 4, 5, 6, 7, 8"',
                # Get contexts and set current context to the other cluster that is not kind-skypilot
                f'kubectl config use-context {context}',
                # H100 should not in the current context
                '! sky show-gpus --cloud kubernetes | grep H100',
                f'sky launch -y -c {name}-1 --cpus 1 echo hi',
                f'sky logs {name}-1 --status',
                # It should be launched not on kind-skypilot
                f'sky status -a {name}-1 | grep "{context}"',
                # Test failure for launching H100 on other cluster
                f'sky launch -y -c {name}-2 --gpus H100 --cpus 1 --cloud kubernetes --region {context} echo hi && exit 1 || true',
                # Test failover
                f'sky launch -y -c {name}-3 --gpus H100 --cpus 1 --cloud kubernetes echo hi',
                f'sky logs {name}-3 --status',
                # Test pods
                f'kubectl get pods --context kind-skypilot | grep "{name}-3"',
                # It should be launched on kind-skypilot
                f'sky status -a {name}-3 | grep "kind-skypilot"',
                # Should be 7 free GPUs
                f'sky show-gpus --cloud kubernetes --region kind-skypilot | grep H100 | grep "  7"',
                # Remove the line with "kind-skypilot"
                f'sed -i "/kind-skypilot/d" {f.name}',
                # Should still be able to exec and launch on existing cluster
                f'sky exec {name}-3 "echo hi"',
                f'sky logs {name}-3 --status',
                f'sky status -r {name}-3 | grep UP',
                f'sky launch -c {name}-3 --gpus h100 echo hi',
                f'sky logs {name}-3 --status',
                f'sky status -r {name}-3 | grep UP',
            ],
            f'sky down -y {name}-1 {name}-3',
            env={'SKYPILOT_CONFIG': f.name},
        )
        run_one_test(test)
