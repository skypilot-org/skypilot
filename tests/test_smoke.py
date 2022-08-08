import hashlib
import inspect
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional, Tuple
import uuid

import colorama
import pytest

import sky
from sky import global_user_state
from sky.data import storage as storage_lib
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
        )
        try:
            proc.wait(timeout=test.timeout)
        except subprocess.TimeoutExpired as e:
            log_file.flush()
            test.echo(f'Timeout after {test.timeout} seconds.')
            test.echo(e)
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
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
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
            'sleep 40',
            f'sky start {name} -y',
            f'sky logs {name} 1 --status',
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
            f'sky launch -y -c {name} examples/env_check.yaml',
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
            f'sky exec {name} -d examples/job_queue/job.yaml',
            f'sky exec {name} -d examples/job_queue/job.yaml',
            f'sky exec {name} -d examples/job_queue/job.yaml',
            f'sky logs {name} 2',
            f'sky queue {name}',
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
            f'sky exec {name} -d examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -d examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -d examples/job_queue/job_multinode.yaml',
            f'sky cancel {name} 1',
            f'sky logs {name} 2',
            f'sky queue {name}',
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
            f'sky stop -y {name}',
            f'sleep 20',
            f'sky start -y {name}',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
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
            f'sky stop -y {name}',
            f'sky start -y {name}',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
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
            f'sky status | grep {name} | grep "1 min"',  # Ensure autostop is set.
            'sleep 180',
            f'sky status --refresh | grep {name} | grep STOPPED',  # Ensure the cluster is STOPPED.
            f'sky start -y {name}',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
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
    test = Test(
        'managed-spot',
        [
            f'sky spot launch -n {name}-1 examples/managed_spot.yaml -y -d',
            f'sky spot launch -n {name}-2 examples/managed_spot.yaml -y -d',
            'sleep 5',
            f'sky spot status | grep {name}-1 | head -n1 | grep STARTING',
            f'sky spot status | grep {name}-2 | head -n1 | grep STARTING',
            f'sky spot cancel -y -n {name}-1',
            'sleep 200',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name}-1 | head -n1 | grep CANCELLED',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        f'sky spot cancel -y -n {name}-1; sky spot cancel -y -n {name}-2',
    )
    run_one_test(test)


# ---------- Testing managed spot ----------
def test_gcp_spot():
    """Test managed spot on GCP."""
    name = _get_cluster_name()
    test = Test(
        'managed-spot-gcp',
        [
            f'sky spot launch -n {name} --cloud gcp "sleep 3600" -y -d',
            'sleep 5',
            # Captures & prints the table for easier debugging. Two echo's to
            # separate the table from the grep output.
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep STARTING',
            'sleep 200',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep RUNNING',
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
            f'sky spot launch --cloud aws --region {region} -n {name} "sleep 1000"  -y -d',
            'sleep 300',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
            # Terminate the cluster manually.
            f'aws ec2 terminate-instances --region {region} --instance-ids $('
            f'aws ec2 describe-instances --region {region} '
            f'--filters Name=tag:ray-cluster-name,Values={name}* '
            f'--query Reservations[].Instances[].InstanceId '
            '--output text)',
            'sleep 50',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RECOVERING"',
            'sleep 200',
            f's=$(sky spot status); printf "$s"; echo; echo; printf "$s" | grep {name} | head -n1 | grep "RUNNING"',
        ],
        f'sky spot cancel -y -n {name}',
    )
    run_one_test(test)


# ---------- Testing storage for managed spot ----------
def test_spot_storage():
    """Test storage with managed spot"""
    name = _get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_spot_with_storage.yaml').read_text()
    yaml_str = yaml_str.replace('sky-workdir-zhwu',
                                f'sky-test-{int(time.time())}')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_str)
        f.flush()
        file_path = f.name
        test = Test(
            'managed-spot-storage',
            [
                f'sky spot launch -n {name} {file_path} -y',
                'sleep 60',  # Wait the spot status to be updated
                f'sky spot status | grep {name} | grep SUCCEEDED',
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
            f'sky launch -c {name} -y --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKY_NODE_IPS\\" ]] && [[ ! -z \\"\$SKY_NODE_RANK\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKY_NODE_IPS\\" ]] && [[ ! -z \\"\$SKY_NODE_RANK\\" ]]) || exit 1"',
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
            f'sky spot launch -n {name} -y --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKY_NODE_IPS\\" ]] && [[ ! -z \\"\$SKY_NODE_RANK\\" ]]) || exit 1"',
            'sleep 10',
            f'sky spot status | grep {name} | grep SUCCEEDED',
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
        'tmp_public_storage_obj, store_type',
        [('s3://tcga-2-open', storage_lib.StoreType.S3),
         ('gs://gcp-public-data-sentinel-2', storage_lib.StoreType.GCS)],
        indirect=['tmp_public_storage_obj'])
    def test_public_bucket(self, tmp_public_storage_obj, store_type):
        # Creates a new bucket with a public source and verifies that it is not
        # added to global_user_state.
        tmp_public_storage_obj.add_store(store_type)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_public_storage_obj.name not in out.decode('utf-8')

    @staticmethod
    def cli_ls_cmd(store_type, bucket_name):
        if store_type == storage_lib.StoreType.S3:
            return ['aws', 's3', 'ls', f's3://{bucket_name}']
        if store_type == storage_lib.StoreType.GCS:
            return ['gsutil', 'ls', f'gs://{bucket_name}']

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
