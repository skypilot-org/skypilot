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

import inspect
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional, Tuple
import urllib.parse
import uuid

import colorama
import jinja2
import pytest

import sky
from sky import global_user_state
from sky import jobs
from sky import serve
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.clouds import AWS
from sky.clouds import Azure
from sky.clouds import GCP
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.data.data_utils import Rclone
from sky.skylet import constants
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import subprocess_utils

# To avoid the second smoke test reusing the cluster launched in the first
# smoke test. Also required for test_managed_jobs_recovery to make sure the
# manual termination with aws ec2 does not accidentally terminate other clusters
# for for the different managed jobs launch with the same job name but a
# different job id.
test_id = str(uuid.uuid4())[-2:]

LAMBDA_TYPE = '--cloud lambda --gpus A10'
FLUIDSTACK_TYPE = '--cloud fluidstack --gpus RTXA4000'

SCP_TYPE = '--cloud scp'
SCP_GPU_V100 = '--gpus V100-32GB'

STORAGE_SETUP_COMMANDS = [
    'touch ~/tmpfile', 'mkdir -p ~/tmp-workdir',
    'touch ~/tmp-workdir/tmp\ file', 'touch ~/tmp-workdir/tmp\ file2',
    'touch ~/tmp-workdir/foo',
    '[ ! -e ~/tmp-workdir/circle-link ] && ln -s ~/tmp-workdir/ ~/tmp-workdir/circle-link || true',
    'touch ~/.ssh/id_rsa.pub'
]

# Wait until the jobs controller is not in INIT state.
# This is a workaround for the issue that when multiple job tests
# are running in parallel, the jobs controller may be in INIT and
# the job queue/cancel command will return staled table.
_JOB_QUEUE_WAIT = ('s=$(sky jobs queue); '
                   'until ! echo "$s" | grep "jobs will not be shown until"; '
                   'do echo "Waiting for job queue to be ready..."; '
                   'sleep 5; s=$(sky jobs queue); done; echo "$s"; '
                   'echo; echo; echo "$s"')
_JOB_CANCEL_WAIT = (
    's=$(sky jobs cancel -y -n {job_name}); '
    'until ! echo "$s" | grep "Please wait for the controller to be ready."; '
    'do echo "Waiting for the jobs controller '
    'to be ready"; sleep 5; s=$(sky jobs cancel -y -n {job_name}); '
    'done; echo "$s"; echo; echo; echo "$s"')
# TODO(zhwu): make the jobs controller on GCP, to avoid parallel test issues
# when the controller being on Azure, which takes a long time for launching
# step.

DEFAULT_CMD_TIMEOUT = 15 * 60


class Test(NamedTuple):
    name: str
    # Each command is executed serially.  If any failed, the remaining commands
    # are not run and the test is treated as failed.
    commands: List[str]
    teardown: Optional[str] = None
    # Timeout for each command in seconds.
    timeout: int = DEFAULT_CMD_TIMEOUT
    # Environment variables to set for each command.
    env: Dict[str, str] = None

    def echo(self, message: str):
        # pytest's xdist plugin captures stdout; print to stderr so that the
        # logs are streaming while the tests are running.
        prefix = f'[{self.name}]'
        message = f'{prefix} {message}'
        message = message.replace('\n', f'\n{prefix} ')
        print(message, file=sys.stderr, flush=True)


def _get_timeout(generic_cloud: str,
                 override_timeout: int = DEFAULT_CMD_TIMEOUT):
    timeouts = {'fluidstack': 60 * 60}  # file_mounts
    return timeouts.get(generic_cloud, override_timeout)


def _get_cluster_name() -> str:
    """Returns a user-unique cluster name for each test_<name>().

    Must be called from each test_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    test_name = common_utils.make_cluster_name_on_cloud(test_name,
                                                        24,
                                                        add_user_hash=False)
    return f'{test_name}-{test_id}'


def _terminate_gcp_replica(name: str, zone: str, replica_id: int) -> str:
    cluster_name = serve.generate_replica_cluster_name(name, replica_id)
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name:{cluster_name})" '
                 f'--zones={zone} --format="value(name)"')
    return (f'gcloud compute instances delete --zone={zone}'
            f' --quiet $({query_cmd})')


def run_one_test(test: Test) -> Tuple[int, str, str]:
    # Fail fast if `sky` CLI somehow errors out.
    subprocess.run(['sky', 'status'], stdout=subprocess.DEVNULL, check=True)
    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)
    test.echo(f'Test started. Log: less {log_file.name}')
    env_dict = os.environ.copy()
    if test.env:
        env_dict.update(test.env)
    for command in test.commands:
        log_file.write(f'+ {command}\n')
        log_file.flush()
        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,
            executable='/bin/bash',
            env=env_dict,
        )
        try:
            proc.wait(timeout=test.timeout)
        except subprocess.TimeoutExpired as e:
            log_file.flush()
            test.echo(f'Timeout after {test.timeout} seconds.')
            test.echo(str(e))
            log_file.write(f'Timeout after {test.timeout} seconds.\n')
            log_file.flush()
            # Kill the current process.
            proc.terminate()
            proc.returncode = 1  # None if we don't set it.
            break

        if proc.returncode:
            break

    style = colorama.Style
    fore = colorama.Fore
    outcome = (f'{fore.RED}Failed{style.RESET_ALL}'
               if proc.returncode else f'{fore.GREEN}Passed{style.RESET_ALL}')
    reason = f'\nReason: {command}' if proc.returncode else ''
    msg = (f'{outcome}.'
           f'{reason}'
           f'\nLog: less {log_file.name}\n')
    test.echo(msg)
    log_file.write(msg)
    if (proc.returncode == 0 or
            pytest.terminate_on_failure) and test.teardown is not None:
        subprocess_utils.run(
            test.teardown,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
            shell=True,
        )

    if proc.returncode:
        raise Exception(f'test failed: less {log_file.name}')


def get_aws_region_for_quota_failover() -> Optional[str]:
    candidate_regions = AWS.regions_with_offering(instance_type='p3.16xlarge',
                                                  accelerators=None,
                                                  use_spot=True,
                                                  region=None,
                                                  zone=None)
    original_resources = sky.Resources(cloud=sky.AWS(),
                                       instance_type='p3.16xlarge',
                                       use_spot=True)

    # Filter the regions with proxy command in ~/.sky/config.yaml.
    filtered_regions = original_resources.get_valid_regions_for_launchable()
    candidate_regions = [
        region for region in candidate_regions
        if region.name in filtered_regions
    ]

    for region in candidate_regions:
        resources = original_resources.copy(region=region.name)
        if not AWS.check_quota_available(resources):
            return region.name

    return None


def get_gcp_region_for_quota_failover() -> Optional[str]:

    candidate_regions = GCP.regions_with_offering(instance_type=None,
                                                  accelerators={'A100-80GB': 1},
                                                  use_spot=True,
                                                  region=None,
                                                  zone=None)

    original_resources = sky.Resources(cloud=sky.GCP(),
                                       instance_type='a2-ultragpu-1g',
                                       accelerators={'A100-80GB': 1},
                                       use_spot=True)

    # Filter the regions with proxy command in ~/.sky/config.yaml.
    filtered_regions = original_resources.get_valid_regions_for_launchable()
    candidate_regions = [
        region for region in candidate_regions
        if region.name in filtered_regions
    ]

    for region in candidate_regions:
        if not GCP.check_quota_available(
                original_resources.copy(region=region.name)):
            return region.name

    return None


# ---------- Dry run: 2 Tasks in a chain. ----------
@pytest.mark.no_fluidstack  #requires GCP and AWS set up
def test_example_app():
    test = Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    run_one_test(test)


_VALIDATE_LAUNCH_OUTPUT = (
    # Validate the output of the job submission:
    # I 05-23 07:52:47 cloud_vm_ray_backend.py:3217] Running setup on 1 node.
    # running setup
    # I 05-23 07:52:49 cloud_vm_ray_backend.py:3230] Setup completed.
    # I 05-23 07:52:55 cloud_vm_ray_backend.py:3319] Job submitted with Job ID: 1
    # I 05-23 07:52:58 log_lib.py:408] Start streaming logs for job 1.
    # INFO: Tip: use Ctrl-C to exit log streaming (task will not be killed).
    # INFO: Waiting for task resources on 1 node. This will block if the cluster is full.
    # INFO: All task resources reserved.
    # INFO: Reserved IPs: ['10.128.0.127']
    # (min, pid=4164) # conda environments:
    # (min, pid=4164) #
    # (min, pid=4164) base                  *  /opt/conda
    # (min, pid=4164)
    # (min, pid=4164) task run finish
    # INFO: Job finished (status: SUCCEEDED).
    'echo "$s" && echo "==Validating setup output==" && '
    'echo "$s" | grep -A 1 "Running setup on" | grep "running setup" && '
    'echo "==Validating running output hints==" && echo "$s" | '
    'grep -A 1 "Job submitted with Job ID:" | '
    'grep "Start streaming logs for job" && '
    'echo "==Validating task output starting==" && echo "$s" | '
    'grep -A 1 "INFO: Reserved IPs" | grep "(min, pid=" && '
    'echo "==Validating task output ending==" && '
    'echo "$s" | grep -A 1 "task run finish" | '
    'grep "INFO: Job finished (status: SUCCEEDED)" && '
    'echo "==Validating task output ending 2==" && '
    'echo "$s" | grep -A 1 "INFO: Job finished (status: SUCCEEDED)" | '
    'grep "Job ID:"')


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


# ---------- Test region ----------
@pytest.mark.aws
def test_aws_region():
    name = _get_cluster_name()
    test = Test(
        'aws_region',
        [
            f'sky launch -y -c {name} --region us-east-2 examples/minimal.yaml',
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-east-2',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-east-2\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} || exit 1\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_region_and_service_account():
    name = _get_cluster_name()
    test = Test(
        'gcp_region',
        [
            f'sky launch -y -c {name} --region us-central1 --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'curl -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?format=standard&audience=gcp"\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-central1',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-central1\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} || exit 1\'',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_region():
    name = _get_cluster_name()
    region = 'eu-de'
    test = Test(
        'region',
        [
            f'sky launch -y -c {name} --cloud ibm --region {region} examples/minimal.yaml',
            f'sky exec {name} --cloud ibm examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep {region}',  # Ensure the region is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_region():
    name = _get_cluster_name()
    test = Test(
        'azure_region',
        [
            f'sky launch -y -c {name} --region eastus2 --cloud azure tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep eastus2',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep eastus2\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .zone | grep null\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} || exit 1\'',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Test zone ----------
@pytest.mark.aws
def test_aws_zone():
    name = _get_cluster_name()
    test = Test(
        'aws_zone',
        [
            f'sky launch -y -c {name} examples/minimal.yaml --zone us-east-2b',
            f'sky exec {name} examples/minimal.yaml --zone us-east-2b',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-east-2b',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_zone():
    name = _get_cluster_name()
    zone = 'eu-de-2'
    test = Test(
        'zone',
        [
            f'sky launch -y -c {name} --cloud ibm examples/minimal.yaml --zone {zone}',
            f'sky exec {name} --cloud ibm examples/minimal.yaml --zone {zone}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep {zone}',  # Ensure the zone is correct.
        ],
        f'sky down -y {name} {name}-2 {name}-3',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_zone():
    name = _get_cluster_name()
    test = Test(
        'gcp_zone',
        [
            f'sky launch -y -c {name} --zone us-central1-a --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky exec {name} --zone us-central1-a --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-central1-a',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Test the image ----------
@pytest.mark.aws
def test_aws_images():
    name = _get_cluster_name()
    test = Test(
        'aws_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i aws\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_images():
    name = _get_cluster_name()
    test = Test(
        'gcp_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-debian-10 --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:cpu-debian-10 --cloud gcp tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i gcp\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_images():
    name = _get_cluster_name()
    test = Test(
        'azure_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-2204 --cloud azure tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:v1-ubuntu-2004 --cloud azure tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i azure\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict():
    name = _get_cluster_name()
    test = Test(
        'aws_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict():
    name = _get_cluster_name()
    test = Test(
        'gcp_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_region():
    name = _get_cluster_name()
    test = Test(
        'aws_image_id_dict_region',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --region us-east-1 examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --region us-east-2 examples/per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-east-2',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --region us-east-2 examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_region():
    name = _get_cluster_name()
    test = Test(
        'gcp_image_id_dict_region',
        [
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --region us-east1 tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --region us-west3 tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --cloud gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-west3',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --region us-west3 tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --cloud gcp --region us-west3 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_zone():
    name = _get_cluster_name()
    test = Test(
        'aws_image_id_dict_zone',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --zone us-east-1b examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --zone us-east-2a examples/per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-east-2a',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --zone us-east-2a examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_zone():
    name = _get_cluster_name()
    test = Test(
        'gcp_image_id_dict_zone',
        [
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --zone us-east1-a tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --zone us-central1-a tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --cloud gcp --image-id skypilot:gpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-central1',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --cloud gcp --zone us-central1-a tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --cloud gcp --region us-central1 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_clone_disk_aws():
    name = _get_cluster_name()
    test = Test(
        'clone_disk_aws',
        [
            f'sky launch -y -c {name} --cloud aws --region us-east-2 --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            'sleep 60',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --cloud aws -d --region us-east-2 "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --cloud aws -d --region us-east-2 "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
def test_clone_disk_gcp():
    name = _get_cluster_name()
    test = Test(
        'clone_disk_gcp',
        [
            f'sky launch -y -c {name} --cloud gcp --zone us-east1-b --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --cloud gcp --zone us-central1-a "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --cloud gcp --zone us-east1-b "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_mig():
    name = _get_cluster_name()
    region = 'us-central1'
    test = Test(
        'gcp_mig',
        [
            f'sky launch -y -c {name} --gpus t4 --num-nodes 2 --image-id skypilot:gpu-debian-10 --cloud gcp --region {region} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check MIG exists.
            f'gcloud compute instance-groups managed list --format="value(name)" | grep "^sky-mig-{name}"',
            f'sky autostop -i 0 --down -y {name}',
            'sleep 120',
            f'sky status -r {name}; sky status {name} | grep "{name} not found"',
            f'gcloud compute instance-templates list | grep "sky-it-{name}"',
            # Launch again with the same region. The original instance template
            # should be removed.
            f'sky launch -y -c {name} --gpus L4 --num-nodes 2 --region {region} nvidia-smi',
            f'sky logs {name} 1 | grep "L4"',
            f'sky down -y {name}',
            f'gcloud compute instance-templates list | grep "sky-it-{name}" && exit 1 || true',
        ],
        f'sky down -y {name}',
        env={'SKYPILOT_CONFIG': 'tests/test_yamls/use_mig_config.yaml'})
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_force_enable_external_ips():
    name = _get_cluster_name()
    test_commands = [
        f'sky launch -y -c {name} --cloud gcp --cpus 2 tests/test_yamls/minimal.yaml',
        # Check network of vm is "default"
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.network)" | grep "networks/default"'),
        # Check External NAT in network access configs, corresponds to external ip
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.accessConfigs[0].name)" | grep "External NAT"'
        ),
        f'sky down -y {name}',
    ]
    skypilot_config = 'tests/test_yamls/force_enable_external_ips_config.yaml'
    test = Test('gcp_force_enable_external_ips',
                test_commands,
                f'sky down -y {name}',
                env={'SKYPILOT_CONFIG': skypilot_config})
    run_one_test(test)


@pytest.mark.aws
def test_image_no_conda():
    name = _get_cluster_name()
    test = Test(
        'image_no_conda',
        [
            # Use image id dict.
            f'sky launch -y -c {name} --region us-east-2 examples/per_region_images.yaml',
            f'sky logs {name} 1 --status',
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
def test_custom_default_conda_env(generic_cloud: str):
    name = _get_cluster_name()
    test = Test('custom_default_conda_env', [
        f'sky launch -c {name} -y --cloud {generic_cloud} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky status -r {name} | grep "UP"',
        f'sky logs {name} 1 --status',
        f'sky logs {name} 1 --no-follow | grep -P "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 2 --status',
        f'sky autostop -y -i 0 {name}',
        'sleep 60',
        f'sky status -r {name} | grep "STOPPED"',
        f'sky start -y {name}',
        f'sky logs {name} 2 --no-follow | grep -P "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 3 --status',
    ], f'sky down -y {name}')
    run_one_test(test)


# ------------ Test stale job ------------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
def test_stale_job(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'stale_job',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            f'sky stop {name} -y',
            'sleep 100',  # Ensure this is large enough, else GCP leaks.
            f'sky start {name} -y',
            f'sky logs {name} 1 --status',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_stale_job_manual_restart():
    name = _get_cluster_name()
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
            'sleep 40',
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            f'sleep {events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_stale_job_manual_restart():
    name = _get_cluster_name()
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
            f'sleep {events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
@pytest.mark.no_fluidstack  # Requires amazon S3
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_env_check(generic_cloud: str):
    name = _get_cluster_name()
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


# ---------- file_mounts ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_file_mounts instead.
def test_file_mounts(generic_cloud: str):
    name = _get_cluster_name()
    extra_flags = ''
    if generic_cloud in 'kubernetes':
        # Kubernetes does not support multi-node
        # NOTE: This test will fail if you have a Kubernetes cluster running on
        #  arm64 (e.g., Apple Silicon) since goofys does not work on arm64.
        extra_flags = '--num-nodes 1'
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} --cloud {generic_cloud} {extra_flags} examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        _get_timeout(generic_cloud, 20 * 60),  # 20 mins
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_file_mounts():
    name = _get_cluster_name()
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} {SCP_TYPE} --num-nodes 1 examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'SCP_using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Requires GCP to be enabled
def test_using_file_mounts_with_env_vars(generic_cloud: str):
    name = _get_cluster_name()
    storage_name = TestStorageWithCredentials.generate_bucket_name()
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
        (f'sky launch -y -c {name} --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name}'),
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        # Override with --env:
        (f'sky launch -y -c {name}-2 --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name} '
         '--env MY_LOCAL_PATH=tmpfile'),
        f'sky logs {name}-2 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts_with_env_vars',
        test_commands,
        (f'sky down -y {name} {name}-2',
         f'sky storage delete -y {storage_name} {storage_name}-2'),
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- storage ----------
@pytest.mark.aws
def test_aws_storage_mounts_with_stop():
    name = _get_cluster_name()
    cloud = 'aws'
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'aws s3 ls {storage_name}/hello.txt',
            f'sky stop -y {name}',
            f'sky start -y {name}',
            # Check if hello.txt from mounting bucket exists after restart in
            # the mounted directory
            f'sky exec {name} -- "set -ex; ls /mount_private_mount/hello.txt"'
        ]
        test = Test(
            'aws_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.gcp
def test_gcp_storage_mounts_with_stop():
    name = _get_cluster_name()
    cloud = 'gcp'
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'gsutil ls gs://{storage_name}/hello.txt',
            f'sky stop -y {name}',
            f'sky start -y {name}',
            # Check if hello.txt from mounting bucket exists after restart in
            # the mounted directory
            f'sky exec {name} -- "set -ex; ls /mount_private_mount/hello.txt"'
        ]
        test = Test(
            'gcp_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.azure
def test_azure_storage_mounts_with_stop():
    name = _get_cluster_name()
    cloud = 'azure'
    storage_name = f'sky-test-{int(time.time())}'
    default_region = 'eastus'
    storage_account_name = (
        storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
            region=default_region, user_hash=common_utils.get_user_hash()))
    storage_account_key = data_utils.get_az_storage_account_key(
        storage_account_name)
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'output=$(az storage blob list -c {storage_name} --account-name {storage_account_name} --account-key {storage_account_key} --prefix hello.txt)'
            # if the file does not exist, az storage blob list returns '[]'
            f'[ "$output" = "[]" ] && exit 1;'
            f'sky stop -y {name}',
            f'sky start -y {name}',
            # Check if hello.txt from mounting bucket exists after restart in
            # the mounted directory
            f'sky exec {name} -- "set -ex; ls /mount_private_mount/hello.txt"'
        ]
        test = Test(
            'azure_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_storage_mounts():
    # Tests bucket mounting on k8s, assuming S3 is configured.
    # This test will fail if run on non x86_64 architecture, since goofys is
    # built for x86_64 only.
    name = _get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud kubernetes {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'aws s3 ls {storage_name}/hello.txt || '
            f'gsutil ls gs://{storage_name}/hello.txt',
        ]
        test = Test(
            'kubernetes_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
    ])
def test_docker_storage_mounts(generic_cloud: str, image_id: str):
    # Tests bucket mounting on docker container
    name = _get_cluster_name()
    timestamp = str(time.time()).replace('.', '')
    storage_name = f'sky-test-{timestamp}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    # ubuntu 18.04 does not support fuse3, and blobfuse2 depends on fuse3.
    azure_mount_unsupported_ubuntu_version = '18.04'
    # Commands to verify bucket upload. We need to check all three
    # storage types because the optimizer may pick any of them.
    s3_command = f'aws s3 ls {storage_name}/hello.txt'
    gsutil_command = f'gsutil ls gs://{storage_name}/hello.txt'
    azure_blob_command = TestStorageWithCredentials.cli_ls_cmd(
        storage_lib.StoreType.AZURE, storage_name, suffix='hello.txt')
    if azure_mount_unsupported_ubuntu_version in image_id:
        # The store for mount_private_mount is not specified in the template.
        # If we're running on Azure, the private mount will be created on
        # azure blob. That will not be supported on the ubuntu 18.04 image
        # and thus fail. For other clouds, the private mount on other
        # storage types (GCS/S3) should succeed.
        include_private_mount = False if generic_cloud == 'azure' else True
        content = template.render(storage_name=storage_name,
                                  include_azure_mount=False,
                                  include_private_mount=include_private_mount)
    else:
        content = template.render(storage_name=storage_name,)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id {image_id} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            # Check AWS, GCP, or Azure storage mount.
            f'{s3_command} || '
            f'{gsutil_command} || '
            f'{azure_blob_command}',
        ]
        test = Test(
            'docker_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.cloudflare
def test_cloudflare_storage_mounts(generic_cloud: str):
    name = _get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_r2_storage_mounting.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name)
    endpoint_url = cloudflare.create_endpoint()
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {generic_cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls s3://{storage_name}/hello.txt --endpoint {endpoint_url} --profile=r2'
        ]

        test = Test(
            'cloudflare_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.ibm
def test_ibm_storage_mounts():
    name = _get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'
    bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
        storage_name, Rclone.RcloneClouds.IBM)
    template_str = pathlib.Path(
        'tests/test_yamls/test_ibm_cos_storage_mounting.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud ibm {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'rclone ls {bucket_rclone_profile}:{storage_name}/hello.txt',
        ]
        test = Test(
            'ibm_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


# ---------- CLI logs ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_logs instead.
def test_cli_logs(generic_cloud: str):
    name = _get_cluster_name()
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
    name = _get_cluster_name()
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


# ---------- Job Queue. ----------
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue instead
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_job_queue instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_oci  # OCI does not have T4 gpus
def test_job_queue(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'job_queue',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 -d examples/job_queue/job.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Job Queue with Docker. ----------
@pytest.mark.no_fluidstack  # FluidStack does not support docker for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_paperspace  # Paperspace doesn't have T4 GPUs
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
        # Axolotl image is a good example custom image that has its conda path
        # set in PATH with dockerfile and uses python>=3.12. It could test:
        #  1. we handle the env var set in dockerfile correctly
        #  2. python>=3.12 works with SkyPilot runtime.
        'docker:winglian/axolotl:main-latest'
    ])
def test_job_queue_with_docker(generic_cloud: str, image_id: str):
    name = _get_cluster_name() + image_id[len('docker:'):][:4]
    total_timeout_minutes = 40 if generic_cloud == 'azure' else 15
    time_to_sleep = 300 if generic_cloud == 'azure' else 180
    test = Test(
        'job_queue_with_docker',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id {image_id} examples/job_queue/cluster_docker.yaml',
            f'sky exec {name} -n {name}-1 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-2 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-3 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            # Make sure the GPU is still visible to the container.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep "Tesla T4"',
            f'sky logs {name} 4 --status',
            f'sky stop -y {name}',
            # Make sure the job status preserve after stop and start the
            # cluster. This is also a test for the docker container to be
            # preserved after stop and start.
            f'sky start -y {name}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep FAILED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep CANCELLED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep CANCELLED',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            # Make sure it is still visible after an stop & start cycle.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep "Tesla T4"',
            f'sky logs {name} 7 --status'
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_job_queue():
    name = _get_cluster_name()
    test = Test(
        'lambda_job_queue',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue():
    name = _get_cluster_name()
    test = Test(
        'ibm_job_queue',
        [
            f'sky launch -y -c {name} --cloud ibm --gpus v100',
            f'sky exec {name} -n {name}-1 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-2 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-3 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_job_queue():
    name = _get_cluster_name()
    num_of_gpu_launch = 1
    num_of_gpu_exec = 0.5
    test = Test(
        'SCP_job_queue',
        [
            f'sky launch -y -c {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue_multinode instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus.
@pytest.mark.no_kubernetes  # Kubernetes not support num_nodes > 1 yet
def test_job_queue_multinode(generic_cloud: str):
    name = _get_cluster_name()
    total_timeout_minutes = 30 if generic_cloud == 'azure' else 15
    test = Test(
        'job_queue_multinode',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/job_queue/cluster_multinode.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job_multinode.yaml',
            f'sky launch -c {name} -n {name}-3 --detach-setup -d examples/job_queue/job_multinode.yaml',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            'sleep 90',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep SETTING_UP',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 --detach-setup -d examples/job_queue/job_multinode.yaml',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            'sleep 5',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
def test_large_job_queue(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --cloud {generic_cloud}',
            f'for i in `seq 1 75`; do sky exec {name} -n {name}-$i -d "echo $i; sleep 100000000"; done',
            f'sky cancel -y {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 90',
            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # The first 16 jobs are canceled, so there should be 75 - 32 = 43 jobs PENDING.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep PENDING | wc -l | grep 43',
            # Make sure the jobs are scheduled in FIFO order
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(1, 17)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in range(17, 33)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep PENDING'
                for i in range(33, 75)
            ],
            f'sky cancel -y {name} 33 35 37 39 17 18 19',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(33, 40, 2)
            ],
            'sleep 10',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in [34, 36, 38]
            ],
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
def test_fast_large_job_queue(generic_cloud: str):
    # This is to test the jobs can be scheduled quickly when there are many jobs in the queue.
    name = _get_cluster_name()
    test = Test(
        'fast_large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --cloud {generic_cloud}',
            f'for i in `seq 1 32`; do sky exec {name} -n {name}-$i -d "echo $i"; done',
            'sleep 60',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep SUCCEEDED | wc -l | grep 32',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue_multinode():
    name = _get_cluster_name()
    task_file = 'examples/job_queue/job_multinode_ibm.yaml'
    test = Test(
        'ibm_job_queue_multinode',
        [
            f'sky launch -y -c {name} --cloud ibm --gpus v100 --num-nodes 2',
            f'sky exec {name} -n {name}-1 -d {task_file}',
            f'sky exec {name} -n {name}-2 -d {task_file}',
            f'sky launch -y -c {name} -n {name}-3 --detach-setup -d {task_file}',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep SETTING_UP)',
            'sleep 90',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 --detach-setup -d {task_file}',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus v100:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- Docker with preinstalled package. ----------
@pytest.mark.no_fluidstack  # Doesn't support Fluidstack for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
# TODO(zhwu): we should fix this for kubernetes
def test_docker_preinstalled_package(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'docker_with_preinstalled_package',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id docker:nginx',
            f'sky exec {name} "nginx -V"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus
def test_multi_echo(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'multi_echo',
        [
            f'python examples/multi_echo.py {name} {generic_cloud}',
            'sleep 120',
        ] +
        # Ensure jobs succeeded.
        [f'sky logs {name} {i + 1} --status' for i in range(32)] +
        # Ensure monitor/autoscaler didn't crash on the 'assert not
        # unfulfilled' error.  If process not found, grep->ssh returns 1.
        [f'ssh {name} \'ps aux | grep "[/]"monitor.py\''],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Task: 1 node training. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not have V100 (16GB) GPUs. Run test_scp_huggingface instead.
def test_huggingface(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_huggingface(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'lambda_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_huggingface(generic_cloud: str):
    name = _get_cluster_name()
    num_of_gpu_launch = 1
    test = Test(
        'SCP_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Inferentia. ----------
@pytest.mark.aws
def test_inferentia():
    name = _get_cluster_name()
    test = Test(
        'test_inferentia',
        [
            f'sky launch -y -c {name} -t inf2.xlarge -- echo hi',
            f'sky exec {name} --gpus Inferentia:1 echo hi',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- TPU. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu():
    name = _get_cluster_name()
    test = Test(
        'tpu_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpu_app.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} examples/tpu/tpu_app.yaml | grep "TPU .* already exists"',  # Ensure sky launch won't create another TPU.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take >20 mins
    )
    run_one_test(test)


# ---------- TPU VM. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm():
    name = _get_cluster_name()
    test = Test(
        'tpu_vm_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',  # Ensure the cluster is STOPPED.
            # Use retry: guard against transient errors observed for
            # just-stopped TPU VMs (#962).
            f'sky start --retry-until-up -y {name}',
            f'sky exec {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    run_one_test(test)


# ---------- TPU VM Pod. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm_pod():
    name = _get_cluster_name()
    test = Test(
        'tpu_pod',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --gpus tpu-v2-32 --use-spot --zone europe-west4-a',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    run_one_test(test)


# ---------- Simple apps. ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_multi_hostname(generic_cloud: str):
    name = _get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = Test(
        'multi_hostname',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/multi_hostname.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 1 | grep "My hostname:" | wc -l | grep 2',  # Ensure there are 2 hosts.
            f'sky exec {name} examples/multi_hostname.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=_get_timeout(generic_cloud, total_timeout_minutes * 60),
    )
    run_one_test(test)


@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_multi_node_failure(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'multi_node_failure',
        [
            # TODO(zhwu): we use multi-thread to run the commands in setup
            # commands in parallel, which makes it impossible to fail fast
            # when one of the nodes fails. We should fix this in the future.
            # The --detach-setup version can fail fast, as the setup is
            # submitted to the remote machine, which does not use multi-thread.
            # Refer to the comment in `subprocess_utils.run_in_parallel`.
            # f'sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/failed_worker_setup.yaml && exit 1',  # Ensure the job setup failed.
            f'sky launch -y -c {name} --cloud {generic_cloud} --detach-setup tests/test_yamls/failed_worker_setup.yaml',
            f'sky logs {name} 1 --status | grep FAILED_SETUP',  # Ensure the job setup failed.
            f'sky exec {name} tests/test_yamls/failed_worker_run.yaml',
            f'sky logs {name} 2 --status | grep FAILED',  # Ensure the job failed.
            f'sky logs {name} 2 | grep "My hostname:" | wc -l | grep 2',  # Ensure there 2 of the hosts printed their hostname.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on GCP. ----------
@pytest.mark.gcp
def test_gcp_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'gcp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud gcp examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on AWS. ----------
@pytest.mark.aws
def test_aws_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'aws_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud aws examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Azure. ----------
@pytest.mark.azure
def test_azure_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'azure_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud azure examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Kubernetes. ----------
@pytest.mark.kubernetes
def test_kubernetes_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'kubernetes_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud kubernetes examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 100); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 5; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Paperspace. ----------
@pytest.mark.paperspace
def test_paperspace_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'paperspace_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud paperspace examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on RunPod. ----------
@pytest.mark.runpod
def test_runpod_http_server_with_custom_ports():
    name = _get_cluster_name()
    test = Test(
        'runpod_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud runpod examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Labels from task on AWS (instance_tags) ----------
@pytest.mark.aws
def test_task_labels_aws():
    name = _get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='aws', region='us-east-1')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_aws',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with aws cli that the tags are set.
                'aws ec2 describe-instances '
                '--query "Reservations[*].Instances[*].InstanceId" '
                '--filters "Name=instance-state-name,Values=running" '
                f'--filters "Name=tag:skypilot-cluster-name,Values={name}*" '
                '--filters "Name=tag:inlinelabel1,Values=inlinevalue1" '
                '--filters "Name=tag:inlinelabel2,Values=inlinevalue2" '
                '--region us-east-1 --output text',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Labels from task on GCP (labels) ----------
@pytest.mark.gcp
def test_task_labels_gcp():
    name = _get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='gcp')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_gcp',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with gcloud cli that the tags are set
                f'gcloud compute instances list --filter="name~\'^{name}\' AND '
                'labels.inlinelabel1=\'inlinevalue1\' AND '
                'labels.inlinelabel2=\'inlinevalue2\'" '
                '--format="value(name)" | grep .',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Labels from task on Kubernetes (labels) ----------
@pytest.mark.kubernetes
def test_task_labels_kubernetes():
    name = _get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='kubernetes')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_kubernetes',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with kubectl that the labels are set.
                'kubectl get pods '
                '--selector inlinelabel1=inlinevalue1 '
                '--selector inlinelabel2=inlinevalue2 '
                '-o jsonpath=\'{.items[*].metadata.name}\' | '
                f'grep \'^{name}\''
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.skip(
    reason=
    'The resnet_distributed_tf_app is flaky, due to it failing to detect GPUs.')
def test_distributed_tf(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            f'python examples/resnet_distributed_tf_app.py {name} {generic_cloud}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
@pytest.mark.gcp
def test_gcp_start_stop():
    name = _get_cluster_name()
    test = Test(
        'gcp-start-stop',
        [
            f'sky launch -y -c {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sleep 20',
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
            'sleep 180',
            f'sky status -r {name} | grep "INIT\|STOPPED"',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
@pytest.mark.azure
def test_azure_start_stop():
    name = _get_cluster_name()
    test = Test(
        'azure-start-stop',
        [
            f'sky launch -y -c {name} examples/azure_start_stop.yaml',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            'sleep 260',
            f's=$(sky status -r {name}) && echo "$s" && echo "$s" | grep "INIT\|STOPPED"'
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    run_one_test(test)


# ---------- Testing Autostopping ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
def test_autostop(generic_cloud: str):
    name = _get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    # Launching and starting Azure clusters can take a long time too. e.g., restart
    # a stopped Azure cluster can take 7m. So we set the total timeout to 70m.
    total_timeout_minutes = 70 if generic_cloud == 'azure' else 20
    test = Test(
        'autostop',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} -i 1',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the cluster is not stopped early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',

            # Ensure the cluster is STOPPED.
            f'sleep {autostop_timeout}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',

            # Ensure the cluster is UP and the autostop setting is reset ('-').
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',

            # Ensure the job succeeded.
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',

            # Test restarting the idleness timer via reset:
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 40',  # Almost reached the threshold.
            f'sky autostop -y {name} -i 1',  # Should restart the timer.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s" | grep {name} | grep UP',
            f'sleep {autostop_timeout}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',

            # Test restarting the idleness timer via exec:
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky exec {name} echo hi',  # Should restart the timer.
            'sleep 45',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',
            f'sleep {autostop_timeout}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


# ---------- Testing Autodowning ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_autodown instead.
def test_autodown(generic_cloud: str):
    name = _get_cluster_name()
    # Azure takes ~ 13m30s (810s) to autodown a VM, so here we use 900 to ensure
    # the VM is terminated.
    autodown_timeout = 900 if generic_cloud == 'azure' else 240
    total_timeout_minutes = 90 if generic_cloud == 'azure' else 20
    test = Test(
        'autodown',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            f'sleep {autodown_timeout}',
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud {generic_cloud} --num-nodes 2 --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            f'sleep {autodown_timeout}',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud {generic_cloud} --num-nodes 2 --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            f'sleep {autodown_timeout}',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_autodown():
    name = _get_cluster_name()
    test = Test(
        'SCP_autodown',
        [
            f'sky launch -y -d -c {name} {SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} {SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 200',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 200',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


def _get_cancel_task_with_cloud(name, cloud, timeout=15 * 60):
    test = Test(
        f'{cloud}-cancel-task',
        [
            f'sky launch -c {name} examples/resnet_app.yaml --cloud {cloud} -y -d',
            # Wait the GPU process to start.
            'sleep 60',
            f'sky exec {name} "nvidia-smi | grep python"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            # check if the python job is gone.
            f'sky exec {name} "! nvidia-smi | grep python"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=timeout,
    )
    return test


# ---------- Testing `sky cancel` ----------
@pytest.mark.aws
@pytest.mark.skip(
    reason='The resnet_app is flaky, due to TF failing to detect GPUs.')
def test_cancel_aws():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'aws')
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.skip(
    reason='The resnet_app is flaky, due to TF failing to detect GPUs.')
def test_cancel_gcp():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'gcp')
    run_one_test(test)


@pytest.mark.azure
@pytest.mark.skip(
    reason='The resnet_app is flaky, due to TF failing to detect GPUs.')
def test_cancel_azure():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'azure', timeout=30 * 60)
    run_one_test(test)


@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_paperspace  # Paperspace has `gnome-shell` on nvidia-smi
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_cancel_pytorch(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'cancel-pytorch',
        [
            f'sky launch -c {name} --cloud {generic_cloud} examples/resnet_distributed_torch.yaml -y -d',
            # Wait the GPU process to start.
            'sleep 90',
            f'sky exec {name} "(nvidia-smi | grep python) || '
            # When run inside container/k8s, nvidia-smi cannot show process ids.
            # See https://github.com/NVIDIA/nvidia-docker/issues/179
            # To work around, we check if GPU utilization is greater than 0.
            f'[ \$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -gt 0 ]"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            f'sky exec {name} "(nvidia-smi | grep \'No running process\') || '
            # Ensure Xorg is the only process running.
            '[ \$(nvidia-smi | grep -A 10 Processes | grep -A 10 === | grep -v Xorg) -eq 2 ]"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# can't use `_get_cancel_task_with_cloud()`, as command `nvidia-smi`
# requires a CUDA public image, which IBM doesn't offer
@pytest.mark.ibm
def test_cancel_ibm():
    name = _get_cluster_name()
    test = Test(
        'ibm-cancel-task',
        [
            f'sky launch -y -c {name} --cloud ibm examples/minimal.yaml',
            f'sky exec {name} -n {name}-1 -d  "while true; do echo \'Hello SkyPilot\'; sleep 2; done"',
            'sleep 20',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky cancel -y {name} 2',
            f'sleep 5',
            f'sky queue {name} | grep {name}-1 | grep CANCELLED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing use-spot option ----------
@pytest.mark.no_fluidstack  # FluidStack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
def test_use_spot(generic_cloud: str):
    """Test use-spot and sky exec."""
    name = _get_cluster_name()
    test = Test(
        'use-spot',
        [
            f'sky launch -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status',
            f'sky exec {name} echo hi',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_stop_gcp_spot():
    """Test GCP spot can be stopped, autostopped, restarted."""
    name = _get_cluster_name()
    test = Test(
        'stop_gcp_spot',
        [
            f'sky launch -c {name} --cloud gcp --use-spot --cpus 2+ -y -- touch myfile',
            # stop should go through:
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 2 --status',
            f'sky autostop {name} -i0 -y',
            'sleep 90',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 3 --status',
            # -i option at launch should go through:
            f'sky launch -c {name} -i0 -y',
            'sleep 120',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing managed job ----------
@pytest.mark.managed_jobs
def test_managed_jobs(generic_cloud: str):
    """Test the managed jobs yaml."""
    name = _get_cluster_name()
    test = Test(
        'managed-jobs',
        [
            f'sky jobs launch -n {name}-1 --cloud {generic_cloud} examples/managed_job.yaml -y -d',
            f'sky jobs launch -n {name}-2 --cloud {generic_cloud} examples/managed_job.yaml -y -d',
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep "STARTING\|RUNNING"',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "STARTING\|RUNNING"',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}-1'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep CANCELLED',
            # Test the functionality for logging.
            f's=$(sky jobs logs -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "start counting"',
            f's=$(sky jobs logs --controller -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "Successfully provisioned cluster:"',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        # TODO(zhwu): Change to _JOB_CANCEL_WAIT.format(job_name=f'{name}-1 -n {name}-2') when
        # canceling multiple job names is supported.
        (_JOB_CANCEL_WAIT.format(job_name=f'{name}-1') + '; ' +
         _JOB_CANCEL_WAIT.format(job_name=f'{name}-2')),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_job_pipeline(generic_cloud: str):
    """Test a job pipeline."""
    name = _get_cluster_name()
    test = Test(
        'spot-pipeline',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline.yaml -y -d',
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING\|RUNNING"',
            # `grep -A 4 {name}` finds the job with {name} and the 4 lines
            # after it, i.e. the 4 tasks within the job.
            # `sed -n 2p` gets the second line of the 4 lines, i.e. the first
            # task within the job.
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "STARTING\|RUNNING"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "PENDING"',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "CANCELLING\|CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "CANCELLING\|CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 4p | grep "CANCELLING\|CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 5p | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            f'{_JOB_QUEUE_WAIT}| grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=f'{name}'),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_failed_setup(generic_cloud: str):
    """Test managed job with failed setup."""
    name = _get_cluster_name()
    test = Test(
        'managed_jobs_failed_setup',
        [
            f'sky jobs launch -n {name} --cloud {generic_cloud} -y -d tests/test_yamls/failed_setup.yaml',
            'sleep 330',
            # Make sure the job failed quickly.
            f'{_JOB_QUEUE_WAIT} | grep {name} | head -n1 | grep "FAILED_SETUP"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_failed_setup(generic_cloud: str):
    """Test managed job with failed setup for a pipeline."""
    name = _get_cluster_name()
    test = Test(
        'managed_jobs_pipeline_failed_setup',
        [
            f'sky jobs launch -n {name} -y -d tests/test_yamls/failed_setup_pipeline.yaml',
            'sleep 600',
            # Make sure the job failed quickly.
            f'{_JOB_QUEUE_WAIT} | grep {name} | head -n1 | grep "FAILED_SETUP"',
            # Task 0 should be SUCCEEDED.
            f'{_JOB_QUEUE_WAIT} | grep -A 4 {name}| sed -n 2p | grep "SUCCEEDED"',
            # Task 1 should be FAILED_SETUP.
            f'{_JOB_QUEUE_WAIT} | grep -A 4 {name}| sed -n 3p | grep "FAILED_SETUP"',
            # Task 2 should be CANCELLED.
            f'{_JOB_QUEUE_WAIT} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            # Task 3 should be CANCELLED.
            f'{_JOB_QUEUE_WAIT} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


# ---------- Testing managed job recovery ----------


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_aws(aws_config_region):
    """Test managed job recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_recovery_aws',
        [
            f'sky jobs launch --cloud aws --region {region} --use-spot -n {name} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 360',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 100',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | grep "$RUN_ID"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_gcp():
    """Test managed job recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-east4-b'
    query_cmd = (
        f'gcloud compute instances list --filter='
        # `:` means prefix match.
        f'"(labels.ray-cluster-name:{name_on_cloud})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_recovery_gcp',
        [
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot --cpus 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 360',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            terminate_cmd,
            'sleep 60',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_aws(aws_config_region):
    """Test managed job recovery for a pipeline."""
    name = _get_cluster_name()
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    region = aws_config_region
    if region != 'us-east-2':
        pytest.skip('Only run spot pipeline recovery test in us-east-2')
    test = Test(
        'managed_jobs_pipeline_recovery_aws',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline_aws.yaml  -y -d',
            'sleep 400',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (
                f'MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
                'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`;'
                f'aws ec2 terminate-instances --region {region} --instance-ids $('
                f'aws ec2 describe-instances --region {region} '
                # TODO(zhwu): fix the name for spot cluster.
                '--filters Name=tag:ray-cluster-name,Values=*-${MANAGED_JOB_ID}'
                f'-{user_hash} '
                f'--query Reservations[].Instances[].InstanceId '
                '--output text)'),
            'sleep 100',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_gcp():
    """Test managed job recovery for a pipeline."""
    name = _get_cluster_name()
    zone = 'us-east4-b'
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    query_cmd = (
        'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:*-${{MANAGED_JOB_ID}}-{user_hash})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_pipeline_recovery_gcp',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline_gcp.yaml  -y -d',
            'sleep 400',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (f'MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
             f'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`; {terminate_cmd}'),
            'sleep 60',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_default_resources(generic_cloud: str):
    """Test managed job recovery for default resources."""
    name = _get_cluster_name()
    test = Test(
        'managed-spot-recovery-default-resources',
        [
            f'sky jobs launch -n {name} --cloud {generic_cloud} --use-spot "sleep 30 && sudo shutdown now && sleep 1000" -y -d',
            'sleep 360',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING\|RECOVERING"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_aws(aws_config_region):
    """Test managed job recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_recovery_multi_node_aws',
        [
            f'sky jobs launch --cloud aws --region {region} -n {name} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 450',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             'Name=tag:ray-node-type,Values=worker '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 50',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 560',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_gcp():
    """Test managed job recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-west2-a'
    # Use ':' to match as the cluster name will contain the suffix with job id
    query_cmd = (
        f'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:{name_on_cloud} AND '
        f'labels.ray-node-type=worker)" --zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_recovery_multi_node_gcp',
        [
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 400',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            terminate_cmd,
            'sleep 50',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 420',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_aws(aws_config_region):
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_2_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-2', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-3', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_cancellation_aws',
        [
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --cloud aws --region {region} -n {name} --use-spot "sleep 1000"  -y -d',
            'sleep 60',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING"',
            _JOB_CANCEL_WAIT.format(job_name=name),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLED"',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --cloud aws --region {region} -n {name}-2 --use-spot tests/test_yamls/test_long_setup.yaml  -y -d',
            'sleep 300',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}-2'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLED"',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_2_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --cloud aws --region {region} -n {name}-3 --use-spot "sleep 1000"  -y -d',
            'sleep 300',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_3_on_cloud}-* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RECOVERING"',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}-3'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLED"',
            # The cluster should be terminated (shutting-down) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_3_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "pending|running|stopped|stopping"'
            ),
        ],
        timeout=25 * 60)
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_gcp():
    name = _get_cluster_name()
    name_3 = f'{name}-3'
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        name_3, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-west3-b'
    query_state_cmd = (
        'gcloud compute instances list '
        f'--filter="(labels.ray-cluster-name:{name_3_on_cloud})" '
        '--format="value(status)"')
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name:{name_3_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_cancellation_gcp',
        [
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot "sleep 1000"  -y -d',
            'sleep 60',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING"',
            _JOB_CANCEL_WAIT.format(job_name=name),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLED"',
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name}-2 --use-spot tests/test_yamls/test_long_setup.yaml  -y -d',
            'sleep 300',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}-2'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLED"',
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name}-3 --use-spot "sleep 1000"  -y -d',
            'sleep 300',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            terminate_cmd,
            'sleep 80',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RECOVERING"',
            _JOB_CANCEL_WAIT.format(job_name=f'{name}-3'),
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_JOB_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLED"',
            # The cluster should be terminated (STOPPING) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$({query_state_cmd}) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "PROVISIONING|STAGING|RUNNING|REPAIRING|TERMINATED|SUSPENDING|SUSPENDED|SUSPENDED"'
            ),
        ],
        timeout=25 * 60)
    run_one_test(test)


# ---------- Testing storage for managed job ----------
@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_storage(generic_cloud: str):
    """Test storage with managed job"""
    name = _get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_job_with_storage.yaml').read_text()
    timestamp = int(time.time())
    storage_name = f'sky-test-{timestamp}'
    output_storage_name = f'sky-test-output-{timestamp}'

    # Also perform region testing for bucket creation to validate if buckets are
    # created in the correct region and correctly mounted in managed jobs.
    # However, we inject this testing only for AWS and GCP since they are the
    # supported object storage providers in SkyPilot.
    region_flag = ''
    region_validation_cmd = 'true'
    use_spot = ' --use-spot'
    if generic_cloud == 'aws':
        region = 'eu-central-1'
        region_flag = f' --region {region}'
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.S3, bucket_name=storage_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        s3_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        output_check_cmd = f'{s3_check_file_count} | grep 1'
    elif generic_cloud == 'gcp':
        region = 'us-west2'
        region_flag = f' --region {region}'
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.GCS, bucket_name=storage_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        gcs_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        output_check_cmd = f'{gcs_check_file_count} | grep 1'
    elif generic_cloud == 'azure':
        region = 'westus2'
        region_flag = f' --region {region}'
        storage_account_name = (
            storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                region=region, user_hash=common_utils.get_user_hash()))
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.AZURE,
            storage_account_name=storage_account_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        az_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.AZURE,
            output_storage_name,
            'output.txt',
            storage_account_name=storage_account_name)
        output_check_cmd = f'{az_check_file_count} | grep 1'
    elif generic_cloud == 'kubernetes':
        # With Kubernetes, we don't know which object storage provider is used.
        # Check both S3 and GCS if bucket exists in either.
        s3_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        s3_output_check_cmd = f'{s3_check_file_count} | grep 1'
        gcs_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        gcs_output_check_cmd = f'{gcs_check_file_count} | grep 1'
        output_check_cmd = f'{s3_output_check_cmd} || {gcs_output_check_cmd}'
        use_spot = ' --no-use-spot'

    yaml_str = yaml_str.replace('sky-workdir-zhwu', storage_name)
    yaml_str = yaml_str.replace('sky-output-bucket', output_storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_str)
        f.flush()
        file_path = f.name
        test = Test(
            'managed_jobs_storage',
            [
                *STORAGE_SETUP_COMMANDS,
                f'sky jobs launch -n {name}{use_spot} --cloud {generic_cloud}{region_flag} {file_path} -y',
                region_validation_cmd,  # Check if the bucket is created in the correct region
                'sleep 60',  # Wait the spot queue to be updated
                f'{_JOB_QUEUE_WAIT}| grep {name} | grep SUCCEEDED',
                f'[ $(aws s3api list-buckets --query "Buckets[?contains(Name, \'{storage_name}\')].Name" --output text | wc -l) -eq 0 ]',
                # Check if file was written to the mounted output bucket
                output_check_cmd
            ],
            (_JOB_CANCEL_WAIT.format(job_name=name),
             f'; sky storage delete {output_storage_name} || true'),
            # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
            timeout=20 * 60,
        )
        run_one_test(test)


# ---------- Testing spot TPU ----------
@pytest.mark.gcp
@pytest.mark.managed_jobs
@pytest.mark.tpu
def test_managed_jobs_tpu():
    """Test managed job on TPU."""
    name = _get_cluster_name()
    test = Test(
        'test-spot-tpu',
        [
            f'sky jobs launch -n {name} --use-spot examples/tpu/tpuvm_mnist.yaml -y -d',
            'sleep 5',
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep STARTING',
            'sleep 900',  # TPU takes a while to launch
            f'{_JOB_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing env for managed jobs ----------
@pytest.mark.managed_jobs
def test_managed_jobs_inline_env(generic_cloud: str):
    """Test managed jobs env"""
    name = _get_cluster_name()
    test = Test(
        'test-managed-jobs-inline-env',
        [
            f'sky jobs launch -n {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            'sleep 20',
            f'{_JOB_QUEUE_WAIT} | grep {name} | grep SUCCEEDED',
        ],
        _JOB_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing env ----------
def test_inline_env(generic_cloud: str):
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            'sleep 20',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        _get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Testing env file ----------
def test_inline_env_file(generic_cloud: str):
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-env-file',
        [
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env-file examples/sample_dotenv "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        _get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Testing custom image ----------
@pytest.mark.aws
def test_aws_custom_image():
    """Test AWS custom image"""
    name = _get_cluster_name()
    test = Test(
        'test-aws-custom-image',
        [
            f'sky launch -c {name} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --cloud aws --region us-east-2 --image-id ami-062ddd90fb6f8267a',  # Nvidia image
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
    ])
def test_kubernetes_custom_image(image_id):
    """Test Kubernetes custom image"""
    name = _get_cluster_name()
    test = Test(
        'test-kubernetes-custom-image',
        [
            f'sky launch -c {name} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --cloud kubernetes --image-id {image_id} --region None --gpus T4:1',
            f'sky logs {name} 1 --status',
            # Try exec to run again and check if the logs are printed
            f'sky exec {name} tests/test_yamls/test_custom_image.yaml --cloud kubernetes --image-id {image_id} --region None --gpus T4:1 | grep "Hello 100"',
            # Make sure ssh is working with custom username
            f'ssh {name} echo hi | grep hi',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_start_stop_two_nodes():
    name = _get_cluster_name()
    test = Test(
        'azure-start-stop-two-nodes',
        [
            f'sky launch --num-nodes=2 -y -c {name} examples/azure_start_stop.yaml',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            'sleep 200',
            f's=$(sky status -r {name}) && echo "$s" && echo "$s" | grep "INIT\|STOPPED"'
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    )
    run_one_test(test)


# ---------- Testing env for disk tier ----------
@pytest.mark.aws
def test_aws_disk_tier():

    def _get_aws_query_command(region, instance_id, field, expected):
        return (f'aws ec2 describe-volumes --region {region} '
                f'--filters Name=attachment.instance-id,Values={instance_id} '
                f'--query Volumes[*].{field} | grep {expected} ; ')

    for disk_tier in list(resources_utils.DiskTier):
        specs = AWS._get_disk_specs(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.AWS.max_cluster_name_length())
        region = 'us-east-2'
        test = Test(
            'aws-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --cloud aws --region {region} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'id=`aws ec2 describe-instances --region {region} --filters '
                f'Name=tag:ray-cluster-name,Values={name_on_cloud} --query '
                f'Reservations[].Instances[].InstanceId --output text`; ' +
                _get_aws_query_command(region, '$id', 'VolumeType',
                                       specs['disk_tier']) +
                ('' if disk_tier == resources_utils.DiskTier.LOW else
                 (_get_aws_query_command(region, '$id', 'Iops',
                                         specs['disk_iops']) +
                  _get_aws_query_command(region, '$id', 'Throughput',
                                         specs['disk_throughput']))),
            ],
            f'sky down -y {name}',
            timeout=10 * 60,  # 10 mins  (it takes around ~6 mins)
        )
        run_one_test(test)


@pytest.mark.gcp
def test_gcp_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        type = GCP._get_disk_type(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.GCP.max_cluster_name_length())
        region = 'us-west2'
        test = Test(
            'gcp-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --cloud gcp --region {region} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'name=`gcloud compute instances list --filter='
                f'"labels.ray-cluster-name:{name_on_cloud}" '
                '--format="value(name)"`; '
                f'gcloud compute disks list --filter="name=$name" '
                f'--format="value(type)" | grep {type} '
            ],
            f'sky down -y {name}',
            timeout=6 * 60,  # 6 mins  (it takes around ~3 mins)
        )
        run_one_test(test)


@pytest.mark.azure
def test_azure_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        if disk_tier == resources_utils.DiskTier.HIGH:
            # Azure does not support high disk tier.
            continue
        type = Azure._get_disk_type(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.Azure.max_cluster_name_length())
        region = 'westus2'
        test = Test(
            'azure-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --cloud azure --region {region} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
                f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
                f'--output tsv | grep {type}'
            ],
            f'sky down -y {name}',
            timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
        )
        run_one_test(test)


@pytest.mark.azure
def test_azure_best_tier_failover():
    type = Azure._get_disk_type(resources_utils.DiskTier.LOW)
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Azure.max_cluster_name_length())
    region = 'westus2'
    test = Test(
        'azure-best-tier-failover',
        [
            f'sky launch -y -c {name} --cloud azure --region {region} '
            f'--disk-tier best --instance-type Standard_D8_v5 echo "hello sky"',
            f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
            f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
            f'--output tsv | grep {type}',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
    )
    run_one_test(test)


# ------ Testing Zero Quota Failover ------
@pytest.mark.aws
def test_aws_zero_quota_failover():

    name = _get_cluster_name()
    region = get_aws_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for EC2 P3 instances were found on all AWS regions. Is this '
            'expected for your account?')
        return

    test = Test(
        'aws-zero-quota-failover',
        [
            f'sky launch -y -c {name} --cloud aws --region {region} --gpus V100:8 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_zero_quota_failover():

    name = _get_cluster_name()
    region = get_gcp_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for A100-80GB GPUs were found on all GCP regions. Is this '
            'expected for your account?')
        return

    test = Test(
        'gcp-zero-quota-failover',
        [
            f'sky launch -y -c {name} --cloud gcp --region {region} --gpus A100-80GB:1 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing skyserve ----------


def _get_service_name() -> str:
    """Returns a user-unique service name for each test_skyserve_<name>().

    Must be called from each test_skyserve_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    test_name = test_name.replace('skyserve-', 'ss-')
    test_name = common_utils.make_cluster_name_on_cloud(test_name, 24)
    return f'{test_name}-{test_id}'


# We check the output of the skyserve service to see if it is ready. Output of
# `REPLICAS` is in the form of `1/2` where the first number is the number of
# ready replicas and the second number is the number of total replicas. We
# grep such format to ensure that the service is ready, and early exit if any
# failure detected. In the end we sleep for
# serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS to make sure load balancer have
# enough time to sync with the controller and get all ready replica IPs.
_SERVE_WAIT_UNTIL_READY = (
    '{{ while true; do'
    '     s=$(sky serve status {name}); echo "$s";'
    '     echo "$s" | grep -q "{replica_num}/{replica_num}" && break;'
    '     echo "$s" | grep -q "FAILED" && exit 1;'
    '     sleep 10;'
    ' done; }}; echo "Got service status $s";'
    f'sleep {serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS + 2};')
_IP_REGEX = r'([0-9]{1,3}\.){3}[0-9]{1,3}'
_AWK_ALL_LINES_BELOW_REPLICAS = r'/Replicas/{flag=1; next} flag'
_SERVICE_LAUNCHING_STATUS_REGEX = 'PROVISIONING\|STARTING'
# Since we don't allow terminate the service if the controller is INIT,
# which is common for simultaneous pytest, we need to wait until the
# controller is UP before we can terminate the service.
# The teardown command has a 10-mins timeout, so we don't need to do
# the timeout here. See implementation of run_one_test() for details.
_TEARDOWN_SERVICE = (
    '(for i in `seq 1 20`; do'
    '     s=$(sky serve down -y {name});'
    '     echo "Trying to terminate {name}";'
    '     echo "$s";'
    '     echo "$s" | grep -q "scheduled to be terminated\|No service to terminate" && break;'
    '     sleep 10;'
    '     [ $i -eq 20 ] && echo "Failed to terminate service {name}";'
    'done)')

_SERVE_ENDPOINT_WAIT = (
    'export ORIGIN_SKYPILOT_DEBUG=$SKYPILOT_DEBUG; export SKYPILOT_DEBUG=0; '
    'endpoint=$(sky serve status --endpoint {name}); '
    'until ! echo "$endpoint" | grep "Controller is initializing"; '
    'do echo "Waiting for serve endpoint to be ready..."; '
    'sleep 5; endpoint=$(sky serve status --endpoint {name}); done; '
    'export SKYPILOT_DEBUG=$ORIGIN_SKYPILOT_DEBUG; echo "$endpoint"')

_SERVE_STATUS_WAIT = ('s=$(sky serve status {name}); '
                      'until ! echo "$s" | grep "Controller is initializing."; '
                      'do echo "Waiting for serve status to be ready..."; '
                      'sleep 5; s=$(sky serve status {name}); done; echo "$s"')


def _get_replica_ip(name: str, replica_id: int) -> str:
    return (f'ip{replica_id}=$(echo "$s" | '
            f'awk "{_AWK_ALL_LINES_BELOW_REPLICAS}" | '
            f'grep -E "{name}\s+{replica_id}" | '
            f'grep -Eo "{_IP_REGEX}")')


def _get_skyserve_http_test(name: str, cloud: str,
                            timeout_minutes: int) -> Test:
    test = Test(
        f'test-skyserve-{cloud.replace("_", "-")}',
        [
            f'sky serve up -n {name} -y tests/skyserve/http/{cloud}.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=timeout_minutes * 60,
    )
    return test


def _check_replica_in_status(name: str, check_tuples: List[Tuple[int, bool,
                                                                 str]]) -> str:
    """Check replicas' status and count in sky serve status

    We will check vCPU=2, as all our tests use vCPU=2.

    Args:
        name: the name of the service
        check_tuples: A list of replica property to check. Each tuple is
            (count, is_spot, status)
    """
    check_cmd = ''
    for check_tuple in check_tuples:
        count, is_spot, status = check_tuple
        resource_str = ''
        if status not in ['PENDING', 'SHUTTING_DOWN'
                         ] and not status.startswith('FAILED'):
            spot_str = ''
            if is_spot:
                spot_str = '\[Spot\]'
            resource_str = f'({spot_str}vCPU=2)'
        check_cmd += (f' echo "$s" | grep "{resource_str}" | '
                      f'grep "{status}" | wc -l | grep {count} || exit 1;')
    return (f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s"; ' + check_cmd)


def _check_service_version(service_name: str, version: str) -> str:
    # Grep the lines before 'Service Replicas' and check if the service version
    # is correct.
    return (f'echo "$s" | grep -B1000 "Service Replicas" | '
            f'grep -E "{service_name}\s+{version}" || exit 1; ')


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_gcp_http():
    """Test skyserve on GCP"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'gcp', 20)
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.serve
def test_skyserve_aws_http():
    """Test skyserve on AWS"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'aws', 20)
    run_one_test(test)


@pytest.mark.azure
@pytest.mark.serve
def test_skyserve_azure_http():
    """Test skyserve on Azure"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'azure', 30)
    run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.serve
def test_skyserve_kubernetes_http():
    """Test skyserve on Kubernetes"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'kubernetes', 30)
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_llm(generic_cloud: str):
    """Test skyserve with real LLM usecase"""
    name = _get_service_name()

    def generate_llm_test_command(prompt: str, expected_output: str) -> str:
        prompt = shlex.quote(prompt)
        expected_output = shlex.quote(expected_output)
        return (
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'python tests/skyserve/llm/get_response.py --endpoint $endpoint '
            f'--prompt {prompt} | grep {expected_output}')

    with open('tests/skyserve/llm/prompt_output.json', 'r',
              encoding='utf-8') as f:
        prompt2output = json.load(f)

    test = Test(
        f'test-skyserve-llm',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/llm/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            *[
                generate_llm_test_command(prompt, output)
                for prompt, output in prompt2output.items()
            ],
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=40 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_spot_recovery():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = Test(
        f'test-skyserve-spot-recovery-gcp',
        [
            f'sky serve up -n {name} -y tests/skyserve/spot/recovery.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            _terminate_gcp_replica(name, zone, 1),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
@pytest.mark.no_kubernetes
def test_skyserve_base_ondemand_fallback(generic_cloud: str):
    name = _get_service_name()
    test = Test(
        f'test-skyserve-base-ondemand-fallback',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/spot/base_ondemand_fallback.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(1, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_dynamic_ondemand_fallback():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = Test(
        f'test-skyserve-dynamic-ondemand-fallback',
        [
            f'sky serve up -n {name} --cloud gcp -y tests/skyserve/spot/dynamic_ondemand_fallback.yaml',
            f'sleep 40',
            # 2 on-demand (provisioning) + 2 Spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s";'
            'echo "$s" | grep -q "0/4" || exit 1',
            # Wait for the provisioning starts
            f'sleep 40',
            _check_replica_in_status(name, [
                (2, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                (2, False, _SERVICE_LAUNCHING_STATUS_REGEX + '\|SHUTTING_DOWN')
            ]),

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
            _terminate_gcp_replica(name, zone, 1),
            f'sleep 40',
            # 1 on-demand (provisioning) + 1 Spot (ready) + 1 spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'echo "$s" | grep -q "1/3"',
            _check_replica_in_status(
                name, [(1, True, 'READY'),
                       (1, True, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]),

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_user_bug_restart(generic_cloud: str):
    """Tests that we restart the service after user bug."""
    # TODO(zhwu): this behavior needs some rethinking.
    name = _get_service_name()
    test = Test(
        f'test-skyserve-user-bug-restart',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/restart/user_bug.yaml',
            f's=$(sky serve status {name}); echo "$s";'
            'until echo "$s" | grep -A 100 "Service Replicas" | grep "SHUTTING_DOWN"; '
            'do echo "Waiting for first service to be SHUTTING DOWN..."; '
            f'sleep 5; s=$(sky serve status {name}); echo "$s"; done; ',
            f's=$(sky serve status {name}); echo "$s";'
            'until echo "$s" | grep -A 100 "Service Replicas" | grep "FAILED"; '
            'do echo "Waiting for first service to be FAILED..."; '
            f'sleep 5; s=$(sky serve status {name}); echo "$s"; done; echo "$s"; '
            + _check_replica_in_status(name, [(1, True, 'FAILED')]) +
            # User bug failure will cause no further scaling.
            f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 1; '
            f'echo "$s" | grep -B 100 "NO_REPLICA" | grep "0/0"',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/auto_restart.yaml',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, SkyPilot here!"; do sleep 2; done; sleep 2; '
            + _check_replica_in_status(name, [(1, False, 'READY'),
                                              (1, False, 'FAILED')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
@pytest.mark.no_kubernetes  # Replicas on k8s may be running on the same node and have the same public IP
def test_skyserve_load_balancer(generic_cloud: str):
    """Test skyserve load balancer round-robin policy"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-load-balancer',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/load_balancer/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            f'{_get_replica_ip(name, 1)}; '
            f'{_get_replica_ip(name, 2)}; {_get_replica_ip(name, 3)}; '
            'python tests/skyserve/load_balancer/test_round_robin.py '
            '--endpoint $endpoint --replica-num 3 --replica-ips $ip1 $ip2 $ip3',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
@pytest.mark.no_kubernetes
def test_skyserve_auto_restart():
    """Test skyserve with auto restart"""
    name = _get_service_name()
    zone = 'us-central1-a'
    test = Test(
        f'test-skyserve-auto-restart',
        [
            # TODO(tian): we can dynamically generate YAML from template to
            # avoid maintaining too many YAML files
            f'sky serve up -n {name} -y tests/skyserve/auto_restart.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            # sleep for 20 seconds (initial delay) to make sure it will
            # be restarted
            f'sleep 20',
            _terminate_gcp_replica(name, zone, 1),
            # Wait for consecutive failure timeout passed.
            # If the cluster is not using spot, it won't check the cluster status
            # on the cloud (since manual shutdown is not a common behavior and such
            # queries takes a lot of time). Instead, we think continuous 3 min probe
            # failure is not a temporary problem but indeed a failure.
            'sleep 180',
            # We cannot use _SERVE_WAIT_UNTIL_READY; there will be a intermediate time
            # that the output of `sky serve status` shows FAILED and this status will
            # cause _SERVE_WAIT_UNTIL_READY to early quit.
            '(while true; do'
            f'    output=$(sky serve status {name});'
            '     echo "$output" | grep -q "1/1" && break;'
            '     sleep 10;'
            f'done); sleep {serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS};',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_cancel(generic_cloud: str):
    """Test skyserve with cancel"""
    name = _get_service_name()

    test = Test(
        f'test-skyserve-cancel',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/cancel/cancel.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; python3 '
            'tests/skyserve/cancel/send_cancel_request.py '
            '--endpoint $endpoint | grep "Request was cancelled"',
            f's=$(sky serve logs {name} 1 --no-follow); '
            'until ! echo "$s" | grep "Please wait for the controller to be"; '
            'do echo "Waiting for serve logs"; sleep 10; '
            f's=$(sky serve logs {name} 1 --no-follow); done; '
            'echo "$s"; echo "$s" | grep "Client disconnected, stopping computation"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_streaming(generic_cloud: str):
    """Test skyserve with streaming"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-streaming',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/streaming/streaming.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'python3 tests/skyserve/streaming/send_streaming_request.py '
            '--endpoint $endpoint | grep "Streaming test passed"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_readiness_timeout_fail(generic_cloud: str):
    """Test skyserve with large readiness probe latency, expected to fail"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-readiness-timeout-fail',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/readiness_timeout/task.yaml',
            # None of the readiness probe will pass, so the service will be
            # terminated after the initial delay.
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_INITIAL_DELAY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            'sleep 60',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s" | grep "{name}" | grep "FAILED_INITIAL_DELAY" | wc -l | grep 1;'
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_large_readiness_timeout(generic_cloud: str):
    """Test skyserve with customized large readiness timeout"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-large-readiness-timeout',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/readiness_timeout/task_large_timeout.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_update(generic_cloud: str):
    """Test skyserve with update"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-update',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/old.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/new.yaml',
            # sleep before update is registered.
            'sleep 20',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done;'
            # Make sure the traffic is not mixed
            'curl http://$endpoint | grep "Hi, new SkyPilot here"',
            # The latest 2 version should be READY and the older versions should be shutting down
            (_check_replica_in_status(name, [(2, False, 'READY'),
                                             (2, False, 'SHUTTING_DOWN')]) +
             _check_service_version(name, "2")),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_rolling_update(generic_cloud: str):
    """Test skyserve with rolling update"""
    name = _get_service_name()
    single_new_replica = _check_replica_in_status(
        name, [(2, False, 'READY'), (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
               (1, False, 'SHUTTING_DOWN')])
    test = Test(
        f'test-skyserve-rolling-update',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/old.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/new.yaml',
            # Make sure the traffic is mixed across two versions, the replicas
            # with even id will sleep 60 seconds before being ready, so we
            # should be able to get observe the period that the traffic is mixed
            # across two versions.
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done; sleep 2; '
            # The latest version should have one READY and the one of the older versions should be shutting down
            f'{single_new_replica} {_check_service_version(name, "1,2")} '
            # Check the output from the old version, immediately after the
            # output from the new version appears. This is guaranteed by the
            # round robin load balancing policy.
            # TODO(zhwu): we should have a more generalized way for checking the
            # mixed version of replicas to avoid depending on the specific
            # round robin load balancing policy.
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_fast_update(generic_cloud: str):
    """Test skyserve with fast update (Increment version of old replicas)"""
    name = _get_service_name()

    test = Test(
        f'test-skyserve-fast-update',
        [
            f'sky serve up -n {name} -y --cloud {generic_cloud} tests/skyserve/update/bump_version_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/bump_version_after.yaml',
            # sleep to wait for update to be registered.
            'sleep 40',
            # 2 on-deamnd (ready) + 1 on-demand (provisioning).
            (
                _check_replica_in_status(
                    name, [(2, False, 'READY'),
                           (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]) +
                # Fast update will directly have the latest version ready.
                _check_service_version(name, "2")),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3) +
            _check_service_version(name, "2"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            # Test rolling update
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/bump_version_before.yaml',
            # sleep to wait for update to be registered.
            'sleep 25',
            # 2 on-deamnd (ready) + 1 on-demand (shutting down).
            _check_replica_in_status(name, [(2, False, 'READY'),
                                            (1, False, 'SHUTTING_DOWN')]),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "3"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_update_autoscale(generic_cloud: str):
    """Test skyserve update with autoscale"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-update-autoscale',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/num_min_two.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "1"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/num_min_one.yaml',
            # sleep before update is registered.
            'sleep 20',
            # Timeout will be triggered when update fails.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1) +
            _check_service_version(name, "2"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here!"',
            # Rolling Update
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/num_min_two.yaml',
            # sleep before update is registered.
            'sleep 20',
            # Timeout will be triggered when update fails.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "3"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here!"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
@pytest.mark.no_kubernetes  # Spot instances are not supported in Kubernetes
@pytest.mark.parametrize('mode', ['rolling', 'blue_green'])
def test_skyserve_new_autoscaler_update(mode: str, generic_cloud: str):
    """Test skyserve with update that changes autoscaler"""
    name = _get_service_name() + mode

    wait_until_no_pending = (
        f's=$(sky serve status {name}); echo "$s"; '
        'until ! echo "$s" | grep PENDING; do '
        '  echo "Waiting for replica to be out of pending..."; '
        f' sleep 5; s=$(sky serve status {name}); '
        '  echo "$s"; '
        'done')
    four_spot_up_cmd = _check_replica_in_status(name, [(4, True, 'READY')])
    update_check = [f'until ({four_spot_up_cmd}); do sleep 5; done; sleep 15;']
    if mode == 'rolling':
        # Check rolling update, it will terminate one of the old on-demand
        # instances, once there are 4 spot instance ready.
        update_check += [
            _check_replica_in_status(
                name, [(1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (1, False, 'SHUTTING_DOWN'), (1, False, 'READY')]) +
            _check_service_version(name, "1,2"),
        ]
    else:
        # Check blue green update, it will keep both old on-demand instances
        # running, once there are 4 spot instance ready.
        update_check += [
            _check_replica_in_status(
                name, [(1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (2, False, 'READY')]) +
            _check_service_version(name, "1"),
        ]
    test = Test(
        'test-skyserve-new-autoscaler-update',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/new_autoscaler_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "1"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            's=$(curl http://$endpoint); echo "$s"; echo "$s" | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode {mode} -y tests/skyserve/update/new_autoscaler_after.yaml',
            # Wait for update to be registered
            f'sleep 90',
            wait_until_no_pending,
            _check_replica_in_status(
                name, [(4, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (2, False, 'READY')]),
            *update_check,
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=5),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
            _check_replica_in_status(name, [(4, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_failures(generic_cloud: str):
    """Test replica failure statuses"""
    name = _get_service_name()

    test = Test(
        'test-skyserve-failures',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/failures/initial_delay.yaml',
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_INITIAL_DELAY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            'sleep 60',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s" | grep "{name}" | grep "FAILED_INITIAL_DELAY" | wc -l | grep 2; '
            # Make sure no new replicas are started for early failure.
            f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 2;',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/failures/probing.yaml',
            f's=$(sky serve status {name}); '
            # Wait for replica to be ready.
            f'until echo "$s" | grep "READY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            # Wait for replica to change to FAILED_PROBING
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_PROBING"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done',
            # Wait for the PENDING replica to appear.
            'sleep 10',
            # Wait until the replica is out of PENDING.
            f's=$(sky serve status {name}); '
            f'until ! echo "$s" | grep "PENDING" && ! echo "$s" | grep "Please wait for the controller to be ready."; do '
            'echo "Waiting for replica to be out of pending..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done; ' +
            _check_replica_in_status(
                name, [(1, False, 'FAILED_PROBING'),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]),
            # TODO(zhwu): add test for FAILED_PROVISION
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO(Ziming, Tian): Add tests for autoscaling.


# ------- Testing user dependencies --------
def test_user_dependencies(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'user-dependencies',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} "pip install ray>2.11; ray start --head"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} "echo hi"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
            f'sky exec {name} "echo bye"',
            f'sky logs {name} 3 --status',
            f'sky launch -c {name} tests/test_yamls/different_default_conda_env.yaml',
            f'sky logs {name} 4 --status',
            # Launch again to test the default env does not affect SkyPilot
            # runtime setup
            f'sky launch -c {name} "python --version 2>&1 | grep \'Python 3.6\' || exit 1"',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ------- Testing the core API --------
# Most of the core APIs have been tested in the CLI tests.
# These tests are for testing the return value of the APIs not fully used in CLI.


@pytest.mark.gcp
def test_core_api_sky_launch_exec():
    name = _get_cluster_name()
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


# ---------- Testing Storage ----------
class TestStorageWithCredentials:
    """Storage tests which require credentials and network connection"""

    AWS_INVALID_NAMES = [
        'ab',  # less than 3 characters
        'abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz1',
        # more than 63 characters
        'Abcdef',  # contains an uppercase letter
        'abc def',  # contains a space
        'abc..def',  # two adjacent periods
        '192.168.5.4',  # formatted as an IP address
        'xn--bucket',  # starts with 'xn--' prefix
        'bucket-s3alias',  # ends with '-s3alias' suffix
        'bucket--ol-s3',  # ends with '--ol-s3' suffix
        '.abc',  # starts with a dot
        'abc.',  # ends with a dot
        '-abc',  # starts with a hyphen
        'abc-',  # ends with a hyphen
    ]

    GCS_INVALID_NAMES = [
        'ab',  # less than 3 characters
        'abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz1',
        # more than 63 characters (without dots)
        'Abcdef',  # contains an uppercase letter
        'abc def',  # contains a space
        'abc..def',  # two adjacent periods
        'abc_.def.ghi.jklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz1'
        # More than 63 characters between dots
        'abc_.def.ghi.jklmnopqrstuvwxyzabcdefghijklmnopqfghijklmnopqrstuvw' * 5,
        # more than 222 characters (with dots)
        '192.168.5.4',  # formatted as an IP address
        'googbucket',  # starts with 'goog' prefix
        'googlebucket',  # contains 'google'
        'g00glebucket',  # variant of 'google'
        'go0glebucket',  # variant of 'google'
        'g0oglebucket',  # variant of 'google'
        '.abc',  # starts with a dot
        'abc.',  # ends with a dot
        '_abc',  # starts with an underscore
        'abc_',  # ends with an underscore
    ]

    AZURE_INVALID_NAMES = [
        'ab',  # less than 3 characters
        # more than 63 characters
        'abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz1',
        'Abcdef',  # contains an uppercase letter
        '.abc',  # starts with a non-letter(dot)
        'a--bc',  # contains consecutive hyphens
    ]

    IBM_INVALID_NAMES = [
        'ab',  # less than 3 characters
        'abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz1',
        # more than 63 characters
        'Abcdef',  # contains an uppercase letter
        'abc def',  # contains a space
        'abc..def',  # two adjacent periods
        '192.168.5.4',  # formatted as an IP address
        'xn--bucket',  # starts with 'xn--' prefix
        '.abc',  # starts with a dot
        'abc.',  # ends with a dot
        '-abc',  # starts with a hyphen
        'abc-',  # ends with a hyphen
        'a.-bc',  # contains the sequence '.-'
        'a-.bc',  # contains the sequence '-.'
        'a&bc'  # contains special characters
        'ab^c'  # contains special characters
    ]
    GITIGNORE_SYNC_TEST_DIR_STRUCTURE = {
        'double_asterisk': {
            'double_asterisk_excluded': None,
            'double_asterisk_excluded_dir': {
                'dir_excluded': None,
            },
        },
        'double_asterisk_parent': {
            'parent': {
                'also_excluded.txt': None,
                'child': {
                    'double_asterisk_parent_child_excluded.txt': None,
                },
                'double_asterisk_parent_excluded.txt': None,
            },
        },
        'excluded.log': None,
        'excluded_dir': {
            'excluded.txt': None,
            'nested_excluded': {
                'excluded': None,
            },
        },
        'exp-1': {
            'be_excluded': None,
        },
        'exp-2': {
            'be_excluded': None,
        },
        'front_slash_excluded': None,
        'included.log': None,
        'included.txt': None,
        'include_dir': {
            'excluded.log': None,
            'included.log': None,
        },
        'nested_double_asterisk': {
            'one': {
                'also_exclude.txt': None,
            },
            'two': {
                'also_exclude.txt': None,
            },
        },
        'nested_wildcard_dir': {
            'monday': {
                'also_exclude.txt': None,
            },
            'tuesday': {
                'also_exclude.txt': None,
            },
        },
        'no_slash_excluded': None,
        'no_slash_tests': {
            'no_slash_excluded': {
                'also_excluded.txt': None,
            },
        },
        'question_mark': {
            'excluded1.txt': None,
            'excluded@.txt': None,
        },
        'square_bracket': {
            'excluded1.txt': None,
        },
        'square_bracket_alpha': {
            'excludedz.txt': None,
        },
        'square_bracket_excla': {
            'excluded2.txt': None,
            'excluded@.txt': None,
        },
        'square_bracket_single': {
            'excluded0.txt': None,
        },
    }

    @staticmethod
    def create_dir_structure(base_path, structure):
        # creates a given file STRUCTURE in BASE_PATH
        for name, substructure in structure.items():
            path = os.path.join(base_path, name)
            if substructure is None:
                # Create a file
                open(path, 'a', encoding='utf-8').close()
            else:
                # Create a subdirectory
                os.mkdir(path)
                TestStorageWithCredentials.create_dir_structure(
                    path, substructure)

    @staticmethod
    def cli_delete_cmd(store_type,
                       bucket_name,
                       storage_account_name: str = None):
        if store_type == storage_lib.StoreType.S3:
            url = f's3://{bucket_name}'
            return f'aws s3 rb {url} --force'
        if store_type == storage_lib.StoreType.GCS:
            url = f'gs://{bucket_name}'
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            return f'{alias_gen}; {gsutil_alias} rm -r {url}'
        if store_type == storage_lib.StoreType.AZURE:
            default_region = 'eastus'
            storage_account_name = (
                storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                    region=default_region,
                    user_hash=common_utils.get_user_hash()))
            storage_account_key = data_utils.get_az_storage_account_key(
                storage_account_name)
            return ('az storage container delete '
                    f'--account-name {storage_account_name} '
                    f'--account-key {storage_account_key} '
                    f'--name {bucket_name}')
        if store_type == storage_lib.StoreType.R2:
            endpoint_url = cloudflare.create_endpoint()
            url = f's3://{bucket_name}'
            return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 rb {url} --force --endpoint {endpoint_url} --profile=r2'
        if store_type == storage_lib.StoreType.IBM:
            bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
                bucket_name, Rclone.RcloneClouds.IBM)
            return f'rclone purge {bucket_rclone_profile}:{bucket_name} && rclone config delete {bucket_rclone_profile}'

    @staticmethod
    def cli_ls_cmd(store_type, bucket_name, suffix=''):
        if store_type == storage_lib.StoreType.S3:
            if suffix:
                url = f's3://{bucket_name}/{suffix}'
            else:
                url = f's3://{bucket_name}'
            return f'aws s3 ls {url}'
        if store_type == storage_lib.StoreType.GCS:
            if suffix:
                url = f'gs://{bucket_name}/{suffix}'
            else:
                url = f'gs://{bucket_name}'
            return f'gsutil ls {url}'
        if store_type == storage_lib.StoreType.AZURE:
            default_region = 'eastus'
            config_storage_account = skypilot_config.get_nested(
                ('azure', 'storage_account'), None)
            storage_account_name = config_storage_account if (
                config_storage_account is not None
            ) else (
                storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                    region=default_region,
                    user_hash=common_utils.get_user_hash()))
            storage_account_key = data_utils.get_az_storage_account_key(
                storage_account_name)
            list_cmd = ('az storage blob list '
                        f'--container-name {bucket_name} '
                        f'--prefix {shlex.quote(suffix)} '
                        f'--account-name {storage_account_name} '
                        f'--account-key {storage_account_key}')
            return list_cmd
        if store_type == storage_lib.StoreType.R2:
            endpoint_url = cloudflare.create_endpoint()
            if suffix:
                url = f's3://{bucket_name}/{suffix}'
            else:
                url = f's3://{bucket_name}'
            return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls {url} --endpoint {endpoint_url} --profile=r2'
        if store_type == storage_lib.StoreType.IBM:
            bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
                bucket_name, Rclone.RcloneClouds.IBM)
            return f'rclone ls {bucket_rclone_profile}:{bucket_name}/{suffix}'

    @staticmethod
    def cli_region_cmd(store_type, bucket_name=None, storage_account_name=None):
        if store_type == storage_lib.StoreType.S3:
            assert bucket_name is not None
            return ('aws s3api get-bucket-location '
                    f'--bucket {bucket_name} --output text')
        elif store_type == storage_lib.StoreType.GCS:
            assert bucket_name is not None
            return (f'gsutil ls -L -b gs://{bucket_name}/ | '
                    'grep "Location constraint" | '
                    'awk \'{print tolower($NF)}\'')
        elif store_type == storage_lib.StoreType.AZURE:
            # For Azure Blob Storage, the location of the containers are
            # determined by the location of storage accounts.
            assert storage_account_name is not None
            return (f'az storage account show --name {storage_account_name} '
                    '--query "primaryLocation" --output tsv')
        else:
            raise NotImplementedError(f'Region command not implemented for '
                                      f'{store_type}')

    @staticmethod
    def cli_count_name_in_bucket(store_type,
                                 bucket_name,
                                 file_name,
                                 suffix='',
                                 storage_account_name=None):
        if store_type == storage_lib.StoreType.S3:
            if suffix:
                return f'aws s3api list-objects --bucket "{bucket_name}" --prefix {suffix} --query "length(Contents[?contains(Key,\'{file_name}\')].Key)"'
            else:
                return f'aws s3api list-objects --bucket "{bucket_name}" --query "length(Contents[?contains(Key,\'{file_name}\')].Key)"'
        elif store_type == storage_lib.StoreType.GCS:
            if suffix:
                return f'gsutil ls -r gs://{bucket_name}/{suffix} | grep "{file_name}" | wc -l'
            else:
                return f'gsutil ls -r gs://{bucket_name} | grep "{file_name}" | wc -l'
        elif store_type == storage_lib.StoreType.AZURE:
            if storage_account_name is None:
                default_region = 'eastus'
                storage_account_name = (
                    storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.
                    format(region=default_region,
                           user_hash=common_utils.get_user_hash()))
            storage_account_key = data_utils.get_az_storage_account_key(
                storage_account_name)
            return ('az storage blob list '
                    f'--container-name {bucket_name} '
                    f'--prefix {shlex.quote(suffix)} '
                    f'--account-name {storage_account_name} '
                    f'--account-key {storage_account_key} | '
                    f'grep {file_name} | '
                    'wc -l')
        elif store_type == storage_lib.StoreType.R2:
            endpoint_url = cloudflare.create_endpoint()
            if suffix:
                return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3api list-objects --bucket "{bucket_name}" --prefix {suffix} --query "length(Contents[?contains(Key,\'{file_name}\')].Key)" --endpoint {endpoint_url} --profile=r2'
            else:
                return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3api list-objects --bucket "{bucket_name}" --query "length(Contents[?contains(Key,\'{file_name}\')].Key)" --endpoint {endpoint_url} --profile=r2'

    @staticmethod
    def cli_count_file_in_bucket(store_type, bucket_name):
        if store_type == storage_lib.StoreType.S3:
            return f'aws s3 ls s3://{bucket_name} --recursive | wc -l'
        elif store_type == storage_lib.StoreType.GCS:
            return f'gsutil ls -r gs://{bucket_name}/** | wc -l'
        elif store_type == storage_lib.StoreType.AZURE:
            default_region = 'eastus'
            storage_account_name = (
                storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                    region=default_region,
                    user_hash=common_utils.get_user_hash()))
            storage_account_key = data_utils.get_az_storage_account_key(
                storage_account_name)
            return ('az storage blob list '
                    f'--container-name {bucket_name} '
                    f'--account-name {storage_account_name} '
                    f'--account-key {storage_account_key} | '
                    'grep \\"name\\": | '
                    'wc -l')
        elif store_type == storage_lib.StoreType.R2:
            endpoint_url = cloudflare.create_endpoint()
            return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls s3://{bucket_name} --recursive --endpoint {endpoint_url} --profile=r2 | wc -l'

    @pytest.fixture
    def tmp_source(self, tmp_path):
        # Creates a temporary directory with a file in it
        tmp_dir = tmp_path / 'tmp-source'
        tmp_dir.mkdir()
        tmp_file = tmp_dir / 'tmp-file'
        tmp_file.write_text('test')
        circle_link = tmp_dir / 'circle-link'
        circle_link.symlink_to(tmp_dir, target_is_directory=True)
        yield str(tmp_dir)

    @staticmethod
    def generate_bucket_name():
        # Creates a temporary bucket name
        # time.time() returns varying precision on different systems, so we
        # replace the decimal point and use whatever precision we can get.
        timestamp = str(time.time()).replace('.', '')
        return f'sky-test-{timestamp}'

    @pytest.fixture
    def tmp_bucket_name(self):
        yield self.generate_bucket_name()

    @staticmethod
    def yield_storage_object(
            name: Optional[str] = None,
            source: Optional[storage_lib.Path] = None,
            stores: Optional[Dict[storage_lib.StoreType,
                                  storage_lib.AbstractStore]] = None,
            persistent: Optional[bool] = True,
            mode: storage_lib.StorageMode = storage_lib.StorageMode.MOUNT):
        # Creates a temporary storage object. Stores must be added in the test.
        storage_obj = storage_lib.Storage(name=name,
                                          source=source,
                                          stores=stores,
                                          persistent=persistent,
                                          mode=mode)
        yield storage_obj
        handle = global_user_state.get_handle_from_storage_name(
            storage_obj.name)
        if handle:
            # If handle exists, delete manually
            # TODO(romilb): This is potentially risky - if the delete method has
            #   bugs, this can cause resource leaks. Ideally we should manually
            #   eject storage from global_user_state and delete the bucket using
            #   boto3 directly.
            storage_obj.delete()

    @pytest.fixture
    def tmp_scratch_storage_obj(self, tmp_bucket_name):
        # Creates a storage object with no source to create a scratch storage.
        # Stores must be added in the test.
        yield from self.yield_storage_object(name=tmp_bucket_name)

    @pytest.fixture
    def tmp_multiple_scratch_storage_obj(self):
        # Creates a list of 5 storage objects with no source to create
        # multiple scratch storages.
        # Stores for each object in the list must be added in the test.
        storage_mult_obj = []
        for _ in range(5):
            timestamp = str(time.time()).replace('.', '')
            store_obj = storage_lib.Storage(name=f'sky-test-{timestamp}')
            storage_mult_obj.append(store_obj)
        yield storage_mult_obj
        for storage_obj in storage_mult_obj:
            handle = global_user_state.get_handle_from_storage_name(
                storage_obj.name)
            if handle:
                # If handle exists, delete manually
                # TODO(romilb): This is potentially risky - if the delete method has
                # bugs, this can cause resource leaks. Ideally we should manually
                # eject storage from global_user_state and delete the bucket using
                # boto3 directly.
                storage_obj.delete()

    @pytest.fixture
    def tmp_multiple_custom_source_storage_obj(self):
        # Creates a list of storage objects with custom source names to
        # create multiple scratch storages.
        # Stores for each object in the list must be added in the test.
        custom_source_names = ['"path With Spaces"', 'path With Spaces']
        storage_mult_obj = []
        for name in custom_source_names:
            src_path = os.path.expanduser(f'~/{name}')
            pathlib.Path(src_path).expanduser().mkdir(exist_ok=True)
            timestamp = str(time.time()).replace('.', '')
            store_obj = storage_lib.Storage(name=f'sky-test-{timestamp}',
                                            source=src_path)
            storage_mult_obj.append(store_obj)
        yield storage_mult_obj
        for storage_obj in storage_mult_obj:
            handle = global_user_state.get_handle_from_storage_name(
                storage_obj.name)
            if handle:
                storage_obj.delete()

    @pytest.fixture
    def tmp_local_storage_obj(self, tmp_bucket_name, tmp_source):
        # Creates a temporary storage object. Stores must be added in the test.
        yield from self.yield_storage_object(name=tmp_bucket_name,
                                             source=tmp_source)

    @pytest.fixture
    def tmp_local_list_storage_obj(self, tmp_bucket_name, tmp_source):
        # Creates a temp storage object which uses a list of paths as source.
        # Stores must be added in the test. After upload, the bucket should
        # have two files - /tmp-file and /tmp-source/tmp-file
        list_source = [tmp_source, tmp_source + '/tmp-file']
        yield from self.yield_storage_object(name=tmp_bucket_name,
                                             source=list_source)

    @pytest.fixture
    def tmp_bulk_del_storage_obj(self, tmp_bucket_name):
        # Creates a temporary storage object for testing bulk deletion.
        # Stores must be added in the test.
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.check_output(f'mkdir -p {tmpdir}/folder{{000..255}}',
                                    shell=True)
            subprocess.check_output(f'touch {tmpdir}/test{{000..255}}.txt',
                                    shell=True)
            subprocess.check_output(
                f'touch {tmpdir}/folder{{000..255}}/test.txt', shell=True)
            yield from self.yield_storage_object(name=tmp_bucket_name,
                                                 source=tmpdir)

    @pytest.fixture
    def tmp_copy_mnt_existing_storage_obj(self, tmp_scratch_storage_obj):
        # Creates a copy mount storage which reuses an existing storage object.
        tmp_scratch_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_scratch_storage_obj.name

        # Try to initialize another storage with the storage object created
        # above, but now in COPY mode. This should succeed.
        yield from self.yield_storage_object(name=storage_name,
                                             mode=storage_lib.StorageMode.COPY)

    @pytest.fixture
    def tmp_gitignore_storage_obj(self, tmp_bucket_name, gitignore_structure):
        # Creates a temporary storage object for testing .gitignore filter.
        # GITIGINORE_STRUCTURE is representing a file structure in a dictionary
        # format. Created storage object will contain the file structure along
        # with .gitignore and .git/info/exclude files to test exclude filter.
        # Stores must be added in the test.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Creates file structure to be uploaded in the Storage
            self.create_dir_structure(tmpdir, gitignore_structure)

            # Create .gitignore and list files/dirs to be excluded in it
            skypilot_path = os.path.dirname(os.path.dirname(sky.__file__))
            temp_path = f'{tmpdir}/.gitignore'
            file_path = os.path.join(skypilot_path, 'tests/gitignore_test')
            shutil.copyfile(file_path, temp_path)

            # Create .git/info/exclude and list files/dirs to be excluded in it
            temp_path = f'{tmpdir}/.git/info/'
            os.makedirs(temp_path)
            temp_exclude_path = os.path.join(temp_path, 'exclude')
            file_path = os.path.join(skypilot_path,
                                     'tests/git_info_exclude_test')
            shutil.copyfile(file_path, temp_exclude_path)

            # Create sky Storage with the files created
            yield from self.yield_storage_object(
                name=tmp_bucket_name,
                source=tmpdir,
                mode=storage_lib.StorageMode.COPY)

    @pytest.fixture
    def tmp_awscli_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using awscli
        bucket_uri = f's3://{tmp_bucket_name}'
        subprocess.check_call(['aws', 's3', 'mb', bucket_uri])
        yield tmp_bucket_name, bucket_uri
        subprocess.check_call(['aws', 's3', 'rb', bucket_uri, '--force'])

    @pytest.fixture
    def tmp_gsutil_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using gsutil
        bucket_uri = f'gs://{tmp_bucket_name}'
        subprocess.check_call(['gsutil', 'mb', bucket_uri])
        yield tmp_bucket_name, bucket_uri
        subprocess.check_call(['gsutil', 'rm', '-r', bucket_uri])

    @pytest.fixture
    def tmp_az_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using gsutil
        default_region = 'eastus'
        storage_account_name = (
            storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                region=default_region, user_hash=common_utils.get_user_hash()))
        storage_account_key = data_utils.get_az_storage_account_key(
            storage_account_name)
        bucket_uri = data_utils.AZURE_CONTAINER_URL.format(
            storage_account_name=storage_account_name,
            container_name=tmp_bucket_name)
        subprocess.check_call([
            'az', 'storage', 'container', 'create', '--name',
            f'{tmp_bucket_name}', '--account-name', f'{storage_account_name}',
            '--account-key', f'{storage_account_key}'
        ])
        yield tmp_bucket_name, bucket_uri
        subprocess.check_call([
            'az', 'storage', 'container', 'delete', '--name',
            f'{tmp_bucket_name}', '--account-name', f'{storage_account_name}',
            '--account-key', f'{storage_account_key}'
        ])

    @pytest.fixture
    def tmp_awscli_bucket_r2(self, tmp_bucket_name):
        # Creates a temporary bucket using awscli
        endpoint_url = cloudflare.create_endpoint()
        bucket_uri = f's3://{tmp_bucket_name}'
        subprocess.check_call(
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 mb {bucket_uri} --endpoint {endpoint_url} --profile=r2',
            shell=True)
        yield tmp_bucket_name, bucket_uri
        subprocess.check_call(
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 rb {bucket_uri} --force --endpoint {endpoint_url} --profile=r2',
            shell=True)

    @pytest.fixture
    def tmp_ibm_cos_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using IBM COS API
        storage_obj = storage_lib.IBMCosStore(source="", name=tmp_bucket_name)
        yield tmp_bucket_name
        storage_obj.delete()

    @pytest.fixture
    def tmp_public_storage_obj(self, request):
        # Initializes a storage object with a public bucket
        storage_obj = storage_lib.Storage(source=request.param)
        yield storage_obj
        # This does not require any deletion logic because it is a public bucket
        # and should not get added to global_user_state.

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
    ])
    def test_new_bucket_creation_and_deletion(self, tmp_local_storage_obj,
                                              store_type):
        # Creates a new bucket with a local source, uploads files to it
        # and deletes it.
        tmp_local_storage_obj.add_store(store_type)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj.name in out.decode('utf-8')

        # Run sky storage delete to delete the storage object
        subprocess.check_output(
            ['sky', 'storage', 'delete', tmp_local_storage_obj.name, '--yes'])

        # Run sky storage ls to check if storage object is deleted
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj.name not in out.decode('utf-8')

    @pytest.mark.no_fluidstack
    @pytest.mark.xdist_group('multiple_bucket_deletion')
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm)
    ])
    def test_multiple_buckets_creation_and_deletion(
            self, tmp_multiple_scratch_storage_obj, store_type):
        # Creates multiple new buckets(5 buckets) with a local source
        # and deletes them.
        storage_obj_name = []
        for store_obj in tmp_multiple_scratch_storage_obj:
            store_obj.add_store(store_type)
            storage_obj_name.append(store_obj.name)

        # Run sky storage ls to check if all storage objects exists in the
        # output filtered by store type
        out_all = subprocess.check_output(['sky', 'storage', 'ls'])
        out = [
            item.split()[0]
            for item in out_all.decode('utf-8').splitlines()
            if store_type.value in item
        ]
        assert all([item in out for item in storage_obj_name])

        # Run sky storage delete all to delete all storage objects
        delete_cmd = ['sky', 'storage', 'delete', '--yes']
        delete_cmd += storage_obj_name
        subprocess.check_output(delete_cmd)

        # Run sky storage ls to check if all storage objects filtered by store
        # type are deleted
        out_all = subprocess.check_output(['sky', 'storage', 'ls'])
        out = [
            item.split()[0]
            for item in out_all.decode('utf-8').splitlines()
            if store_type.value in item
        ]
        assert all([item not in out for item in storage_obj_name])

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
    ])
    def test_upload_source_with_spaces(self, store_type,
                                       tmp_multiple_custom_source_storage_obj):
        # Creates two buckets with specified local sources
        # with spaces in the name
        storage_obj_names = []
        for storage_obj in tmp_multiple_custom_source_storage_obj:
            storage_obj.add_store(store_type)
            storage_obj_names.append(storage_obj.name)

        # Run sky storage ls to check if all storage objects exists in the
        # output filtered by store type
        out_all = subprocess.check_output(['sky', 'storage', 'ls'])
        out = [
            item.split()[0]
            for item in out_all.decode('utf-8').splitlines()
            if store_type.value in item
        ]
        assert all([item in out for item in storage_obj_names])

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
    ])
    def test_bucket_external_deletion(self, tmp_scratch_storage_obj,
                                      store_type):
        # Creates a bucket, deletes it externally using cloud cli commands
        # and then tries to delete it using sky storage delete.
        tmp_scratch_storage_obj.add_store(store_type)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_scratch_storage_obj.name in out.decode('utf-8')

        # Delete bucket externally
        cmd = self.cli_delete_cmd(store_type, tmp_scratch_storage_obj.name)
        subprocess.check_output(cmd, shell=True)

        # Run sky storage delete to delete the storage object
        out = subprocess.check_output(
            ['sky', 'storage', 'delete', tmp_scratch_storage_obj.name, '--yes'])
        # Make sure bucket was not created during deletion (see issue #1322)
        assert 'created' not in out.decode('utf-8').lower()

        # Run sky storage ls to check if storage object is deleted
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_scratch_storage_obj.name not in out.decode('utf-8')

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
    ])
    def test_bucket_bulk_deletion(self, store_type, tmp_bulk_del_storage_obj):
        # Creates a temp folder with over 256 files and folders, upload
        # files and folders to a new bucket, then delete bucket.
        tmp_bulk_del_storage_obj.add_store(store_type)

        subprocess.check_output([
            'sky', 'storage', 'delete', tmp_bulk_del_storage_obj.name, '--yes'
        ])

        output = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_bulk_del_storage_obj.name not in output.decode('utf-8')

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'tmp_public_storage_obj, store_type',
        [('s3://tcga-2-open', storage_lib.StoreType.S3),
         ('s3://digitalcorpora', storage_lib.StoreType.S3),
         ('gs://gcp-public-data-sentinel-2', storage_lib.StoreType.GCS),
         pytest.param(
             'https://azureopendatastorage.blob.core.windows.net/nyctlc',
             storage_lib.StoreType.AZURE,
             marks=pytest.mark.azure)],
        indirect=['tmp_public_storage_obj'])
    def test_public_bucket(self, tmp_public_storage_obj, store_type):
        # Creates a new bucket with a public source and verifies that it is not
        # added to global_user_state.
        tmp_public_storage_obj.add_store(store_type)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_public_storage_obj.name not in out.decode('utf-8')

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'nonexist_bucket_url',
        [
            's3://{random_name}',
            'gs://{random_name}',
            pytest.param(
                'https://{account_name}.blob.core.windows.net/{random_name}',  # pylint: disable=line-too-long
                marks=pytest.mark.azure),
            pytest.param('cos://us-east/{random_name}', marks=pytest.mark.ibm),
            pytest.param('r2://{random_name}', marks=pytest.mark.cloudflare)
        ])
    def test_nonexistent_bucket(self, nonexist_bucket_url):
        # Attempts to create fetch a stroage with a non-existent source.
        # Generate a random bucket name and verify it doesn't exist:
        retry_count = 0
        while True:
            nonexist_bucket_name = str(uuid.uuid4())
            if nonexist_bucket_url.startswith('s3'):
                command = f'aws s3api head-bucket --bucket {nonexist_bucket_name}'
                expected_output = '404'
            elif nonexist_bucket_url.startswith('gs'):
                command = f'gsutil ls {nonexist_bucket_url.format(random_name=nonexist_bucket_name)}'
                expected_output = 'BucketNotFoundException'
            elif nonexist_bucket_url.startswith('https'):
                default_region = 'eastus'
                storage_account_name = (
                    storage_lib.AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.
                    format(region=default_region,
                           user_hash=common_utils.get_user_hash()))
                storage_account_key = data_utils.get_az_storage_account_key(
                    storage_account_name)
                command = f'az storage container exists --account-name {storage_account_name} --account-key {storage_account_key} --name {nonexist_bucket_name}'
                expected_output = '"exists": false'
            elif nonexist_bucket_url.startswith('r2'):
                endpoint_url = cloudflare.create_endpoint()
                command = f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3api head-bucket --bucket {nonexist_bucket_name} --endpoint {endpoint_url} --profile=r2'
                expected_output = '404'
            elif nonexist_bucket_url.startswith('cos'):
                # Using API calls, since using rclone requires a profile's name
                try:
                    expected_output = command = "echo"  # avoid unrelated exception in case of failure.
                    bucket_name = urllib.parse.urlsplit(
                        nonexist_bucket_url.format(
                            random_name=nonexist_bucket_name)).path.strip('/')
                    client = ibm.get_cos_client('us-east')
                    client.head_bucket(Bucket=bucket_name)
                except ibm.ibm_botocore.exceptions.ClientError as e:
                    if e.response['Error']['Code'] == '404':
                        # success
                        return
            else:
                raise ValueError('Unsupported bucket type '
                                 f'{nonexist_bucket_url}')

            # Check if bucket exists using the cli:
            try:
                out = subprocess.check_output(command,
                                              stderr=subprocess.STDOUT,
                                              shell=True)
            except subprocess.CalledProcessError as e:
                out = e.output
            out = out.decode('utf-8')
            if expected_output in out:
                break
            else:
                retry_count += 1
                if retry_count > 3:
                    raise RuntimeError('Unable to find a nonexistent bucket '
                                       'to use. This is higly unlikely - '
                                       'check if the tests are correct.')

        with pytest.raises(sky.exceptions.StorageBucketGetError,
                           match='Attempted to use a non-existent'):
            if nonexist_bucket_url.startswith('https'):
                storage_obj = storage_lib.Storage(
                    source=nonexist_bucket_url.format(
                        account_name=storage_account_name,
                        random_name=nonexist_bucket_name))
            else:
                storage_obj = storage_lib.Storage(
                    source=nonexist_bucket_url.format(
                        random_name=nonexist_bucket_name))

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'private_bucket',
        [
            f's3://imagenet',
            f'gs://imagenet',
            pytest.param('https://smoketestprivate.blob.core.windows.net/test',
                         marks=pytest.mark.azure),  # pylint: disable=line-too-long
            pytest.param('cos://us-east/bucket1', marks=pytest.mark.ibm)
        ])
    def test_private_bucket(self, private_bucket):
        # Attempts to access private buckets not belonging to the user.
        # These buckets are known to be private, but may need to be updated if
        # they are removed by their owners.
        store_type = urllib.parse.urlsplit(private_bucket).scheme
        if store_type == 'https' or store_type == 'cos':
            private_bucket_name = urllib.parse.urlsplit(
                private_bucket).path.strip('/')
        else:
            private_bucket_name = urllib.parse.urlsplit(private_bucket).netloc
        match_str = storage_lib._BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
            name=private_bucket_name)
        if store_type == 'https':
            # Azure blob uses a different error string since container may
            # not exist even though the bucket name is ok.
            match_str = 'Attempted to fetch a non-existent public container'
        with pytest.raises(sky.exceptions.StorageBucketGetError,
                           match=match_str):
            storage_obj = storage_lib.Storage(source=private_bucket)

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS),
                              pytest.param('tmp_az_bucket',
                                           storage_lib.StoreType.AZURE,
                                           marks=pytest.mark.azure),
                              pytest.param('tmp_ibm_cos_bucket',
                                           storage_lib.StoreType.IBM,
                                           marks=pytest.mark.ibm),
                              pytest.param('tmp_awscli_bucket_r2',
                                           storage_lib.StoreType.R2,
                                           marks=pytest.mark.cloudflare)])
    def test_upload_to_existing_bucket(self, ext_bucket_fixture, request,
                                       tmp_source, store_type):
        # Tries uploading existing files to newly created bucket (outside of
        # sky) and verifies that files are written.
        bucket_name, _ = request.getfixturevalue(ext_bucket_fixture)
        storage_obj = storage_lib.Storage(name=bucket_name, source=tmp_source)
        storage_obj.add_store(store_type)

        # Check if tmp_source/tmp-file exists in the bucket using aws cli
        out = subprocess.check_output(self.cli_ls_cmd(store_type, bucket_name),
                                      shell=True)
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))

        # Check symlinks - symlinks don't get copied by sky storage
        assert (pathlib.Path(tmp_source) / 'circle-link').is_symlink(), (
            'circle-link was not found in the upload source - '
            'are the test fixtures correct?')
        assert 'circle-link' not in out.decode('utf-8'), (
            'Symlink found in bucket - ls output was : {}'.format(
                out.decode('utf-8')))

        # Run sky storage ls to check if storage object exists in the output.
        # It should not exist because the bucket was created externally.
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert storage_obj.name not in out.decode('utf-8')

    @pytest.mark.no_fluidstack
    def test_copy_mount_existing_storage(self,
                                         tmp_copy_mnt_existing_storage_obj):
        # Creates a bucket with no source in MOUNT mode (empty bucket), and
        # then tries to load the same storage in COPY mode.
        tmp_copy_mnt_existing_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_copy_mnt_existing_storage_obj.name

        # Check `sky storage ls` to ensure storage object exists
        out = subprocess.check_output(['sky', 'storage', 'ls']).decode('utf-8')
        assert storage_name in out, f'Storage {storage_name} not found in sky storage ls.'

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
    ])
    def test_list_source(self, tmp_local_list_storage_obj, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        tmp_local_list_storage_obj.add_store(store_type)

        # Check if tmp-file exists in the bucket root using cli
        out = subprocess.check_output(self.cli_ls_cmd(
            store_type, tmp_local_list_storage_obj.name),
                                      shell=True)
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))

        # Check if tmp-file exists in the bucket/tmp-source using cli
        out = subprocess.check_output(self.cli_ls_cmd(
            store_type, tmp_local_list_storage_obj.name, 'tmp-source/'),
                                      shell=True)
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('invalid_name_list, store_type',
                             [(AWS_INVALID_NAMES, storage_lib.StoreType.S3),
                              (GCS_INVALID_NAMES, storage_lib.StoreType.GCS),
                              pytest.param(AZURE_INVALID_NAMES,
                                           storage_lib.StoreType.AZURE,
                                           marks=pytest.mark.azure),
                              pytest.param(IBM_INVALID_NAMES,
                                           storage_lib.StoreType.IBM,
                                           marks=pytest.mark.ibm),
                              pytest.param(AWS_INVALID_NAMES,
                                           storage_lib.StoreType.R2,
                                           marks=pytest.mark.cloudflare)])
    def test_invalid_names(self, invalid_name_list, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        for name in invalid_name_list:
            with pytest.raises(sky.exceptions.StorageNameError):
                storage_obj = storage_lib.Storage(name=name)
                storage_obj.add_store(store_type)

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'gitignore_structure, store_type',
        [(GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.S3),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.GCS),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.AZURE),
         pytest.param(GITIGNORE_SYNC_TEST_DIR_STRUCTURE,
                      storage_lib.StoreType.R2,
                      marks=pytest.mark.cloudflare)])
    def test_excluded_file_cloud_storage_upload_copy(self, gitignore_structure,
                                                     store_type,
                                                     tmp_gitignore_storage_obj):
        # tests if files included in .gitignore and .git/info/exclude are
        # excluded from being transferred to Storage

        tmp_gitignore_storage_obj.add_store(store_type)

        upload_file_name = 'included'
        # Count the number of files with the given file name
        up_cmd = self.cli_count_name_in_bucket(store_type, \
            tmp_gitignore_storage_obj.name, file_name=upload_file_name)
        git_exclude_cmd = self.cli_count_name_in_bucket(store_type, \
            tmp_gitignore_storage_obj.name, file_name='.git')
        cnt_num_file_cmd = self.cli_count_file_in_bucket(
            store_type, tmp_gitignore_storage_obj.name)

        up_output = subprocess.check_output(up_cmd, shell=True)
        git_exclude_output = subprocess.check_output(git_exclude_cmd,
                                                     shell=True)
        cnt_output = subprocess.check_output(cnt_num_file_cmd, shell=True)

        assert '3' in up_output.decode('utf-8'), \
                'Files to be included are not completely uploaded.'
        # 1 is read as .gitignore is uploaded
        assert '1' in git_exclude_output.decode('utf-8'), \
               '.git directory should not be uploaded.'
        # 4 files include .gitignore, included.log, included.txt, include_dir/included.log
        assert '4' in cnt_output.decode('utf-8'), \
               'Some items listed in .gitignore and .git/info/exclude are not excluded.'

    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS),
                              pytest.param('tmp_awscli_bucket_r2',
                                           storage_lib.StoreType.R2,
                                           marks=pytest.mark.cloudflare)])
    def test_externally_created_bucket_mount_without_source(
            self, ext_bucket_fixture, request, store_type):
        # Non-sky managed buckets(buckets created outside of Skypilot CLI)
        # are allowed to be MOUNTed by specifying the URI of the bucket to
        # source field only. When it is attempted by specifying the name of
        # the bucket only, it should error out.
        #
        # TODO(doyoung): Add test for IBM COS. Currently, this is blocked
        # as rclone used to interact with IBM COS does not support feature to
        # create a bucket, and the ibmcloud CLI is not supported in Skypilot.
        # Either of the feature is necessary to simulate an external bucket
        # creation for IBM COS.
        # https://github.com/skypilot-org/skypilot/pull/1966/files#r1253439837

        ext_bucket_name, ext_bucket_uri = request.getfixturevalue(
            ext_bucket_fixture)
        # invalid spec
        with pytest.raises(sky.exceptions.StorageSpecError) as e:
            storage_obj = storage_lib.Storage(
                name=ext_bucket_name, mode=storage_lib.StorageMode.MOUNT)
            storage_obj.add_store(store_type)

        assert 'Attempted to mount a non-sky managed bucket' in str(e)

        # valid spec
        storage_obj = storage_lib.Storage(source=ext_bucket_uri,
                                          mode=storage_lib.StorageMode.MOUNT)
        handle = global_user_state.get_handle_from_storage_name(
            storage_obj.name)
        if handle:
            storage_obj.delete()

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('region', [
        'ap-northeast-1', 'ap-northeast-2', 'ap-northeast-3', 'ap-south-1',
        'ap-southeast-1', 'ap-southeast-2', 'eu-central-1', 'eu-north-1',
        'eu-west-1', 'eu-west-2', 'eu-west-3', 'sa-east-1', 'us-east-1',
        'us-east-2', 'us-west-1', 'us-west-2'
    ])
    def test_aws_regions(self, tmp_local_storage_obj, region):
        # This tests creation and upload to bucket in all AWS s3 regions
        # To test full functionality, use test_managed_jobs_storage above.
        store_type = storage_lib.StoreType.S3
        tmp_local_storage_obj.add_store(store_type, region=region)
        bucket_name = tmp_local_storage_obj.name

        # Confirm that the bucket was created in the correct region
        region_cmd = self.cli_region_cmd(store_type, bucket_name=bucket_name)
        out = subprocess.check_output(region_cmd, shell=True)
        output = out.decode('utf-8')
        expected_output_region = region
        if region == 'us-east-1':
            expected_output_region = 'None'  # us-east-1 is the default region
        assert expected_output_region in out.decode('utf-8'), (
            f'Bucket was not found in region {region} - '
            f'output of {region_cmd} was: {output}')

        # Check if tmp_source/tmp-file exists in the bucket using cli
        ls_cmd = self.cli_ls_cmd(store_type, bucket_name)
        out = subprocess.check_output(ls_cmd, shell=True)
        output = out.decode('utf-8')
        assert 'tmp-file' in output, (
            f'tmp-file not found in bucket - output of {ls_cmd} was: {output}')

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('region', [
        'northamerica-northeast1', 'northamerica-northeast2', 'us-central1',
        'us-east1', 'us-east4', 'us-east5', 'us-south1', 'us-west1', 'us-west2',
        'us-west3', 'us-west4', 'southamerica-east1', 'southamerica-west1',
        'europe-central2', 'europe-north1', 'europe-southwest1', 'europe-west1',
        'europe-west2', 'europe-west3', 'europe-west4', 'europe-west6',
        'europe-west8', 'europe-west9', 'europe-west10', 'europe-west12',
        'asia-east1', 'asia-east2', 'asia-northeast1', 'asia-northeast2',
        'asia-northeast3', 'asia-southeast1', 'asia-south1', 'asia-south2',
        'asia-southeast2', 'me-central1', 'me-central2', 'me-west1',
        'australia-southeast1', 'australia-southeast2', 'africa-south1'
    ])
    def test_gcs_regions(self, tmp_local_storage_obj, region):
        # This tests creation and upload to bucket in all GCS regions
        # To test full functionality, use test_managed_jobs_storage above.
        store_type = storage_lib.StoreType.GCS
        tmp_local_storage_obj.add_store(store_type, region=region)
        bucket_name = tmp_local_storage_obj.name

        # Confirm that the bucket was created in the correct region
        region_cmd = self.cli_region_cmd(store_type, bucket_name=bucket_name)
        out = subprocess.check_output(region_cmd, shell=True)
        output = out.decode('utf-8')
        assert region in out.decode('utf-8'), (
            f'Bucket was not found in region {region} - '
            f'output of {region_cmd} was: {output}')

        # Check if tmp_source/tmp-file exists in the bucket using cli
        ls_cmd = self.cli_ls_cmd(store_type, bucket_name)
        out = subprocess.check_output(ls_cmd, shell=True)
        output = out.decode('utf-8')
        assert 'tmp-file' in output, (
            f'tmp-file not found in bucket - output of {ls_cmd} was: {output}')


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
    name = _get_cluster_name()
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
    name = _get_cluster_name()
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
    name = _get_cluster_name()
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
    name = _get_cluster_name()
    test = Test(
        'multiple-accelerators-unordered',
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
    name = _get_cluster_name()
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
    name = _get_cluster_name()
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
