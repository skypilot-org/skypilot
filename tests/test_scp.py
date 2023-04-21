


import hashlib
import sys
import uuid
import tempfile
import inspect
from typing import Dict, List, NamedTuple, Optional, Tuple
import colorama
import jinja2
import pytest
import time
from sky.utils import common_utils
import subprocess
from sky.utils import subprocess_utils

_smoke_test_hash = hashlib.md5(
    common_utils.user_and_hostname_hash().encode()).hexdigest()[:4]
test_id = str(uuid.uuid4())[-2:]


def _get_cluster_name() -> str:
    """Returns a user-unique cluster name for each test_<name>().

    Must be called from each test_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    if len(test_name) > 18:
        test_name = test_name[:18] + hashlib.md5(
            test_name.encode()).hexdigest()[:3]
    return f'{test_name}-{_smoke_test_hash}-{test_id}'

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



# ------------ Test stale job ------------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
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



def test_large_job_queue(generic_cloud: str):
    name = _get_cluster_name()
    test = Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud}',
            f'for i in `seq 1 75`; do sky exec {name} -d "echo $i; sleep 100000000"; done',
            f'sky cancel -y {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 20',
            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # The first 16 jobs are canceled, so there should be 75 - 32 = 43 jobs PENDING.
            f'sky queue {name} | grep -v grep | grep PENDING | wc -l | grep 43',
        ],
        f'sky down -y {name}',
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














LAMBDA_TYPE = '--cloud lambda --gpus A100'


storage_setup_commands = [
    'touch ~/tmpfile', 'mkdir -p ~/tmp-workdir',
    'touch ~/tmp-workdir/tmp\ file', 'touch ~/tmp-workdir/foo',
    'ln -f -s ~/tmp-workdir/ ~/tmp-workdir/circle-link',
    'touch ~/.ssh/id_rsa.pub'
]


@pytest.mark.lambda_cloud
def test_lambda_file_mounts():
    name = _get_cluster_name()
    test_commands = [
        *storage_setup_commands,
        f'sky launch -y -c {name} {LAMBDA_TYPE} --num-nodes 1 examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'lambda_using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)



@pytest.mark.lambda_cloud
def test_lambda_logs():
    name = _get_cluster_name()
    timestamp = time.time()
    test = Test(
        'lambda_cli_logs',
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


@pytest.mark.lambda_cloud
def test_lambda_job_queue():
    name = _get_cluster_name()
    test = Test(
        'lambda_job_queue',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --gpus A100:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --gpus A100:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --gpus A100:0.5 -d examples/job_queue/job.yaml',
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




@pytest.mark.lambda_cloud
def test_lambda_autodown():
    name = _get_cluster_name()
    test = Test(
        'lambda_autodown',
        [
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} {LAMBDA_TYPE} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 200',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {LAMBDA_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 200',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    run_one_test(test)