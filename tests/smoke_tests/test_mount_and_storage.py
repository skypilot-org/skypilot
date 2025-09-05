# Smoke tests for SkyPilot for mounting storage
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_mount_and_storage.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_mount_and_storage.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_mount_and_storage.py::test_file_mounts
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_mount_and_storage.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_mount_and_storage.py --generic-cloud aws

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Dict, Optional, TextIO
import urllib.parse
import uuid

import jinja2
import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import clouds
from sky import global_user_state
from sky import skypilot_config
from sky.adaptors import azure
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.adaptors import nebius
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.skylet import constants
from sky.utils import controller_utils


# ---------- file_mounts ----------
@pytest.mark.no_vast  # VAST does not support num_nodes > 1 yet
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_file_mounts instead.
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 and storage mounting yet.
def test_file_mounts(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    extra_flags = ''
    if generic_cloud in 'kubernetes':
        # Kubernetes does not support multi-node
        # NOTE: S3 mounting now works on all architectures including ARM64
        #  (uses rclone fallback for ARM64, goofys for x86_64).
        extra_flags = '--num-nodes 1'
    test_commands = [
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {extra_flags} examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = smoke_tests_utils.Test(
        'using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud, 20 * 60),  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_file_mounts():
    name = smoke_tests_utils.get_cluster_name()
    test_commands = [
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} --num-nodes 1 examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = smoke_tests_utils.Test(
        'SCP_using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.oci  # For OCI object storage mounts and file mounts.
def test_oci_mounts():
    name = smoke_tests_utils.get_cluster_name()
    test_commands = [
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} --infra oci --num-nodes 2 examples/oci/oci-mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = smoke_tests_utils.Test(
        'oci_mounts',
        test_commands,
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Requires GCP
@pytest.mark.no_fluidstack  # Requires GCP to be enabled
@pytest.mark.no_hyperbolic  # Requires GCP to be enabled
def test_using_file_mounts_with_env_vars(generic_cloud: str):
    if smoke_tests_utils.is_remote_server_test():
        enabled_cloud_storages = smoke_tests_utils.get_enabled_cloud_storages()
        if not clouds.cloud_in_iterable(clouds.GCP(), enabled_cloud_storages):
            pytest.skip('Skipping test because GCS is not enabled')

    name = smoke_tests_utils.get_cluster_name()
    storage_name = TestStorageWithCredentials.generate_bucket_name()
    test_commands = [
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        (f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name} '
         f'--secret SECRETE_BUCKET_NAME={storage_name}'),
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        # Override with --env:
        (f'sky launch -y -c {name}-2 {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name} '
         '--env MY_LOCAL_PATH=tmpfile '
         f'--secret SECRETE_BUCKET_NAME={storage_name}'),
        f'sky logs {name}-2 1 --status',  # Ensure the job succeeded.
    ]
    test = smoke_tests_utils.Test(
        'using_file_mounts_with_env_vars',
        test_commands,
        (f'sky down -y {name} {name}-2',
         f'sky storage delete -y {storage_name} {storage_name}-2'),
        timeout=20 * 60,  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- storage ----------
def _storage_mounts_commands_generator(f: TextIO, cluster_name: str,
                                       storage_name: str, ls_hello_command: str,
                                       cloud: str, only_mount: bool,
                                       include_mount_cached: bool):
    assert cloud in ['aws', 'gcp', 'azure', 'kubernetes']
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)

    # Set mount flags based on cloud provider
    include_s3_mount = cloud in ['aws', 'kubernetes']
    include_gcs_mount = cloud in ['gcp', 'kubernetes']
    include_azure_mount = cloud == 'azure'

    if cloud == 'kubernetes':
        enabled_cloud_storages = smoke_tests_utils.get_enabled_cloud_storages()
        if not clouds.cloud_in_iterable(clouds.AWS(), enabled_cloud_storages):
            include_s3_mount = False
        if not clouds.cloud_in_iterable(clouds.GCP(), enabled_cloud_storages):
            include_gcs_mount = False

    content = template.render(
        storage_name=storage_name,
        cloud=cloud,
        only_mount=only_mount,
        include_s3_mount=include_s3_mount,
        include_gcs_mount=include_gcs_mount,
        include_azure_mount=include_azure_mount,
        include_mount_cached=include_mount_cached,
    )
    f.write(content)
    f.flush()
    file_path = f.name

    test_commands = [
        smoke_tests_utils.launch_cluster_for_cloud_cmd(cloud, cluster_name),
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {cluster_name} --infra {cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
        f'sky logs {cluster_name} 1 --status',  # Ensure job succeeded.
        smoke_tests_utils.run_cloud_cmd_on_cluster(cluster_name,
                                                   cmd=ls_hello_command),
        f'sky stop -y {cluster_name}',
        f'sky start -y {cluster_name}',
        # Check if hello.txt from mounting bucket exists after restart in
        # the mounted directory
        f'sky exec {cluster_name} -- "set -ex; ls /mount_private_mount/hello.txt"',
    ]
    if include_mount_cached and cloud != 'kubernetes':
        if cloud == 'aws':
            rclone_stores = data_utils.Rclone.RcloneStores.S3
        elif cloud == 'gcp':
            rclone_stores = data_utils.Rclone.RcloneStores.GCS
        elif cloud == 'azure':
            rclone_stores = data_utils.Rclone.RcloneStores.AZURE
        else:
            raise ValueError(f'Invalid cloud provider: {cloud}')
        rclone_profile_name = rclone_stores.get_profile_name(storage_name)
        test_commands.append(
            f'sky exec {cluster_name} -- "set -ex; '
            f'rclone ls {rclone_profile_name}:{storage_name}/hello.txt;"')
    clean_command = (
        f'sky down -y {cluster_name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(cluster_name)} && '
        f'sky storage delete -y {storage_name}')
    return test_commands, clean_command


@pytest.mark.aws
def test_aws_storage_mounts_arm64():
    """Test S3 storage mounting on ARM64 architecture using rclone."""
    name = smoke_tests_utils.get_cluster_name()
    cloud = 'aws'
    storage_name = f'sky-test-arm64-{int(time.time())}'
    ls_hello_command = f'aws s3 ls {storage_name}/hello.txt'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        # Reuse the existing storage mounts command generator
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, cloud, False, False)

        # Modify the sky launch command to force ARM64 instance
        for i, cmd in enumerate(test_commands):
            if cmd.startswith('sky launch') and '--infra aws' in cmd:
                # Insert ARM64 instance type before the YAML file path
                test_commands[i] = cmd.replace(
                    'sky launch', 'sky launch --instance-type m6g.large'
                ).replace(
                    '--infra aws',
                    # Use ARM64 AMI to make sure the launch succeeds.
                    # The image ID is retrieved with:
                    # aws ec2 describe-images --owners amazon --filters "Name=name,Values=Deep Learning ARM64 Base OSS*Ubuntu 22.04*" --region $REGION --query "Images | sort_by(@, &CreationDate) | [-1].{Name:Name,ImageId:ImageId}" --output text | cat
                    '--infra aws/us-west-2 --image-id ami-03ac43540bf1c63c0')
                break

        # Add ARM64-specific verification
        test_commands.append(
            f'sky exec {name} -- "echo \\"ARM64 architecture: $(uname -m)\\" && cat /mount_private_mount/hello.txt"'
        )

        test = smoke_tests_utils.Test(
            'aws_storage_mounts_arm64',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_storage_mounts_with_stop():
    name = smoke_tests_utils.get_cluster_name()
    cloud = 'aws'
    storage_name = f'sky-test-{int(time.time())}'
    ls_hello_command = f'aws s3 ls {storage_name}/hello.txt'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, cloud, False, True)
        test = smoke_tests_utils.Test(
            'aws_storage_mounts',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_storage_mounts_with_stop_only_mount():
    name = smoke_tests_utils.get_cluster_name()
    cloud = 'aws'
    storage_name = f'sky-test-{int(time.time())}'
    ls_hello_command = f'aws s3 ls {storage_name}/hello.txt'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, cloud, True, False)
        test = smoke_tests_utils.Test(
            'aws_storage_mounts_only_mount',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_storage_mounts_with_stop():
    name = smoke_tests_utils.get_cluster_name()
    cloud = 'gcp'
    storage_name = f'sky-test-{int(time.time())}'
    ls_hello_command = f'{smoke_tests_utils.ACTIVATE_SERVICE_ACCOUNT_AND_GSUTIL} ls gs://{storage_name}/hello.txt'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, cloud, False, True)
        test = smoke_tests_utils.Test(
            'gcp_storage_mounts',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_storage_mounts_with_stop():
    name = smoke_tests_utils.get_cluster_name()
    cloud = 'azure'
    storage_name = f'sky-test-{int(time.time())}'
    storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
    )
    storage_account_key = data_utils.get_az_storage_account_key(
        storage_account_name)
    # if the file does not exist, az storage blob list returns '[]'
    ls_hello_command = (f'output=$(az storage blob list -c {storage_name} '
                        f'--account-name {storage_account_name} '
                        f'--account-key {storage_account_key} '
                        f'--prefix hello.txt) '
                        f'[ "$output" = "[]" ] && exit 1 || exit 0')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, cloud, False, True)
        test = smoke_tests_utils.Test(
            'azure_storage_mounts',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_storage_mounts():
    # Tests bucket mounting on k8s, assuming S3 is configured.
    # S3 mounting now works on all architectures including ARM64
    # (uses rclone fallback for ARM64, goofys for x86_64).
    name = smoke_tests_utils.get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'

    s3_ls_cmd = TestStorageWithCredentials.cli_ls_cmd(storage_lib.StoreType.S3,
                                                      storage_name, 'hello.txt')
    gcs_ls_cmd = TestStorageWithCredentials.cli_ls_cmd(
        storage_lib.StoreType.GCS, storage_name, 'hello.txt')
    azure_ls_cmd = TestStorageWithCredentials.cli_ls_cmd(
        storage_lib.StoreType.AZURE, storage_name, 'hello.txt')

    # For Azure, we need to check if the output is empty list, as it returns []
    # instead of a non-zero exit code when the file doesn't exist
    azure_check_cmd = (f'output=$({azure_ls_cmd}); '
                       f'exit_code=$?; '
                       f'[ "$output" != "[]" ] && '
                       f'[ $exit_code -eq 0 ]')

    ls_hello_command = (f'{s3_ls_cmd} || {{ '
                        f'{gcs_ls_cmd}; }} || {{ '
                        f'{azure_check_cmd}; }}')
    cloud_cmd_cluster_setup_cmd_list = controller_utils._get_cloud_dependencies_installation_commands(
        controller_utils.Controllers.JOBS_CONTROLLER)
    cloud_cmd_cluster_setup_cmd = ' && '.join(cloud_cmd_cluster_setup_cmd_list)
    ls_hello_command = f'{cloud_cmd_cluster_setup_cmd} && {ls_hello_command}'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_command = _storage_mounts_commands_generator(
            f, name, storage_name, ls_hello_command, 'kubernetes', False, False)
        test = smoke_tests_utils.Test(
            'kubernetes_storage_mounts',
            test_commands,
            clean_command,
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_context_switch():
    name = smoke_tests_utils.get_cluster_name()
    new_context = f'sky-test-context-{int(time.time())}'
    new_namespace = f'sky-test-namespace-{int(time.time())}'

    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip('Skipping test because the Kubernetes configs and '
                    'credentials are located on the remote API server '
                    'and not the machine where the test is running')

    test_commands = [
        # Launch a cluster and run a simple task
        f'sky launch -y -c {name} --infra kubernetes "echo Hello from original context"',
        f'sky logs {name} 1 --status',  # Ensure job succeeded

        # Get current context details and save to a file for later use in cleanup
        'CURRENT_CONTEXT=$(kubectl config current-context); '
        'echo "$CURRENT_CONTEXT" > /tmp/sky_test_current_context; '
        'CURRENT_CLUSTER=$(kubectl config view -o jsonpath="{.contexts[?(@.name==\\"$CURRENT_CONTEXT\\")].context.cluster}"); '
        'CURRENT_USER=$(kubectl config view -o jsonpath="{.contexts[?(@.name==\\"$CURRENT_CONTEXT\\")].context.user}"); '

        # Create a new context with a different name and namespace
        f'kubectl config set-context {new_context} --cluster="$CURRENT_CLUSTER" --user="$CURRENT_USER" --namespace={new_namespace}',

        # Create the new namespace if it doesn't exist
        f'kubectl create namespace {new_namespace} --dry-run=client -o yaml | kubectl apply -f -',

        # Set the new context as active
        f'kubectl config use-context {new_context}',

        # Verify the new context is active
        f'[ "$(kubectl config current-context)" = "{new_context}" ] || exit 1',

        # Try to run sky exec on the original cluster (should still work)
        f'sky exec {name} "echo Success: sky exec works after context switch"',

        # Test sky queue
        f'sky queue {name}',

        # Test SSH access
        f'ssh {name} whoami',
    ]

    cleanup_commands = (
        f'kubectl delete namespace {new_namespace}; '
        f'kubectl config delete-context {new_context}; '
        'kubectl config use-context $(cat /tmp/sky_test_current_context); '
        'rm /tmp/sky_test_current_context; '
        f'sky down -y {name}')

    test = smoke_tests_utils.Test(
        'kubernetes_context_switch',
        test_commands,
        cleanup_commands,
        timeout=20 * 60,  # 20 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.cloudflare
def test_cloudflare_storage_mounts(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
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
            *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --infra {generic_cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls s3://{storage_name}/hello.txt --endpoint {endpoint_url} --profile=r2'
        ]

        test = smoke_tests_utils.Test(
            'cloudflare_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.nebius
def test_nebius_storage_mounts(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_nebius_storage_mounting.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --infra {generic_cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'aws s3 ls s3://{storage_name}/hello.txt --profile={nebius.NEBIUS_PROFILE_NAME}'
        ]

        test = smoke_tests_utils.Test(
            'nebius_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_storage_mounts():
    name = smoke_tests_utils.get_cluster_name()
    storage_name = f'sky-test-{int(time.time())}'
    rclone_profile_name = data_utils.Rclone.RcloneStores.IBM.get_profile_name(
        storage_name)
    template_str = pathlib.Path(
        'tests/test_yamls/test_ibm_cos_storage_mounting.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --infra ibm {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'rclone ls {rclone_profile_name}:{storage_name}/hello.txt',
        ]
        test = smoke_tests_utils.Test(
            'ibm_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # VAST does not support multi-cloud features
@pytest.mark.no_fluidstack  # FluidStack doesn't have stable package installation
@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-cloud features
@pytest.mark.parametrize('ignore_file',
                         [constants.SKY_IGNORE_FILE, constants.GIT_IGNORE_FILE])
def test_ignore_exclusions(generic_cloud: str, ignore_file: str):
    """Tests that .skyignore patterns correctly exclude files when using sky launch and sky jobs launch.

    Creates a temporary directory with various files and folders, adds a .skyignore file
    that excludes specific files and folders, then verifies the exclusions work properly
    when using sky launch and sky jobs launch commands.
    """
    ignore_type = ignore_file.lstrip('.').replace('ignore', '')
    name = smoke_tests_utils.get_cluster_name()
    name = f'{name}-{ignore_type}'
    jobs_name = f'{name}-job'

    # Path to the YAML file that defines the task
    yaml_path = 'tests/test_yamls/test_skyignore.yaml'

    # Prepare a temporary directory with test files and .skyignore or .gitignore
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create directory structure
        dirs = [
            'keep_dir', 'exclude_dir', 'nested/keep_subdir',
            'nested/exclude_subdir'
        ]
        files = [
            'script.py',  # Keep (not relevant as we don't use it)
            'exclude.py',  # Exclude
            'data.txt',  # Keep
            'temp.log',  # Exclude
            'keep_dir/keep.txt',  # Keep
            'exclude_dir/notes.md',  # Directory excluded
            'nested/keep_subdir/test.py',  # Keep
            'nested/exclude_subdir/test.sh',  # Directory excluded
        ]

        # Create directories
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)

        # Create files
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(f'Content for {file_path}')

        # Create .skyignore file
        ignore_content = """
# Exclude specific files
exclude.py
*.log

# Exclude directories
/exclude_dir
/nested/exclude_subdir
"""
        with open(os.path.join(temp_dir, ignore_file), 'w',
                  encoding='utf-8') as f:
            f.write(ignore_content)
        if ignore_file == constants.GIT_IGNORE_FILE:
            # Initialize git repository to make sure gitignore is used
            subprocess.run(['git', 'init'], cwd=temp_dir, check=True)

        # Run test commands
        test_commands = [
            # Test with sky launch
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --workdir {temp_dir} {yaml_path}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded

            # Test with sky jobs launch
            f'sky jobs launch -y -n {jobs_name} --infra {generic_cloud} --workdir {temp_dir} {yaml_path}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{jobs_name}',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=60),
        ]

        teardown_commands = [
            f'sky down -y {name}', f'sky jobs cancel -y {jobs_name}'
        ]

        test = smoke_tests_utils.Test(
            'skyignore_exclusion_test',
            test_commands,
            teardown_commands,
            smoke_tests_utils.get_timeout(generic_cloud, 15 * 60),  # 15 mins
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.local
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
    def get_az_storage_account_name(default_region: str = 'centralus'):
        config_storage_account = skypilot_config.get_nested(
            ('azure', 'storage_account'), None)
        if config_storage_account is not None:
            storage_account_name = config_storage_account
        else:
            storage_account_name = (
                storage_lib.AzureBlobStore.get_default_storage_account_name(
                    default_region))
        return storage_account_name

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
            storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
            )
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
        if store_type == storage_lib.StoreType.NEBIUS:
            url = f's3://{bucket_name}'
            return f'aws s3 rb {url} --force --profile={nebius.NEBIUS_PROFILE_NAME}'

        if store_type == storage_lib.StoreType.IBM:
            rclone_profile_name = (data_utils.Rclone.RcloneStores.IBM.
                                   get_profile_name(bucket_name))
            return f'rclone purge {rclone_profile_name}:{bucket_name} && rclone config delete {rclone_profile_name}'

    @classmethod
    def list_all_files(cls, store_type: storage_lib.StoreType,
                       bucket_name: str):
        cmd = cls.cli_ls_cmd(store_type, bucket_name, recursive=True)
        if store_type == storage_lib.StoreType.GCS:
            try:
                out = subprocess.check_output(cmd,
                                              shell=True,
                                              stderr=subprocess.PIPE)
                files = [line[5:] for line in out.decode('utf-8').splitlines()]
            except subprocess.CalledProcessError as e:
                error_output = e.stderr.decode('utf-8')
                if "One or more URLs matched no objects" in error_output:
                    files = []
                else:
                    raise
        elif store_type == storage_lib.StoreType.AZURE:
            out = subprocess.check_output(cmd, shell=True)
            try:
                blobs = json.loads(out.decode('utf-8'))
                files = [blob['name'] for blob in blobs]
            except json.JSONDecodeError:
                files = []
        elif store_type == storage_lib.StoreType.IBM:
            # rclone ls format: "   1234 path/to/file"
            out = subprocess.check_output(cmd, shell=True)
            files = []
            for line in out.decode('utf-8').splitlines():
                # Skip empty lines
                if not line.strip():
                    continue
                # Split by whitespace and get the file path (last column)
                parts = line.strip().split(
                    None, 1)  # Split into max 2 parts (size and path)
                if len(parts) == 2:
                    files.append(parts[1])
        else:
            out = subprocess.check_output(cmd, shell=True)
            files = [
                line.split()[-1] for line in out.decode('utf-8').splitlines()
            ]
        return files

    @staticmethod
    def cli_ls_cmd(store_type, bucket_name, suffix='', recursive=False):
        if store_type == storage_lib.StoreType.S3:
            if suffix:
                url = f's3://{bucket_name}/{suffix}'
            else:
                url = f's3://{bucket_name}'
            cmd = f'aws s3 ls {url}'
            if recursive:
                cmd += ' --recursive'
            return cmd
        if store_type == storage_lib.StoreType.GCS:
            if suffix:
                url = f'gs://{bucket_name}/{suffix}'
            else:
                url = f'gs://{bucket_name}'
            if recursive:
                url = f'"{url}/**"'
            return f'{smoke_tests_utils.ACTIVATE_SERVICE_ACCOUNT_AND_GSUTIL} ls {url}'
        if store_type == storage_lib.StoreType.AZURE:
            # azure isrecursive by default
            storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
            )
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
            recursive_flag = '--recursive' if recursive else ''
            return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls {url} --endpoint {endpoint_url} --profile=r2 {recursive_flag}'
        if store_type == storage_lib.StoreType.NEBIUS:
            if suffix:
                url = f's3://{bucket_name}/{suffix}'
            else:
                url = f's3://{bucket_name}'
            recursive_flag = '--recursive' if recursive else ''
            return f'aws s3 ls {url} --profile={nebius.NEBIUS_PROFILE_NAME} {recursive_flag}'

        if store_type == storage_lib.StoreType.IBM:
            # rclone ls is recursive by default
            bucket_rclone_profile = data_utils.Rclone.generate_rclone_bucket_profile_name(
                bucket_name, data_utils.Rclone.RcloneClouds.IBM)
            return f'rclone ls {bucket_rclone_profile}:{bucket_name}/{suffix}'

    @staticmethod
    def cli_region_cmd(store_type, bucket_name=None, storage_account_name=None):
        if store_type == storage_lib.StoreType.S3:
            assert bucket_name is not None
            return ('aws s3api get-bucket-location '
                    f'--bucket {bucket_name} --output text')
        elif store_type == storage_lib.StoreType.GCS:
            assert bucket_name is not None
            return (
                f'{smoke_tests_utils.ACTIVATE_SERVICE_ACCOUNT_AND_GSUTIL} ls -L -b gs://{bucket_name}/ | '
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
                storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
                )
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
        elif store_type == storage_lib.StoreType.NEBIUS:
            if suffix:
                return f'aws s3api list-objects --bucket "{bucket_name}" --prefix {suffix} --query "length(Contents[?contains(Key,\'{file_name}\')].Key)" --profile={nebius.NEBIUS_PROFILE_NAME}'
            else:
                return f'aws s3api list-objects --bucket "{bucket_name}" --query "length(Contents[?contains(Key,\'{file_name}\')].Key)" --profile={nebius.NEBIUS_PROFILE_NAME}'

    @staticmethod
    def cli_count_file_in_bucket(store_type, bucket_name):
        if store_type == storage_lib.StoreType.S3:
            return f'aws s3 ls s3://{bucket_name} --recursive | wc -l'
        elif store_type == storage_lib.StoreType.GCS:
            return f'gsutil ls -r gs://{bucket_name}/** | wc -l'
        elif store_type == storage_lib.StoreType.AZURE:
            storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
            )
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
        elif store_type == storage_lib.StoreType.NEBIUS:
            return f'aws s3 ls s3://{bucket_name} --recursive --profile={nebius.NEBIUS_PROFILE_NAME} | wc -l'

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
    def tmp_sub_path(self):
        tmp_dir1 = uuid.uuid4().hex[:8]
        tmp_dir2 = uuid.uuid4().hex[:8]
        yield "/".join([tmp_dir1, tmp_dir2])

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
            mode: storage_lib.StorageMode = storage_lib.StorageMode.MOUNT,
            _bucket_sub_path: Optional[str] = None):
        # Creates a temporary storage object. Stores must be added in the test.
        storage_obj = storage_lib.Storage(name=name,
                                          source=source,
                                          stores=stores,
                                          persistent=persistent,
                                          mode=mode,
                                          _bucket_sub_path=_bucket_sub_path)
        storage_obj.construct()
        try:
            yield storage_obj
        finally:
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
            store_obj.construct()
            storage_mult_obj.append(store_obj)
        try:
            yield storage_mult_obj
        finally:
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
        temp_dir = tempfile.TemporaryDirectory(suffix='skypilot-test')
        for name in custom_source_names:
            src_path = pathlib.Path(temp_dir.name) / name
            src_path.expanduser().mkdir(exist_ok=True)
            timestamp = str(time.time()).replace('.', '')
            store_obj = storage_lib.Storage(name=f'sky-test-{timestamp}',
                                            source=str(src_path))
            store_obj.construct()
            storage_mult_obj.append(store_obj)
        try:
            yield storage_mult_obj
        finally:
            for storage_obj in storage_mult_obj:
                handle = global_user_state.get_handle_from_storage_name(
                    storage_obj.name)
                if handle:
                    storage_obj.delete()
            temp_dir.cleanup()

    @pytest.fixture
    def tmp_local_storage_obj(self, tmp_bucket_name, tmp_source):
        # Creates a temporary storage object. Stores must be added in the test.
        yield from self.yield_storage_object(name=tmp_bucket_name,
                                             source=tmp_source)

    @pytest.fixture
    def tmp_local_storage_obj_with_sub_path(self, tmp_bucket_name, tmp_source,
                                            tmp_sub_path):
        # Creates a temporary storage object with sub. Stores must be added in the test.
        list_source = [tmp_source, tmp_source + '/tmp-file']
        yield from self.yield_storage_object(name=tmp_bucket_name,
                                             source=list_source,
                                             _bucket_sub_path=tmp_sub_path)

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
            # Initialize git repository
            subprocess.check_call(['git', 'init'], cwd=tmpdir)

            # Creates file structure to be uploaded in the Storage
            self.create_dir_structure(tmpdir, gitignore_structure)

            # Create .gitignore and list files/dirs to be excluded in it
            skypilot_path = os.path.dirname(os.path.dirname(sky.__file__))
            temp_path = f'{tmpdir}/.gitignore'
            file_path = os.path.join(skypilot_path, 'tests/gitignore_test')
            shutil.copyfile(file_path, temp_path)

            # Create .git/info/exclude and list files/dirs to be excluded in it
            temp_path = f'{tmpdir}/.git/info/'
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
        storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
        )
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
    def tmp_awscli_bucket_nebius(self, tmp_bucket_name):
        # Creates a temporary bucket using awscli
        bucket_uri = f's3://{tmp_bucket_name}'
        subprocess.check_call(
            f'aws s3 mb {bucket_uri} --profile={nebius.NEBIUS_PROFILE_NAME}',
            shell=True)
        yield tmp_bucket_name, f'nebius://{tmp_bucket_name}'
        subprocess.check_call(
            f'aws s3 rb {bucket_uri} --force --profile={nebius.NEBIUS_PROFILE_NAME}',
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
        storage_obj.construct()
        yield storage_obj
        # This does not require any deletion logic because it is a public bucket
        # and should not get added to global_user_state.

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.no_postgres
    @pytest.mark.no_kubernetes
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize('store_type', [
        pytest.param(storage_lib.StoreType.S3, marks=pytest.mark.aws),
        pytest.param(storage_lib.StoreType.GCS, marks=pytest.mark.gcp),
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
    ])
    def test_bucket_sub_path(self, tmp_local_storage_obj_with_sub_path,
                             store_type):
        # Creates a new bucket with a local source, uploads files to it
        # and deletes it.
        region_kwargs = {}
        if store_type == storage_lib.StoreType.AZURE:
            # We have to specify the region for Azure storage, as the default
            # Azure storage account is in centralus region.
            region_kwargs['region'] = 'centralus'

        tmp_local_storage_obj_with_sub_path.add_store(store_type,
                                                      **region_kwargs)

        # Check files under bucket and filter by prefix
        files = self.list_all_files(store_type,
                                    tmp_local_storage_obj_with_sub_path.name)
        assert len(files) > 0
        if store_type == storage_lib.StoreType.GCS:
            assert all([
                file.startswith(
                    tmp_local_storage_obj_with_sub_path.name + '/' +
                    tmp_local_storage_obj_with_sub_path._bucket_sub_path)
                for file in files
            ])
        else:
            assert all([
                file.startswith(
                    tmp_local_storage_obj_with_sub_path._bucket_sub_path)
                for file in files
            ])

        # Check bucket is empty, all files under sub directory should be deleted
        store = tmp_local_storage_obj_with_sub_path.stores[store_type]
        store.is_sky_managed = False
        if store_type == storage_lib.StoreType.AZURE:
            azure.assign_storage_account_iam_role(
                storage_account_name=store.storage_account_name,
                resource_group_name=store.resource_group_name)
        store.delete()
        files = self.list_all_files(store_type,
                                    tmp_local_storage_obj_with_sub_path.name)
        assert len(files) == 0

        # Now, delete the entire bucket
        store.is_sky_managed = True
        tmp_local_storage_obj_with_sub_path.delete()

        # Run sky storage ls to check if storage object is deleted
        out = subprocess.check_output(['sky', 'storage', 'ls'])
        assert tmp_local_storage_obj_with_sub_path.name not in out.decode(
            'utf-8')

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.no_postgres
    @pytest.mark.no_kubernetes
    @pytest.mark.xdist_group('multiple_bucket_deletion')
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius),
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.no_postgres
    @pytest.mark.no_kubernetes
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.no_postgres
    @pytest.mark.no_kubernetes
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize(
        'nonexist_bucket_url',
        [
            's3://{random_name}',
            'gs://{random_name}',
            pytest.param(
                'https://{account_name}.blob.core.windows.net/{random_name}',  # pylint: disable=line-too-long
                marks=pytest.mark.azure),
            pytest.param('cos://us-east/{random_name}', marks=pytest.mark.ibm),
            pytest.param('r2://{random_name}', marks=pytest.mark.cloudflare),
            pytest.param('nebius://{random_name}', marks=pytest.mark.nebius)
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
                storage_account_name = TestStorageWithCredentials.get_az_storage_account_name(
                )
                storage_account_key = data_utils.get_az_storage_account_key(
                    storage_account_name)
                command = f'az storage container exists --account-name {storage_account_name} --account-key {storage_account_key} --name {nonexist_bucket_name}'
                expected_output = '"exists": false'
            elif nonexist_bucket_url.startswith('r2'):
                endpoint_url = cloudflare.create_endpoint()
                command = f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3api head-bucket --bucket {nonexist_bucket_name} --endpoint {endpoint_url} --profile=r2'
                expected_output = '404'
            elif nonexist_bucket_url.startswith('nebius'):
                command = f'aws s3api head-bucket --bucket {nonexist_bucket_name} --profile={nebius.NEBIUS_PROFILE_NAME}'
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
            storage_obj.construct()

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
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
        with pytest.raises(
                sky.exceptions.StorageBucketGetError,
                match=storage_lib._BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
                    name=private_bucket_name)):
            storage_obj = storage_lib.Storage(source=private_bucket)
            storage_obj.construct()

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
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
                                           marks=pytest.mark.cloudflare),
                              pytest.param('tmp_awscli_bucket_nebius',
                                           storage_lib.StoreType.NEBIUS,
                                           marks=pytest.mark.nebius)])
    def test_upload_to_existing_bucket(self, ext_bucket_fixture, request,
                                       tmp_source, store_type):
        # Tries uploading existing files to newly created bucket (outside of
        # sky) and verifies that files are written.
        bucket_name, _ = request.getfixturevalue(ext_bucket_fixture)
        storage_obj = storage_lib.Storage(name=bucket_name, source=tmp_source)
        storage_obj.construct()
        region_kwargs = {}
        if store_type == storage_lib.StoreType.AZURE:
            # We have to specify the region for Azure storage, as the default
            # Azure storage account is in centralus region.
            region_kwargs['region'] = 'centralus'

        storage_obj.add_store(store_type, **region_kwargs)

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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.no_postgres
    @pytest.mark.no_kubernetes
    def test_copy_mount_existing_storage(self,
                                         tmp_copy_mnt_existing_storage_obj):
        # Creates a bucket with no source in MOUNT mode (empty bucket), and
        # then tries to load the same storage in COPY mode.
        tmp_copy_mnt_existing_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_copy_mnt_existing_storage_obj.name

        # Check `sky storage ls` to ensure storage object exists
        out = subprocess.check_output(['sky', 'storage', 'ls']).decode('utf-8')
        assert storage_name in out, f'Storage {storage_name} not found in sky storage ls.'

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare),
        pytest.param(storage_lib.StoreType.NEBIUS, marks=pytest.mark.nebius)
    ])
    def test_list_source(self, tmp_local_list_storage_obj, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        region_kwargs = {}
        if store_type == storage_lib.StoreType.AZURE:
            # We have to specify the region for Azure storage, as the default
            # Azure storage account is in centralus region.
            region_kwargs['region'] = 'centralus'

        tmp_local_list_storage_obj.add_store(store_type, **region_kwargs)

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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
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
                                           marks=pytest.mark.cloudflare),
                              pytest.param(AWS_INVALID_NAMES,
                                           storage_lib.StoreType.NEBIUS,
                                           marks=pytest.mark.nebius)])
    def test_invalid_names(self, invalid_name_list, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        for name in invalid_name_list:
            with pytest.raises(sky.exceptions.StorageNameError):
                storage_obj = storage_lib.Storage(name=name)
                storage_obj.construct()
                storage_obj.add_store(store_type)

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_fluidstack
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize(
        'gitignore_structure, store_type',
        [(GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.S3),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.GCS),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.AZURE),
         pytest.param(GITIGNORE_SYNC_TEST_DIR_STRUCTURE,
                      storage_lib.StoreType.R2,
                      marks=pytest.mark.cloudflare),
         pytest.param(GITIGNORE_SYNC_TEST_DIR_STRUCTURE,
                      storage_lib.StoreType.NEBIUS,
                      marks=pytest.mark.nebius)])
    def test_excluded_file_cloud_storage_upload_copy(self, gitignore_structure,
                                                     store_type,
                                                     tmp_gitignore_storage_obj):
        # tests if files included in .gitignore and .git/info/exclude are
        # excluded from being transferred to Storage
        region_kwargs = {}
        if store_type == storage_lib.StoreType.AZURE:
            # We have to specify the region for Azure storage, as the default
            # Azure storage account is in centralus region.
            region_kwargs['region'] = 'centralus'

        tmp_gitignore_storage_obj.add_store(store_type, **region_kwargs)
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

    @pytest.mark.no_vast  # Requires AWS or S3
    @pytest.mark.no_hyperbolic
    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS),
                              pytest.param('tmp_awscli_bucket_r2',
                                           storage_lib.StoreType.R2,
                                           marks=pytest.mark.cloudflare),
                              pytest.param('tmp_awscli_bucket_nebius',
                                           storage_lib.StoreType.NEBIUS,
                                           marks=pytest.mark.nebius)])
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
            storage_obj.construct()
            storage_obj.add_store(store_type)

        assert 'Attempted to mount a non-sky managed bucket' in str(e)

        # valid spec
        storage_obj = storage_lib.Storage(source=ext_bucket_uri,
                                          mode=storage_lib.StorageMode.MOUNT)
        storage_obj.construct()
        handle = global_user_state.get_handle_from_storage_name(
            storage_obj.name)
        if handle:
            storage_obj.delete()

    @pytest.mark.aws
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

    @pytest.mark.gcp
    @pytest.mark.parametrize('region', [
        'northamerica-northeast1', 'northamerica-northeast2', 'us-central1',
        'us-east1', 'us-east4', 'us-east5', 'us-south1', 'us-west1', 'us-west2',
        'us-west3', 'us-west4', 'southamerica-east1', 'southamerica-west1',
        'europe-central2', 'europe-north1', 'europe-southwest1', 'europe-west1',
        'europe-west2', 'europe-west3', 'europe-west4', 'europe-west6',
        'europe-west8', 'europe-west9', 'europe-west10', 'europe-west12',
        'asia-east1', 'asia-east2', 'asia-northeast1', 'asia-northeast2',
        'asia-northeast3', 'asia-southeast1', 'asia-south1', 'asia-south2',
        'asia-southeast2', 'me-central1', 'me-west1', 'australia-southeast1',
        'australia-southeast2', 'africa-south1'
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
