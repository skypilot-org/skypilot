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
# Only run managed spot tests
# > pytest tests/test_smoke.py --managed-spot
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
from sky import serve
from sky import spot
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.clouds import AWS
from sky.clouds import Azure
from sky.clouds import GCP
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.data.data_utils import Rclone
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import subprocess_utils

# To avoid the second smoke test reusing the cluster launched in the first
# smoke test. Also required for test_spot_recovery to make sure the manual
# termination with aws ec2 does not accidentally terminate other spot clusters
# from the different spot launch with the same cluster name but a different job
# id.
test_id = str(uuid.uuid4())[-2:]

LAMBDA_TYPE = '--cloud lambda --gpus A10'

SCP_TYPE = '--cloud scp'
SCP_GPU_V100 = '--gpus V100-32GB'

storage_setup_commands = [
    'touch ~/tmpfile', 'mkdir -p ~/tmp-workdir',
    'touch ~/tmp-workdir/tmp\ file', 'touch ~/tmp-workdir/tmp\ file2',
    'touch ~/tmp-workdir/foo',
    'ln -f -s ~/tmp-workdir/ ~/tmp-workdir/circle-link',
    'touch ~/.ssh/id_rsa.pub'
]

# Wait until the spot controller is not in INIT state.
# This is a workaround for the issue that when multiple spot tests
# are running in parallel, the spot controller may be in INIT and
# the spot queue/cancel command will return staled table.
_SPOT_QUEUE_WAIT = ('s=$(sky spot queue); '
                    'until [ `echo "$s" '
                    '| grep "jobs will not be shown until it becomes UP." '
                    '| wc -l` -eq 0 ]; '
                    'do echo "Waiting for spot queue to be ready..."; '
                    'sleep 5; s=$(sky spot queue); done; echo "$s"; '
                    'echo; echo; echo "$s"')
_SPOT_CANCEL_WAIT = (
    's=$(sky spot cancel -y -n {job_name}); until [ `echo "$s" '
    '| grep "Please wait for the controller to be ready." '
    '| wc -l` -eq 0 ]; do echo "Waiting for the spot controller '
    'to be ready"; sleep 5; s=$(sky spot cancel -y -n {job_name}); '
    'done; echo "$s"; echo; echo; echo "$s"')
# TODO(zhwu): make the spot controller on GCP.


class Test(NamedTuple):
    name: str
    # Each command is executed serially.  If any failed, the remaining commands
    # are not run and the test is treated as failed.
    commands: List[str]
    teardown: Optional[str] = None
    # Timeout for each command in seconds.
    timeout: int = 15 * 60

    def echo(self, message: str):
        # pytest's xdist plugin captures stdout; print to stderr so that the
        # logs are streaming while the tests are running.
        prefix = f'[{self.name}]'
        message = f'{prefix} {message}'
        message = message.replace('\n', f'\n{prefix} ')
        print(message, file=sys.stderr, flush=True)


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


def run_one_test(test: Test) -> Tuple[int, str, str]:
    # Fail fast if `sky` CLI somehow errors out.
    subprocess.run(['sky', 'status'], stdout=subprocess.DEVNULL, check=True)
    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)
    test.echo(f'Test started. Log: less {log_file.name}')
    for command in test.commands:
        log_file.write(f'+ {command}\n')
        log_file.flush()
        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,
            executable='/bin/bash',
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

    for region in candidate_regions:
        resources = sky.Resources(cloud=sky.AWS(),
                                  instance_type='p3.16xlarge',
                                  region=region.name,
                                  use_spot=True)
        if not AWS.check_quota_available(resources):
            return region.name

    return None


def get_gcp_region_for_quota_failover() -> Optional[str]:

    candidate_regions = GCP.regions_with_offering(instance_type=None,
                                                  accelerators={'A100-80GB': 1},
                                                  use_spot=True,
                                                  region=None,
                                                  zone=None)

    for region in candidate_regions:
        if not GCP.check_quota_available(
                sky.Resources(cloud=sky.GCP(),
                              region=region.name,
                              accelerators={'A100-80GB': 1},
                              use_spot=True)):
            return region.name

    return None


# ---------- Dry run: 2 Tasks in a chain. ----------
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
            f'sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Check the logs downloading
            f'log_path=$(sky logs {name} 1 --sync-down | grep "Job 1 logs:" | sed -E "s/^.*Job 1 logs: (.*)\\x1b\\[0m/\\1/g") && echo $log_path && test -f $log_path/run.log',
            # Ensure the raylet process has the correct file descriptor limit.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
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
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_region():
    name = _get_cluster_name()
    test = Test(
        'gcp_region',
        [
            f'sky launch -y -c {name} --region us-central1 --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-central1',  # Ensure the region is correct.
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


# ------------ Test stale job ------------
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
        *storage_setup_commands,
        f'sky launch -y -c {name} --cloud {generic_cloud} {extra_flags} examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_file_mounts():
    name = _get_cluster_name()
    test_commands = [
        *storage_setup_commands,
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


def test_using_file_mounts_with_env_vars(generic_cloud: str):
    name = _get_cluster_name()
    test_commands = [
        *storage_setup_commands,
        (f'sky launch -y -c {name} --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml'),
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        # Override with --env:
        (f'sky launch -y -c {name}-2 --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         '--env MY_LOCAL_PATH=tmpfile'),
        f'sky logs {name}-2 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts_with_env_vars',
        test_commands,
        f'sky down -y {name} {name}-2',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- storage ----------
@pytest.mark.aws
def test_aws_storage_mounts_with_stop():
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
            *storage_setup_commands,
            f'sky launch -y -c {name} --cloud aws {file_path}',
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
            *storage_setup_commands,
            f'sky launch -y -c {name} --cloud gcp {file_path}',
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
            *storage_setup_commands,
            f'sky launch -y -c {name} --cloud kubernetes {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'aws s3 ls {storage_name}/hello.txt',
        ]
        test = Test(
            'kubernetes_storage_mounts',
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
            *storage_setup_commands,
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
            *storage_setup_commands,
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
    test = Test(
        'cli_logs',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --num-nodes {num_nodes} "echo {timestamp} 1"',
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
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue instead
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_job_queue instead
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
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
def test_job_queue_with_docker(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'job_queue_with_docker',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/job_queue/cluster_docker.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-3 -d examples/job_queue/job_docker.yaml',
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


@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue_multinode instead
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
            'sleep 75',
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
        timeout=20 * 60,
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
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
def test_docker_preinstalled_package(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'docker_with_preinstalled_package',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id docker:nginx',
            f'sky exec {name} "nginx -V" | grep SUCCEEDED',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
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


# ---------- TPU. ----------
@pytest.mark.gcp
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
        timeout=total_timeout_minutes * 60,
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
            'sleep 10',
            'ip=$(grep -A1 "Host ' + name +
            '" ~/.ssh/config | grep "HostName" | awk \'{print $2}\'); curl $ip:33828 | grep "<h1>This is a demo HTML page.</h1>"',
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
            'sleep 10',
            'ip=$(grep -A1 "Host ' + name +
            '" ~/.ssh/config | grep "HostName" | awk \'{print $2}\'); curl $ip:33828 | grep "<h1>This is a demo HTML page.</h1>"',
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
            'sleep 10',
            'ip=$(grep -A1 "Host ' + name +
            '" ~/.ssh/config | grep "HostName" | awk \'{print $2}\'); curl $ip:33828 | grep "<h1>This is a demo HTML page.</h1>"',
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
            'sleep 200',
            f's=$(sky status -r {name}) && echo $s && echo $s | grep "INIT\|STOPPED"'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    run_one_test(test)


# ---------- Testing Autostopping ----------
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
            'sleep 45',
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

            # Test restarting the idleness timer via cancel + reset:
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky autostop -y {name} --cancel',
            f'sky autostop -y {name} -i 1',  # Should restart the timer.
            'sleep 45',
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
            'sleep 45',
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
@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
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


# ---------- Testing managed spot ----------
@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot(generic_cloud: str):
    """Test the spot yaml."""
    name = _get_cluster_name()
    test = Test(
        'managed-spot',
        [
            f'sky spot launch -n {name}-1 --cloud {generic_cloud} examples/managed_spot.yaml -y -d',
            f'sky spot launch -n {name}-2 --cloud {generic_cloud} examples/managed_spot.yaml -y -d',
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep "STARTING\|RUNNING"',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "STARTING\|RUNNING"',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}-1'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-1 | head -n1 | grep CANCELLED',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        # TODO(zhwu): Change to _SPOT_CANCEL_WAIT.format(job_name=f'{name}-1 -n {name}-2') when
        # canceling multiple job names is supported.
        (_SPOT_CANCEL_WAIT.format(job_name=f'{name}-1') + '; ' +
         _SPOT_CANCEL_WAIT.format(job_name=f'{name}-2')),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_pipeline(generic_cloud: str):
    """Test a spot pipeline."""
    name = _get_cluster_name()
    test = Test(
        'spot-pipeline',
        [
            f'sky spot launch -n {name} tests/test_yamls/pipeline.yaml -y -d',
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING\|RUNNING"',
            # `grep -A 4 {name}` finds the job with {name} and the 4 lines
            # after it, i.e. the 4 tasks within the job.
            # `sed -n 2p` gets the second line of the 4 lines, i.e. the first
            # task within the job.
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "STARTING\|RUNNING"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "PENDING"',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "CANCELLING\|CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "CANCELLING\|CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 4p | grep "CANCELLING\|CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 5p | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 2p | grep "CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 3p | grep "CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            f'{_SPOT_QUEUE_WAIT}| grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=f'{name}'),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_failed_setup(generic_cloud: str):
    """Test managed spot job with failed setup."""
    name = _get_cluster_name()
    test = Test(
        'spot_failed_setup',
        [
            f'sky spot launch -n {name} --cloud {generic_cloud} -y -d tests/test_yamls/failed_setup.yaml',
            'sleep 330',
            # Make sure the job failed quickly.
            f'{_SPOT_QUEUE_WAIT} | grep {name} | head -n1 | grep "FAILED_SETUP"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_pipeline_failed_setup(generic_cloud: str):
    """Test managed spot job with failed setup for a pipeline."""
    name = _get_cluster_name()
    test = Test(
        'spot_pipeline_failed_setup',
        [
            f'sky spot launch -n {name} -y -d tests/test_yamls/failed_setup_pipeline.yaml',
            'sleep 800',
            # Make sure the job failed quickly.
            f'{_SPOT_QUEUE_WAIT} | grep {name} | head -n1 | grep "FAILED_SETUP"',
            # Task 0 should be SUCCEEDED.
            f'{_SPOT_QUEUE_WAIT} | grep -A 4 {name}| sed -n 2p | grep "SUCCEEDED"',
            # Task 1 should be FAILED_SETUP.
            f'{_SPOT_QUEUE_WAIT} | grep -A 4 {name}| sed -n 3p | grep "FAILED_SETUP"',
            # Task 2 should be CANCELLED.
            f'{_SPOT_QUEUE_WAIT} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            # Task 3 should be CANCELLED.
            f'{_SPOT_QUEUE_WAIT} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


# ---------- Testing managed spot recovery ----------


@pytest.mark.aws
@pytest.mark.managed_spot
def test_spot_recovery_aws(aws_config_region):
    """Test managed spot recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'spot_recovery_aws',
        [
            f'sky spot launch --cloud aws --region {region} -n {name} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 360',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 100',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | grep "$RUN_ID"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_spot
def test_spot_recovery_gcp():
    """Test managed spot recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-east4-b'
    query_cmd = (
        f'gcloud compute instances list --filter='
        # `:` means prefix match.
        f'"(labels.ray-cluster-name:{name_on_cloud})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'spot_recovery_gcp',
        [
            f'sky spot launch --cloud gcp --zone {zone} -n {name} --cpus 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 360',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            terminate_cmd,
            'sleep 100',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_spot
def test_spot_pipeline_recovery_aws(aws_config_region):
    """Test managed spot recovery for a pipeline."""
    name = _get_cluster_name()
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    region = aws_config_region
    if region != 'us-east-2':
        pytest.skip('Only run spot pipeline recovery test in us-east-2')
    test = Test(
        'spot_pipeline_recovery_aws',
        [
            f'sky spot launch -n {name} tests/test_yamls/pipeline_aws.yaml  -y -d',
            'sleep 400',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky spot logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (
                f'SPOT_JOB_ID=`cat /tmp/{name}-run-id | rev | '
                'cut -d\'-\' -f2 | rev`;'
                f'aws ec2 terminate-instances --region {region} --instance-ids $('
                f'aws ec2 describe-instances --region {region} '
                # TODO(zhwu): fix the name for spot cluster.
                '--filters Name=tag:ray-cluster-name,Values=*-${SPOT_JOB_ID}'
                f'-{user_hash} '
                f'--query Reservations[].Instances[].InstanceId '
                '--output text)'),
            'sleep 100',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky spot logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_spot
def test_spot_pipeline_recovery_gcp():
    """Test managed spot recovery for a pipeline."""
    name = _get_cluster_name()
    zone = 'us-east4-b'
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    query_cmd = ('gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name:*-${{SPOT_JOB_ID}}-{user_hash})" '
                 f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'spot_pipeline_recovery_gcp',
        [
            f'sky spot launch -n {name} tests/test_yamls/pipeline_gcp.yaml  -y -d',
            'sleep 400',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky spot logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (f'SPOT_JOB_ID=`cat /tmp/{name}-run-id | rev | '
             f'cut -d\'-\' -f2 | rev`;{terminate_cmd}'),
            'sleep 100',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky spot logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_recovery_default_resources(generic_cloud: str):
    """Test managed spot recovery for default resources."""
    name = _get_cluster_name()
    test = Test(
        'managed-spot-recovery-default-resources',
        [
            f'sky spot launch -n {name} --cloud {generic_cloud} "sleep 30 && sudo shutdown now && sleep 1000" -y -d',
            'sleep 360',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING\|RECOVERING"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_spot
def test_spot_recovery_multi_node_aws(aws_config_region):
    """Test managed spot recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'spot_recovery_multi_node_aws',
        [
            f'sky spot launch --cloud aws --region {region} -n {name} --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 450',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             'Name=tag:ray-node-type,Values=worker '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 50',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 560',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_spot
def test_spot_recovery_multi_node_gcp():
    """Test managed spot recovery."""
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-west2-a'
    # Use ':' to match as the cluster name will contain the suffix with job id
    query_cmd = (
        f'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:{name_on_cloud} AND '
        f'labels.ray-node-type=worker)" --zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'spot_recovery_multi_node_gcp',
        [
            f'sky spot launch --cloud gcp --zone {zone} -n {name} --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            'sleep 400',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            terminate_cmd,
            'sleep 50',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 420',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_spot
def test_spot_cancellation_aws(aws_config_region):
    name = _get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_2_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-2', spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-3', spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'spot_cancellation_aws',
        [
            # Test cancellation during spot cluster being launched.
            f'sky spot launch --cloud aws --region {region} -n {name} "sleep 1000"  -y -d',
            'sleep 60',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING"',
            _SPOT_CANCEL_WAIT.format(job_name=name),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLED"',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky spot launch --cloud aws --region {region} -n {name}-2 tests/test_yamls/test_long_setup.yaml  -y -d',
            'sleep 300',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}-2'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLED"',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_2_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancellation during spot job is recovering.
            f'sky spot launch --cloud aws --region {region} -n {name}-3 "sleep 1000"  -y -d',
            'sleep 300',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_3_on_cloud}-* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RECOVERING"',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}-3'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLED"',
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
@pytest.mark.managed_spot
def test_spot_cancellation_gcp():
    name = _get_cluster_name()
    name_3 = f'{name}-3'
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        name_3, spot.SPOT_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
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
        'spot_cancellation_gcp',
        [
            # Test cancellation during spot cluster being launched.
            f'sky spot launch --cloud gcp --zone {zone} -n {name} "sleep 1000"  -y -d',
            'sleep 60',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "STARTING"',
            _SPOT_CANCEL_WAIT.format(job_name=name),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "CANCELLED"',
            # Test cancelling the spot cluster during spot job being setup.
            f'sky spot launch --cloud gcp --zone {zone} -n {name}-2 tests/test_yamls/test_long_setup.yaml  -y -d',
            'sleep 300',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}-2'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-2 | head -n1 | grep "CANCELLED"',
            # Test cancellation during spot job is recovering.
            f'sky spot launch --cloud gcp --zone {zone} -n {name}-3 "sleep 1000"  -y -d',
            'sleep 300',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            terminate_cmd,
            'sleep 100',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "RECOVERING"',
            _SPOT_CANCEL_WAIT.format(job_name=f'{name}-3'),
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLING\|CANCELLED"',
            'sleep 120',
            f'{_SPOT_QUEUE_WAIT}| grep {name}-3 | head -n1 | grep "CANCELLED"',
            # The cluster should be terminated (STOPPING) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$({query_state_cmd}) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "PROVISIONING|STAGING|RUNNING|REPAIRING|TERMINATED|SUSPENDING|SUSPENDED|SUSPENDED"'
            ),
        ],
        timeout=25 * 60)
    run_one_test(test)


# ---------- Testing storage for managed spot ----------
@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_storage(generic_cloud: str):
    """Test storage with managed spot"""
    name = _get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_spot_with_storage.yaml').read_text()
    storage_name = f'sky-test-{int(time.time())}'
    yaml_str = yaml_str.replace('sky-workdir-zhwu', storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_str)
        f.flush()
        file_path = f.name
        test = Test(
            'spot_storage',
            [
                *storage_setup_commands,
                f'sky spot launch -n {name} --cloud {generic_cloud} {file_path} -y',
                'sleep 60',  # Wait the spot queue to be updated
                f'{_SPOT_QUEUE_WAIT}| grep {name} | grep SUCCEEDED',
                f'[ $(aws s3api list-buckets --query "Buckets[?contains(Name, \'{storage_name}\')].Name" --output text | wc -l) -eq 0 ]'
            ],
            _SPOT_CANCEL_WAIT.format(job_name=name),
            # Increase timeout since sky spot queue -r can be blocked by other spot tests.
            timeout=20 * 60,
        )
        run_one_test(test)


# ---------- Testing spot TPU ----------
@pytest.mark.gcp
@pytest.mark.managed_spot
def test_spot_tpu():
    """Test managed spot on TPU."""
    name = _get_cluster_name()
    test = Test(
        'test-spot-tpu',
        [
            f'sky spot launch -n {name} examples/tpu/tpuvm_mnist.yaml -y -d',
            'sleep 5',
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep STARTING',
            'sleep 840',  # TPU takes a while to launch
            f'{_SPOT_QUEUE_WAIT}| grep {name} | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing env for spot ----------
@pytest.mark.no_azure  # Azure does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_spot
def test_spot_inline_env(generic_cloud: str):
    """Test spot env"""
    name = _get_cluster_name()
    test = Test(
        'test-spot-inline-env',
        [
            f'sky spot launch -n {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            'sleep 20',
            f'{_SPOT_QUEUE_WAIT} | grep {name} | grep SUCCEEDED',
        ],
        _SPOT_CANCEL_WAIT.format(job_name=name),
        # Increase timeout since sky spot queue -r can be blocked by other spot tests.
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
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing env file ----------
def test_inline_env_file(generic_cloud: str):
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-env-file',
        [
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env-file examples/sample_dotenv "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing custom image ----------
@pytest.mark.aws
def test_custom_image():
    """Test custom image"""
    name = _get_cluster_name()
    test = Test(
        'test-custom-image',
        [
            f'sky launch -c {name} --retry-until-up -y examples/custom_image.yaml',
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.slow
def test_azure_start_stop_two_nodes():
    name = _get_cluster_name()
    test = Test(
        'azure-start-stop-two-nodes',
        [
            f'sky launch --num-nodes=2 -y -c {name} examples/azure_start_stop.yaml',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name}',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
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

    for disk_tier in ['low', 'medium', 'high']:
        specs = AWS._get_disk_specs(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.AWS.max_cluster_name_length())
        region = 'us-east-2'
        test = Test(
            'aws-disk-tier',
            [
                f'sky launch -y -c {name} --cloud aws --region {region} '
                f'--disk-tier {disk_tier} echo "hello sky"',
                f'id=`aws ec2 describe-instances --region {region} --filters '
                f'Name=tag:ray-cluster-name,Values={name_on_cloud} --query '
                f'Reservations[].Instances[].InstanceId --output text`; ' +
                _get_aws_query_command(region, '$id', 'VolumeType',
                                       specs['disk_tier']) +
                ('' if disk_tier == 'low' else
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
    for disk_tier in ['low', 'medium', 'high']:
        type = GCP._get_disk_type(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.GCP.max_cluster_name_length())
        region = 'us-west2'
        test = Test(
            'gcp-disk-tier',
            [
                f'sky launch -y -c {name} --cloud gcp --region {region} '
                f'--disk-tier {disk_tier} echo "hello sky"',
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
    for disk_tier in ['low', 'medium']:
        type = Azure._get_disk_type(disk_tier)
        name = _get_cluster_name() + '-' + disk_tier
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.Azure.max_cluster_name_length())
        region = 'westus2'
        test = Test(
            'azure-disk-tier',
            [
                f'sky launch -y -c {name} --cloud azure --region {region} '
                f'--disk-tier {disk_tier} echo "hello sky"',
                f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
                f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
                f'--output tsv | grep {type}'
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
    '(while true; do'
    '     output=$(sky serve status {name});'
    '     echo "$output" | grep -q "{replica_num}/{replica_num}" && break;'
    '     echo "$output" | grep -q "FAILED" && exit 1;'
    '     sleep 10;'
    f' done); sleep {serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS};')
_IP_REGEX = r'([0-9]{1,3}\.){3}[0-9]{1,3}'
_ENDPOINT_REGEX = _IP_REGEX + r':[0-9]{1,5}'
_AWK_ALL_LINES_BELOW_REPLICAS = r'/Replicas/{flag=1; next} flag'
# Since we don't allow terminate the service if the controller is INIT,
# which is common for simultaneous pytest, we need to wait until the
# controller is UP before we can terminate the service.
# The teardown command has a 10-mins timeout, so we don't need to do
# the timeout here. See implementation of run_one_test() for details.
_TEARDOWN_SERVICE = (
    '(while true; do'
    '     output=$(sky serve down -y {name});'
    '     echo "$output" | grep -q "scheduled to be terminated" && break;'
    '     sleep 10;'
    'done)')


def _get_serve_endpoint(name: str) -> str:
    return f'endpoint=$(sky serve status {name} | grep -Eo "{_ENDPOINT_REGEX}")'


def _get_replica_line(name: str, replica_id: int) -> str:
    return (f'sky serve status {name} | awk "{_AWK_ALL_LINES_BELOW_REPLICAS}"'
            f' | grep -E "{name}\s+{replica_id}"')


def _get_replica_ip(name: str, replica_id: int) -> str:
    return (f'ip{replica_id}=$({_get_replica_line(name, replica_id)}'
            f' | grep -Eo "{_IP_REGEX}")')


def _get_skyserve_http_test(name: str, cloud: str,
                            timeout_minutes: int) -> Test:
    test = Test(
        f'test-skyserve-{cloud.replace("_", "-")}',
        [
            f'sky serve up -n {name} -y tests/skyserve/http/{cloud}.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_get_serve_endpoint(name)}; curl -L http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=timeout_minutes * 60,
    )
    return test


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_gcp_http():
    """Test skyserve on GCP"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'gcp', 20)
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.sky_serve
def test_skyserve_aws_http():
    """Test skyserve on AWS"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'aws', 20)
    run_one_test(test)


@pytest.mark.azure
@pytest.mark.sky_serve
def test_skyserve_azure_http():
    """Test skyserve on Azure"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'azure', 30)
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_llm():
    """Test skyserve with real LLM usecase"""
    name = _get_service_name()

    def generate_llm_test_command(prompt: str, expected_output: str) -> str:
        prompt = shlex.quote(prompt)
        expected_output = shlex.quote(expected_output)
        return (
            f'{_get_serve_endpoint(name)}; python tests/skyserve/llm/get_response.py'
            f' --endpoint $endpoint --prompt {prompt} | grep {expected_output}')

    with open('tests/skyserve/llm/prompt_output.json', 'r') as f:
        prompt2output = json.load(f)

    test = Test(
        f'test-skyserve-llm',
        [
            f'sky serve up -n {name} -y tests/skyserve/llm/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            *[
                generate_llm_test_command(prompt, output)
                for prompt, output in prompt2output.items()
            ],
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_spot_recovery():
    name = _get_service_name()
    zone = 'us-central1-a'

    # Reference: test_spot_recovery_gcp
    def terminate_replica(replica_id: int) -> str:
        cluster_name = serve.generate_replica_cluster_name(name, replica_id)
        query_cmd = (f'gcloud compute instances list --filter='
                     f'"(labels.ray-cluster-name:{cluster_name})" '
                     f'--zones={zone} --format="value(name)"')
        return (f'gcloud compute instances delete --zone={zone}'
                f' --quiet $({query_cmd})')

    test = Test(
        f'test-skyserve-spot-recovery-gcp',
        [
            f'sky serve up -n {name} -y tests/skyserve/spot/recovery.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_get_serve_endpoint(name)}; curl -L http://$endpoint | grep "Hi, SkyPilot here"',
            terminate_replica(1),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_get_serve_endpoint(name)}; curl -L http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_spot_user_bug():
    """Tests that spot recovery doesn't occur for non-preemption failures"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-spot-user-bug-gcp',
        [
            f'sky serve up -n {name} -y tests/skyserve/spot/user_bug.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            # After failure due to user bug, the service should fail instead of
            # triggering spot recovery.
            '(while true; do'
            f'    output=$(sky serve status {name});'
            '     echo "$output" | grep -q "FAILED" && break;'
            '     echo "$output" | grep -q "PROVISIONING" && exit 1;'
            '     sleep 10;'
            f'done)',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_replica_failure():
    """Test skyserve with manually interrupting some replica"""
    name = _get_service_name()
    zone = 'us-central1-a'

    # Reference: test_spot_recovery_gcp
    def terminate_replica(replica_id: int) -> str:
        cluster_name = serve.generate_replica_cluster_name(name, replica_id)
        query_cmd = (f'gcloud compute instances list --filter='
                     f'"(labels.ray-cluster-name:{cluster_name})" '
                     f'--zones={zone} --format="value(name)"')
        return (f'gcloud compute instances delete --zone={zone}'
                f' --quiet $({query_cmd})')

    # In the worst case, the controller will first wait
    # ENDPOINT_PROBE_INTERVAL_SECONDS for next probe, and wait
    # LB_CONTROLLER_SYNC_INTERVAL_SECONDS for load balancer's
    # next sync with controller. We add 5s more for any overhead,
    # such as database read/write.
    time_to_wait_after_terminate = (serve.ENDPOINT_PROBE_INTERVAL_SECONDS +
                                    serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS +
                                    5)

    test = Test(
        f'test-skyserve-replica-failure',
        [
            f'sky serve up -n {name} -y tests/skyserve/replica_failure/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3),
            f'{_get_serve_endpoint(name)}; {_get_replica_ip(name, 1)}; '
            f'{_get_replica_ip(name, 2)}; {_get_replica_ip(name, 3)}; '
            'python tests/skyserve/replica_failure/test_round_robin.py '
            '--endpoint $endpoint --replica-num 3 --replica-ips $ip1 $ip2 $ip3',
            terminate_replica(1),
            f'sleep {time_to_wait_after_terminate}',
            f'sky serve status {name} | grep 2/3',
            f'{_get_replica_line(name, 1)} | grep NOT_READY',
            f'{_get_serve_endpoint(name)}; {_get_replica_ip(name, 2)}; '
            f'{_get_replica_ip(name, 3)}; '
            'python tests/skyserve/replica_failure/test_round_robin.py '
            '--endpoint $endpoint --replica-num 2 --replica-ips $ip2 $ip3',
            terminate_replica(2),
            f'sleep {time_to_wait_after_terminate}',
            f'sky serve status {name} | grep 1/3',
            f'{_get_replica_line(name, 2)} | grep NOT_READY',
            f'{_get_serve_endpoint(name)}; {_get_replica_ip(name, 3)}; '
            'python tests/skyserve/replica_failure/test_round_robin.py '
            '--endpoint $endpoint --replica-num 1 --replica-ips $ip3',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_auto_restart():
    """Test skyserve with auto restart"""
    name = _get_service_name()
    zone = 'us-central1-a'

    # Reference: test_spot_recovery_gcp
    def terminate_replica(replica_id: int) -> str:
        cluster_name = serve.generate_replica_cluster_name(name, replica_id)
        query_cmd = (f'gcloud compute instances list --filter='
                     f'"(labels.ray-cluster-name:{cluster_name})" '
                     f'--zones={zone} --format="value(name)"')
        return (f'gcloud compute instances delete --zone={zone}'
                f' --quiet $({query_cmd})')

    test = Test(
        f'test-skyserve-auto-restart',
        [
            # TODO(tian): we can dynamically generate YAML from template to
            # avoid maintaining too many YAML files
            f'sky serve up -n {name} -y tests/skyserve/auto_restart.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_get_serve_endpoint(name)}; curl -L http://$endpoint | grep "Hi, SkyPilot here"',
            # sleep for 20 seconds (initial delay) to make sure it will
            # be restarted
            f'sleep 20',
            terminate_replica(1),
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
            f'{_get_serve_endpoint(name)}; curl -L http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.sky_serve
def test_skyserve_cancel():
    """Test skyserve with cancel"""
    name = _get_service_name()

    test = Test(
        f'test-skyserve-cancel',
        [
            f'sky serve up -n {name} -y tests/skyserve/cancel/cancel.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_get_serve_endpoint(name)}; python3 '
            'tests/skyserve/cancel/send_cancel_request.py '
            '--endpoint $endpoint | grep "Request was cancelled"',
            f'sky serve logs {name} 1 --no-follow | grep "Client disconnected, stopping computation"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# ------- Testing user ray cluster --------
def test_user_ray_cluster(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'user-ray-cluster',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} "ray start --head"',
            f'sky exec {name} "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky status -r {name} | grep UP',
            f'sky exec {name} "echo bye"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


_CODE_PREFIX = ['import sky']


def _build(code: List[str]) -> str:
    code = _CODE_PREFIX + code
    code = ';'.join(code)
    return f'python3 -u -c {shlex.quote(code)}'


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
                open(path, 'a').close()
            else:
                # Create a subdirectory
                os.mkdir(path)
                TestStorageWithCredentials.create_dir_structure(
                    path, substructure)

    @staticmethod
    def cli_delete_cmd(store_type, bucket_name):
        if store_type == storage_lib.StoreType.S3:
            url = f's3://{bucket_name}'
            return f'aws s3 rb {url} --force'
        if store_type == storage_lib.StoreType.GCS:
            url = f'gs://{bucket_name}'
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            return f'{alias_gen}; {gsutil_alias} rm -r {url}'
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
    def cli_count_name_in_bucket(store_type, bucket_name, file_name, suffix=''):
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

    @pytest.fixture
    def tmp_bucket_name(self):
        # Creates a temporary bucket name
        # time.time() returns varying precision on different systems, so we
        # replace the decimal point and use whatever precision we can get.
        timestamp = str(time.time()).replace('.', '')
        yield f'sky-test-{timestamp}'

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
        # foramt. Created storage object will contain the file structure along
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
        subprocess.check_call(['aws', 's3', 'mb', f's3://{tmp_bucket_name}'])
        yield tmp_bucket_name
        subprocess.check_call(
            ['aws', 's3', 'rb', f's3://{tmp_bucket_name}', '--force'])

    @pytest.fixture
    def tmp_gsutil_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using gsutil
        subprocess.check_call(['gsutil', 'mb', f'gs://{tmp_bucket_name}'])
        yield tmp_bucket_name
        subprocess.check_call(['gsutil', 'rm', '-r', f'gs://{tmp_bucket_name}'])

    @pytest.fixture
    def tmp_awscli_bucket_r2(self, tmp_bucket_name):
        # Creates a temporary bucket using awscli
        endpoint_url = cloudflare.create_endpoint()
        subprocess.check_call(
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 mb s3://{tmp_bucket_name} --endpoint {endpoint_url} --profile=r2',
            shell=True)
        yield tmp_bucket_name
        subprocess.check_call(
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 rb s3://{tmp_bucket_name} --force --endpoint {endpoint_url} --profile=r2',
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

    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
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

    @pytest.mark.xdist_group('multiple_bucket_deletion')
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
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

    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
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

    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
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

    @pytest.mark.parametrize(
        'tmp_public_storage_obj, store_type',
        [('s3://tcga-2-open', storage_lib.StoreType.S3),
         ('s3://digitalcorpora', storage_lib.StoreType.S3),
         ('gs://gcp-public-data-sentinel-2', storage_lib.StoreType.GCS)],
        indirect=['tmp_public_storage_obj'])
    def test_public_bucket(self, tmp_public_storage_obj, store_type):
        # Creates a new bucket with a public source and verifies that it is not
        # added to global_user_state.
        tmp_public_storage_obj.add_store(store_type)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_public_storage_obj.name not in out.decode('utf-8')

    @pytest.mark.parametrize('nonexist_bucket_url', [
        's3://{random_name}', 'gs://{random_name}',
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

        with pytest.raises(
                sky.exceptions.StorageBucketGetError,
                match='Attempted to use a non-existent bucket as a source'):
            storage_obj = storage_lib.Storage(source=nonexist_bucket_url.format(
                random_name=nonexist_bucket_name))

    @pytest.mark.parametrize('private_bucket', [
        f's3://imagenet', f'gs://imagenet',
        pytest.param('cos://us-east/bucket1', marks=pytest.mark.ibm)
    ])
    def test_private_bucket(self, private_bucket):
        # Attempts to access private buckets not belonging to the user.
        # These buckets are known to be private, but may need to be updated if
        # they are removed by their owners.
        private_bucket_name = urllib.parse.urlsplit(private_bucket).netloc if \
              urllib.parse.urlsplit(private_bucket).scheme != 'cos' else \
                  urllib.parse.urlsplit(private_bucket).path.strip('/')
        with pytest.raises(
                sky.exceptions.StorageBucketGetError,
                match=storage_lib._BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
                    name=private_bucket_name)):
            storage_obj = storage_lib.Storage(source=private_bucket)

    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS),
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
        bucket_name = request.getfixturevalue(ext_bucket_fixture)
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

    def test_copy_mount_existing_storage(self,
                                         tmp_copy_mnt_existing_storage_obj):
        # Creates a bucket with no source in MOUNT mode (empty bucket), and
        # then tries to load the same storage in COPY mode.
        tmp_copy_mnt_existing_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_copy_mnt_existing_storage_obj.name

        # Check `sky storage ls` to ensure storage object exists
        out = subprocess.check_output(['sky', 'storage', 'ls']).decode('utf-8')
        assert storage_name in out, f'Storage {storage_name} not found in sky storage ls.'

    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
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

    @pytest.mark.parametrize('invalid_name_list, store_type',
                             [(AWS_INVALID_NAMES, storage_lib.StoreType.S3),
                              (GCS_INVALID_NAMES, storage_lib.StoreType.GCS),
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

    @pytest.mark.parametrize(
        'gitignore_structure, store_type',
        [(GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.S3),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.GCS),
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


# ---------- Testing YAML Specs ----------
# Our sky storage requires credentials to check the bucket existance when
# loading a task from the yaml file, so we cannot make it a unit test.
class TestYamlSpecs:
    # TODO(zhwu): Add test for `to_yaml_config` for the Storage object.
    #  We should not use `examples/storage_demo.yaml` here, since it requires
    #  users to ensure bucket names to not exist and/or be unique.
    _TEST_YAML_PATHS = [
        'examples/minimal.yaml', 'examples/managed_spot.yaml',
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
def test_multiple_accelerators_ordered():
    name = _get_cluster_name()
    test = Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


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
