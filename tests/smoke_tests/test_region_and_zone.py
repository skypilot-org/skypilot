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

import tempfile
import textwrap

import pytest
from smoke_tests.util import get_cluster_name
from smoke_tests.util import get_cmd_wait_until_cluster_status_contains_wildcard
from smoke_tests.util import (
    get_cmd_wait_until_managed_job_status_contains_matching_job_name)
from smoke_tests.util import run_one_test
from smoke_tests.util import Test

from sky.jobs.state import ManagedJobStatus
from sky.skylet import constants
from sky.status_lib import ClusterStatus


# ---------- Test region ----------
@pytest.mark.aws
def test_aws_region():
    name = get_cluster_name()
    test = Test(
        'aws_region',
        [
            f'sky launch -y -c {name} --region us-east-2 examples/minimal.yaml',
            f'sky exec {name} examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-east-2',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-east-2\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} && exit 1 || true\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_with_ssh_proxy_command():
    name = get_cluster_name()

    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(
            textwrap.dedent(f"""\
        aws:
            ssh_proxy_command: ssh -W %h:%p -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null jump-{name}
        """))
        f.flush()
        test = Test(
            'aws_with_ssh_proxy_command',
            [
                f'sky launch -y -c jump-{name} --cloud aws --cpus 2 --region us-east-1',
                # Use jump config
                f'export SKYPILOT_CONFIG={f.name}; '
                f'sky launch -y -c {name} --cloud aws --cpus 2 --region us-east-1 echo hi',
                f'sky logs {name} 1 --status',
                f'export SKYPILOT_CONFIG={f.name}; sky exec {name} echo hi',
                f'sky logs {name} 2 --status',
                # Start a small job to make sure the controller is created.
                f'sky jobs launch -n {name}-0 --cloud aws --cpus 2 --use-spot -y echo hi',
                # Wait other tests to create the job controller first, so that
                # the job controller is not launched with proxy command.
                get_cmd_wait_until_cluster_status_contains_wildcard(
                    cluster_name_wildcard='sky-jobs-controller-*',
                    cluster_status=ClusterStatus.UP.value,
                    timeout=300),
                f'export SKYPILOT_CONFIG={f.name}; sky jobs launch -n {name} --cpus 2 --cloud aws --region us-east-1 -yd echo hi',
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        ManagedJobStatus.SUCCEEDED, ManagedJobStatus.RUNNING,
                        ManagedJobStatus.STARTING
                    ],
                    timeout=300),
            ],
            f'sky down -y {name} jump-{name}; sky jobs cancel -y -n {name}',
        )
        run_one_test(test)


@pytest.mark.gcp
def test_gcp_region_and_service_account():
    name = get_cluster_name()
    test = Test(
        'gcp_region',
        [
            f'sky launch -y -c {name} --region us-central1 --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'curl -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?format=standard&audience=gcp"\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-central1',  # Ensure the region is correct.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .region | grep us-central1\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # A user program should not access SkyPilot runtime env python by default.
            f'sky exec {name} \'which python | grep {constants.SKY_REMOTE_PYTHON_ENV_NAME} && exit 1 || true\'',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_region():
    name = get_cluster_name()
    region = 'eu-de'
    test = Test(
        'region',
        [
            f'sky launch -y -c {name} --cloud ibm --region {region} examples/minimal.yaml',
            f'sky exec {name} --cloud ibm examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep {region}',  # Ensure the region is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_region():
    name = get_cluster_name()
    test = Test(
        'azure_region',
        [
            f'sky launch -y -c {name} --region eastus2 --cloud azure tests/test_yamls/minimal.yaml',
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep eastus2',  # Ensure the region is correct.
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
    run_one_test(test)


# ---------- Test zone ----------
@pytest.mark.aws
def test_aws_zone():
    name = get_cluster_name()
    test = Test(
        'aws_zone',
        [
            f'sky launch -y -c {name} examples/minimal.yaml --zone us-east-2b',
            f'sky exec {name} examples/minimal.yaml --zone us-east-2b',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-east-2b',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_zone():
    name = get_cluster_name()
    zone = 'eu-de-2'
    test = Test(
        'zone',
        [
            f'sky launch -y -c {name} --cloud ibm examples/minimal.yaml --zone {zone}',
            f'sky exec {name} --cloud ibm examples/minimal.yaml --zone {zone}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep {zone}',  # Ensure the zone is correct.
        ],
        f'sky down -y {name} {name}-2 {name}-3',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_zone():
    name = get_cluster_name()
    test = Test(
        'gcp_zone',
        [
            f'sky launch -y -c {name} --zone us-central1-a --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky exec {name} --zone us-central1-a --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status --all | grep {name} | grep us-central1-a',  # Ensure the zone is correct.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)
