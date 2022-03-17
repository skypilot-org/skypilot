import subprocess
import tempfile
from typing import List, Optional, Tuple, NamedTuple
from sky.backends import backend_utils


class Test(NamedTuple):
    name: str
    # Each command is executed serially.  If any failed, the remaining commands
    # are not run and the test is treated as failed.
    commands: List[str]
    teardown: Optional[str] = None
    # Timeout for each command in seconds.
    timeout: int = 15 * 60
    # timeout: int = 10


# Hack: without this, Ray seems to mess up \r and this script will output
# misaligned spacing after running for a while.
def echo(*args):
    with open('test.log', 'a') as f:
        print(*args, file=f)


def run_one_test(test: Test) -> Tuple[int, str, str]:
    # FIXME(zongheng,suquark): starting all tests (almost) together fails
    # backend_utils#write_cluster_config() -> wheel_utils#build_sky_wheel().
    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)

    echo(f'{test.name}: per-command timeout'
         f'={test.timeout} seconds.')
    echo(f'  tail -f -n100 {log_file.name}')
    for command in test.commands:
        echo(f'  {command}')
        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,
        )
        message = (f'{test.name}: test command exited with non-zero status: '
                   f'{command}')
        error_occurred = (f'{message}')
        try:
            proc.wait(timeout=test.timeout)
        except subprocess.TimeoutExpired as e:
            log_file.flush()
            echo(e)
            echo(error_occurred)
            proc.returncode = 1  # None if we don't set it.

            # raise e  # Raise = retry
            break  # no retry

        if proc.returncode:
            echo(error_occurred)
            break

    outcome = 'failed' if proc.returncode else 'succeeded'
    echo(f'{test.name} {outcome}. Log: less {log_file.name}')
    if test.teardown is not None:
        echo(f'Teardown: {test.teardown}')
        backend_utils.run(
            test.teardown,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
            shell=True,
        )

    if proc.returncode:
        raise Exception(
            f'{test.name}: test command exited with non-zero status.\n'
            f' Log: less {log_file.name}')


# ---------- Dry run: 2 Tasks in a chain. ----------
def test_example_app():
    test = Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    run_one_test(test)


def test_min():
    test = Test(
        'minimal',
        [
            'sky launch -y -c test-min examples/minimal.yaml',
            'sky logs test-min 1 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-min',
    )
    run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
def test_env_check():
    test = Test(
        'env_check',
        [
            'sky launch -y -c test-env examples/env_check.yaml',
            'sky logs test-env 1 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-env',
    )
    run_one_test(test)


# ---------- file_mounts ----------
def test_file_mounts():
    test = Test(
        'using_file_mounts',
        [
            'touch ~/tmpfile',
            'mkdir -p ~/tmp-workdir',
            'touch ~/tmp-workdir/foo',
            'sky launch -y -c test-fm examples/using_file_mounts.yaml',
            'sky logs test-fm 1 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-fm',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- Job Queue. ----------
def test_job_queue():
    test = Test(
        'job_queue',
        [
            'sky launch -y -c test-jq examples/job_queue/cluster.yaml',
            'sky exec test-jq -d examples/job_queue/job.yaml',
            'sky exec test-jq -d examples/job_queue/job.yaml',
            'sky exec test-jq -d examples/job_queue/job.yaml',
            'sky logs test-jq 2',
            'sky queue test-jq',
        ],
        'sky down -y test-jq',
    )
    run_one_test(test)


def test_multi_node_job_queue():
    test = Test(
        'job_queue_multinode',
        [
            'sky launch -y -c test-mjq examples/job_queue/cluster_multinode.yaml',
            'sky exec test-mjq -d examples/job_queue/job_multinode.yaml',
            'sky exec test-mjq -d examples/job_queue/job_multinode.yaml',
            'sky exec test-mjq -d examples/job_queue/job_multinode.yaml',
            'sky cancel test-mjq 1',
            'sky logs test-mjq 2',
            'sky queue test-mjq',
        ],
        'sky down -y test-mjq',
    )
    run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster.. ----------
def test_multi_echo():
    test = Test(
        'multi_echo',
        ['python examples/multi_echo.py'] +
        # Ensure jobs succeeded.
        [f'sky logs test-multi-echo {i + 1} --status' for i in range(16)],
        'sky down -y test-multi-echo',
    )
    run_one_test(test)


# ---------- Task: 1 node training. ----------
def test_huggingface_glue_imdb():
    test = Test(
        'huggingface_glue_imdb_app',
        [
            ('sky launch -y -c test-huggingface '
             'examples/huggingface_glue_imdb_app.yaml'),
            'sky logs test-huggingface 1 --status',  # Ensure the job succeeded.
            'sky exec test-huggingface examples/huggingface_glue_imdb_app.yaml',
            'sky logs test-huggingface 2 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-huggingface',
    )
    run_one_test(test)


# ---------- TPU. ----------
def test_tpu():
    test = Test(
        'tpu_app',
        [
            'sky launch -y -c test-tpu examples/tpu_app.yaml',
            'sky logs test-tpu 1 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-tpu',
    )
    run_one_test(test)


# ---------- Simple apps. ----------
def test_multi_hostname():
    test = Test(
        'multi_hostname',
        [
            'sky launch -y -c test-mh examples/multi_hostname.yaml',
            'sky logs test-mh 1 --status',  # Ensure the job succeeded.
            'sky exec test-mh examples/multi_hostname.yaml',
            'sky logs test-mh 2 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-mh',
    )
    run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
def test_distributed_tf():
    test = Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            'python examples/resnet_distributed_tf_app.py',
            'sky logs test-dtf 1 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-dtf',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
def test_azure_start_stop():
    test = Test(
        'azure-start-stop',
        [
            'sky launch -y -c test-azure-start-stop examples/azure_start_stop.yaml',
            'sky exec test-azure-start-stop examples/azure_start_stop.yaml',
            'sky logs test-azure-start-stop 1 --status',  # Ensure the job succeeded.
            'sky stop -y test-azure-start-stop',
            'sky start -y test-azure-start-stop',
            'sky exec test-azure-start-stop examples/azure_start_stop.yaml',
            'sky logs test-azure-start-stop 2 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-azure-start-stop',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    )
    run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
def test_gcp_start_stop():
    test = Test(
        'gcp-start-stop',
        [
            'sky launch -y -c test-gcp-start-stop examples/gcp_start_stop.yaml',
            'sky logs test-gcp-start-stop 1 --status',  # Ensure the job succeeded.
            'sky exec test-gcp-start-stop examples/gcp_start_stop.yaml',
            'sky logs test-gcp-start-stop 2 --status',  # Ensure the job succeeded.
            'sky stop -y test-gcp-start-stop',
            'sky start -y test-gcp-start-stop',
            'sky exec test-gcp-start-stop examples/gcp_start_stop.yaml',
            'sky logs test-gcp-start-stop 3 --status',  # Ensure the job succeeded.
        ],
        'sky down -y test-gcp-start-stop',
    )
    run_one_test(test)
