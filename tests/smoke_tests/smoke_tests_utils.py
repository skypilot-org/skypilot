import contextlib
import enum
import inspect
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from typing import (Any, Dict, Generator, List, NamedTuple, Optional, Sequence,
                    Set, Tuple)
import uuid

import colorama
import pytest
import requests
from smoke_tests.docker import docker_utils

import sky
from sky import serve
from sky import skypilot_config
from sky.client import sdk
from sky.clouds import AWS
from sky.clouds import gcp
from sky.clouds import GCP
from sky.jobs import utils as managed_job_utils
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import env_options
from sky.utils import subprocess_utils
from sky.utils import yaml_utils

# To avoid the second smoke test reusing the cluster launched in the first
# smoke test. Also required for test_managed_jobs_recovery to make sure the
# manual termination with aws ec2 does not accidentally terminate other clusters
# for the different managed jobs launch with the same job name but a
# different job id.
test_id = str(uuid.uuid4())[-2:]

LAMBDA_TYPE = '--infra lambda --gpus A10'
FLUIDSTACK_TYPE = '--infra fluidstack --gpus RTXA4000'

SCP_TYPE = '--infra scp'
SCP_GPU_V100 = '--gpus V100-32GB'

STORAGE_SETUP_COMMANDS = [
    'touch ~/tmpfile', 'mkdir -p ~/tmp-workdir',
    r'touch ~/tmp-workdir/tmp\ file', r'touch ~/tmp-workdir/tmp\ file2',
    'touch ~/tmp-workdir/foo',
    '[ ! -e ~/tmp-workdir/circle-link ] && ln -s ~/tmp-workdir/ ~/tmp-workdir/circle-link || true',
    'touch ~/.ssh/id_rsa.pub'
]

LOW_RESOURCE_ARG = '--cpus 2+ --memory 4+'
LOW_RESOURCE_PARAM = {
    'cpus': '2+',
    'memory': '4+',
}
LOW_CONTROLLER_RESOURCE_ENV = {
    skypilot_config.ENV_VAR_GLOBAL_CONFIG: 'tests/test_yamls/low_resource_sky_config.yaml',
}
LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG = {
    'jobs': {
        'controller': {
            'resources': {
                'cpus': '2+',
                'memory': '4+'
            }
        }
    },
    'serve': {
        'controller': {
            'resources': {
                'cpus': '2+',
                'memory': '4+'
            }
        }
    }
}

# Get the job queue, and print it once on its own, then print it again to
# use with grep by the caller.
GET_JOB_QUEUE = 's=$(sky jobs queue); echo "$s"; echo "$s"'
# Wait for a job to be not in RUNNING state. Used to check for RECOVERING.
JOB_WAIT_NOT_RUNNING = (
    's=$(sky jobs queue);'
    'until ! echo "$s" | grep "{job_name}" | grep "RUNNING"; do '
    'sleep 10; s=$(sky jobs queue);'
    'echo "Waiting for job to stop RUNNING"; echo "$s"; done')

ACTIVATE_SERVICE_ACCOUNT_AND_GSUTIL = (
    'GOOGLE_APPLICATION_CREDENTIALS='
    f'{gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH}; '
    'gcloud auth activate-service-account '
    '--key-file=$GOOGLE_APPLICATION_CREDENTIALS '
    '2> /dev/null || true; '
    'gsutil')

ENDPOINT = 'http://127.0.0.1:46580/api/health'

# Fix the flakyness of the test, server may not ready when we run the command after restart.
WAIT_FOR_API = (
    'for i in $(seq 1 30); do '
    f'if curl -s {ENDPOINT} > /dev/null; then '
    'echo "API is up and running"; break; fi; '
    'echo "Waiting for API to be ready... ($i/30)"; '
    '[ $i -eq 30 ] && echo "Timed out waiting for API to be ready" && exit 1; '
    'sleep 1; done')

SKY_API_RESTART = f'sky api stop || true && sky api start && {WAIT_FOR_API}'

# Cluster functions
_ALL_JOB_STATUSES = "|".join([status.value for status in sky.JobStatus])
_ALL_CLUSTER_STATUSES = "|".join([status.value for status in sky.ClusterStatus])
_ALL_MANAGED_JOB_STATUSES = "|".join(
    [status.value for status in sky.ManagedJobStatus])


def _statuses_to_str(statuses: Sequence[enum.Enum]):
    """Convert a list of enums to a string with all the values separated by |."""
    assert len(statuses) > 0, 'statuses must not be empty'
    if len(statuses) > 1:
        return '(' + '|'.join([status.value for status in statuses]) + ')'
    else:
        return statuses[0].value


_WAIT_UNTIL_CLUSTER_STATUS_CONTAINS = (
    # A while loop to wait until the cluster status
    # becomes certain status, with timeout.
    'start_time=$SECONDS; '
    'while true; do '
    'if (( $SECONDS - $start_time > {timeout} )); then '
    '  echo "Timeout after {timeout} seconds waiting for cluster status \'{cluster_status}\'"; exit 1; '
    'fi; '
    'current_status=$(sky status {cluster_name} --refresh | '
    'awk "/^{cluster_name}/ '
    r'{{for (i=1; i<=NF; i++) if (\$i ~ /^(' + _ALL_CLUSTER_STATUSES +
    r')$/) print \$i}}"); '
    'if [[ "$current_status" =~ {cluster_status} ]]; '
    'then echo "Target cluster status {cluster_status} reached."; break; fi; '
    'echo "Waiting for cluster status to become {cluster_status}, current status: $current_status"; '
    'sleep 10; '
    'done')


def get_cloud_specific_resource_config(generic_cloud: str):
    # Kubernetes (EKS) requires more resources to avoid flakiness.
    # Only some EKS tests use this function - specifically those that previously
    # failed with low resources. Other EKS tests that work fine with low resources
    # don't need to call this function.
    if generic_cloud == 'kubernetes':
        resource_arg = ""
        env = None
    else:
        resource_arg = LOW_RESOURCE_ARG
        env = LOW_CONTROLLER_RESOURCE_ENV
    return resource_arg, env


def get_cmd_wait_until_cluster_status_contains(
        cluster_name: str, cluster_status: List[sky.ClusterStatus],
        timeout: int):
    return _WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
        cluster_name=cluster_name,
        cluster_status=_statuses_to_str(cluster_status),
        timeout=timeout)


def get_cmd_wait_until_cluster_status_contains_wildcard(
        cluster_name_wildcard: str, cluster_status: List[sky.ClusterStatus],
        timeout: int):
    wait_cmd = _WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.replace(
        'sky status {cluster_name}',
        'sky status "{cluster_name}"').replace('awk "/^{cluster_name}/',
                                               'awk "/^{cluster_name_awk}/')
    return wait_cmd.format(cluster_name=cluster_name_wildcard,
                           cluster_name_awk=cluster_name_wildcard.replace(
                               '*', '.*'),
                           cluster_status=_statuses_to_str(cluster_status),
                           timeout=timeout)


_WAIT_UNTIL_CLUSTER_IS_NOT_FOUND = (
    # A while loop to wait until the cluster is not found or timeout
    'start_time=$SECONDS; '
    'while true; do '
    'if (( $SECONDS - $start_time > {timeout} )); then '
    '  echo "Timeout after {timeout} seconds waiting for cluster to be removed"; exit 1; '
    'fi; '
    'if sky status -r {cluster_name}; sky status {cluster_name} | grep "\'{cluster_name}\' not found"; then '
    '  echo "Cluster {cluster_name} successfully removed."; break; '
    'fi; '
    'echo "Waiting for cluster {cluster_name} to be removed..."; '
    'sleep 10; '
    'done')


def get_cmd_wait_until_cluster_is_not_found(cluster_name: str, timeout: int):
    return _WAIT_UNTIL_CLUSTER_IS_NOT_FOUND.format(cluster_name=cluster_name,
                                                   timeout=timeout)


_WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID = (
    # A while loop to wait until the job status
    # contains certain status, with timeout.
    'start_time=$SECONDS; '
    'while true; do '
    'if (( $SECONDS - $start_time > {timeout} )); then '
    '  echo "Timeout after {timeout} seconds waiting for job status \'{job_status}\'"; exit 1; '
    'fi; '
    'current_status=$(sky queue {cluster_name} | '
    'awk "\\$1 == \\"{job_id}\\" '
    r'{{for (i=1; i<=NF; i++) if (\$i ~ /^(' + _ALL_JOB_STATUSES +
    r')$/) print \$i}}"); '
    'found=0; '  # Initialize found variable outside the loop
    'while read -r line; do '  # Read line by line
    '  if [[ "$line" =~ {job_status} ]]; then '  # Check each line
    '    echo "Target job status {job_status} reached."; '
    '    found=1; '
    '    break; '  # Break inner loop
    '  fi; '
    'done <<< "$current_status"; '
    'if [ "$found" -eq 1 ]; then break; fi; '  # Break outer loop if match found
    'echo "Waiting for job status to contain {job_status}, current status: $current_status"; '
    'sleep 10; '
    'done')

_WAIT_UNTIL_JOB_STATUS_CONTAINS_WITHOUT_MATCHING_JOB = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.replace(
    'awk "\\$1 == \\"{job_id}\\"', 'awk "')

_WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.replace(
    'awk "\\$1 == \\"{job_id}\\"', 'awk "\\$2 == \\"{job_name}\\"')


def get_cmd_wait_until_job_status_contains_matching_job_id(
        cluster_name: str,
        job_id: str,
        job_status: List[sky.JobStatus],
        timeout: int,
        all_users: bool = False):
    cmd = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.format(
        cluster_name=cluster_name,
        job_id=job_id,
        job_status=_statuses_to_str(job_status),
        timeout=timeout)
    if all_users:
        cmd = cmd.replace('sky queue ', 'sky queue -u ')
    return cmd


def get_cmd_wait_until_job_status_contains_without_matching_job(
        cluster_name: str, job_status: List[sky.JobStatus], timeout: int):
    return _WAIT_UNTIL_JOB_STATUS_CONTAINS_WITHOUT_MATCHING_JOB.format(
        cluster_name=cluster_name,
        job_status=_statuses_to_str(job_status),
        timeout=timeout)


def get_cmd_wait_until_job_status_contains_matching_job_name(
        cluster_name: str, job_name: str, job_status: List[sky.JobStatus],
        timeout: int):
    return _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
        cluster_name=cluster_name,
        job_name=job_name,
        job_status=_statuses_to_str(job_status),
        timeout=timeout)


# Managed job functions

_WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.replace(
    'sky queue {cluster_name}', 'sky jobs queue').replace(
        'awk "\\$2 == \\"{job_name}\\"',
        'awk "\\$2 == \\"{job_name}\\" || \\$3 == \\"{job_name}\\"').replace(
            _ALL_JOB_STATUSES, _ALL_MANAGED_JOB_STATUSES)


def get_cmd_wait_until_managed_job_status_contains_matching_job_name(
        job_name: str, job_status: Sequence[sky.ManagedJobStatus],
        timeout: int):
    return _WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
        job_name=job_name,
        job_status=_statuses_to_str(job_status),
        timeout=timeout)


_WAIT_UNTIL_JOB_STATUS_SUCCEEDED = (
    'start_time=$SECONDS; '
    'while true; do '
    'if (( $SECONDS - $start_time > {timeout} )); then '
    '  echo "Timeout after {timeout} seconds waiting for job to succeed"; exit 1; '
    'fi; '
    'if sky logs {cluster_name} {job_id} --status | grep "SUCCEEDED"; then '
    '  echo "Job {job_id} succeeded."; break; '
    'fi; '
    'echo "Waiting for job {job_id} to succeed..."; '
    'sleep 10; '
    'done')


def get_cmd_wait_until_job_status_succeeded(cluster_name: str,
                                            job_id: str,
                                            timeout: int = 30):
    return _WAIT_UNTIL_JOB_STATUS_SUCCEEDED.format(cluster_name=cluster_name,
                                                   job_id=job_id,
                                                   timeout=timeout)


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
    env: Optional[Dict[str, str]] = None

    def echo(self, message: str):
        # pytest's xdist plugin captures stdout; print to stderr so that the
        # logs are streaming while the tests are running.
        prefix = f'[{self.name}]'
        message = f'{prefix} {message}'
        message = message.replace('\n', f'\n{prefix} ')
        self.echo_without_prefix(message)

    @classmethod
    def echo_without_prefix(cls, message: str):
        print(message, file=sys.stderr, flush=True)


def get_timeout(generic_cloud: str,
                override_timeout: int = DEFAULT_CMD_TIMEOUT):
    timeouts = {'fluidstack': 60 * 60}  # file_mounts
    return timeouts.get(generic_cloud, override_timeout)


def get_cluster_name() -> str:
    """Returns a user-unique cluster name for each test_<name>().

    Must be called from each test_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    test_name = test_name.replace('managed-jobs', 'jobs')
    # Use 20 to avoid cluster name to be truncated twice for managed jobs.
    test_name = common_utils.make_cluster_name_on_cloud(test_name,
                                                        20,
                                                        add_user_hash=False)
    return f'{test_name}-{test_id}'


def is_eks_cluster() -> bool:
    cmd = 'kubectl config view --minify -o jsonpath='\
          '{.clusters[0].cluster.server}' \
          ' | grep -q "eks\.amazonaws\.com"'
    result = subprocess.run(cmd,
                            shell=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.returncode == 0


def get_replica_cluster_name_on_gcp(name: str, replica_id: int) -> str:
    cluster_name = serve.generate_replica_cluster_name(name, replica_id)
    return common_utils.make_cluster_name_on_cloud(
        cluster_name, sky.GCP.max_cluster_name_length())


def terminate_gcp_replica(name: str, zone: str, replica_id: int) -> str:
    name_on_cloud = get_replica_cluster_name_on_gcp(name, replica_id)
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name:{name_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    return (f'gcloud compute instances delete --zone={zone}'
            f' --quiet $({query_cmd})')


@contextlib.contextmanager
def override_sky_config(
    test: Optional[Test] = None,
    env_dict: Optional[Dict[str, str]] = None,
    config_dict: Optional[Dict[str, Any]] = None,
) -> Generator[Optional[tempfile.NamedTemporaryFile], None, None]:
    echo = Test.echo_without_prefix if test is None else test.echo
    env_before_override: Optional[Dict[str, Any]] = None
    config_file_override = pytest_config_file_override()
    if config_file_override:
        override_sky_config_dict = (
            skypilot_config.parse_and_validate_config_file(config_file_override)
        )
    else:
        override_sky_config_dict = (skypilot_config.config_utils.Config())

    if env_dict is None:
        env_dict = os.environ
        env_before_override = os.environ.copy()

    if config_dict is not None:
        override_sky_config_dict.update(config_dict)

    if is_remote_server_test():
        endpoint = docker_utils.get_api_server_endpoint_inside_docker()
        override_sky_config_dict.set_nested(('api_server', 'endpoint'),
                                            endpoint)
        # For test that use SDK, not subprocess, the python process already
        # cache the lru_cache of get_server_url and created the sky_config
        # before we override the environment, so we need to disabled the
        # lru_cache of get_server_url and set SKY_API_SERVER_URL_ENV_VAR
        # to make sure the new endpoint is used.
        env_dict[constants.SKY_API_SERVER_URL_ENV_VAR] = endpoint
        # Clear the get_server_url cache
        server_common.get_server_url.cache_clear()
        echo(
            f'Overriding API server endpoint: '
            f'{override_sky_config_dict.get_nested(("api_server", "endpoint"), "UNKNOWN")}'
        )
    if pytest_controller_cloud():
        cloud = pytest_controller_cloud()
        override_sky_config_dict.set_nested(
            ('jobs', 'controller', 'resources', 'cloud'), cloud)
        override_sky_config_dict.set_nested(
            ('serve', 'controller', 'resources', 'cloud'), cloud)
        echo(
            f'Overriding controller cloud: '
            f'{override_sky_config_dict.get_nested(("jobs", "controller", "resources", "cloud"), "UNKNOWN")}'
        )
    if is_grpc_enabled_test():
        env_dict[env_options.Options.ENABLE_GRPC.env_key] = '1'

    if not override_sky_config_dict:
        yield None
        return

    temp_config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml')
    if skypilot_config.ENV_VAR_GLOBAL_CONFIG in env_dict:
        # Read the original config
        original_config = skypilot_config.parse_and_validate_config_file(
            env_dict[skypilot_config.ENV_VAR_GLOBAL_CONFIG])
    else:
        original_config = skypilot_config.config_utils.Config()
    overlay_config = skypilot_config.overlay_skypilot_config(
        original_config, override_sky_config_dict)
    temp_config_file.write(yaml_utils.dump_yaml_str(dict(overlay_config)))
    temp_config_file.flush()
    # Update the environment variable to use the temporary file
    env_dict[skypilot_config.ENV_VAR_GLOBAL_CONFIG] = temp_config_file.name
    yield temp_config_file
    if env_before_override is not None:
        os.environ.clear()
        os.environ.update(env_before_override)


def run_one_test(test: Test, check_sky_status: bool = True) -> None:
    # Fail fast if `sky` CLI somehow errors out.
    if check_sky_status:
        test.commands.insert(0, 'sky status')

    log_to_stdout = os.environ.get('LOG_TO_STDOUT', None)
    if log_to_stdout:
        write = test.echo
        flush = lambda: None
        subprocess_out = sys.stderr
        test.echo('Test started. Log to stdout')
    else:
        log_file = tempfile.NamedTemporaryFile('a',
                                               prefix=f'{test.name}-',
                                               suffix='.log',
                                               delete=False)
        write = log_file.write
        flush = log_file.flush
        subprocess_out = log_file
        test.echo(f'Test started. Log: less -r {log_file.name}')

    env_dict = os.environ.copy()
    if test.env:
        env_dict.update(test.env)

    with override_sky_config(test, env_dict):
        for command in test.commands:
            write(f'+ {command}\n')
            flush()
            proc = subprocess.Popen(
                command,
                stdout=subprocess_out,
                stderr=subprocess.STDOUT,
                shell=True,
                executable='/bin/bash',
                env=env_dict,
            )
            try:
                proc.wait(timeout=test.timeout)
            except subprocess.TimeoutExpired as e:
                flush()
                test.echo(f'Timeout after {test.timeout} seconds.')
                test.echo(str(e))
                write(f'Timeout after {test.timeout} seconds.\n')
                flush()
                # Kill the current process.
                proc.terminate()
                proc.returncode = 1  # None if we don't set it.
                break

            if proc.returncode:
                break

        style = colorama.Style
        fore = colorama.Fore
        outcome = (
            f'{fore.RED}Failed{style.RESET_ALL} (returned {proc.returncode})'
            if proc.returncode else f'{fore.GREEN}Passed{style.RESET_ALL}')
        reason = f'\nReason: {command}' if proc.returncode else ''
        msg = (f'{outcome}.'
               f'{reason}')
        if log_to_stdout:
            test.echo(msg)
        else:
            msg += f'\nLog: less -r {log_file.name}\n'
            test.echo(msg)
            write(msg)

        if (proc.returncode == 0 or
                pytest.terminate_on_failure) and test.teardown is not None:
            subprocess_utils.run(
                test.teardown,
                stdout=subprocess_out,
                stderr=subprocess.STDOUT,
                timeout=10 * 60,  # 10 mins
                shell=True,
                env=env_dict,
            )

        if proc.returncode:
            if log_to_stdout:
                raise Exception(f'test failed')
            else:
                raise Exception(f'test failed: less -r {log_file.name}')


def get_aws_region_for_quota_failover() -> Optional[str]:
    candidate_regions = AWS.regions_with_offering(instance_type='p3.16xlarge',
                                                  accelerators=None,
                                                  use_spot=True,
                                                  region=None,
                                                  zone=None)
    original_resources = sky.Resources(infra='aws',
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

    original_resources = sky.Resources(infra='gcp',
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


VALIDATE_LAUNCH_OUTPUT = (
    # Validate the output of the job submission:
    # ⚙️ Launching on Kubernetes.
    #   Pod is up.
    # ✓ Cluster launched: test. View logs at: ~/sky_logs/sky-2024-10-07-19-44-18-177288/provision.log
    # ✓ Setup Detached.
    # ⚙️ Job submitted, ID: 1.
    # ├── Waiting for task resources on 1 node.
    # └── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
    # (setup pid=1277) running setup
    # (min, pid=1277) # conda environments:
    # (min, pid=1277) #
    # (min, pid=1277) base                  *  /opt/conda
    # (min, pid=1277)
    # (min, pid=1277) task run finish
    # ✓ Job finished (status: SUCCEEDED).
    #
    # Job ID: 1
    # 📋 Useful Commands
    # ├── To cancel the job:          sky cancel test 1
    # ├── To stream job logs:         sky logs test 1
    # └── To view job queue:          sky queue test
    #
    # Cluster name: test
    # ├── To log into the head VM:    ssh test
    # ├── To submit a job:            sky exec test yaml_file
    # ├── To stop the cluster:        sky stop test
    # └── To teardown the cluster:    sky down test
    'echo "$s" && echo "==Validating launching==" && '
    'echo "$s" | grep -A 1 "Launching on" | grep "is up." && '
    'echo "$s" && echo "==Validating setup output==" && '
    'echo "$s" | grep -A 1 "Setup detached" | grep "Job submitted" && '
    'echo "==Validating running output hints==" && echo "$s" | '
    'grep -A 1 "Job submitted, ID:" | '
    'grep "Waiting for task resources on " && '
    'echo "==Validating task setup/run output starting==" && echo "$s" | '
    'grep -A 1 "Job started. Streaming logs..." | grep "(setup" | '
    'grep "running setup" && '
    'echo "$s" | grep -A 1 "(setup" | grep "(min, pid=" && '
    'echo "==Validating task output ending==" && '
    'echo "$s" | grep -A 1 "task run finish" | '
    'grep "Job finished (status: SUCCEEDED)" && '
    'echo "==Validating task output ending 2==" && '
    'echo "$s" | grep -A 5 "Job finished (status: SUCCEEDED)" | '
    'grep "Job ID:" && '
    'echo "$s" | grep -A 1 "Useful Commands" | grep "Job ID:"')

_CLOUD_CMD_CLUSTER_NAME_SUFFIX = '-cloud-cmd'


# === Helper functions for executing cloud commands ===
# When the API server is remote, we should make sure that the tests can run
# without cloud credentials or cloud dependencies locally. To do this, we run
# the cloud commands required in tests on a separate remote cluster with the
# cloud credentials and dependencies setup.
#
# Set `skip_remote_server_check=True` to opt-in to using the remote helper
# cluster regardless of whether the API server is local or remote. This is
# useful for running cloud commands while the cluster being tested is
# stopped and you need to run cloud commands.
#
# Example usage:
# Test(
#     'mytest',
#     [
#         launch_cluster_for_cloud_cmd('aws', 'mytest-cluster'),
#         # ... commands for the test ...
#         # Run the cloud commands on the remote cluster.
#         run_cloud_cmd_on_cluster('mytest-cluster', 'aws ec2 describe-instances'),
#         # ... commands for the test ...
#     ],
#     f'sky down -y mytest-cluster && {down_cluster_for_cloud_cmd('mytest-cluster')}',
# )
def launch_cluster_for_cloud_cmd(cloud: str,
                                 test_cluster_name: str,
                                 skip_remote_server_check: bool = False) -> str:
    """Launch the cluster for cloud commands asynchronously."""
    cluster_name = test_cluster_name + _CLOUD_CMD_CLUSTER_NAME_SUFFIX
    if not skip_remote_server_check and sky.server.common.is_api_server_local(
    ) and not is_remote_server_test():
        # We need is_remote_server_test() because we override the SKY_API_SERVER_URL_ENV_VAR
        # in the middle of the test, which is after the test is launched, so the
        # is_api_server_local() already cached and returned True but we're actually
        # running the test on the remote server if --remote-server is specified.
        return 'true'
    else:
        return (
            f'sky launch -y -c {cluster_name} --infra {cloud} {LOW_RESOURCE_ARG} --async'
        )


def run_cloud_cmd_on_cluster(test_cluster_name: str,
                             cmd: str,
                             envs: Set[str] = None,
                             timeout: int = 180,
                             skip_remote_server_check: bool = False) -> str:
    """Run the cloud command on the remote cluster for cloud commands."""
    cluster_name = test_cluster_name + _CLOUD_CMD_CLUSTER_NAME_SUFFIX
    if not skip_remote_server_check and sky.server.common.is_api_server_local(
    ) and not is_remote_server_test():
        return cmd
    else:
        cmd = f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV} && {cmd}'
        wait_for_cluster_up = get_cmd_wait_until_cluster_status_contains(
            cluster_name=cluster_name,
            cluster_status=[sky.ClusterStatus.UP],
            timeout=timeout,
        )
        envs_str = ''
        if envs is not None:
            envs_str = ' '.join([f'--env {env}' for env in envs])
        return (f'{wait_for_cluster_up}; '
                f'sky exec {envs_str} {cluster_name} {shlex.quote(cmd)} && '
                f'sky logs {cluster_name} --status')


def down_cluster_for_cloud_cmd(test_cluster_name: str,
                               skip_remote_server_check: bool = False) -> str:
    """Down the cluster for cloud commands."""
    cluster_name = test_cluster_name + _CLOUD_CMD_CLUSTER_NAME_SUFFIX
    if not skip_remote_server_check and sky.server.common.is_api_server_local(
    ) and not is_remote_server_test():
        return 'true'
    else:
        return f'sky down -y {cluster_name}'


def _increase_initial_delay_seconds(original_cmd: str,
                                    factor: float = 2) -> Tuple[str, str]:
    yaml_file = re.search(r'\s([^ ]+\.yaml)', original_cmd).group(1)
    with open(yaml_file, 'r') as f:
        yaml_content = f.read()
    original_initial_delay_seconds = re.search(r'initial_delay_seconds: (\d+)',
                                               yaml_content).group(1)
    new_initial_delay_seconds = int(original_initial_delay_seconds) * factor
    yaml_content = re.sub(
        r'initial_delay_seconds: \d+',
        f'initial_delay_seconds: {new_initial_delay_seconds}', yaml_content)
    f = tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False)
    f.write(yaml_content)
    f.flush()
    return f.name, original_cmd.replace(yaml_file, f.name)


@contextlib.contextmanager
def increase_initial_delay_seconds_for_slow_cloud(cloud: str):
    """Increase initial delay seconds for slow clouds to reduce flakiness and failure during setup."""

    def _context_func(original_cmd: str, factor: float = 2):
        if cloud != 'kubernetes':
            return original_cmd
        file_name, new_cmd = _increase_initial_delay_seconds(
            original_cmd, factor)
        files.append(file_name)
        return new_cmd

    files = []
    try:
        yield _context_func
    finally:
        for file in files:
            os.unlink(file)


def is_remote_server_test() -> bool:
    return os.environ.get(
        'PYTEST_SKYPILOT_REMOTE_SERVER_TEST',
        None) is not None or api_server_endpoint_configured_in_env_file()


def pytest_controller_cloud() -> Optional[str]:
    return os.environ.get('PYTEST_SKYPILOT_CONTROLLER_CLOUD', None)


def is_postgres_backend_test() -> bool:
    return os.environ.get('PYTEST_SKYPILOT_POSTGRES_BACKEND', None) is not None


def is_grpc_enabled_test() -> bool:
    return os.environ.get('PYTEST_SKYPILOT_GRPC_ENABLED', None) is not None


def pytest_config_file_override() -> Optional[str]:
    return os.environ.get('PYTEST_SKYPILOT_CONFIG_FILE_OVERRIDE', None)


def api_server_endpoint_configured_in_env_file() -> bool:
    file_path = pytest_config_file_override()
    if file_path is not None:
        with open(file_path, 'r') as f:
            content = f.read()
            return 'endpoint' in content
    return False


def services_account_token_configured_in_env_file() -> bool:
    file_path = pytest_config_file_override()
    if file_path is not None:
        with open(file_path, 'r') as f:
            content = f.read()
            print(content, file=sys.stderr, flush=True)
            return 'service_account_token' in content
    return False


def pytest_override_env_config_file(config: Dict[str, str]):
    """Override the environment variable for the test."""
    for key, value in config.items():
        os.environ[key] = value


def get_api_server_url() -> str:
    """Get the API server URL in the test environment."""
    if is_remote_server_test():
        return docker_utils.get_api_server_endpoint_inside_docker()
    return server_common.get_server_url()


def get_dashboard_cluster_status_request_id() -> str:
    """Get the status of the cluster from the dashboard."""
    body = payloads.StatusBody(all_users=True,)
    response = requests.post(
        f'{get_api_server_url()}/internal/dashboard/status',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


def get_dashboard_jobs_queue_request_id() -> str:
    """Get the jobs queue from the dashboard."""
    body = payloads.JobsQueueBody(all_users=True,)
    response = requests.post(
        f'{get_api_server_url()}/internal/dashboard/jobs/queue',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


def get_response_from_request_id(request_id: str) -> Any:
    """Waits for and gets the result of a request.

    Args:
        request_id: The request ID of the request to get.

    Returns:
        The ``Request Returns`` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        Exception: It raises the same exceptions as the specific requests,
            see ``Request Raises`` in the documentation of the specific requests
            above.
    """
    response = requests.get(
        f'{get_api_server_url()}/internal/dashboard/api/get?request_id={request_id}',
        timeout=15)
    request_task = None
    if response.status_code == 200:
        request_task = requests_lib.Request.decode(
            payloads.RequestPayload(**response.json()))
        return request_task.get_return_value()
    raise RuntimeError(f'Failed to get request {request_id}: '
                       f'{response.status_code} {response.text}')


def with_config(cmd: str, config_path: str) -> str:
    return (f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={config_path}; '
            f'{cmd}')


def _get_controller_pod_name(controller_name: str) -> str:
    return (
        'kubectl get pods -l app -o custom-columns=NAME:.metadata.name,'
        'APP:.metadata.labels.app --no-headers | '
        f'awk \'$2 ~ /sky-{controller_name}-controller/ {{print $1; exit}}\'')


def kill_and_wait_controller(controller_name: str) -> str:
    """Kill the controller pod and wait for a new one to be ready."""
    assert controller_name in ['serve', 'jobs'
                              ], (f'Invalid controller name: {controller_name}')
    return (
        f'initial_controller_pod=$({_get_controller_pod_name(controller_name)}); '
        f'echo "Killing {controller_name} controller pod: $initial_controller_pod"; '
        'kubectl delete pod $initial_controller_pod; '
        f'until new_controller_pod=$({_get_controller_pod_name(controller_name)}) && '
        '[ "$new_controller_pod" != "$initial_controller_pod" ] && '
        'kubectl get pod $new_controller_pod | grep "1/1"; do '
        f'  echo "Waiting for new {controller_name} controller pod..."; sleep 5; '
        'done; '
        f'echo "New {controller_name} controller pod ready: $new_controller_pod"'
    )


def server_side_is_consolidation_mode() -> bool:
    """Returns whether the consolidation mode is enabled on the server side.

    This is required because when --postgres and --jobs-consolidation specified
    at the same time, the server side will have config for consolidation mode,
    but the client side will only have a config to specify the db url for
    postgres. Here we manually retrieve the config from the server side to
    check if the consolidation mode is enabled.
    """
    if is_remote_server_test():
        # The buildkite pre_command setup does not affect the remote server
        # config. So --postgres and --jobs-consolidation will not be enabled
        # even if they are specified.
        # (TODO: zeping) support this in the future.
        return False
    response = requests.get(f'{get_api_server_url()}/workspaces/config')
    request_id = server_common.get_request_id(response)
    config = config_utils.Config.from_dict(sdk.get(request_id))
    config = skypilot_config.overlay_skypilot_config(
        original_config=config, override_configs=skypilot_config.to_dict())
    with skypilot_config.replace_skypilot_config(config):
        return managed_job_utils.is_consolidation_mode()


def is_in_buildkite_env() -> bool:
    """Check if the test is running in the Buildkite environment."""
    return env_options.Options.RUNNING_IN_BUILDKITE.get()
