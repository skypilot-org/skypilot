import enum
import inspect
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
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
from sky.adaptors import azure
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.clouds import AWS
from sky.clouds import Azure
from sky.clouds import GCP
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.data.data_utils import Rclone
from sky.jobs.state import ManagedJobStatus
from sky.skylet import constants
from sky.skylet import events
from sky.skylet.job_lib import JobStatus
from sky.status_lib import ClusterStatus
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

# Get the job queue, and print it once on its own, then print it again to
# use with grep by the caller.
_GET_JOB_QUEUE = 's=$(sky jobs queue); echo "$s"; echo "$s"'
# Wait for a job to be not in RUNNING state. Used to check for RECOVERING.
_JOB_WAIT_NOT_RUNNING = (
    's=$(sky jobs queue);'
    'until ! echo "$s" | grep "{job_name}" | grep "RUNNING"; do '
    'sleep 10; s=$(sky jobs queue);'
    'echo "Waiting for job to stop RUNNING"; echo "$s"; done')

# Cluster functions
_ALL_JOB_STATUSES = "|".join([status.value for status in JobStatus])
_ALL_CLUSTER_STATUSES = "|".join([status.value for status in ClusterStatus])
_ALL_MANAGED_JOB_STATUSES = "|".join(
    [status.value for status in ManagedJobStatus])

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
    '{{for (i=1; i<=NF; i++) if (\$i ~ /^(' + _ALL_CLUSTER_STATUSES +
    ')$/) print \$i}}"); '
    'if [[ "$current_status" =~ {cluster_status} ]]; '
    'then echo "Target cluster status {cluster_status} reached."; break; fi; '
    'echo "Waiting for cluster status to become {cluster_status}, current status: $current_status"; '
    'sleep 10; '
    'done')


def _get_cmd_wait_until_cluster_status_contains_wildcard(
        cluster_name_wildcard: str, cluster_status: str, timeout: int):
    wait_cmd = _WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.replace(
        'sky status {cluster_name}',
        'sky status "{cluster_name}"').replace('awk "/^{cluster_name}/',
                                               'awk "/^{cluster_name_awk}/')
    return wait_cmd.format(cluster_name=cluster_name_wildcard,
                           cluster_name_awk=cluster_name_wildcard.replace(
                               '*', '.*'),
                           cluster_status=cluster_status,
                           timeout=timeout)


_WAIT_UNTIL_CLUSTER_IS_NOT_FOUND = (
    # A while loop to wait until the cluster is not found or timeout
    'start_time=$SECONDS; '
    'while true; do '
    'if (( $SECONDS - $start_time > {timeout} )); then '
    '  echo "Timeout after {timeout} seconds waiting for cluster to be removed"; exit 1; '
    'fi; '
    'if sky status -r {cluster_name}; sky status {cluster_name} | grep "{cluster_name} not found"; then '
    '  echo "Cluster {cluster_name} successfully removed."; break; '
    'fi; '
    'echo "Waiting for cluster {name} to be removed..."; '
    'sleep 10; '
    'done')

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
    '{{for (i=1; i<=NF; i++) if (\$i ~ /^(' + _ALL_JOB_STATUSES +
    ')$/) print \$i}}"); '
    'found=0; '  # Initialize found variable outside the loop
    'while read -r line; do '  # Read line by line
    '  if [[ "$line" =~ {job_status} ]]; then '  # Check each line
    '    echo "Target job status {job_status} reached."; '
    '    found=1; '
    '    break; '  # Break inner loop
    '  fi; '
    'done <<< "$current_status"; '
    'if [ "$found" -eq 1 ]; then break; fi; '  # Break outer loop if match found
    'echo "Waiting for job status to contains {job_status}, current status: $current_status"; '
    'sleep 10; '
    'done')

_WAIT_UNTIL_JOB_STATUS_CONTAINS_WITHOUT_MATCHING_JOB = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.replace(
    'awk "\\$1 == \\"{job_id}\\"', 'awk "')

_WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.replace(
    'awk "\\$1 == \\"{job_id}\\"', 'awk "\\$2 == \\"{job_name}\\"')

# Managed job functions

_WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME = _WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.replace(
    'sky queue {cluster_name}', 'sky jobs queue').replace(
        'awk "\\$2 == \\"{job_name}\\"',
        'awk "\\$2 == \\"{job_name}\\" || \\$3 == \\"{job_name}\\"').replace(
            _ALL_JOB_STATUSES, _ALL_MANAGED_JOB_STATUSES)

# After the timeout, the cluster will stop if autostop is set, and our check
# should be more than the timeout. To address this, we extend the timeout by
# _BUMP_UP_SECONDS before exiting.
_BUMP_UP_SECONDS = 35

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
    log_to_stdout = os.environ.get('LOG_TO_STDOUT', None)
    if log_to_stdout:
        write = test.echo
        flush = lambda: None
        out = sys.stdout
        test.echo(f'Test started. Log to stdout')
    else:
        log_file = tempfile.NamedTemporaryFile('a',
                                               prefix=f'{test.name}-',
                                               suffix='.log',
                                               delete=False)
        write = log_file.write
        flush = log_file.flush
        out = log_file
        test.echo(f'Test started. Log: less {log_file.name}')

    env_dict = os.environ.copy()
    if test.env:
        env_dict.update(test.env)
    for command in test.commands:
        write(f'+ {command}\n')
        flush()
        proc = subprocess.Popen(
            command,
            stdout=out,
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
    outcome = (f'{fore.RED}Failed{style.RESET_ALL}'
               if proc.returncode else f'{fore.GREEN}Passed{style.RESET_ALL}')
    reason = f'\nReason: {command}' if proc.returncode else ''
    msg = (f'{outcome}.'
           f'{reason}')
    if log_to_stdout:
        test.echo(msg)
    else:
        msg += f'\nLog: less {log_file.name}\n'
        test.echo(msg)
        write(msg)

    if (proc.returncode == 0 or
            pytest.terminate_on_failure) and test.teardown is not None:
        subprocess_utils.run(
            test.teardown,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
            shell=True,
        )

    if proc.returncode:
        if log_to_stdout:
            raise Exception(f'test failed')
        else:
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


_VALIDATE_LAUNCH_OUTPUT = (
    # Validate the output of the job submission:
    # ⚙️ Launching on Kubernetes.
    #   Pod is up.
    # ✓ Cluster launched: test. View logs at: ~/sky_logs/sky-2024-10-07-19-44-18-177288/provision.log
    # ⚙️ Running setup on 1 pod.
    # running setup
    # ✓ Setup completed.
    # ⚙️ Job submitted, ID: 1.
    # ├── Waiting for task resources on 1 node.
    # └── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
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
    'echo "$s" | grep -A 1 "Running setup on" | grep "running setup" && '
    'echo "==Validating running output hints==" && echo "$s" | '
    'grep -A 1 "Job submitted, ID:" | '
    'grep "Waiting for task resources on " && '
    'echo "==Validating task output starting==" && echo "$s" | '
    'grep -A 1 "Job started. Streaming logs..." | grep "(min, pid=" && '
    'echo "==Validating task output ending==" && '
    'echo "$s" | grep -A 1 "task run finish" | '
    'grep "Job finished (status: SUCCEEDED)" && '
    'echo "==Validating task output ending 2==" && '
    'echo "$s" | grep -A 5 "Job finished (status: SUCCEEDED)" | '
    'grep "Job ID:" && '
    'echo "$s" | grep -A 1 "Job ID:" | grep "Useful Commands"')
