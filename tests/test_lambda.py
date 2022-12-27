import hashlib
import inspect
import subprocess
import sys
import tempfile
import time
from typing import List, NamedTuple, Optional, Tuple
import uuid

import colorama

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
# smoke test.
test_id = str(uuid.uuid4())[-2:]

# We have credits for gpu_1x_a100_sxm4
LAMBDA_TYPE = '--cloud lambda --gpus A100 --instance-type gpu_1x_a100_sxm4'


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


# ---------- A minimal task ----------
def test_minimal():
    name = _get_cluster_name()
    test = Test(
        'minimal',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/minimal.yaml',
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
            f'sky launch -y -c {name} {LAMBDA_TYPE} --region us-west-2 examples/minimal.yaml',
            f'sky exec {name} {LAMBDA_TYPE} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-west-2',  # Ensure the region is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- File mounts ----------
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
            f'sky launch -y -c {name} {LAMBDA_TYPE} --num-nodes 1 examples/using_file_mounts.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        [
            f'sky down -y {name}',
            'rm ~/tmpfile',
            'rm -r ~/tmp-workdir',
        ],
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
            f'sky launch -y -c {name} {LAMBDA_TYPE} "echo {timestamp} 1"',
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
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --cloud lambda --gpus A100:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --cloud lambda --gpus A100:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --cloud lambda --gpus A100:0.5 -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Task: 1 node training. ----------
def test_huggingface():
    name = _get_cluster_name()
    test = Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing Autodowning ----------
def test_autodown():
    name = _get_cluster_name()
    test = Test(
        'autodown',
        [
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} examples/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} --down examples/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} {LAMBDA_TYPE} examples/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 240',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} --down examples/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 240',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing `sky cancel` ----------
def test_cancel_lambda():
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-cancel-lambda',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/minimal.yaml',
            f'sky exec {name} -n {name}-1 -d {LAMBDA_TYPE} "while true; do echo \'Hello SkyPilot\'; sleep 2; done"',
            'sleep 20',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky cancel {name} 2',
            f'sleep 5',
            f'sky queue {name} | grep {name}-1 | grep CANCELLED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing env ----------
def test_inline_env():
    """Test env"""
    name = _get_cluster_name()
    test = Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y {LAMBDA_TYPE} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)
