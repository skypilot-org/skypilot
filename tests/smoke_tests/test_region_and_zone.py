# Smoke tests for SkyPilot for reg
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_region_and_zone.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_region_and_zone.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_region_and_zone.py::test_aws_region
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_region_and_zone.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_region_and_zone.py --generic-cloud aws

import pathlib
import shlex
import tempfile
import textwrap
import time

import jinja2
import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests import test_mount_and_storage

import sky
from sky import skypilot_config
from sky.data import storage as storage_lib
from sky.skylet import constants
from sky.utils import controller_utils


# ---------- Test region ----------
@pytest.mark.aws
def test_aws_region():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_region',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra */us-east-2 examples/minimal.yaml',
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep us-east-2',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-east-2\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} && exit 1 || true\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_with_ssh_proxy_command():
    name = smoke_tests_utils.get_cluster_name()
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(
            textwrap.dedent(f"""\
        aws:
            ssh_proxy_command: ssh -W '[%h]:%p' -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null jump-{name}
        """))
        f.write(
            textwrap.dedent(f"""\
            api_server:
                endpoint: {smoke_tests_utils.get_api_server_url()}
            """))
        f.flush()

        jobs_controller_proxy_test_cmds = []
        jobs_controller_proxy_test_cmds_down = ''
        # Disable controller related tests for consolidation mode, as there will
        # not be any controller VM to launch.
        if not smoke_tests_utils.server_side_is_consolidation_mode():
            jobs_controller_proxy_test_cmds = [
                # Start a small job to make sure the controller is created.
                f'sky jobs launch -n {name}-0 --infra aws {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot -y echo hi',
                # Wait other tests to create the job controller first, so that
                # the job controller is not launched with proxy command.
                smoke_tests_utils.
                get_cmd_wait_until_cluster_status_contains_wildcard(
                    cluster_name_wildcard='sky-jobs-controller-*',
                    cluster_status=[sky.ClusterStatus.UP],
                    timeout=300),
                f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={f.name}; sky jobs launch -n {name} --infra aws/us-east-1 {smoke_tests_utils.LOW_RESOURCE_ARG} -yd echo hi',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.SUCCEEDED,
                        sky.ManagedJobStatus.RUNNING,
                        sky.ManagedJobStatus.STARTING
                    ],
                    timeout=300),
            ]
            jobs_controller_proxy_test_cmds_down = f'sky jobs cancel -y -n {name}'

        test = smoke_tests_utils.Test(
            'aws_with_ssh_proxy_command',
            [
                f'sky launch -y -c jump-{name} --infra aws/us-east-1 {smoke_tests_utils.LOW_RESOURCE_ARG}',
                # Use jump config
                f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={f.name}; '
                f'sky launch -y -c {name} --infra aws/us-east-1 {smoke_tests_utils.LOW_RESOURCE_ARG} echo hi',
                f'sky logs {name} 1 --status',
                f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={f.name}; sky exec {name} echo hi',
                f'sky logs {name} 2 --status',
                *jobs_controller_proxy_test_cmds,
            ],
            f'sky down -y {name} jump-{name}; {jobs_controller_proxy_test_cmds_down}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_region_and_service_account():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_region',
        [
            f'sky launch -y -c {name} --infra gcp/us-central1 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'curl -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?format=standard&audience=gcp"\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep us-central1',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-central1\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} && exit 1 || true\'',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_region():
    name = smoke_tests_utils.get_cluster_name()
    region = 'eu-de'
    test = smoke_tests_utils.Test(
        'region',
        [
            f'sky launch -y -c {name} --infra ibm/{region} examples/minimal.yaml',
            f'sky exec {name} --infra ibm examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep {region}',  # Ensure the region is correct.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_region():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure_region',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra azure/eastus2 tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep eastus2',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep eastus2\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .zone | grep null\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} && exit 1 || true\'',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test zone ----------
@pytest.mark.aws
def test_aws_zone():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_zone',
        [
            f'sky launch -y -c {name} examples/minimal.yaml {smoke_tests_utils.LOW_RESOURCE_ARG} --infra */*/us-east-2b',
            f'sky exec {name} examples/minimal.yaml --infra */*/us-east-2b',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep us-east-2b',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_zone():
    name = smoke_tests_utils.get_cluster_name()
    zone = 'eu-de-2'
    test = smoke_tests_utils.Test(
        'zone',
        [
            f'sky launch -y -c {name} --infra ibm/*/{zone} examples/minimal.yaml {smoke_tests_utils.LOW_RESOURCE_ARG}',
            f'sky exec {name} --infra ibm/*/{zone} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep {zone}',  # Ensure the zone is correct.
        ],
        f'sky down -y {name} {name}-2 {name}-3',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_zone():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_zone',
        [
            f'sky launch -y -c {name} --infra gcp/*/us-central1-a {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky exec {name} --infra gcp/*/us-central1-a tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status -v | grep {name} | grep us-central1-a',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# TODO (SKY-1119): These tests may fail as it can require access cloud
# credentials for getting azure storage commands, even though the API server
# is running remotely. We should fix this.
@pytest.mark.no_vast  # Requires AWS
@pytest.mark.no_hyperbolic  # Requires AWS
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
    name = smoke_tests_utils.get_cluster_name()
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
    gsutil_command = (
        f'{{ {smoke_tests_utils.ACTIVATE_SERVICE_ACCOUNT_AND_GSUTIL} '
        f'ls gs://{storage_name}/hello.txt; }}')
    azure_blob_command = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
        storage_lib.StoreType.AZURE, storage_name, suffix='hello.txt')
    # TODO(zpoint): this is a temporary fix. We should make it more robust.
    # If azure is used, the azure blob storage checking assumes the bucket is
    # created in the centralus region when getting the storage account. We
    # should set the cluster to be launched in the same region.
    region_str = f'/centralus' if generic_cloud == 'azure' else ''
    if smoke_tests_utils.api_server_endpoint_configured_in_env_file():
        # Assume only AWS is used for storage when using a remote API server.
        # TODO: Find a better way to decide which cloud to use for storage
        # when using a remote API server.
        content = template.render(storage_name=storage_name,
                                  include_gcs_mount=False,
                                  include_azure_mount=False)
    elif azure_mount_unsupported_ubuntu_version in image_id:
        # The store for mount_private_mount is not specified in the template.
        # If we're running on Azure, the private mount will be created on
        # azure blob. Also, if we're running on Kubernetes, the private mount
        # might be created on azure blob to avoid the issue of the fuse adapter
        # not being able to access the mount point. That will not be supported on
        # the ubuntu 18.04 image and thus fail. For other clouds, the private mount
        # on other storage types (GCS/S3) should succeed.
        include_private_mount = False if (
            generic_cloud == 'azure' or generic_cloud == 'kubernetes') else True
        content = template.render(storage_name=storage_name,
                                  include_azure_mount=False,
                                  include_private_mount=include_private_mount)
    else:
        content = template.render(storage_name=storage_name,)
    cloud_dependencies_setup_cmd = ' && '.join(
        controller_utils._get_cloud_dependencies_installation_commands(
            controller_utils.Controllers.JOBS_CONTROLLER))
    quoted_check = shlex.quote(
        f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV} && '
        f'{cloud_dependencies_setup_cmd}; {s3_command} || {gsutil_command} || '
        f'{azure_blob_command}')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test_commands = [
            *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
            f'sky launch -y -c {name} --infra {generic_cloud}{region_str} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id {image_id} {file_path}',
            f'sky logs {name} 1 --status',  # Ensure job succeeded.
            # Check AWS, GCP, or Azure storage mount.
            f'sky exec {name} {quoted_check}',
            f'sky logs {name} 2 --status',  # Ensure the bucket check succeeded.
        ]
        test = smoke_tests_utils.Test(
            'docker_storage_mounts',
            test_commands,
            f'sky down -y {name}; sky storage delete -y {storage_name}',
            timeout=20 * 60,  # 20 mins
        )
        smoke_tests_utils.run_one_test(test)
