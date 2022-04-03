import getpass
import inspect
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Tuple, NamedTuple
import uuid

import colorama
import pytest

from sky import global_user_state
from sky.backends import backend_utils
from sky.data import storage as storage_lib

# (username, mac addr last 4 chars): for uniquefying users on shared-account
# cloud providers.
_user_and_mac = f'{getpass.getuser()}-{hex(uuid.getnode())[-4:]}'


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


def _get_cluster_name():
    """Returns a user-unique cluster name for each test_<name>().

    Must be called from each test_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-')
    return f'{test_name}-{_user_and_mac}'


def run_one_test(test: Test) -> Tuple[int, str, str]:
    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)
    test.echo(f'Test started. Log: less {log_file.name}')
    for command in test.commands:
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
            test.echo(e)
            proc.returncode = 1  # None if we don't set it.
            break

        if proc.returncode:
            break

    style = colorama.Style
    fore = colorama.Fore
    outcome = (f'{fore.RED}Failed{style.RESET_ALL}'
               if proc.returncode else f'{fore.GREEN}Passed{style.RESET_ALL}')
    reason = f'\nReason: {command!r}' if proc.returncode else ''
    test.echo(f'{outcome}.'
              f'{reason}'
              f'\nLog: less {log_file.name}\n')
    if proc.returncode == 0 and test.teardown is not None:
        backend_utils.run(
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
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
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
            f'sky launch -y -c {name} examples/using_file_mounts.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
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


def test_multi_node_job_queue():
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


# ---------- Submitting multiple tasks to the same cluster.. ----------
def test_multi_echo():
    name = _get_cluster_name()  # Keep consistent with the py script.
    test = Test(
        'multi_echo',
        ['python examples/multi_echo.py'] +
        # Ensure jobs succeeded.
        [f'sky logs {name} {i + 1} --status' for i in range(16)],
        f'sky down -y {name}',
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
            f'sky launch -y -c {name} examples/tpu_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
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
    name = _get_cluster_name()  # Keep consistent with the py script.
    test = Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            'python examples/resnet_distributed_tf_app.py',
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
# @pytest.mark.slow
def test_autostop():
    name = _get_cluster_name()
    test = Test(
        'autostop',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 examples/minimal.yaml',
            f'sky autostop {name} -i 0',
            f'sky status --refresh | grep {name} | grep -q "0 min"',  # Ensure the cluster is STOPPED.
            'sleep 120',
            f'sky status --refresh | grep {name} | grep -q STOPPED',  # Ensure the cluster is STOPPED.
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -q UP',  # Ensure the cluster is UP.
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
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


class TestStorageWithCredentials:
    """Storage tests which require credentials and network connection"""

    @pytest.fixture
    def tmp_mount(self, tmp_path):
        # Creates a temporary directory with a file in it
        tmp_dir = tmp_path / 'tmp-mount'
        tmp_dir.mkdir()
        tmp_file = tmp_dir / 'tmp-file'
        tmp_file.write_text('test')
        yield str(tmp_dir)

    @pytest.fixture
    def tmp_bucket_name(self):
        # Creates a temporary bucket name
        yield f'sky-test-{int(time.time())}'

    @pytest.fixture
    def tmp_local_storage_obj(self, tmp_bucket_name, tmp_mount):
        # Creates a temporary storage object. Stores must be added in the test.
        storage_obj = storage_lib.Storage(name=tmp_bucket_name,
                                          source=tmp_mount)
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
    def tmp_awscli_bucket(self, tmp_bucket_name):
        # Creates a temporary bucket using awscli
        subprocess.check_call(['aws', 's3', 'mb', f's3://{tmp_bucket_name}'])
        yield tmp_bucket_name
        subprocess.check_call(
            ['aws', 's3', 'rb', f's3://{tmp_bucket_name}', '--force'])

    @pytest.fixture
    def tmp_public_storage_obj(self, tmp_bucket_name):
        # Initializes a storage object with a public bucket
        storage_obj = storage_lib.Storage(source='s3://tcga-2-open')
        yield storage_obj
        # This does not require any deletion logic because it is a public bucket
        # and should not get added to global_user_state.

    def test_new_bucket_creation_and_deletion(self, tmp_local_storage_obj):
        # Creates a new bucket with a local source, uploads files to it
        # and deletes it.
        tmp_local_storage_obj.add_store(storage_lib.StoreType.S3)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj.name in out.decode('utf-8')

        # Run sky storage delete to delete the storage object
        subprocess.check_output(
            ['sky', 'storage', 'delete', tmp_local_storage_obj.name])

        # Run sky storage ls to check if storage object is deleted
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj.name not in out.decode('utf-8')

    def test_public_bucket(self, tmp_public_storage_obj):
        # Creates a new bucket with a public source and verifies that it is not
        # added to global_user_state.
        tmp_public_storage_obj.add_store(storage_lib.StoreType.S3)

        # Run sky storage ls to check if storage object exists in the output
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_public_storage_obj.name not in out.decode('utf-8')

    def test_upload_to_existing_bucket(self, tmp_awscli_bucket, tmp_mount):
        # Tries uploading existing files to newly created bucket (outside of
        # sky) and verifies that files are written.
        storage_obj = storage_lib.Storage(name=tmp_awscli_bucket,
                                          source=tmp_mount)
        storage_obj.add_store(storage_lib.StoreType.S3)

        # Check if tmp_mount/tmp-file exists in the bucket using aws cli
        out = subprocess.check_output(
            ['aws', 's3', 'ls', f's3://{tmp_awscli_bucket}'])
        assert 'tmp-file' in out.decode('utf-8'), \
            'File not found in bucket - output was : {}'.format(out.decode
                                                                ('utf-8'))

        # Run sky storage ls to check if storage object exists in the output.
        # It should not exist because the bucket was created externally.
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert storage_obj.name not in out.decode('utf-8')
