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

import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Dict, Optional
import urllib.parse
import uuid

import jinja2
import pytest
from smoke_tests.util import get_cluster_name
from smoke_tests.util import get_timeout
from smoke_tests.util import run_one_test
from smoke_tests.util import SCP_TYPE
from smoke_tests.util import STORAGE_SETUP_COMMANDS
from smoke_tests.util import Test

import sky
from sky import global_user_state
from sky import skypilot_config
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.data.data_utils import Rclone


# ---------- file_mounts ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_file_mounts instead.
def test_file_mounts(generic_cloud: str):
    name = get_cluster_name()
    extra_flags = ''
    if generic_cloud in 'kubernetes':
        # Kubernetes does not support multi-node
        # NOTE: This test will fail if you have a Kubernetes cluster running on
        #  arm64 (e.g., Apple Silicon) since goofys does not work on arm64.
        extra_flags = '--num-nodes 1'
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
        f'sky launch -y -c {name} --cloud {generic_cloud} {extra_flags} examples/using_file_mounts.yaml',
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts',
        test_commands,
        f'sky down -y {name}',
        get_timeout(generic_cloud, 20 * 60),  # 20 mins
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_file_mounts():
    name = get_cluster_name()
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
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


@pytest.mark.no_fluidstack  # Requires GCP to be enabled
def test_using_file_mounts_with_env_vars(generic_cloud: str):
    name = get_cluster_name()
    storage_name = TestStorageWithCredentials.generate_bucket_name()
    test_commands = [
        *STORAGE_SETUP_COMMANDS,
        (f'sky launch -y -c {name} --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name}'),
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        # Override with --env:
        (f'sky launch -y -c {name}-2 --cpus 2+ --cloud {generic_cloud} '
         'examples/using_file_mounts_with_env_vars.yaml '
         f'--env MY_BUCKET={storage_name} '
         '--env MY_LOCAL_PATH=tmpfile'),
        f'sky logs {name}-2 1 --status',  # Ensure the job succeeded.
    ]
    test = Test(
        'using_file_mounts_with_env_vars',
        test_commands,
        (f'sky down -y {name} {name}-2',
         f'sky storage delete -y {storage_name} {storage_name}-2'),
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- storage ----------
@pytest.mark.aws
def test_aws_storage_mounts_with_stop():
    name = get_cluster_name()
    cloud = 'aws'
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
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
    name = get_cluster_name()
    cloud = 'gcp'
    storage_name = f'sky-test-{int(time.time())}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
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


@pytest.mark.azure
def test_azure_storage_mounts_with_stop():
    name = get_cluster_name()
    cloud = 'azure'
    storage_name = f'sky-test-{int(time.time())}'
    default_region = 'eastus'
    storage_account_name = (storage_lib.AzureBlobStore.
                            get_default_storage_account_name(default_region))
    storage_account_key = data_utils.get_az_storage_account_key(
        storage_account_name)
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(storage_name=storage_name, cloud=cloud)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {cloud} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'output=$(az storage blob list -c {storage_name} --account-name {storage_account_name} --account-key {storage_account_key} --prefix hello.txt)'
            # if the file does not exist, az storage blob list returns '[]'
            f'[ "$output" = "[]" ] && exit 1;'
            f'sky stop -y {name}',
            f'sky start -y {name}',
            # Check if hello.txt from mounting bucket exists after restart in
            # the mounted directory
            f'sky exec {name} -- "set -ex; ls /mount_private_mount/hello.txt"'
        ]
        test = Test(
            'azure_storage_mounts',
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
    name = get_cluster_name()
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
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud kubernetes {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            f'aws s3 ls {storage_name}/hello.txt || '
            f'gsutil ls gs://{storage_name}/hello.txt',
        ]
        test = Test(
            'kubernetes_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_context_switch():
    name = get_cluster_name()
    new_context = f'sky-test-context-{int(time.time())}'
    new_namespace = f'sky-test-namespace-{int(time.time())}'

    test_commands = [
        # Launch a cluster and run a simple task
        f'sky launch -y -c {name} --cloud kubernetes "echo Hello from original context"',
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

    test = Test(
        'kubernetes_context_switch',
        test_commands,
        cleanup_commands,
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
    ])
def test_docker_storage_mounts(generic_cloud: str, image_id: str):
    # Tests bucket mounting on docker container
    name = get_cluster_name()
    timestamp = str(time.time()).replace('.', '')
    storage_name = f'sky-test-{timestamp}'
    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mounting.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    # ubuntu 18.04 does not support fuse3, and blobfuse2 depends on fuse3.
    azure_mount_unsupported_ubuntu_version = '18.04'
    # Commands to verify bucket upload. We need to check all three
    # storage types because the optimizer may pick any of them.
    s3_command = f'aws s3 ls {storage_name}/hello.txt'
    gsutil_command = f'gsutil ls gs://{storage_name}/hello.txt'
    azure_blob_command = TestStorageWithCredentials.cli_ls_cmd(
        storage_lib.StoreType.AZURE, storage_name, suffix='hello.txt')
    if azure_mount_unsupported_ubuntu_version in image_id:
        # The store for mount_private_mount is not specified in the template.
        # If we're running on Azure, the private mount will be created on
        # azure blob. That will not be supported on the ubuntu 18.04 image
        # and thus fail. For other clouds, the private mount on other
        # storage types (GCS/S3) should succeed.
        include_private_mount = False if generic_cloud == 'azure' else True
        content = template.render(storage_name=storage_name,
                                  include_azure_mount=False,
                                  include_private_mount=include_private_mount)
    else:
        content = template.render(storage_name=storage_name,)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id {image_id} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            # Check AWS, GCP, or Azure storage mount.
            f'{s3_command} || '
            f'{gsutil_command} || '
            f'{azure_blob_command}',
        ]
        test = Test(
            'docker_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        run_one_test(test)


@pytest.mark.cloudflare
def test_cloudflare_storage_mounts(generic_cloud: str):
    name = get_cluster_name()
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
            *STORAGE_SETUP_COMMANDS,
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
    name = get_cluster_name()
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
            *STORAGE_SETUP_COMMANDS,
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
            default_region = 'eastus'
            storage_account_name = (
                storage_lib.AzureBlobStore.get_default_storage_account_name(
                    default_region))
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
        if store_type == storage_lib.StoreType.AZURE:
            default_region = 'eastus'
            config_storage_account = skypilot_config.get_nested(
                ('azure', 'storage_account'), None)
            storage_account_name = config_storage_account if (
                config_storage_account is not None) else (
                    storage_lib.AzureBlobStore.get_default_storage_account_name(
                        default_region))
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
            return f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} aws s3 ls {url} --endpoint {endpoint_url} --profile=r2'
        if store_type == storage_lib.StoreType.IBM:
            bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
                bucket_name, Rclone.RcloneClouds.IBM)
            return f'rclone ls {bucket_rclone_profile}:{bucket_name}/{suffix}'

    @staticmethod
    def cli_region_cmd(store_type, bucket_name=None, storage_account_name=None):
        if store_type == storage_lib.StoreType.S3:
            assert bucket_name is not None
            return ('aws s3api get-bucket-location '
                    f'--bucket {bucket_name} --output text')
        elif store_type == storage_lib.StoreType.GCS:
            assert bucket_name is not None
            return (f'gsutil ls -L -b gs://{bucket_name}/ | '
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
                default_region = 'eastus'
                storage_account_name = (
                    storage_lib.AzureBlobStore.get_default_storage_account_name(
                        default_region))
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

    @staticmethod
    def cli_count_file_in_bucket(store_type, bucket_name):
        if store_type == storage_lib.StoreType.S3:
            return f'aws s3 ls s3://{bucket_name} --recursive | wc -l'
        elif store_type == storage_lib.StoreType.GCS:
            return f'gsutil ls -r gs://{bucket_name}/** | wc -l'
        elif store_type == storage_lib.StoreType.AZURE:
            default_region = 'eastus'
            storage_account_name = (
                storage_lib.AzureBlobStore.get_default_storage_account_name(
                    default_region))
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
    def tmp_multiple_custom_source_storage_obj(self):
        # Creates a list of storage objects with custom source names to
        # create multiple scratch storages.
        # Stores for each object in the list must be added in the test.
        custom_source_names = ['"path With Spaces"', 'path With Spaces']
        storage_mult_obj = []
        for name in custom_source_names:
            src_path = os.path.expanduser(f'~/{name}')
            pathlib.Path(src_path).expanduser().mkdir(exist_ok=True)
            timestamp = str(time.time()).replace('.', '')
            store_obj = storage_lib.Storage(name=f'sky-test-{timestamp}',
                                            source=src_path)
            storage_mult_obj.append(store_obj)
        yield storage_mult_obj
        for storage_obj in storage_mult_obj:
            handle = global_user_state.get_handle_from_storage_name(
                storage_obj.name)
            if handle:
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
        # format. Created storage object will contain the file structure along
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
        default_region = 'eastus'
        storage_account_name = (
            storage_lib.AzureBlobStore.get_default_storage_account_name(
                default_region))
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
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

    @pytest.mark.no_fluidstack
    @pytest.mark.xdist_group('multiple_bucket_deletion')
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
        pytest.param(storage_lib.StoreType.IBM, marks=pytest.mark.ibm),
        pytest.param(storage_lib.StoreType.R2, marks=pytest.mark.cloudflare)
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
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

    @pytest.mark.no_fluidstack
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'nonexist_bucket_url',
        [
            's3://{random_name}',
            'gs://{random_name}',
            pytest.param(
                'https://{account_name}.blob.core.windows.net/{random_name}',  # pylint: disable=line-too-long
                marks=pytest.mark.azure),
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
            elif nonexist_bucket_url.startswith('https'):
                default_region = 'eastus'
                storage_account_name = (
                    storage_lib.AzureBlobStore.get_default_storage_account_name(
                        default_region))
                storage_account_key = data_utils.get_az_storage_account_key(
                    storage_account_name)
                command = f'az storage container exists --account-name {storage_account_name} --account-key {storage_account_key} --name {nonexist_bucket_name}'
                expected_output = '"exists": false'
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

    @pytest.mark.no_fluidstack
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

    @pytest.mark.no_fluidstack
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
                                           marks=pytest.mark.cloudflare)])
    def test_upload_to_existing_bucket(self, ext_bucket_fixture, request,
                                       tmp_source, store_type):
        # Tries uploading existing files to newly created bucket (outside of
        # sky) and verifies that files are written.
        bucket_name, _ = request.getfixturevalue(ext_bucket_fixture)
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

    @pytest.mark.no_fluidstack
    def test_copy_mount_existing_storage(self,
                                         tmp_copy_mnt_existing_storage_obj):
        # Creates a bucket with no source in MOUNT mode (empty bucket), and
        # then tries to load the same storage in COPY mode.
        tmp_copy_mnt_existing_storage_obj.add_store(storage_lib.StoreType.S3)
        storage_name = tmp_copy_mnt_existing_storage_obj.name

        # Check `sky storage ls` to ensure storage object exists
        out = subprocess.check_output(['sky', 'storage', 'ls']).decode('utf-8')
        assert storage_name in out, f'Storage {storage_name} not found in sky storage ls.'

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('store_type', [
        storage_lib.StoreType.S3, storage_lib.StoreType.GCS,
        pytest.param(storage_lib.StoreType.AZURE, marks=pytest.mark.azure),
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

    @pytest.mark.no_fluidstack
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
                                           marks=pytest.mark.cloudflare)])
    def test_invalid_names(self, invalid_name_list, store_type):
        # Uses a list in the source field to specify a file and a directory to
        # be uploaded to the storage object.
        for name in invalid_name_list:
            with pytest.raises(sky.exceptions.StorageNameError):
                storage_obj = storage_lib.Storage(name=name)
                storage_obj.add_store(store_type)

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize(
        'gitignore_structure, store_type',
        [(GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.S3),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.GCS),
         (GITIGNORE_SYNC_TEST_DIR_STRUCTURE, storage_lib.StoreType.AZURE),
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

    @pytest.mark.parametrize('ext_bucket_fixture, store_type',
                             [('tmp_awscli_bucket', storage_lib.StoreType.S3),
                              ('tmp_gsutil_bucket', storage_lib.StoreType.GCS),
                              pytest.param('tmp_awscli_bucket_r2',
                                           storage_lib.StoreType.R2,
                                           marks=pytest.mark.cloudflare)])
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
            storage_obj.add_store(store_type)

        assert 'Attempted to mount a non-sky managed bucket' in str(e)

        # valid spec
        storage_obj = storage_lib.Storage(source=ext_bucket_uri,
                                          mode=storage_lib.StorageMode.MOUNT)
        handle = global_user_state.get_handle_from_storage_name(
            storage_obj.name)
        if handle:
            storage_obj.delete()

    @pytest.mark.no_fluidstack
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

    @pytest.mark.no_fluidstack
    @pytest.mark.parametrize('region', [
        'northamerica-northeast1', 'northamerica-northeast2', 'us-central1',
        'us-east1', 'us-east4', 'us-east5', 'us-south1', 'us-west1', 'us-west2',
        'us-west3', 'us-west4', 'southamerica-east1', 'southamerica-west1',
        'europe-central2', 'europe-north1', 'europe-southwest1', 'europe-west1',
        'europe-west2', 'europe-west3', 'europe-west4', 'europe-west6',
        'europe-west8', 'europe-west9', 'europe-west10', 'europe-west12',
        'asia-east1', 'asia-east2', 'asia-northeast1', 'asia-northeast2',
        'asia-northeast3', 'asia-southeast1', 'asia-south1', 'asia-south2',
        'asia-southeast2', 'me-central1', 'me-central2', 'me-west1',
        'australia-southeast1', 'australia-southeast2', 'africa-south1'
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
