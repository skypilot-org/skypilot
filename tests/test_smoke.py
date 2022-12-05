import hashlib
import inspect
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional, Tuple
import urllib.parse
import uuid

import colorama
import pytest

import sky
from sky import global_user_state
from sky.data import storage as storage_lib
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import subprocess_utils

# For uniquefying users on shared-account cloud providers. Used as part of the
# cluster names.
_smoke_test_hash = hashlib.md5(
    common_utils.user_and_hostname_hash().encode()).hexdigest()[:8]

# To avoid the second smoke test reusing the cluster launched in the first
# smoke test. Also required for test_spot_recovery to make sure the manual
# termination with aws ec2 does not accidentally terminate other spot clusters
# from the different spot launch with the same cluster name but a different job
# id.
test_id = str(uuid.uuid4())[-2:]


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
    test_name = caller_func_name.replace('_', '-')
    return f'{test_name}-{_smoke_test_hash}-{test_id}'


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
            test.echo(e)
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
    if proc.returncode == 0 and test.teardown is not None:
        subprocess_utils.run(
            test.teardown,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
            shell=True,
        )

    if proc.returncode:
        raise Exception(f'test failed: less {log_file.name}')


# ---------- Dry run: 2 Tasks in a chain. ----------
def test_example_app():
    test = Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    run_one_test(test)


# ---------- A minimal task ----------
def test_minimal():
    name = _get_cluster_name()
    test = Test(
        'minimal',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Ensure the raylet process has the correct file descriptor limit.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Test region ----------
def test_region():
    name = _get_cluster_name()
    test = Test(
        'region',
        [
            f'sky launch -y -c {name} --region us-west-2 examples/minimal.yaml',
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-west-2',  # Ensure the region is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Test zone ----------
def test_zone():
    name = _get_cluster_name()
    test = Test(
        'zone',
        [
            f'sky launch -y -c {name} examples/minimal.yaml --zone us-west-2b',
            f'sky exec {name} examples/minimal.yaml --zone us-west-2b',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-west-2b',  # Ensure the zone is correct.
        ],
        f'sky down -y {name} {name}-2 {name}-3',
    )
    run_one_test(test)


def test_image_id_dict():
    name = _get_cluster_name()
    test = Test(
        'image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name} {name}-2 {name}-3',
    )
    run_one_test(test)


def test_image_id_dict_with_region():
    name = _get_cluster_name()
    test = Test(
        'image_id_dict_with_region',
        [
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --region us-west-2 examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --region us-west-1 examples/per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-west-1',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --region us-west-1 examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-west-1 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_image_id_dict_with_zone():
    name = _get_cluster_name()
    test = Test(
        'image_id_dict_with_region',
        [
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --zone us-west-2b examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --zone us-west-1a examples/per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-west-1a',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --zone us-west-1a examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-west-1 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_stale_job():
    name = _get_cluster_name()
    test = Test(
        'stale-job',
        [
            f'sky launch -y -c {name} --cloud gcp "echo hi"',
            f'sky exec {name} --cloud gcp -d "echo start; sleep 10000"',
            f'sky stop {name} -y',
            'sleep 100',  # Ensure this is large enough, else GCP leaks.
            f'sky start {name} -y',
            f'sky logs {name} 1 --status',
            f's=$(sky queue {name}); printf "$s"; echo; echo; printf "$s" | grep FAILED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_stale_job_manual_restart():
    name = _get_cluster_name()
    region = 'us-west-2'
    test = Test(
        'stale-job',
        [
            f'sky launch -y -c {name} --cloud aws --region {region} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            f'id=`aws ec2 describe-instances --region {region} --filters '
            f'Name=tag:ray-cluster-name,Values={name} '
            f'--query Reservations[].Instances[].InstanceId '
            '--output text`; '
            f'aws ec2 stop-instances --region {region} '
            '--instance-ids $id',
            'sleep 40',
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            f'sleep {events.JobUpdateEvent.EVENT_INTERVAL_SECONDS}',
            f's=$(sky queue {name}); printf "$s"; echo; echo; printf "$s" | grep FAILED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
def test_env_check():
    name = _get_cluster_name()
    test = Test(
        'env_check',
        [
            f'sky launch -y -c {name} --detach-setup examples/env_check.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- file_mounts ----------
def test_file_mounts():
    name = _get_cluster_name()
    test = Test(
        'using_file_mounts',
        [
            'touch ~/tmpfile',
            'mkdir -p ~/tmp-workdir',
            'touch ~/tmp-workdir/tmp\ file',
            'touch ~/tmp-workdir/foo',
            'ln -f -s ~/tmp-workdir/ ~/tmp-workdir/circle-link',
            f'sky launch -y -c {name} examples/using_file_mounts.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- CLI logs ----------
def test_logs():
    name = _get_cluster_name()
    timestamp = time.time()
    test = Test(
        'cli_logs',
        [
            f'sky launch -y -c {name} --num-nodes 2 "echo {timestamp} 1"',
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
def test_job_queue():
    name = _get_cluster_name()
    test = Test(
        'job_queue',
        [
            f'sky launch -y -c {name} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel {name} 3',
            f'sky exec {name} --gpus K80:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus K80:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_n_node_job_queue():
    name = _get_cluster_name()
    test = Test(
        'job_queue_multinode',
        [
            f'sky launch -y -c {name} examples/job_queue/cluster_multinode.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job_multinode.yaml',
            f'sky launch -c {name} -n {name}-3 --detach-setup -d examples/job_queue/job_multinode.yaml',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep SETTING_UP)',
            'sleep 90',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            f'sky cancel {name} 1',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 --detach-setup -d examples/job_queue/job_multinode.yaml',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel {name} 4',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus K80:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus K80:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus K80:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_large_job_queue():
    name = _get_cluster_name()
    test = Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cloud gcp',
            f'for i in `seq 1 75`; do sky exec {name} -d "echo $i; sleep 100000000"; done',
            f'sky cancel {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 20',
            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # The first 16 jobs are canceled, so there should be 75 - 32 = 43 jobs PENDING.
            f'sky queue {name} | grep -v grep | grep PENDING | wc -l | grep 43',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
def test_multi_echo():
    name = _get_cluster_name()
    test = Test(
        'multi_echo',
        [
            f'python examples/multi_echo.py {name}',
            'sleep 70',
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
def test_huggingface():
    name = _get_cluster_name()
    test = Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- TPU. ----------
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
def test_tpu_vm():
    name = _get_cluster_name()
    test = Test(
        'tpu_vm_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky status --refresh | grep {name} | grep STOPPED',  # Ensure the cluster is STOPPED.
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
def test_multi_hostname():
    name = _get_cluster_name()
    test = Test(
        'multi_hostname',
        [
            f'sky launch -y -c {name} examples/multi_hostname.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/multi_hostname.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
def test_distributed_tf():
    name = _get_cluster_name()
    test = Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            f'python examples/resnet_distributed_tf_app.py {name}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
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
            f'sky start -y {name}',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
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
            f'sky start -y {name}',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    run_one_test(test)


# ---------- Testing Autostopping ----------
def test_autostop():
    name = _get_cluster_name()
    test = Test(
        'autostop',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 examples/minimal.yaml',
            f'sky autostop -y {name} -i 1',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the cluster is not stopped early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',

            # Ensure the cluster is STOPPED.
            'sleep 90',
            f'sky status --refresh | grep {name} | grep STOPPED',

            # Ensure the cluster is UP and the autostop setting is reset ('-').
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',

            # Ensure the job succeeded.
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',

            # Test restarting the idleness timer via cancel + reset:
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky autostop -y {name} --cancel',
            f'sky autostop -y {name} -i 1',  # Should restart the timer.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            'sleep 90',
            f'sky status --refresh | grep {name} | grep STOPPED',

            # Test restarting the idleness timer via exec:
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky exec {name} echo hi',  # Should restart the timer.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            'sleep 90',
            f'sky status --refresh | grep {name} | grep STOPPED',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing Autodowning ----------
def test_autodown():
    name = _get_cluster_name()
    test = Test(
        'autodown',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --cloud aws examples/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud aws --num-nodes 2 --down examples/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} --cloud aws examples/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 240',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud aws --num-nodes 2 --down examples/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 240',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
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
            f'sky cancel {name} 1',
            'sleep 60',
            # check if the python job is gone.
            f'sky exec {name} "! nvidia-smi | grep python"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=timeout,
    )
    return test


# ---------- Testing `sky cancel` on AWS ----------
def test_cancel_aws():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'aws')
    run_one_test(test)


# ---------- Testing `sky cancel` on Azure ----------
def test_cancel_azure():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'azure', timeout=30 * 60)
    run_one_test(test)


# ---------- Testing `sky cancel` on GCP ----------
def test_cancel_gcp():
    name = _get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'gcp')
    run_one_test(test)


# ---------- Testing `sky cancel` ----------
def test_cancel_pytorch():
    name = _get_cluster_name()
    test = Test(
        'cancel-pytorch',
        [
            f'sky launch -c {name} examples/resnet_distributed_torch.yaml -y -d',
            # Wait the GPU process to start.
            'sleep 90',
            f'sky exec {name} "nvidia-smi | grep python"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel {name} 1',
            'sleep 60',
            f'sky exec {name} "(nvidia-smi | grep \'No running process\') || '
            # Ensure Xorg is the only process running.
            '[ \$(nvidia-smi | grep -A 10 Processes | grep -A 10 === | grep -v Xorg) -eq 2 ]"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing use-spot option ----------
def test_use_spot():
    """Test use-spot and sky exec."""
    name = _get_cluster_name()
    test = Test(
        'use-spot',
        [
            f'sky launch -c {name} examples/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status',
            f'sky exec {name} echo hi',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing managed spot ----------
def test_spot():
    """Test the spot yaml."""
    name = _get_cluster_name()
    cancel_command = (
        f'sky spot cancel -y -n {name}-1; sky spot cancel -y -n {name}-2')
    test = Test(
        'managed-spot',
        [
            f'sky spot launch -n {name}-1 examples/managed_spot.yaml -y -d',
            f'sky spot launch -n {name}-2 examples/managed_spot.yaml -y -d',
            'sleep 5',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-1 | head -n1 | grep "STARTING\|RUNNING"',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-2 | head -n1 | grep "STARTING\|RUNNING"',
            f'sky spot cancel -y -n {name}-1',
            'sleep 5',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-1 | head -n1 | grep CANCELLED',
            'sleep 200',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
            # Test autostop. This assumes no regular spot jobs are running.
            cancel_command,
            'sleep 720',  # Sleep for a bit more than the default 10m.
            'sky status --refresh | grep sky-spot-controller- | grep STOPPED',
            'sky start "sky-spot-controller-*" -y',
            # Ensures it's up and the autostop setting is restored.
            'sky status | grep sky-spot-controller- | grep UP | grep 10m',
        ],
        cancel_command,
    )
    run_one_test(test)


# ---------- Testing managed spot ----------
def test_spot_gcp():
    """Test managed spot on GCP."""
    name = _get_cluster_name()
    test = Test(
        'managed-spot-gcp',
        [
            f'sky spot launch -n {name} --cloud gcp "sleep 3600" -y -d',
            'sleep 5',
            # Captures & prints the table for easier debugging. Two echo's to
            # separate the table from the grep output.
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep STARTING',
            'sleep 200',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep RUNNING',
        ],
        f'sky spot cancel -y -n {name}',
    )
    run_one_test(test)


# ---------- Testing managed spot recovery ----------
def test_spot_recovery():
    """Test managed spot recovery."""
    name = _get_cluster_name()
    region = 'us-west-2'
    test = Test(
        'managed-spot-recovery',
        [
            f'sky spot launch --cloud aws --region {region} -n {name} "echo SKYPILOT_JOB_ID: \$SKYPILOT_JOB_ID; sleep 1800"  -y -d',
            'sleep 360',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_JOB_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 50',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_JOB_ID | grep "$RUN_ID"',
        ],
        f'sky spot cancel -y -n {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


def test_spot_recovery_multi_node():
    """Test managed spot recovery."""
    name = _get_cluster_name()
    region = 'us-west-2'
    test = Test(
        'managed-spot-recovery-multi',
        [
            f'sky spot launch --cloud aws --region {region} -n {name} --num-nodes 2 "echo SKYPILOT_JOB_ID: \$SKYPILOT_JOB_ID; sleep 1800"  -y -d',
            'sleep 400',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(sky spot logs -n {name} --no-follow | grep SKYPILOT_JOB_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}* '
             'Name=tag:ray-node-type,Values=worker '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 50',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 420',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky spot logs -n {name} --no-follow | grep SKYPILOT_JOB_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        f'sky spot cancel -y -n {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


def test_spot_cancellation():
    name = _get_cluster_name()
    region = 'us-east-2'
    test = Test(
        'managed-spot-cancellation',
        [
            # Test cancellation during spot cluster being launched.
            f'sky spot launch --cloud aws --region {region} -n {name} "sleep 1000"  -y -d',
            'sleep 60',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "STARTING"',
            f'sky spot cancel -y -n {name}',
            'sleep 5',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "CANCELLED"',
            'sleep 100',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && printf "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky spot launch --cloud aws --region {region} -n {name}-2 tests/test_yamls/long_setup.yaml  -y -d',
            'sleep 300',
            f'sky spot cancel -y -n {name}-2',
            'sleep 5',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-2 | head -n1 | grep "CANCELLED"',
            'sleep 100',
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}-2* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && printf "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancellation during spot job is recovering.
            f'sky spot launch --cloud aws --region {region} -n {name}-3 "sleep 1000"  -y -d',
            'sleep 300',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-3 | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}-3* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            'sleep 50',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-3 | head -n1 | grep "RECOVERING"',
            f'sky spot cancel -y -n {name}-3',
            'sleep 10',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name}-3 | head -n1 | grep "CANCELLED"',
            'sleep 90',
            # The cluster should be terminated (shutting-down) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name}-3* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && printf "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "pending|running|stopped|stopping"'
            ),
        ])
    run_one_test(test)


# ---------- Testing storage for managed spot ----------
def test_spot_storage():
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
            'managed-spot-storage',
            [
                f'sky spot launch -n {name} {file_path} -y',
                'sleep 60',  # Wait the spot queue to be updated
                f'sky spot queue | grep {name} | grep SUCCEEDED',
                f'[ $(aws s3api list-buckets --query "Buckets[?contains(Name, \'{storage_name}\')].Name" --output text | wc -l) -eq 0 ]'
            ],
            f'sky spot cancel -y -n {name}',
        )
        run_one_test(test)


# ---------- Testing spot TPU ----------
def test_spot_tpu():
    """Test managed spot on TPU."""
    name = _get_cluster_name()
    test = Test(
        'test-spot-tpu',
        [
            f'sky spot launch -n {name} examples/tpu/tpuvm_mnist.yaml -y -d',
            'sleep 5',
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep STARTING',
            'sleep 600',  # TPU takes a while to launch
            f's=$(sky spot queue); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        f'sky spot cancel -y -n {name}',
    )
    run_one_test(test)


# ---------- Testing env ----------
def test_inline_env():
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing env for spot ----------
def test_inline_spot_env():
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-spot-env',
        [
            f'sky spot launch -n {name} -y --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            'sleep 20',
            f's=$(sky spot queue) && printf "$s" && echo "$s"  | grep {name} | grep SUCCEEDED',
        ],
        f'sky spot cancel -y -n {name}',
    )
    run_one_test(test)


# ---------- Testing custom image ----------
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


# ------- Testing the core API --------
# Most of the core APIs have been tested in the CLI tests.
# These tests are for testing the return value of the APIs not fully used in CLI.
def test_core_api():
    name = _get_cluster_name()
    sky.launch
    # TODO(zhwu): Add a test for core api.


# ---------- Testing Storage ----------
class TestStorageWithCredentials:
    """Storage tests which require credentials and network connection"""

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
    def tmp_copy_mnt_existing_storage_obj(self, tmp_scratch_storage_obj):
        # Creates a copy mount storage which reuses an existing storage object.
        tmp_scratch_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_scratch_storage_obj.name

        # Try to initialize another storage with the storage object created
        # above, but now in COPY mode. This should succeed.
        yield from self.yield_storage_object(name=storage_name,
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
    def tmp_public_storage_obj(self, request):
        # Initializes a storage object with a public bucket
        storage_obj = storage_lib.Storage(source=request.param)
        yield storage_obj
        # This does not require any deletion logic because it is a public bucket
        # and should not get added to global_user_state.

    @pytest.mark.parametrize(
        'store_type', [storage_lib.StoreType.S3, storage_lib.StoreType.GCS])
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
            ['sky', 'storage', 'delete', tmp_local_storage_obj.name])

        # Run sky storage ls to check if storage object is deleted
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj.name not in out.decode('utf-8')

    @pytest.mark.parametrize(
        'store_type', [storage_lib.StoreType.S3, storage_lib.StoreType.GCS])
    def test_bucket_bulk_deletion(self, store_type):
        # Create a temp folder with over 256 files and folders, upload
        # files and folders to a new bucket, then delete bucket.
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.check_output(f'mkdir -p {tmpdir}/folder{{000..255}}',
                                    shell=True)
            subprocess.check_output(f'touch {tmpdir}/test{{000..255}}.txt',
                                    shell=True)
            subprocess.check_output(
                f'touch {tmpdir}/folder{{000..255}}/test.txt', shell=True)

            timestamp = str(time.time()).replace('.', '')
            store_obj = storage_lib.Storage(name=f'sky-test-{timestamp}',
                                            source=tmpdir)
            store_obj.add_store(store_type)

        subprocess.check_output(['sky', 'storage', 'delete', store_obj.name])

        output = subprocess.check_output(['sky', 'storage', 'ls'])
        assert store_obj.name not in output.decode('utf-8')

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

    @pytest.mark.parametrize('nonexist_bucket_url',
                             ['s3://{random_name}', 'gs://{random_name}'])
    def test_nonexistent_bucket(self, nonexist_bucket_url):
        # Attempts to create fetch a stroage with a non-existent source.
        # Generate a random bucket name and verify it doesn't exist:
        retry_count = 0
        while True:
            nonexist_bucket_name = str(uuid.uuid4())
            if nonexist_bucket_url.startswith('s3'):
                command = [
                    'aws', 's3api', 'head-bucket', '--bucket',
                    nonexist_bucket_name
                ]
                expected_output = '404'
            elif nonexist_bucket_url.startswith('gs'):
                command = [
                    'gsutil', 'ls',
                    nonexist_bucket_url.format(random_name=nonexist_bucket_name)
                ]
                expected_output = 'BucketNotFoundException'
            else:
                raise ValueError('Unsupported bucket type '
                                 f'{nonexist_bucket_url}')

            # Check if bucket exists using the cli:
            try:
                out = subprocess.check_output(command, stderr=subprocess.STDOUT)
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
                match='Attempted to connect to a non-existent bucket'):
            storage_obj = storage_lib.Storage(source=nonexist_bucket_url.format(
                random_name=nonexist_bucket_name))

    @pytest.mark.parametrize('private_bucket',
                             [f's3://imagenet', f'gs://imagenet'])
    def test_private_bucket(self, private_bucket):
        # Attempts to access private buckets not belonging to the user.
        # These buckets are known to be private, but may need to be updated if
        # they are removed by their owners.
        private_bucket_name = urllib.parse.urlsplit(private_bucket).netloc
        with pytest.raises(
                sky.exceptions.StorageBucketGetError,
                match=storage_lib._BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
                    name=private_bucket_name)):
            storage_obj = storage_lib.Storage(source=private_bucket)

    @staticmethod
    def cli_ls_cmd(store_type, bucket_name, suffix=''):
        if store_type == storage_lib.StoreType.S3:
            if suffix:
                url = f's3://{bucket_name}/{suffix}'
            else:
                url = f's3://{bucket_name}'
            return ['aws', 's3', 'ls', url]
        if store_type == storage_lib.StoreType.GCS:
            if suffix:
                url = f'gs://{bucket_name}/{suffix}'
            else:
                url = f'gs://{bucket_name}'
            return ['gsutil', 'ls', url]

    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS)])
    def test_upload_to_existing_bucket(self, ext_bucket_fixture, request,
                                       tmp_source, store_type):
        # Tries uploading existing files to newly created bucket (outside of
        # sky) and verifies that files are written.
        bucket_name = request.getfixturevalue(ext_bucket_fixture)
        storage_obj = storage_lib.Storage(name=bucket_name, source=tmp_source)
        storage_obj.add_store(store_type)

        # Check if tmp_source/tmp-file exists in the bucket using aws cli
        out = subprocess.check_output(self.cli_ls_cmd(store_type, bucket_name))
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

    @pytest.mark.parametrize(
        'store_type', [storage_lib.StoreType.S3, storage_lib.StoreType.GCS])
    def test_list_source(self, tmp_local_list_storage_obj, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        tmp_local_list_storage_obj.add_store(store_type)

        # Check if tmp-file exists in the bucket root using cli
        out = subprocess.check_output(
            self.cli_ls_cmd(store_type, tmp_local_list_storage_obj.name))
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))

        # Check if tmp-file exists in the bucket/tmp-source using cli
        out = subprocess.check_output(
            self.cli_ls_cmd(store_type, tmp_local_list_storage_obj.name,
                            'tmp-source/'))
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))


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
